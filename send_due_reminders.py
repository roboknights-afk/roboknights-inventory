# Sends "your loan is due back soon" emails. This is NOT part of app.py —
# it's a separate script meant to be run once a day by a GitHub Actions
# schedule (see .github/workflows/due-reminders.yml), independent of
# whether anyone has the Streamlit app open. That's the whole point: a
# reminder due on a Tuesday should go out on Tuesday, not "whenever
# someone next happens to open the app."
#
# To test this on your own laptop: `python send_due_reminders.py`
# (needs the same .env file app.py uses).

import os
import re
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Duplicated from shared.py's EXUN_CHANNEL_STUDENT_EMAILS (this script
# deliberately doesn't import shared.py — see the WhatsApp helpers below
# for why) — keep in sync if EXUN_CHANNEL_MEMBERS ever changes there.
EXUN_CHANNEL_STUDENT_EMAILS = {
    "r22639naitik@dpsrkp.net",  # Naitik Jindal
    "r23444kyraan@dpsrkp.net",  # Kyraan Katyal
    "v09045medhansh@dpsrkp.net",  # Medhansh Tanmay Pandya
    "v09145aryamman@dpsrkp.net",  # Aryamman Ojha
}


def _as_date(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()


def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_SENDER"]
    msg["To"] = to_email

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
        server.starttls()
        server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)


WHATSAPP_API_VERSION = "v22.0"


def _normalize_india_phone(raw):
    # Members typed a plain 10-digit local number at signup, not the
    # country-code'd format WhatsApp's API needs — see the matching
    # helper (and why) in shared.py, duplicated here since this cron
    # script deliberately doesn't import shared.py (no Streamlit
    # dependency needed for a scheduled job).
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("91") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return "91" + digits
    return None


def send_whatsapp(to_phone, template_name, params=None, language_code="en_US"):
    # Best-effort, same spirit as send_email above not being allowed to
    # take down the whole run — except this ALSO no-ops quietly whenever
    # the WhatsApp credentials simply aren't set up yet (a manual step in
    # Meta's console; see CLAUDE.md), so this can be wired in now and just
    # starts working the day those two secrets are added.
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    if not phone_number_id or not access_token:
        return

    to = _normalize_india_phone(to_phone)
    if not to:
        return

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {"name": template_name, "language": {"code": language_code}},
    }
    if params:
        payload["template"]["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}
        ]

    try:
        requests.post(
            f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


def send_reminders_for(days_before, sent_column):
    # e.g. days_before=2 finds every loan due exactly 2 days from today
    # that hasn't had its 2-day reminder sent yet.
    target_date = (date.today() + timedelta(days=days_before)).isoformat()

    due_requests = (
        client.table("requests")
        .select("*")
        .eq("status", "approved")
        .eq("due_date", target_date)
        .eq(sent_column, False)
        .execute()
        .data
    )
    if not due_requests:
        print(f"No {days_before}-day reminders to send.")
        return

    users = {
        u["user_id"]: u
        for u in client.table("users").select("user_id, name, email, phone_no").execute().data
    }
    parts = {p["part_id"]: p for p in client.table("parts").select("part_id, part_number, name").execute().data}

    for req in due_requests:
        requester = users.get(req["requester_id"])
        part = parts.get(req["part_id"])
        if not requester or not part:
            continue

        send_email(
            requester["email"],
            f"Reminder: {part['part_number']} due back in {days_before} day(s)",
            f"Just a reminder — {part['part_number']} ({part['name']}) is due back "
            f"in {days_before} day(s), on {req['due_date']}.",
        )
        # First WhatsApp template — see CLAUDE.md for the exact wording to
        # submit for approval in Meta's console: "part_due_reminder",
        # Utility category, body "Reminder: {{1}} is due back in {{2}}
        # day(s), on {{3}}." No-ops until WHATSAPP_* secrets exist.
        send_whatsapp(
            requester.get("phone_no"), "part_due_reminder",
            [f"{part['part_number']} ({part['name']})", days_before, req["due_date"]],
        )
        client.table("requests").update({sent_column: True}).eq("request_id", req["request_id"]).execute()
        print(f"Sent {days_before}-day reminder for request {req['request_id']} ({part['part_number']})")


def send_competition_reminders():
    # Finds every competition happening tomorrow, and emails everyone
    # selected (event_volunteers.selected) for one of its events — same
    # "hasn't been sent yet" guard as the loan reminders above, just its
    # own column since this is a different kind of reminder.
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # not_attending competitions are explicitly skipped — nobody should get
    # a "get ready for tomorrow" reminder for something the club isn't
    # actually sending anyone to.
    competitions = (
        client.table("competitions")
        .select("*")
        .eq("competition_date", tomorrow)
        .eq("not_attending", False)
        .execute()
        .data
    )
    if not competitions:
        print("No competitions tomorrow.")
        return

    users = {u["user_id"]: u for u in client.table("users").select("user_id, name, email").execute().data}

    for comp in competitions:
        events = (
            client.table("competition_events")
            .select("*")
            .eq("competition_id", comp["competition_id"])
            .execute()
            .data
        )
        for event in events:
            selected_volunteers = (
                client.table("event_volunteers")
                .select("*")
                .eq("event_id", event["event_id"])
                .eq("selected", True)
                .eq("reminder_sent", False)
                .execute()
                .data
            )
            for volunteer in selected_volunteers:
                user = users.get(volunteer["user_id"])
                if not user:
                    continue

                send_email(
                    user["email"],
                    f"Tomorrow: {event['name']} at {comp['name']}",
                    f"Reminder — {comp['name']} is tomorrow, and you're selected for "
                    f"{event['name']}.\n\n"
                    f"Log in to the app and update your bot's status before the "
                    f"competition.",
                )
                client.table("event_volunteers").update({"reminder_sent": True}).eq(
                    "volunteer_id", volunteer["volunteer_id"]
                ).execute()
                print(f"Sent competition reminder to {user['name']} for {event['name']}")


def send_registration_deadline_reminders():
    # Fires the morning (9am IST, same run as the other reminders below) a
    # competition's own registration_deadline arrives. Tries the named
    # student in-charge first — a simple exact name match against users,
    # since that's what's actually typed into that free-text field — and
    # falls back to the two standing club addresses when there's no match
    # (typo'd name, in-charge isn't a member with an app account, etc.),
    # so a deadline reminder is never silently dropped.
    today = date.today().isoformat()

    competitions = (
        client.table("competitions")
        .select("*")
        .eq("registration_deadline", today)
        .eq("not_attending", False)
        .eq("registration_reminder_sent", False)
        .execute()
        .data
    )
    if not competitions:
        print("No registration deadlines today.")
        return

    user_by_name = {
        u["name"].strip().lower(): u
        for u in client.table("users").select("user_id, name, email").execute().data
        if u.get("name")
    }

    for comp in competitions:
        incharge_name = (comp.get("student_incharge") or "").strip()
        matched_user = user_by_name.get(incharge_name.lower()) if incharge_name else None

        subject = f"Registration deadline today: {comp['name']}"
        body = (
            f"Today ({comp['registration_deadline']}) is the registration deadline for "
            f"{comp['name']}" + (f" at {comp['venue']}" if comp.get("venue") else "") + ". "
            f"Please make sure registration is completed."
        )

        if matched_user:
            send_email(matched_user["email"], subject, body)
            print(f"Sent registration deadline reminder for {comp['name']} to {matched_user['name']}")
        else:
            send_email("roboknights@dpsrkp.net", subject, body)
            send_email("exun@dpsrkp.net", subject, body)
            print(
                f"Sent registration deadline reminder for {comp['name']} to roboknights@/exun@ "
                f"(no member matched student in-charge \"{incharge_name}\")"
            )

        client.table("competitions").update({"registration_reminder_sent": True}).eq(
            "competition_id", comp["competition_id"]
        ).execute()


def send_unread_query_reminders():
    # A nudge on TOP of the immediate "New reply" email queries.py already
    # sends — for whoever missed/ignored that one. Only ever targets the
    # student side: the host is always staff, and this reminder is
    # students-only by design (see EXUN_CHANNEL_STUDENT_EMAILS above for
    # the same rule applied to the Exun channel). Only fires for a reply
    # that's been sitting unread since before today, so it never doubles
    # up with the immediate same-day email.
    today = date.today()
    queries = client.table("queries").select("*").execute().data
    if not queries:
        return
    messages = client.table("query_messages").select("*").execute().data
    users = {u["user_id"]: u for u in client.table("users").select("user_id, name, email").execute().data}

    messages_by_query = {}
    for m in messages:
        messages_by_query.setdefault(m["query_id"], []).append(m)

    for q in queries:
        student = users.get(q["student_id"])
        if not student:
            continue
        thread_messages = messages_by_query.get(q["query_id"], [])
        latest_from_host = max(
            (m["created_at"] for m in thread_messages if m["sender_id"] != q["student_id"]),
            default=None,
        )
        if not latest_from_host or _as_date(latest_from_host) >= today:
            continue
        student_read_at = q.get("student_read_at")
        if student_read_at and student_read_at >= latest_from_host:
            continue  # already read
        sent_at = q.get("student_unread_reminder_sent_at")
        if sent_at and sent_at >= latest_from_host:
            continue  # already nudged for this exact reply
        send_email(
            student["email"],
            "You have an unread reply in Queries",
            f"You have a reply waiting in your Queries thread on the app that you "
            f"haven't seen yet.\n\nLog in to the app to read it: {os.environ.get('APP_URL', '')}",
        )
        client.table("queries").update({
            "student_unread_reminder_sent_at": datetime.now(timezone.utc).isoformat(),
        }).eq("query_id", q["query_id"]).execute()
        print(f"Sent unread-query reminder to {student['name']}")


def send_unread_exun_reminders():
    # Same "unread since before today, only nudge once" shape as the query
    # reminder above, but per-member (not per-thread) since the Exun
    # channel is one flat shared conversation. Only the four named
    # RoboKnights STUDENT members get nudged — never the staff/host
    # accounts or Exun themselves, per explicit instruction.
    today = date.today()
    messages = client.table("exun_channel_messages").select("created_at").execute().data
    latest_message_at = max((m["created_at"] for m in messages), default=None)
    if not latest_message_at or _as_date(latest_message_at) >= today:
        return

    users = {
        u["email"]: u
        for u in client.table("users").select("user_id, name, email").execute().data
        if u["email"] in EXUN_CHANNEL_STUDENT_EMAILS
    }
    if not users:
        return
    reads = {
        r["user_id"]: r
        for r in client.table("exun_channel_reads").select("*").execute().data
    }

    for email, user in users.items():
        read_row = reads.get(user["user_id"])
        last_read_at = read_row.get("last_read_at") if read_row else None
        if last_read_at and last_read_at >= latest_message_at:
            continue  # already read
        sent_at = read_row.get("unread_reminder_sent_at") if read_row else None
        if sent_at and sent_at >= latest_message_at:
            continue  # already nudged for this exact message
        send_email(
            email,
            "You have unread messages in the Exun channel",
            f"There's a message waiting in the Exun channel on the app that you "
            f"haven't seen yet.\n\nLog in to the app to read it: {os.environ.get('APP_URL', '')}",
        )
        client.table("exun_channel_reads").upsert({
            "user_id": user["user_id"],
            "unread_reminder_sent_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"Sent unread-Exun-channel reminder to {user['name']}")


send_reminders_for(2, "reminder_2day_sent")
send_reminders_for(1, "reminder_1day_sent")
send_competition_reminders()
send_registration_deadline_reminders()
send_unread_query_reminders()
send_unread_exun_reminders()

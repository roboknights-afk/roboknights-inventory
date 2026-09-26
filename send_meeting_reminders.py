# Posts "this meeting is in ~24 hours" / "~1 hour" reminders to Discord.
# This is NOT part of app.py — it's a separate script meant to be run
# every 15 minutes by a GitHub Actions schedule (see
# .github/workflows/meeting-reminders.yml), independent of whether anyone
# has the Streamlit app open. Unlike every other scheduled job in this
# project (once a day is plenty for a loan/competition reminder), a
# "1 hour before" reminder needs much tighter polling than the existing
# daily due-reminders.yml runs on, hence its own workflow instead of a
# fifth step tacked onto that one.
#
# Like send_due_reminders.py and send_dashboard_update.py, this
# deliberately does NOT import shared.py: that module imports streamlit,
# which the Actions runner has no reason to install. The handful of
# things it needs (the webhook post, the DM send, the role tags, the
# meeting message wording) are short enough to repeat here — kept in sync
# by hand with the matching pieces of shared.py (send_discord_message,
# send_discord_dm, discord_role_tags, _meeting_discord_body,
# notify_meeting_discord).
#
# To test on your own laptop: `python send_meeting_reminders.py` (needs
# the same .env file app.py uses, including DISCORD_BOT_TOKEN and
# DISCORD_ANNOUNCEMENTS_WEBHOOK_URL).

import os
import re
from datetime import date, datetime, time, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def plain_text_from_rich_html(html):
    # Same as shared.py's plain_text_from_rich_html — kept in sync by
    # hand, same reasoning as every other duplicated helper in this file
    # (see the header comment): agenda is now rich HTML (2026-09-26,
    # dashboard-wide rich-text rollout), and Discord needs plain text.
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()

IST = timezone(timedelta(hours=5, minutes=30))

DISCORD_BOT_USERNAME = "roboknightsbot"
DISCORD_BOT_AVATAR_URL = (
    "https://cdn.discordapp.com/avatars/1536836032329416724/5ebc6d79217e395322b1faf5107e095f.png"
)
DISCORD_AUTOMATED_MARKER = "\n\n***This is automated message***"


def discord_role_tags():
    role_ids = [os.environ.get("DISCORD_MEMBER_ROLE_ID"), os.environ.get("DISCORD_ADHOC_ROLE_ID")]
    return " ".join(f"<@&{rid}>" for rid in role_ids if rid)


def send_discord_channel_message(content):
    webhook_url = os.environ.get("DISCORD_ANNOUNCEMENTS_WEBHOOK_URL")
    if not webhook_url:
        return
    full_content = f"{content}{DISCORD_AUTOMATED_MARKER}"
    try:
        response = requests.post(
            webhook_url,
            json={
                "content": full_content,
                "username": DISCORD_BOT_USERNAME,
                "avatar_url": DISCORD_BOT_AVATAR_URL,
            },
            params={"wait": "true"},
            timeout=10,
        )
        message_id = response.json().get("id")
        if message_id:
            client.table("discord_messages").insert({
                "message_id": message_id, "content": full_content, "channel": "announcements",
            }).execute()
    except Exception as e:
        print(f"Couldn't post the channel reminder ({e}).")


def send_discord_dm(discord_user_id, content):
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token or not discord_user_id:
        return
    headers = {"Authorization": f"Bot {token}"}
    try:
        channel_resp = requests.post(
            "https://discord.com/api/v10/users/@me/channels",
            headers=headers, json={"recipient_id": str(discord_user_id)}, timeout=10,
        )
        channel_id = channel_resp.json().get("id")
        if not channel_id:
            return
        requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers, json={"content": content}, timeout=10,
        )
    except Exception as e:
        print(f"Couldn't DM {discord_user_id} ({e}).")


def meeting_body(meeting, kind, is_private):
    # Same wording/shape as shared.py's _meeting_discord_body — kept in
    # sync by hand. Never includes the join link, meeting ID, or
    # password, on the channel or in a DM: those stay dashboard-only.
    when = date.fromisoformat(meeting["meeting_date"]).strftime("%A, %d %b %Y")
    if meeting.get("meeting_time"):
        when += f" at {meeting['meeting_time'][:5]} IST"
    if kind == "reminder_24h":
        headline = f":alarm_clock: **Reminder — {meeting['title']} is about 24 hours away**"
    else:  # reminder_1h
        headline = f":alarm_clock: **Starting soon — {meeting['title']} is about 1 hour away**"
    body = f"{headline}\n:date: {when}"
    if meeting.get("agenda"):
        body += f"\n{plain_text_from_rich_html(meeting['agenda'])}"
    body += "\n\nFull details, the join link and RSVP are on the dashboard — not posted here."
    return body


def notify_meeting(meeting, invitee_ids, kind):
    is_private = bool(invitee_ids)
    body = meeting_body(meeting, kind, is_private)
    if is_private:
        rows = client.table("users").select("user_id, discord_user_id").in_(
            "user_id", list(invitee_ids)
        ).execute().data
        for row in rows:
            if row.get("discord_user_id"):
                send_discord_dm(row["discord_user_id"], body)
    else:
        tags = discord_role_tags()
        message = body + (f"\n{tags}" if tags else "")
        send_discord_channel_message(message)


def send_reminders_for(hours_before, sent_column, kind):
    now = datetime.now(IST)

    meetings = client.table("meetings").select("*").eq(sent_column, False).execute().data
    if not meetings:
        print(f"No {kind} candidates at all (everything already sent or table empty).")
        return

    invitee_rows = client.table("meeting_invitees").select("meeting_id, user_id").execute().data
    invitees_by_meeting = {}
    for row in invitee_rows:
        invitees_by_meeting.setdefault(row["meeting_id"], []).append(row["user_id"])

    sent_any = False
    for m in meetings:
        if not m.get("meeting_time"):
            continue  # no specific time - nothing to be "N hours before" of
        meeting_dt = datetime.combine(
            date.fromisoformat(m["meeting_date"]), time.fromisoformat(m["meeting_time"]), tzinfo=IST
        )
        target = meeting_dt - timedelta(hours=hours_before)
        # Only fires once the reminder moment has actually arrived (and the
        # meeting itself hasn't happened yet) - not "any time it's <= 24h
        # away", or a meeting scheduled with 2 hours' notice would fire
        # BOTH the 24h and 1h reminders back to back in the same run.
        if target <= now < meeting_dt:
            notify_meeting(m, invitees_by_meeting.get(m["meeting_id"], []), kind)
            client.table("meetings").update({sent_column: True}).eq(
                "meeting_id", m["meeting_id"]
            ).execute()
            sent_any = True
            print(f"Sent {kind} reminder for meeting {m['meeting_id']} ({m['title']})")

    if not sent_any:
        print(f"No {kind} reminders due right now.")


send_reminders_for(24, "reminder_24h_sent", "reminder_24h")
send_reminders_for(1, "reminder_1h_sent", "reminder_1h")

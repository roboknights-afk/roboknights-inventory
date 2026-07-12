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
import smtplib
from datetime import date, timedelta
from email.mime.text import MIMEText

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_SENDER"]
    msg["To"] = to_email

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
        server.starttls()
        server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)


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

    users = {u["user_id"]: u for u in client.table("users").select("user_id, name, email").execute().data}
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
        client.table("requests").update({sent_column: True}).eq("request_id", req["request_id"]).execute()
        print(f"Sent {days_before}-day reminder for request {req['request_id']} ({part['part_number']})")


send_reminders_for(2, "reminder_2day_sent")
send_reminders_for(1, "reminder_1day_sent")

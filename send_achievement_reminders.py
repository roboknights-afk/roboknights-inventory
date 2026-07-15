# Sends "log your result" emails at 4pm IST on the day of the competition
# itself — a different time and trigger from send_due_reminders.py (which
# runs at 9am IST and only handles day-before nudges), so this is its own
# script, run by the same GitHub Actions workflow via a second cron entry.
#
# To test this on your own laptop: `python send_achievement_reminders.py`
# (needs the same .env file app.py uses).

import os
import smtplib
from datetime import date
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


today = date.today().isoformat()

competitions = client.table("competitions").select("*").eq("competition_date", today).execute().data
if not competitions:
    print("No competitions today.")
else:
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
                .eq("achievement_reminder_sent", False)
                .execute()
                .data
            )
            for volunteer in selected_volunteers:
                # Ignore if they've already logged an achievement for this
                # exact event — no need to nag someone who already did it.
                already_logged = (
                    client.table("achievements")
                    .select("achievement_id")
                    .eq("user_id", volunteer["user_id"])
                    .eq("event_id", event["event_id"])
                    .execute()
                    .data
                )
                # Still mark reminder_sent even when skipped, so a later
                # run today doesn't re-check the same already-handled row.
                client.table("event_volunteers").update({"achievement_reminder_sent": True}).eq(
                    "volunteer_id", volunteer["volunteer_id"]
                ).execute()

                if already_logged:
                    print(f"Skipping {volunteer['user_id']} for {event['name']} — already logged.")
                    continue

                user = users.get(volunteer["user_id"])
                if not user:
                    continue

                send_email(
                    user["email"],
                    f"How did {event['name']} go?",
                    f"{comp['name']} was today — how did {event['name']} go?\n\n"
                    f"Log in to the app and add your result under Achievements "
                    f"(position, plus a photo/video link if you have one).",
                )
                print(f"Sent achievement reminder to {user['name']} for {event['name']}")

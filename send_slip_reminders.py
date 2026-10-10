# Posts a "hoping the permission slip is signed" reminder to the RoboKnights
# channel in the Exun server at 12:00 AM IST on the day BEFORE each
# competition (so a competition on 12 October gets its reminder just after
# midnight on 11 October).
#
# Like send_meeting_reminders.py this is NOT part of app.py: it runs from a
# GitHub Actions schedule (.github/workflows/slip-reminders.yml) whether or
# not anyone has the dashboard open, and it deliberately does NOT import
# shared.py (that module imports streamlit, which the runner has no reason
# to install).
#
# It posts as the RoboKnights BOT itself (its bot token, name, avatar and
# role) - never through a webhook, which can't carry the role. A webhook url
# is only ever used to LOOK UP which channel to post in (a GET, nothing is
# sent through it), and only if DISCORD_EXUN_CHANNEL_ID isn't set.
#
# "Sent already" is competitions.slip_reminded_for: the competition date a
# reminder went out for. Storing the date rather than a true/false means a
# competition whose date is edited afterwards gets a fresh reminder for the
# new date without any page having to remember to reset a flag.
#
# To test on your own laptop (needs the same .env app.py uses):
#   python send_slip_reminders.py --dry-run
#   python send_slip_reminders.py --dry-run --today 2026-10-23
# --dry-run prints what would be posted and posts nothing, marks nothing.

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
DISCORD_API = "https://discord.com/api/v10"
DISCORD_AUTOMATED_MARKER = "\n\n***This is an automated message***"

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--today", help="pretend today (IST) is this date, YYYY-MM-DD - for testing")
args = parser.parse_args()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def exun_channel_id():
    explicit = (os.environ.get("DISCORD_EXUN_CHANNEL_ID") or "").strip()
    if explicit:
        return explicit
    webhook_url = os.environ.get("DISCORD_EXUN_WEBHOOK_URL")
    if not webhook_url:
        return None
    try:
        return requests.get(webhook_url, timeout=10).json().get("channel_id")
    except Exception:
        return None


def post_as_bot(channel_id, content):
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN isn't set - nothing can be posted.")
        return None
    try:
        response = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}"}, json={"content": content}, timeout=10,
        )
    except Exception as e:
        print(f"Couldn't reach Discord ({e}).")
        return None
    if not response.ok:
        print(f"Discord refused the post ({response.status_code}): {response.text[:200]}")
        return None
    return response.json().get("id")


def slip_message(name, comp_date):
    when = f"{comp_date.day} {comp_date:%B}, {comp_date:%A}"
    return (
        f":memo: Hoping the slip for **{name}** on **{when}** is signed. "
        f"If not, please get it done today, ASAP!"
    )


today = date.fromisoformat(args.today) if args.today else datetime.now(IST).date()
tomorrow = today + timedelta(days=1)

# Not-attending competitions are skipped: nobody is being sent, so no slip.
try:
    competitions = (
        client.table("competitions")
        .select("competition_id, name, competition_date, slip_reminded_for")
        .eq("competition_date", tomorrow.isoformat())
        .eq("not_attending", False)
        .execute()
        .data
    )
except Exception as e:
    print(
        "Couldn't read competitions. If this mentions slip_reminded_for, the migration at the "
        f"end of supabase_schema.sql hasn't been run yet. ({str(e)[:200]})"
    )
    sys.exit(1)

due = [c for c in competitions if c.get("slip_reminded_for") != tomorrow.isoformat()]
if not due:
    print(f"No slip reminders due (nothing on {tomorrow}, or already reminded).")
    sys.exit(0)

channel_id = exun_channel_id()
if not channel_id:
    print("Can't tell which channel to post in: set DISCORD_EXUN_CHANNEL_ID (or DISCORD_EXUN_WEBHOOK_URL).")
    sys.exit(1)

failed = False
for comp in due:
    content = slip_message(comp["name"], tomorrow) + DISCORD_AUTOMATED_MARKER
    if args.dry_run:
        print(f"[dry run] would post to channel {channel_id}:\n{content}\n")
        continue

    message_id = post_as_bot(channel_id, content)
    if not message_id:
        failed = True  # not marked as sent, so the next run retries
        continue

    try:
        client.table("competitions").update({"slip_reminded_for": tomorrow.isoformat()}).eq(
            "competition_id", comp["competition_id"]
        ).execute()
    except Exception as e:
        failed = True
        print(f"Posted for {comp['name']} but couldn't mark it as sent - the next run would repeat it ({e}).")
    try:
        # So the Discord Messages page can still edit or delete it later.
        client.table("discord_messages").insert({
            "message_id": message_id, "content": content, "channel": "exun_rk",
        }).execute()
    except Exception as e:
        print(f"Couldn't log the message for the Discord Messages page ({e}).")
    print(f"Posted the slip reminder for {comp['name']} ({tomorrow}).")

sys.exit(1 if failed else 0)

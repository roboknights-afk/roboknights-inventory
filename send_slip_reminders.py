# At 12:00 AM IST on the day BEFORE each competition (so a competition on 12
# October is checked just after midnight on 11 October), looks in the club's
# perm slip Google Doc and tells the RoboKnights channel in the Exun server
# whether that competition's slip is there - or, if no doc is configured,
# just asks whether it has been signed. Competitions marked not-attending
# are skipped.
#
# The doc check lives in perm_slip_check.py: the competition's block must
# have Event, Organising, Mode and Date filled and at least 2 names listed.
# The bot reads the doc as the Google service account, which has Viewer
# access only. It never prints or posts a student's details - the repo and
# its Actions logs are public.
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
# new date without any page having to remember to reset a flag. If the doc
# can't be read, NOTHING is posted (a wrong "isn't there" would be worse than
# silence) and the run fails so it shows in Actions; the 1 AM run retries.
#
# A second job, --kind early (9:00 AM IST), nudges the channel EARLIER: from 4
# days before a competition, "please make the perm slip ... and get it
# signed". Tracked in competitions.slip_early_reminded_for the same way. A
# competition added with less than 4 days to go gets it on the next morning
# run instead (any day from 4 days out to 2 days out), and a dropped run is
# caught the next morning.
#
# To test on your own laptop (needs the same .env app.py uses, plus
# PERM_SLIP_DOC_URL and GOOGLE_SERVICE_ACCOUNT_JSON_B64):
#   python send_slip_reminders.py --dry-run
#   python send_slip_reminders.py --dry-run --today 2026-10-11
#   python send_slip_reminders.py --dry-run --kind early --today 2026-10-20
# --dry-run prints what would be posted and posts nothing, marks nothing.

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

import perm_slip_check

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
DISCORD_API = "https://discord.com/api/v10"

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--kind", choices=["check", "early"], default="check",
                    help="check = the day-before doc check (default); early = the 4-days-before nudge")
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


def _when(comp_date):
    return f"{comp_date.day} {comp_date:%B}, {comp_date:%A}"


def slip_message(name, comp_date, verdict=None, missing=()):
    # Plain text on purpose: a reminder and nothing else - no emoji, no
    # link, no "automated message" footer (host's request, 2026-10-11).
    # verdict None = no perm slip doc configured: just ask if it is signed.
    when = _when(comp_date)
    if verdict == "ready":
        return f"The perm slip for {name} on {when} is there, kindly check."
    if verdict == "incomplete":
        return (
            f"The perm slip for {name} on {when} isn't complete yet "
            f"(missing: {', '.join(missing)}), kindly check ASAP."
        )
    if verdict == "missing":
        return f"The perm slip for {name} on {when} isn't there, kindly check ASAP."
    return (
        f"Hoping the perm slip for {name} on {when} is signed. "
        f"If not, please get it done today, ASAP!"
    )


def selected_count(competition_id):
    # How many students are selected for the competition, so a competition
    # sending a single student isn't told its slip is incomplete for having
    # one name instead of two.
    events = client.table("competition_events").select("event_id").eq("competition_id", competition_id).execute().data
    ids = [e["event_id"] for e in events]
    if not ids:
        return 0
    rows = client.table("event_volunteers").select("user_id").in_("event_id", ids).eq("selected", True).execute().data
    return len({r["user_id"] for r in rows})


today = date.fromisoformat(args.today) if args.today else datetime.now(IST).date()
tomorrow = today + timedelta(days=1)

if args.kind == "early":
    # "Make the perm slip and get it signed", from 4 days out. Window is 4 to
    # 2 days before so a competition added late, or a dropped run, still gets
    # it; at 1 day the doc check above takes over.
    first, last = today + timedelta(days=2), today + timedelta(days=4)

    def read_early(columns):
        return (
            client.table("competitions").select(columns)
            .gte("competition_date", first.isoformat()).lte("competition_date", last.isoformat())
            .eq("not_attending", False).execute().data
        )

    try:
        upcoming = read_early("competition_id, name, competition_date, slip_early_reminded_for")
    except Exception as e:
        if args.dry_run:
            print("(slip_early_reminded_for doesn't exist yet - treating everything as not reminded)")
            upcoming = read_early("competition_id, name, competition_date")
        else:
            print(
                "Couldn't read competitions. If this mentions slip_early_reminded_for, the migration "
                f"at the end of supabase_schema.sql hasn't been run yet. ({str(e)[:200]})"
            )
            sys.exit(1)

    due_early = [c for c in upcoming if c.get("slip_early_reminded_for") != c["competition_date"]]
    if not due_early:
        print(f"No early slip reminders due (nothing between {first} and {last}, or already reminded).")
        sys.exit(0)

    channel_id = exun_channel_id()
    if not channel_id:
        print("Can't tell which channel to post in: set DISCORD_EXUN_CHANNEL_ID (or DISCORD_EXUN_WEBHOOK_URL).")
        sys.exit(1)

    failed = False
    for comp in sorted(due_early, key=lambda c: c["competition_date"]):
        comp_date = date.fromisoformat(comp["competition_date"])
        content = (
            f"Reminder: please make the perm slip for {comp['name']} on {_when(comp_date)} "
            f"and get it signed."
        )
        if args.dry_run:
            print(f"[dry run] would post to channel {channel_id}:" + chr(10) + content + chr(10))
            continue
        message_id = post_as_bot(channel_id, content)
        if not message_id:
            failed = True  # not marked, so the next run retries
            continue
        try:
            client.table("competitions").update({"slip_early_reminded_for": comp["competition_date"]}).eq(
                "competition_id", comp["competition_id"]
            ).execute()
        except Exception as e:
            failed = True
            print(f"Posted for {comp['name']} but couldn't mark it as sent - the next run would repeat it ({e}).")
        try:
            client.table("discord_messages").insert({
                "message_id": message_id, "content": content, "channel": "exun_rk",
            }).execute()
        except Exception as e:
            print(f"Couldn't log the message for the Discord Messages page ({e}).")
        print(f"Posted the early slip reminder for {comp['name']} ({comp['competition_date']}).")
    sys.exit(1 if failed else 0)


# Not-attending competitions are skipped: nobody is being sent, so no slip.
def read_competitions(columns):
    return (
        client.table("competitions").select(columns)
        .eq("competition_date", tomorrow.isoformat()).eq("not_attending", False)
        .execute().data
    )


try:
    competitions = read_competitions("competition_id, name, competition_date, slip_reminded_for")
except Exception as e:
    if args.dry_run:
        # So the check can be tried before the slip_reminded_for migration.
        print("(slip_reminded_for doesn't exist yet - treating everything as not reminded)")
        competitions = read_competitions("competition_id, name, competition_date")
    else:
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

doc_url = os.environ.get("PERM_SLIP_DOC_URL")
service_account = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
blocks = None
if doc_url and service_account:
    try:
        blocks = perm_slip_check.parse_blocks(
            perm_slip_check.fetch_doc(perm_slip_check.doc_id_from_url(doc_url), service_account)
        )
        print(f"Read the perm slip doc: {len(blocks)} competition block(s).")
    except Exception as e:
        print(f"Couldn't read the perm slip doc, so nothing was posted ({str(e)[:300]}).")
        sys.exit(1)
else:
    print("PERM_SLIP_DOC_URL / GOOGLE_SERVICE_ACCOUNT_JSON_B64 not set - asking whether the slip is signed instead.")

failed = False
for comp in due:
    verdict, missing = None, []
    if blocks is not None:
        needed = min(perm_slip_check.MIN_NAMES, max(1, selected_count(comp["competition_id"])))
        verdict, missing = perm_slip_check.judge(comp["name"], tomorrow, blocks, needed)
        print(f"{comp['name']}: slip {verdict}{' - missing ' + ', '.join(missing) if missing else ''}")
    content = slip_message(comp["name"], tomorrow, verdict, missing)
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

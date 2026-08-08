# Things both pages (Inventory and Competitions) need. Kept separate from
# app.py because Streamlit's multi-page apps run each page as its own
# script — importing straight from app.py (the entry point) would re-run
# the whole login screen, so shared code lives here instead.

import os
import re
import smtplib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
import streamlit as st
from supabase import create_client

# Where this app is running. Emails link back here. Reads from an APP_URL
# secret if one is set (e.g. once deployed), otherwise falls back to your
# own laptop — so deploying doesn't need a code change, just one new secret.
APP_URL = os.environ.get("APP_URL", "http://localhost:8501")

# Only these accounts can see the host-only Competitions tools (add a
# competition, finalize volunteers, send announcements, the Members
# directory, etc). Ordered by real-world hierarchy (Vice Principal, then
# HOD, then the teacher in-charge) purely for readability here — the app
# itself doesn't tier host access, every HOST_EMAILS account has
# identical permissions regardless of title.
HOST_EMAILS = {
    "roboknights@dpsrkp.net",
    "mukeshkumar@dpsrkp.net",  # Mr Mukesh Kumar, Vice Principal
    "hemajain@dpsrkp.net",  # Ms Hema Jain, HOD Computer Science
    "ajithkumar@dpsrkp.net",  # Mr Ajith Kumar KG, Robotics In-Charge
}

# Display title shown next to a host's name (sidebar account badge). Not
# used for permissions anywhere — every HOST_EMAILS account has identical
# access regardless of title; this is purely who's-who for people using
# the app, not a tiered-access system.
HOST_ROLES = {
    "mukeshkumar@dpsrkp.net": "Vice Principal",
    "hemajain@dpsrkp.net": "HOD, Computer Science",
    "ajithkumar@dpsrkp.net": "Robotics In-Charge",
}

# A limited external tier for RoboKnights' sister club, Exun — can VIEW
# Competitions, Meetings, Achievements, and the Members directory, but
# can't volunteer, RSVP, log an achievement, or touch anything host-only.
# A set (not one email) since more Exun accounts may be added later, same
# shape as HOST_EMAILS.
EXUN_EMAILS = {
    "exun@dpsrkp.net",
}

# The private RoboKnights <> Exun channel is scoped to this specific,
# hand-picked list of people (both clubs' leadership plus a few named
# RoboKnights members), not "every host" or "every member" — matches
# what was actually asked for, not a broader role. Anyone not in this
# set doesn't see the channel exist at all.
EXUN_CHANNEL_MEMBERS = {
    "roboknights@dpsrkp.net",
    "mukeshkumar@dpsrkp.net",
    "hemajain@dpsrkp.net",
    "ajithkumar@dpsrkp.net",
    "exun@dpsrkp.net",
    "r22639naitik@dpsrkp.net",  # Naitik Jindal
    "r23444kyraan@dpsrkp.net",  # Kyraan Katyal
    "v09045medhansh@dpsrkp.net",  # Medhansh Tanmay Pandya
    "v09145aryamman@dpsrkp.net",  # Aryamman Ojha
}

# Derived, not hand-maintained, so it can't drift out of sync with
# EXUN_CHANNEL_MEMBERS: everyone in that channel who's neither a
# host/staff account nor Exun themselves — i.e. the named RoboKnights
# STUDENT members. Used to scope the "unread channel message" nudge to
# students only, per the student's explicit instruction that staff never
# get nagged about unread messages.
EXUN_CHANNEL_STUDENT_EMAILS = EXUN_CHANNEL_MEMBERS - HOST_EMAILS - EXUN_EMAILS

# The club's real competition-tracking sheet ("E2C"). Always this one sheet,
# so the host scans it directly instead of pasting a link every time.
E2C_SHEET_ID = "1RLSXcAJ4t44M_wQ_hKlZqaTVmWjwAXrI8FInjHIZRTw"

# A fast, live alternative to the Queries page for something urgent — a
# plain wa.me link needs no API, unlike automated WhatsApp notifications
# (which stay out of scope; see CLAUDE.md).
WHATSAPP_HELP_NUMBER = "919311259439"


@st.cache_resource
def get_client():
    # Talks to Supabase over the internet instead of opening a local file.
    # @st.cache_resource means this only actually runs once per app
    # process, not once per page load — Streamlit reuses the same client.
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


# Every page was re-fetching whole tables from Supabase on every single
# click (Streamlit reruns the entire script per interaction), often the
# same table several times over with different filters — 35 round trips on
# one Competitions render, measured directly. This is what actually made
# the app feel slow, not Streamlit itself.
#
# Fix: fetch a whole table ONCE per short window (ttl below) and filter it
# in Python instead — the same pattern several pages already used for their
# own "all_events"/"all_volunteers" style lookups, just applied everywhere
# and cached. A short ttl (not "forever") means even if a write forgets to
# invalidate, the page self-corrects within a few seconds rather than
# staying wrong indefinitely.
CACHE_TTL = 8  # seconds


@st.cache_data(ttl=CACHE_TTL)
def cached_table(table_name):
    return get_client().table(table_name).select("*").execute().data


def invalidate_cache():
    # Called right after any insert/update/delete, so YOUR OWN action shows
    # up immediately on the rerun that follows — never waiting out the ttl
    # for your own change. Clears every table's cache, not just the one
    # just written to, since most actions touch more than one table anyway
    # (e.g. approving a request updates both requests and parts) and a
    # blanket clear can't miss one by mistake.
    cached_table.clear()


def has_unread_queries(user_id, is_host):
    # Same "message from the other side newer than my last read" check
    # queries.py itself does per-thread (see render_thread there) — reused
    # here so the sidebar nav badge and the actual read-marking logic can
    # never disagree about what counts as unread.
    queries = [q for q in cached_table("queries") if is_host or q["student_id"] == user_id]
    if not queries:
        return False
    messages_by_query = {}
    for m in cached_table("query_messages"):
        messages_by_query.setdefault(m["query_id"], []).append(m)
    read_field = "host_read_at" if is_host else "student_read_at"
    for q in queries:
        thread_messages = messages_by_query.get(q["query_id"], [])
        latest_from_other = max(
            (m["created_at"] for m in thread_messages if m["sender_id"] != user_id),
            default=None,
        )
        my_read_at = q.get(read_field)
        if latest_from_other and (not my_read_at or latest_from_other > my_read_at):
            return True
    return False


def has_unread_exun_channel(user_id):
    messages = cached_table("exun_channel_messages")
    latest = max((m["created_at"] for m in messages), default=None)
    if not latest:
        return False
    my_read = next(
        (r for r in cached_table("exun_channel_reads") if r["user_id"] == user_id), None
    )
    my_read_at = my_read.get("last_read_at") if my_read else None
    return not my_read_at or latest > my_read_at


IST = timezone(timedelta(hours=5, minutes=30))


def today_ist():
    # "Today" as the people using this app experience it — NOT the
    # server's own date. Streamlit Cloud and GitHub Actions both run in
    # UTC, where between midnight and 5:30 AM IST the calendar date is
    # still "yesterday" — every due-date / is-it-today comparison in the
    # app should go through this, never a bare date.today().
    return datetime.now(IST).date()


def format_ist(created_at):
    # Supabase stores timestamps in UTC; convert to IST for display since
    # that's the timezone everyone using this app is actually in.
    posted = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return posted.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def format_relative(created_at):
    # "5m ago" reads faster than a full timestamp for anything recent —
    # used on chat-like screens (queries, announcements, the Exun
    # channel), usually with the exact format_ist time tucked into a
    # help tooltip. Falls back to the full date once it's over a week
    # old, where "9d ago" stops being helpful.
    posted = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    seconds = int((datetime.now(timezone.utc) - posted).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}d ago"
    return format_ist(created_at)


def _progressive(action_description):
    # "approve this request" -> "Approving this request…" for a spinner
    # label. Every safe_write() call site already phrases its description
    # as a plain imperative verb phrase, so one standard English gerund
    # rule (drop a trailing silent e, else just add -ing) covers all of
    # them without a per-call-site lookup table.
    verb, _, rest = action_description.partition(" ")
    gerund = verb[:-1] + "ing" if verb.endswith("e") and not verb.endswith("ee") else verb + "ing"
    return f"{gerund[0].upper()}{gerund[1:]}{' ' + rest if rest else ''}…"


@contextmanager
def safe_write(action_description):
    # Wraps a block of Supabase writes so a transient failure (network
    # blip, a Supabase hiccup) shows a clean inline error instead of
    # crashing the whole page for whoever's using it right then. Safe
    # around st.rerun()/st.stop() too: those work by raising exceptions
    # that inherit from BaseException specifically so a broad
    # "except Exception" like this one can't swallow them.
    #
    # Also the one spinner every write action in the app gets "for free" —
    # every safe_write() call site already has a specific, human
    # description, so this reads as real per-action feedback ("Approving
    # this request…") rather than a generic "Loading..." — instead of
    # writes (Supabase round trip + often an outgoing email) looking like
    # a dead click on a slow connection.
    try:
        with st.spinner(_progressive(action_description)):
            yield
    except Exception as e:
        st.error(f"Couldn't {action_description}: {e}")


def send_email(to_email, subject, body):
    # Plain-text email over the same Brevo SMTP relay Supabase's own login
    # emails already use. If sending fails for any reason (bad network, a
    # typo'd email, Brevo hiccup), we don't want that to break whatever
    # action triggered it — the database change already happened; the
    # email is a nice-to-have on top, not something to fail loudly over.
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_SENDER"]
    msg["To"] = to_email

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
            server.starttls()
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    except Exception:
        pass


WHATSAPP_API_VERSION = "v22.0"


def _normalize_india_phone(raw):
    # Members typed a plain 10-digit local number at signup, not the
    # country-code'd format WhatsApp's API needs (e.g. "9876543210" ->
    # "919876543210"). Handles the common variants people actually type
    # (with a leading 0, spaces/dashes, or already having "91"/"+91").
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("91") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return "91" + digits
    return None  # not a recognizable Indian mobile number — don't guess


def send_whatsapp(to_phone, template_name, params=None, language_code="en_US"):
    # WhatsApp Cloud API, template-based (the only kind Meta allows for a
    # message the business sends first, rather than a reply). Silently does
    # nothing — same best-effort spirit as send_email — if the WhatsApp
    # credentials aren't set up yet (still a manual, human-only step in
    # Meta's own console; see CLAUDE.md) or the recipient has no usable
    # phone number on file, so this can be wired in everywhere before the
    # Meta side is finished without breaking anything.
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


def send_discord_message(content):
    # A Discord Incoming Webhook is a plain HTTP POST — unlike a real bot,
    # it needs no persistent gateway connection or separate 24/7 process,
    # so it fits this app's existing hosting (only runs when someone's
    # using it, or during a scheduled GitHub Actions job) with no new
    # infra. Silently no-ops if the webhook isn't set up yet, same best-
    # effort spirit as send_email/send_whatsapp — safe to wire in
    # anywhere before the Discord side is finished.
    #
    # ?wait=true makes Discord return the created message (instead of a
    # bare 204) so the caller gets its id back — needed to delete this
    # specific message later via delete_discord_message, without that
    # meaning "wait for real delivery confirmation" or anything slower.
    webhook_url = os.environ.get("DISCORD_COMPETITIONS_WEBHOOK_URL")
    if not webhook_url:
        return None
    try:
        response = requests.post(
            webhook_url, json={"content": content}, params={"wait": "true"}, timeout=10
        )
        return response.json().get("id")
    except Exception:
        return None


def delete_discord_message(message_id):
    # A webhook can only delete messages IT sent (not just anyone's in the
    # channel) — exactly the scope needed here: undoing a message this
    # app itself just posted, via the same webhook, nothing broader.
    webhook_url = os.environ.get("DISCORD_COMPETITIONS_WEBHOOK_URL")
    if not webhook_url or not message_id:
        return
    try:
        requests.delete(f"{webhook_url}/messages/{message_id}", timeout=10)
    except Exception:
        pass

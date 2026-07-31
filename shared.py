# Things both pages (Inventory and Competitions) need. Kept separate from
# app.py because Streamlit's multi-page apps run each page as its own
# script — importing straight from app.py (the entry point) would re-run
# the whole login screen, so shared code lives here instead.

import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import streamlit as st
from supabase import create_client

# Where this app is running. Emails link back here. Reads from an APP_URL
# secret if one is set (e.g. once deployed), otherwise falls back to your
# own laptop — so deploying doesn't need a code change, just one new secret.
APP_URL = os.environ.get("APP_URL", "http://localhost:8501")

# Only these accounts can see the host-only Competitions tools (add a
# competition, finalize volunteers, send announcements). Just two people
# ever, so a plain list is simpler than building role-management UI for it.
HOST_EMAILS = {
    "roboknights@dpsrkp.net",
    "ajithkumar@dpsrkp.net",  # Mr Ajith Kumar KG, teacher in-charge
}

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


IST = timezone(timedelta(hours=5, minutes=30))


def format_ist(created_at):
    # Supabase stores timestamps in UTC; convert to IST for display since
    # that's the timezone everyone using this app is actually in.
    posted = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return posted.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


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

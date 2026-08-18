# Things both pages (Inventory and Competitions) need. Kept separate from
# app.py because Streamlit's multi-page apps run each page as its own
# script — importing straight from app.py (the entry point) would re-run
# the whole login screen, so shared code lives here instead.

import os
import re
import smtplib
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from urllib.parse import urlencode

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
    "official.kavyadayal@gmail.com",  # Kavya Dayal, Exun core member
}

# A read-only account for looking around the whole dashboard without being
# able to change anything (2026-08-16). Broader than EXUN_EMAILS above —
# these accounts also see Inventory, Announcements and Feedback — but
# still strictly view-only, and still SHORT of what a host sees: the
# private student<>host Queries threads, the AI chat logs, and the Discord
# messaging tools are all excluded, as are members' phone numbers and
# admission numbers on the Members page. Those belong to real students
# (most of them minors) who never agreed to a visitor account reading
# them, and none of it is needed to evaluate how the dashboard works.
VIEWER_EMAILS = {
    "r24334kiara@dpsrkp.net",  # Kiara Kapoor, test/demo account
}

# Host-requested ban (2026-08-14): these two get no reply from the
# dashboard's AI Assistant page - not a moderation feature, just a kill
# switch on that one page talking back to them. The Discord bot has its
# own matching AI_ASSISTANT_BANNED_DISCORD_IDS (discord_bot/bot.py can't
# import this file - see its own top-of-file comment on why - so it's a
# separate, Discord-ID-keyed list there instead of email-keyed here).
AI_ASSISTANT_BANNED_EMAILS = {
    "e11356lav@dpsrkp.net",  # Lav Singh
    "e11357kush@dpsrkp.net",  # Kush Singh (also fully account-disabled separately)
}

# Hard, code-level block on roast/insult requests, checked before any
# model call. The no-roasting rule also lives in both system prompts, but
# a prompt rule is only an instruction a model can choose to ignore -
# confirmed live 2026-08-15 that members got real roasts anyway by
# framing it as "for testing purposes", because replies that day were
# falling through to the weakest fallback provider. This check can't be
# talked around, and costs nothing to run.
#
# Duplicated in discord_bot/bot.py (which can't import this module - see
# its top-of-file comment, same reason send_due_reminders.py duplicates
# send_email). Keep the two lists in sync. That file's
# ROAST_REQUEST_PATTERNS comment explains why deliberately ambiguous
# words ("burn" as in a bootloader, "flame" as in the sensor) are left
# out rather than risking a false refusal on a real build question.
ROAST_REQUEST_PATTERNS = (
    r"\broast(s|ed|ing|er)?\b",
    r"\binsult(s|ed|ing)?\b",
    r"\bdiss(ing)?\b",
    r"\bbully(ing)?\b",
    r"\bclown\b",
    r"\bhumiliat(e|es|ing)\b",
    r"\bridicul(e|es|ing)\b",
    r"\bbelittl(e|es|ing)\b",
    r"\bdemean(ing)?\b",
    r"\bgaali\b",
    r"\bbe[iy]?zzat[iy]\b",
    r"make fun of",
    r"poke fun",
    r"trash talk",
    r"talk (shit|trash)",
    r"say something (mean|nasty|rude|bad)",
    r"be (mean|brutal|savage|harsh|rude) (to|about)",
    r"who('s| is) the (worst|most useless|laziest)",
    # "make a joke on X" is the same request in friendlier words - it got
    # a real Exun joke out of the bot minutes after the first version of
    # this block shipped, and "make a joke on naitik" was already sitting
    # in the logs I built the list from. "joke/meme ON or AT someone" is
    # always at their expense; "joke ABOUT" is included too because
    # "make a joke about medhansh" is no different. A bare "tell me a
    # joke" still works - only a joke pointed at a subject is refused.
    r"\b(jokes?|memes?|comebacks?|one.?liners?) (on|at|about|for)\b",
    r"make (a|some|me a) (joke|meme)",
    # "if u were me, what could u say funny about naitik" - the next
    # phrasing that got through, and the giveaway is the same every time:
    # something funny aimed AT a named person, however it's framed.
    r"say (something|anything)? ?funny (about|on|regarding)",
    r"(something|anything) funny (about|on) ",
    # "a small script on ayush goyal in carryminati's humorous parody
    # style" - asked live, and it's a roast wearing a YouTube format.
    # Roast-comedy styles and diss formats ARE mockery by definition, so
    # they're refused whoever the target is - including, as here, the
    # person asking. "Do it to myself" has never been an exception.
    # Needs a target ("parody about naitik", "rap battle between X and
    # Y") - a bare "what is a rap battle" is a real question and must
    # still get a real answer.
    r"\b(parod(y|ies)|diss track|rap battle|impression) (of|on|about|for|between)\b",
    # The FORMAT is the giveaway, in either word order - "a script in
    # carryminati's humorous parody style" names no roster member (the
    # person asking wasn't signed up on the dashboard at all, so the
    # member-name check below couldn't see him) but is unmistakably a
    # roast. A plain "write a python script for line following" has none
    # of these words and still goes straight through.
    r"\b(parody|roast|diss|humorous|comedic|savage)\b.{0,30}\b(style|script|video|sketch|bit)\b",
    r"\b(script|video|sketch|bit)\b.{0,40}\b(parody|roast|diss)\b",
)

# The "never discuss these at all" list from both system prompts, enforced
# in code for the same reason as the roasting rule: the prompt version was
# ignored by the weak fallback model within minutes. Only blocks when
# paired with a mockery word below, so genuinely neutral questions ("when
# is the Exun symposium") still reach the model and get the prompt's own
# polite decline rather than this blunter one.
PROTECTED_ENTITY_PATTERNS = (
    r"\bexun\b", r"\bdomain\s*square\b", r"\bdpsrkp\b", r"\bdps\b",
    r"\bikkumpal\b", r"\bmukesh\b", r"\bhema\b", r"\bajith\b",
    r"\bvice.?principal\b", r"\bprincipal\b",
)
MOCKERY_WORD_PATTERNS = (
    r"\bjokes?\b", r"\bmemes?\b", r"\bfunny\b", r"\bcomeback\b",
    r"\bsavage\b", r"\bcook(ed)?\b", r"\bexpose\b", r"\bdrag\b",
    r"\bparod(y|ies)\b", r"\bhumorous\b", r"\bmock(ing|ery)?\b",
    r"\bsarcas(m|tic)\b", r"\bcringe\b", r"\bcarry\s*minati\b",
)
ROAST_REFUSAL = (
    "That's not my job — I don't roast or take shots at anyone here. "
    "Happy to help with club stuff or any actual question though."
)


# The club's OWN members count as protected targets too, not just the
# fixed list above. A hand-written list can only ever cover the names I
# thought to type, and every bypass so far came in through a member's
# name ("make a joke on naitik", "say something funny about naitik") -
# so this reads the real roster instead of guessing. Short names are
# skipped: anything under 4 letters collides with ordinary words too
# easily to be a safe trigger.
MIN_MEMBER_NAME_LENGTH = 4


def _member_name_pattern():
    names = set()
    try:
        for row in cached_table("users"):
            for word in (row.get("name") or "").split():
                word = word.strip(".,").lower()
                if len(word) >= MIN_MEMBER_NAME_LENGTH:
                    names.add(word)
    except Exception:
        # Best-effort: a failed read must never quietly switch the block
        # off, so the fixed entity list below still applies on its own.
        return None
    if not names:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names)) + r")\b")


def _targets_a_person(lowered):
    if any(re.search(p, lowered) for p in PROTECTED_ENTITY_PATTERNS):
        return True
    pattern = _member_name_pattern()
    return bool(pattern and pattern.search(lowered))


def _roast_request(text):
    lowered = text.lower()
    if any(re.search(p, lowered) for p in ROAST_REQUEST_PATTERNS):
        return True
    # A joke aimed at a real person or a protected name is the same
    # request wearing a friendlier word, so the two lists only trigger
    # together - "tell me a joke" and "is this funny" still get through.
    if any(re.search(p, lowered) for p in MOCKERY_WORD_PATTERNS) and _targets_a_person(lowered):
        return True
    return False

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

# The school's own admission roster, "RoboKnights Clio" (2026-08-14) — one
# tab per school year, plus an Alumni tab. Unlike E2C above (read-only, a
# plain API key is enough), this needs WRITE access, so it uses a Google
# service account instead (see get_sheets_write_client below) — an API key
# alone can never write to a sheet, only read one that's shared publicly.
CLIO_SHEET_ID = "18M5VLCmC0tczG61m9Zx2OWQXP9jOa_oAfpPBo_BhAi8"
# Which tab the verify-details popup writes into. Pointed at a separate
# test tab for now (2026-08-14, student's explicit call) instead of the
# real "2026-2027" roster tab, so this can be tried out live without
# touching the school's actual admission records. Same column layout
# (Admission No / Name / Class / Institutional Email / Contact Info /
# blank / Personal Email) as the real per-year tabs, so switching this
# constant to "2026-2027" later is the only change needed to go live.
CLIO_CURRENT_TAB = "RK Verify (Test)"

# Adhoc members get their own labeled block, below everyone else, in the
# SAME tab (student's explicit call — not a separate tab like Alumni).
# Reserved at a FIXED row rather than dynamically inserted, so adding a
# new main-section member never has to shift the adhoc block down (which
# would risk corrupting it, or any real data the sheet might have further
# right in columns beyond G) — same reasoning as picking an explicit
# target row over gspread's append_row earlier. Moved from 500 to 50
# (2026-08-14, student's explicit call) — leaves room for ~47 main
# members (rows 2-49); the roster was 30 non-adhoc members at the time
# of this change, so there's headroom for now but nowhere near the ~490
# the old row 500 gave. Revisit this number if the main section ever
# gets close to filling it.
CLIO_ADHOC_MARKER_ROW = 50

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


@st.cache_resource
def get_sheets_write_client():
    # Returns None (never raises) when the credential isn't set up yet, so
    # a missing/not-yet-configured service account degrades to "the sync
    # silently doesn't happen" instead of crashing the whole app — same
    # best-effort spirit as send_email/send_discord_message elsewhere here.
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    if not b64:
        return None
    import base64
    import json

    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(base64.b64decode(b64))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _find_or_next_row(ws, admission_no, start_row, end_row):
    # Searches column A within [start_row, end_row] for a matching
    # admission number; returns that row if found, otherwise the first
    # EMPTY row in that range.
    #
    # Bug fixed here after it corrupted a real member's row (2026-08-14):
    # ws.get() only trims TRAILING empty rows, not gaps in the middle —
    # a range with an empty row followed by a filled one still comes back
    # as e.g. [[], ['R22639']], not just [['R22639']]. The first version
    # of this function assumed "how many rows came back" meant "how far
    # the data goes", so it computed start_row + len(values) and skipped
    # right past a genuinely empty row into the next member's row
    # instead. Now it scans every returned row explicitly and only falls
    # back to "one past the last row" when it finds no gap at all.
    values = ws.get(f"A{start_row}:A{end_row}")
    first_empty = None
    for i, row_vals in enumerate(values):
        val = row_vals[0] if row_vals else ""
        if val == admission_no:
            return start_row + i
        if not val and first_empty is None:
            first_empty = start_row + i
    return first_empty if first_empty is not None else start_row + len(values)


def sync_member_to_clio_sheet(
    admission_no, name, class_str, email, phone_no, phone_no_2="", personal_email="", is_adhoc=False,
):
    # Keeps the school's own Clio roster in sync with what a member just
    # confirmed in the app (see render_verify_details_dialog in app.py).
    # Matches an existing row by admission number and updates it in
    # place; a member not already listed gets a new row appended instead,
    # so this never accidentally creates a duplicate.
    #
    # Adhoc members (users.role == 'adhoc') go in their own labeled block
    # BELOW everyone else in this same tab, starting right after
    # CLIO_ADHOC_MARKER_ROW — a fixed row, not one that moves as the main
    # section grows, so a new main-section member can never shift the
    # adhoc block (or anything below it) down.
    #
    # Best-effort like every other outside-this-app write here — wrapped
    # by the caller in the same try/except spirit as send_email, so a
    # Google Sheets hiccup never blocks saving the member's own details in
    # our own database.
    gc = get_sheets_write_client()
    if gc is None or not admission_no:
        return
    ws = gc.open_by_key(CLIO_SHEET_ID).worksheet(CLIO_CURRENT_TAB)

    # Cheap to re-set every time (one small write) rather than reading
    # first to check if it's already there — keeps this function simple,
    # and a label that's already correct just gets overwritten with the
    # same text.
    ws.update(f"A{CLIO_ADHOC_MARKER_ROW}", [["ADHOC MEMBERS"]])
    ws.format(f"A{CLIO_ADHOC_MARKER_ROW}:G{CLIO_ADHOC_MARKER_ROW}", {
        "textFormat": {"bold": True, "fontFamily": "Nunito", "fontSize": 11},
    })

    if is_adhoc:
        start_row, end_row = CLIO_ADHOC_MARKER_ROW + 1, ws.row_count
    else:
        start_row, end_row = 2, CLIO_ADHOC_MARKER_ROW - 1
    target_row = _find_or_next_row(ws, admission_no, start_row, end_row)

    # Column layout matches the real per-year tabs exactly: A=Admission
    # No, B=Name, C=Class, D=Institutional Email, E=Phone 1, F=Phone 2,
    # G=Personal Email — confirmed by reading the real "2025-2026" tab's
    # data directly, not just its header row (the header on F is blank).
    row = [admission_no, name, class_str, email, phone_no, phone_no_2, personal_email]
    # Writing to an explicit "A{row}:G{row}" range rather than gspread's
    # append_row — append_row tries to auto-detect where the sheet's
    # "table" already starts, and on this sheet that guess landed 6
    # columns off (wrote into G:M instead of A:G), confirmed live.
    # Computing the row number ourselves sidesteps that guesswork.
    ws.update(f"A{target_row}:G{target_row}", [row])


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


def has_unread_chats(user_id):
    # Same per-thread "newer than my last read" check messages.py does for
    # its own 🔵 badges — reused here so the nav badge and the page can
    # never disagree about what counts as unread.
    my_thread_ids = {
        p["thread_id"] for p in cached_table("chat_participants")
        if p["user_id"] == user_id
    }
    if not my_thread_ids:
        return False
    my_reads = {
        r["thread_id"]: r.get("last_read_at") for r in cached_table("chat_reads")
        if r["user_id"] == user_id
    }
    latest_by_thread = {}
    for m in cached_table("chat_messages"):
        if m["thread_id"] in my_thread_ids and m["sender_id"] != user_id:
            current = latest_by_thread.get(m["thread_id"])
            if not current or m["created_at"] > current:
                latest_by_thread[m["thread_id"]] = m["created_at"]
    for thread_id, latest in latest_by_thread.items():
        my_read_at = my_reads.get(thread_id)
        if not my_read_at or latest > my_read_at:
            return True
    return False


def create_request_thread(request_group_id, requester_id, owner_id, title):
    # Every borrow request gets its own chat between the two people
    # involved, created upfront with the request rather than waiting for
    # someone to click "message" — so there's always somewhere obvious to
    # ask "which one did you mean?" or "can I pick it up tomorrow?".
    #
    # Best-effort on purpose: a chat is an add-on to the request, so a
    # failure here (most likely the migration not having been run yet)
    # must never turn an otherwise-successful borrow request into an
    # error. Same spirit as notify_if_roster_complete.
    try:
        client = get_client()
        new_thread = client.table("chat_threads").insert({
            "is_group": False,
            "title": title,
            "request_group_id": request_group_id,
            "created_by": requester_id,
        }).execute()
        thread_id = new_thread.data[0]["thread_id"]
        client.table("chat_participants").insert([
            {"thread_id": thread_id, "user_id": requester_id},
            {"thread_id": thread_id, "user_id": owner_id},
        ]).execute()
        invalidate_cache()
        return thread_id
    except Exception:
        return None


def request_thread_id(request_group_id):
    # Which chat belongs to this request group, if the tables exist and one
    # was made. Defensive for the same reason as above.
    try:
        return next(
            (
                t["thread_id"] for t in cached_table("chat_threads")
                if t.get("request_group_id") == request_group_id
            ),
            None,
        )
    except Exception:
        return None


def render_chat_thread(thread_id, participant_ids, key_prefix="chat"):
    # The actual conversation UI, shared by the Messages page and the
    # request cards on Inventory. Lives here rather than in messages.py
    # because app_pages/*.py run top-to-bottom as scripts on import — one
    # page importing another would execute the whole page.
    #
    # key_prefix keeps widget keys unique when the same thread is rendered
    # somewhere other than the Messages page.
    client = get_client()
    current_user_id = st.session_state.current_user_id
    user_name_by_id = st.session_state.user_name_by_id

    thread_messages = sorted(
        (m for m in cached_table("chat_messages") if m["thread_id"] == thread_id),
        key=lambda m: m["created_at"],
    )

    # Read receipt: only WRITE when there's actually something newer from
    # someone else than what's on record, so re-opening an already-read
    # thread doesn't spam updates and cache invalidations for no reason.
    # Skipped entirely for read-only tiers — safe_write isn't used here,
    # but a read-only account has no business writing a receipt either.
    my_read_row = next(
        (r for r in cached_table("chat_reads")
         if r["thread_id"] == thread_id and r["user_id"] == current_user_id),
        None,
    )
    my_read_at = my_read_row.get("last_read_at") if my_read_row else None
    latest_from_other = max(
        (m["created_at"] for m in thread_messages if m["sender_id"] != current_user_id),
        default=None,
    )
    if (
        latest_from_other
        and (not my_read_at or latest_from_other > my_read_at)
        and not st.session_state.get("is_read_only")
    ):
        client.table("chat_reads").upsert({
            "thread_id": thread_id,
            "user_id": current_user_id,
            "last_read_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        invalidate_cache()

    # Everyone else's last-read time, so a sent message can show whether
    # it's been read. In a group that means "read by everyone", which is
    # the only reading of it that doesn't need a per-person breakdown.
    other_ids = [uid for uid in participant_ids if uid != current_user_id]
    other_reads = [
        r.get("last_read_at") for r in cached_table("chat_reads")
        if r["thread_id"] == thread_id and r["user_id"] in other_ids
    ]
    everyone_read_at = (
        min(other_reads)
        if other_reads and len(other_reads) == len(other_ids) and all(other_reads)
        else None
    )

    if not thread_messages:
        st.caption("No messages yet — say something.")

    for msg in thread_messages:
        mine = msg["sender_id"] == current_user_id
        sender_name = "You" if mine else user_name_by_id.get(msg["sender_id"], "Unknown")
        with st.chat_message("user" if mine else "assistant", avatar=":material/person:"):
            if st.session_state.get("editing_chat_message_id") == msg["message_id"]:
                edited_body = st.text_area(
                    "Edit message", value=msg["body"],
                    key=f"{key_prefix}_edit_{msg['message_id']}",
                    label_visibility="collapsed",
                )
                save_col, cancel_col = st.columns([1, 1])
                if save_col.button(
                    "Save", key=f"{key_prefix}_save_{msg['message_id']}", icon=":material/check:"
                ):
                    with safe_write("save this edit"):
                        client.table("chat_messages").update({
                            "body": edited_body.strip(),
                            "edited_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("message_id", msg["message_id"]).execute()
                        invalidate_cache()
                    st.session_state.editing_chat_message_id = None
                    st.rerun()
                if cancel_col.button(
                    "Cancel", key=f"{key_prefix}_cancel_{msg['message_id']}", icon=":material/close:"
                ):
                    st.session_state.editing_chat_message_id = None
                    st.rerun()
            else:
                col1, col2 = st.columns([5, 1], vertical_alignment="center")
                label = f"**{sender_name}**"
                if msg.get("edited_at"):
                    label += " _(edited)_"
                col1.markdown(label)
                st.write(msg["body"])
                timestamp_line = f":material/schedule: {format_relative(msg['created_at'])}"
                if mine:
                    if everyone_read_at and msg["created_at"] <= everyone_read_at:
                        timestamp_line += "  •  :blue[✓✓ Read]"
                    else:
                        timestamp_line += "  •  ✓ Sent"
                st.caption(timestamp_line, help=format_ist(msg["created_at"]))
                # You can only edit your own messages, same as Queries.
                if mine and col2.button(
                    "Edit", key=f"{key_prefix}_editbtn_{msg['message_id']}", icon=":material/edit:"
                ):
                    st.session_state.editing_chat_message_id = msg["message_id"]
                    st.rerun()

    new_text = st.chat_input("Type a message...", key=f"{key_prefix}_input_{thread_id}")
    if new_text and new_text.strip():
        with safe_write("send this message"):
            client.table("chat_messages").insert({
                "thread_id": thread_id,
                "sender_id": current_user_id,
                "body": new_text.strip(),
            }).execute()
            invalidate_cache()
            _notify_new_chat_message(thread_id, other_ids, thread_messages)
        st.rerun()


def _notify_new_chat_message(thread_id, other_ids, existing_messages):
    # Who to email about a message just sent. A chat isn't a query thread —
    # people send several messages in a row — so emailing on EVERY one
    # would be pure spam. Only people who are CAUGHT UP get a mail: if
    # someone already has an unread message sitting in this thread, they've
    # been told once already and don't need telling again for each
    # follow-up. A burst of ten messages therefore sends one email, not ten.
    #
    # The mail deliberately carries NO message text — just "go look". These
    # are private member-to-member conversations, and email is the one
    # place their contents would end up outside the app's own access
    # control, sitting in an inbox indefinitely.
    #
    # Best-effort like every other notification in this app — a mail
    # failure must never lose the message that was actually sent.
    try:
        sender_name = st.session_state.current_user_name
        user_email_by_id = st.session_state.user_email_by_id
        latest_existing = max(
            (m["created_at"] for m in existing_messages), default=None
        )
        reads = {
            r["user_id"]: r.get("last_read_at") for r in cached_table("chat_reads")
            if r["thread_id"] == thread_id
        }
        # Named so the recipient knows WHICH conversation without the app
        # having to put any of its contents in the mail — a group's own
        # name, or a request chat's "2 × Johnson 600rpm".
        thread = next(
            (t for t in cached_table("chat_threads") if t["thread_id"] == thread_id),
            None,
        )
        thread_title = (thread or {}).get("title")
        where = f'"{thread_title}"' if thread_title else "this group"
        for uid in other_ids:
            their_read_at = reads.get(uid)
            caught_up = (
                latest_existing is None
                or (their_read_at and their_read_at >= latest_existing)
            )
            if not caught_up:
                continue
            email = user_email_by_id.get(uid)
            if not email:
                continue
            send_email(
                email,
                f"New message from {sender_name}",
                f"A new message has been sent on the Messages channel in "
                f"{where}, kindly check it.\n\n"
                f"Open the dashboard here: {APP_URL}",
            )
    except Exception:
        pass


def google_calendar_link(title, meeting_date, meeting_time=None, details="", location=""):
    # A plain "add to Google Calendar" URL - no OAuth, no API key, no
    # calendar integration to maintain. The club is on Google Workspace
    # (@dpsrkp.net), so one click puts it in the calendar they already
    # use, and the link still works for anyone else with a Google account.
    #
    # ctz=Asia/Kolkata means the times below are read as IST rather than
    # needing conversion to UTC. A meeting with no time set becomes an
    # all-day entry (Google wants the end date as the NEXT day for those).
    if meeting_time:
        start = datetime.combine(meeting_date, meeting_time)
        end = start + timedelta(hours=1)  # no end time is stored; an hour is the sane default
        dates = f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}"
    else:
        dates = f"{meeting_date.strftime('%Y%m%d')}/{(meeting_date + timedelta(days=1)).strftime('%Y%m%d')}"
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": dates,
        "ctz": "Asia/Kolkata",
        "details": details,
        "location": location,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(
        {k: v for k, v in params.items() if v}
    )


def meeting_email_body(meeting, calendar_link, intro):
    # Shared by the "scheduled" and "moved" emails so the two can't drift.
    lines = [intro, ""]
    lines.append(f"What: {meeting['title']}")
    lines.append(f"When: {date.fromisoformat(meeting['meeting_date']).strftime('%A, %d %B %Y')}"
                 + (f" at {meeting['meeting_time'][:5]}" if meeting.get("meeting_time") else ""))
    if meeting.get("agenda"):
        lines.append(f"Agenda: {meeting['agenda']}")
    if meeting.get("join_link"):
        lines.append(f"Join: {meeting['join_link']}")
    if meeting.get("meeting_id_code"):
        lines.append(f"Meeting ID: {meeting['meeting_id_code']}")
    if meeting.get("meeting_password"):
        lines.append(f"Password: {meeting['meeting_password']}")
    lines += ["", f"Add to your calendar: {calendar_link}", "",
              f"RSVP on the dashboard: {APP_URL}", "", "- RoboKnights"]
    return "\n".join(lines)


def meeting_invitee_rows():
    # Returns [] if the meeting_invitees table doesn't exist yet, instead
    # of letting a PostgREST "relation does not exist" error take down
    # whichever page asked. App code and the SQL schema get deployed
    # separately here (push goes live before anyone runs the migration in
    # Supabase), and an unrun migration should degrade to the old
    # behaviour - every meeting club-wide - not a crashed Home page.
    try:
        return cached_table("meeting_invitees")
    except Exception:
        return []


def meeting_invited_ids(invitee_rows):
    # {meeting_id: {user_id, ...}} — only meetings that actually have named
    # invitees appear as keys, which is what makes "absent = open to
    # everyone" work below.
    invited = {}
    for row in invitee_rows:
        invited.setdefault(row["meeting_id"], set()).add(row["user_id"])
    return invited


def is_meeting_visible(meeting, invited_by_meeting, user_id, is_host=False):
    # ONE definition of who can see a meeting, shared by the Meetings page,
    # the Home page's next-meeting nudge, and the AI Assistant's context —
    # three separate readers that would otherwise drift apart and leak a
    # private meeting through whichever one got missed.
    #
    # No named invitees at all = a normal club-wide meeting (including
    # every meeting that predates this feature). Named invitees = only
    # those people, plus hosts, who schedule and run them.
    invited = invited_by_meeting.get(meeting["meeting_id"])
    if not invited:
        return True
    return is_host or user_id in invited


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
    #
    # Read-only tiers are stopped HERE rather than only at each button
    # (2026-08-16). Inventory alone has 21 write controls; gating every
    # one by hand is how a view-only account eventually writes real data
    # through the one that got missed. Individual controls are still
    # hidden or disabled where it matters for clarity — this is the
    # backstop that makes "view-only" actually true, not the only guard.
    # st.stop(), not `return`: a bare return before the yield would make
    # @contextmanager raise "generator didn't yield". st.stop() raises a
    # BaseException Streamlit handles by ending this run cleanly, which
    # also means the caller's `with` body — the actual writes — never
    # executes at all.
    #
    # TRAP, hit for real (Exun couldn't see a single competition): because
    # this ENDS THE PAGE RUN, a safe_write that runs on page LOAD rather
    # than on a click blanks the whole page for read-only tiers — every
    # section below it simply never renders. Housekeeping writes the page
    # does to itself (the past-competition auto-flip, the Exun channel read
    # receipt) must therefore be guarded with `if not is_read_only:` at the
    # call site. Only writes behind a button belong in a bare safe_write.
    if st.session_state.get("is_read_only"):
        st.error("This is a read-only account — it can't make changes.")
        st.stop()
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


def discord_role_tags():
    # <@&ROLE_ID> pings a Discord ROLE (the & is what distinguishes it from
    # <@USER_ID>, which pings one person). Used instead of tagging members
    # individually: every notification just pings whoever's currently in
    # these two server roles — "Member" for registered students, "Adhoc"
    # for guest/ad-hoc participants who have no account in this app at
    # all — rather than this app trying to work out who's eligible and
    # look up their personal discord_user_id. Skips a role entirely if its
    # ID isn't set, so this still works with only one of the two configured.
    role_ids = [
        os.environ.get("DISCORD_MEMBER_ROLE_ID"),
        os.environ.get("DISCORD_ADHOC_ROLE_ID"),
    ]
    return " ".join(f"<@&{rid}>" for rid in role_ids if rid)


# Appended to the end of EVERY message this app sends to Discord, whichever
# channel — a bold-italic marker saying it wasn't typed by a person. Same
# wording on every channel now (an earlier version had the dashboard channel
# say "an automated channel" instead — reverted, student wanted it consistent
# with the other two).
#
# The app link is still competitions-only: those messages are all "go do
# something in the app" (volunteer, check a new event), where a link back
# genuinely helps; the other channels' posts are FYI-only.
#
# Added centrally here (not at each call site) so the footer can't be
# forgotten by a future call site.
DISCORD_AUTOMATED_MARKER = "\n\n***This is automated message***"


def discord_message_suffix(channel):
    if channel == "competitions":
        return f"\n\n:link: {APP_URL}{DISCORD_AUTOMATED_MARKER}"
    return DISCORD_AUTOMATED_MARKER


# The Discord channels this app can post to, each its own Incoming Webhook
# (a webhook is tied to exactly one channel — there's no such thing as "one
# webhook, pick a channel"), each its own env secret. "competitions" is the
# default everywhere below since it's the original, far more common case
# (every automatic notification), so call sites that don't care about the
# other channels don't need to change.
#
# "dashboard" is written to by send_dashboard_update.py (a GitHub Action on
# every push to master), not by the app itself — it's listed here so the
# Discord Messages page can still show, edit and delete what got posted.
DISCORD_CHANNELS = {
    "competitions": "DISCORD_COMPETITIONS_WEBHOOK_URL",
    "exun_rk": "DISCORD_EXUN_WEBHOOK_URL",
    "dashboard": "DISCORD_DASHBOARD_WEBHOOK_URL",
    "general": "DISCORD_GENERAL_WEBHOOK_URL",
    "announcements": "DISCORD_ANNOUNCEMENTS_WEBHOOK_URL",
}

# A webhook lets Discord's `username`/`avatar_url` fields on the POST body
# override how the message displays — used so these notifications show up
# looking like they came from the bot (discord_bot/bot.py, name
# "roboknightsbot") instead of a generic incoming-webhook poster, without
# actually routing the send through that separate always-on process. It
# won't carry Discord's blue "APP" badge (only a message the bot account
# itself sends gets that) — purely a display-identity match, not a real
# bot-authored message.
DISCORD_BOT_USERNAME = "roboknightsbot"
DISCORD_BOT_AVATAR_URL = "https://cdn.discordapp.com/avatars/1536836032329416724/5ebc6d79217e395322b1faf5107e095f.png"

# Rate limit on the Discord Messages page's free-text custom-message tool
# specifically (host-requested, 2026-08-15, after a burst of ad-hoc
# messages got sent in quick succession) — NOT applied to
# send_discord_message() itself, since that would also throttle the
# automated notifications (new competition event, roster-complete, vacant
# events reminder, dashboard-update changelog) that have nothing to do
# with someone spamming the free-text box. Shared across every host/every
# session in this process (an @st.cache_resource list, same "one shared
# mutable object per process" pattern as get_sheets_write_client above),
# not per-host, since the actual risk is the channel getting flooded
# regardless of which host's session did it.
CUSTOM_DISCORD_MESSAGE_HOURLY_LIMIT = 15


@st.cache_resource
def _custom_discord_send_log():
    return []


def custom_discord_send_allowed():
    now = time.time()
    log = _custom_discord_send_log()
    while log and now - log[0] > 3600:
        log.pop(0)
    if len(log) >= CUSTOM_DISCORD_MESSAGE_HOURLY_LIMIT:
        return False
    log.append(now)
    return True


def send_discord_message(content, channel="competitions"):
    # A Discord Incoming Webhook is a plain HTTP POST — unlike a real bot,
    # it needs no persistent gateway connection or separate 24/7 process,
    # so it fits this app's existing hosting (only runs when someone's
    # using it, or during a scheduled GitHub Actions job) with no new
    # infra. Silently no-ops if that channel's webhook isn't set up yet,
    # same best-effort spirit as send_email/send_whatsapp — safe to wire
    # in anywhere before the Discord side is finished.
    #
    # ?wait=true makes Discord return the created message (instead of a
    # bare 204) so the caller gets its id back — needed to delete/edit
    # this specific message later, without that meaning "wait for real
    # delivery confirmation" or anything slower.
    # Logged to discord_messages (with which channel it went to, so a
    # later edit/delete knows which webhook to use) on success, so a host
    # can come back later and edit or delete an OLDER message too, not
    # just the one just sent.
    webhook_url = os.environ.get(DISCORD_CHANNELS[channel])
    if not webhook_url:
        return None
    full_content = f"{content}{discord_message_suffix(channel)}"
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
            get_client().table("discord_messages").insert({
                "message_id": message_id, "content": full_content, "channel": channel,
            }).execute()
        return message_id
    except Exception:
        return None


def delete_discord_message(channel, message_id):
    # A webhook can only delete messages IT sent (not just anyone's in the
    # channel) — exactly the scope needed here: undoing a message this
    # app itself posted, via the same webhook, nothing broader. Also
    # removes its discord_messages row, so that log only ever reflects
    # what's currently still live in Discord.
    webhook_url = os.environ.get(DISCORD_CHANNELS[channel])
    if not webhook_url or not message_id:
        return
    try:
        requests.delete(f"{webhook_url}/messages/{message_id}", timeout=10)
        get_client().table("discord_messages").delete().eq("message_id", message_id).execute()
    except Exception:
        pass


def edit_discord_message(channel, message_id, new_content):
    # Same "a webhook can only touch messages IT sent" scope as delete,
    # just PATCHing instead. Re-appends the same suffix send_discord_message
    # would (the caller passes just the body) so an edit can't accidentally
    # drop it. Returns True/False instead of silently no-oping like
    # send/delete, since the caller here is an inline edit box that needs
    # to tell the host whether it actually worked before clearing the editor.
    webhook_url = os.environ.get(DISCORD_CHANNELS[channel])
    if not webhook_url or not message_id:
        return False
    full_content = f"{new_content}{discord_message_suffix(channel)}"
    try:
        response = requests.patch(
            f"{webhook_url}/messages/{message_id}", json={"content": full_content}, timeout=10
        )
        if response.status_code >= 400:
            return False
        get_client().table("discord_messages").update(
            {"content": full_content}
        ).eq("message_id", message_id).execute()
        return True
    except Exception:
        return False


def build_vacant_events_message():
    # "Vacant" = still has open participation slots (team_size * max_teams
    # per event) that no one has volunteered for yet — same capacity math
    # the Volunteer button on the Competitions page already uses. An event
    # with MORE volunteers than capacity (already oversubscribed) or
    # exactly full isn't vacant, so it's skipped rather than shown with a
    # confusing negative or zero number. Covers every upcoming competition,
    # not just specific ones — a competition with nothing vacant just
    # doesn't get a section below. Lives here (not in competitions.py) so
    # the Discord Messages page can call it without executing that whole
    # page script.
    competitions_of_interest = [c for c in cached_table("competitions") if not c.get("is_past")]
    if not competitions_of_interest:
        return None

    all_events = cached_table("competition_events")
    all_volunteers = cached_table("event_volunteers")

    sections = []
    for comp in sorted(competitions_of_interest, key=lambda c: c["name"]):
        comp_events = [e for e in all_events if e["competition_id"] == comp["competition_id"]]
        vacant_lines = []
        for e in sorted(comp_events, key=lambda e: e["name"]):
            capacity = e["team_size"] * e["max_teams"]
            filled = len([v for v in all_volunteers if v["event_id"] == e["event_id"]])
            vacant = capacity - filled
            if vacant <= 0:
                continue
            spot_word = "spot" if vacant == 1 else "spots"
            vacant_lines.append(
                f"- **{e['name']}** (Grade {e['min_grade']}–{e['max_grade']}) — {vacant} {spot_word} open"
            )
        if vacant_lines:
            sections.append(f"**{comp['name']}**\n" + "\n".join(vacant_lines))

    if not sections:
        return None

    # Pings the @member / @adhoc SERVER ROLES once at the end, not
    # individual members per event.
    tags = discord_role_tags()
    message = (
        ":rotating_light: **Vacant events — sign up now!**\n\n"
        + "\n\n".join(sections)
        + "\n\nLog in to the app to volunteer."
    )
    if tags:
        message += f"\n{tags}"
    return message


def notify_if_roster_complete(competition_id):
    # "Complete" = every event under this competition has as many SELECTED
    # (finalized, not just volunteered) people as its capacity
    # (team_size * max_teams) — i.e. the actual team NAMES are locked in
    # for the whole competition, not just "enough people signed up".
    # Posts to the exun_rk channel specifically (that's what this was
    # asked for), once per time the competition transitions from
    # incomplete to complete — competitions.roster_complete_notified
    # tracks that, and gets reset back to False the moment it's no longer
    # complete (someone unselected), so a LATER re-completion notifies
    # again instead of staying silently stuck "already notified".
    #
    # Deliberately NOT cached_table: called right after a write that
    # changes `selected`, so it needs the true current state, not up to
    # 8s old — same reasoning as _insert_matched_participants in
    # competitions.py.
    #
    # Best-effort, same spirit as send_discord_message/send_email: this
    # runs as a side effect tacked onto a real write (saving a selection,
    # syncing from E2C) that must still succeed even if this check can't
    # (e.g. the roster_complete_notified column hasn't been migrated in
    # yet) — a notification failing here should never make safe_write
    # report the actual save itself as failed.
    try:
        client = get_client()
        comp_rows = client.table("competitions").select(
            "competition_id, name, roster_complete_notified"
        ).eq("competition_id", competition_id).execute().data
        if not comp_rows:
            return
        comp = comp_rows[0]

        events = client.table("competition_events").select(
            "event_id, name, team_size, max_teams"
        ).eq("competition_id", competition_id).execute().data
        if not events:
            return

        event_ids = [e["event_id"] for e in events]
        volunteers = client.table("event_volunteers").select("event_id, selected").in_(
            "event_id", event_ids
        ).execute().data
        selected_counts = {}
        for v in volunteers:
            if v.get("selected"):
                selected_counts[v["event_id"]] = selected_counts.get(v["event_id"], 0) + 1
        is_complete = all(
            selected_counts.get(e["event_id"], 0) >= e["team_size"] * e["max_teams"] for e in events
        )

        already_notified = bool(comp.get("roster_complete_notified"))
        if is_complete and not already_notified:
            message = (
                f":white_check_mark: **Team names finalized: {comp['name']}**\n"
                f"Every event now has its full roster selected — nothing left vacant."
            )
            send_discord_message(message, channel="exun_rk")
            client.table("competitions").update({"roster_complete_notified": True}).eq(
                "competition_id", competition_id
            ).execute()
        elif not is_complete and already_notified:
            client.table("competitions").update({"roster_complete_notified": False}).eq(
                "competition_id", competition_id
            ).execute()
    except Exception:
        pass

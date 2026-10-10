# Host-only viewer for the audit trail: who changed what, and when.
#
# The rows are written by shared.py (AuditedClient wraps every dashboard
# write; log_action records sign-ins, sign-outs and Discord posts). This
# page only reads. Nothing here edits or deletes a row - the table is
# append-only for the normal key on purpose (see the migration).
#
# Why it exists: on 2026-10-10 a member's account was disabled and nobody
# could say when or by whom - the dashboard stored a bare true/false.
#
# Times are shown in IST. The "Device" column is only filled on sign-in and
# sign-out rows and is wiped after AUDIT_DEVICE_DATA_DAYS days. It is the
# app SERVER's view of the request headers, so treat it as a hint about
# which browser a session used, never as proof of who a person is.

import csv
import io
from datetime import datetime, timedelta, timezone

import streamlit as st

from shared import AUDIT_DEVICE_DATA_DAYS, IST, get_client, today_ist

is_host = st.session_state.is_host

st.title(":material/policy: Audit Log")

if not is_host:
    st.info(":material/lock: Host-only page.")
    st.stop()

st.caption(
    "Who changed what, and when - every save on the dashboard, every message "
    "it posts to Discord, and every sign-in. Read-only: rows can't be edited "
    "or deleted from here."
)

PAGE_SIZE = 500

ACTION_GROUPS = {
    "All": None,
    "Accounts (disable, roles, edits)": ("account.", "users."),
    "Sign-ins and sign-outs": ("session.",),
    "Discord posts": ("discord.",),
    "Merch": ("merch_",),
    "Competitions and volunteers": ("competitions.", "competition_", "event_volunteers."),
    "Deletes": (".delete",),
}

col_group, col_who, col_from, col_to = st.columns([2, 2, 1, 1], vertical_alignment="bottom")
group = col_group.selectbox("Show", list(ACTION_GROUPS), key="audit_group")
who = col_who.text_input("Account (email contains)", key="audit_who")
start = col_from.date_input("From", value=today_ist() - timedelta(days=7), key="audit_from")
end = col_to.date_input("To", value=today_ist(), key="audit_to")
search = st.text_input("Search the description", key="audit_search", placeholder="e.g. Kyraan")


def _fetch():
    # Straight from Supabase rather than the 8-second table cache: this
    # page is queried and filtered server-side, and a table that grows
    # forever should not be pulled whole into memory.
    start_utc = datetime.combine(start, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    end_utc = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    query = (
        get_client().table("audit_log").select("*")
        .gte("at", start_utc.isoformat()).lt("at", end_utc.isoformat())
        .order("at", desc=True).limit(PAGE_SIZE)
    )
    if who.strip():
        query = query.ilike("actor_email", f"%{who.strip()}%")
    if search.strip():
        query = query.ilike("summary", f"%{search.strip()}%")
    prefixes = ACTION_GROUPS[group]
    if prefixes:
        query = query.or_(",".join(f"action.ilike.%{p}%" for p in prefixes))
    return query.execute().data


try:
    rows = _fetch()
except Exception:
    st.warning(
        ":material/database: The audit log table doesn't exist yet. Run the "
        "\"Audit log\" migration at the end of `supabase_schema.sql` in the Supabase SQL "
        "editor, then reload this page."
    )
    st.stop()


def _ist(timestamp):
    posted = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return posted.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p")


table = [
    {
        "When (IST)": _ist(r["at"]),
        "Who": r.get("actor_name") or r.get("actor_email") or "system / unknown",
        "Account": r.get("actor_email") or "",
        "Action": r["action"],
        "What": r.get("summary") or r.get("target") or "",
        "Device": " | ".join(x for x in (r.get("client_ip"), r.get("user_agent")) if x),
    }
    for r in rows
]

st.metric("Entries shown", len(table), help=f"Newest first, up to {PAGE_SIZE}. Narrow the filters for more.")
if len(rows) == PAGE_SIZE:
    st.caption(f"Showing the newest {PAGE_SIZE}. Narrow the dates or filters to see older entries.")

if not table:
    st.info("Nothing recorded for these filters.")
else:
    st.dataframe(table, hide_index=True, width="stretch")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    st.download_button(
        "Download as CSV", buffer.getvalue(), file_name="audit_log.csv",
        mime="text/csv", icon=":material/download:",
    )

st.caption(
    f"The Device column (address and browser) appears only on sign-in and sign-out rows and "
    f"is wiped after {AUDIT_DEVICE_DATA_DAYS} days. It shows what the app server saw, so it "
    f"can suggest which browser was used but can't prove who someone is."
)

# Landing page after login — a quick "what's relevant to you right now"
# instead of making everyone click through every one of the app's other
# pages just to check for anything new. Every section here only reads data
# those pages already write; nothing new is tracked to get this. In
# particular, a query thread's "new reply" isn't a stored read/unread flag
# — it's just whether the thread's last message wasn't sent by you.
#
# Layout: a metrics strip across the top (the "how many things want me?"
# glance), then a wide left column for things needing action and a narrow
# right column for announcements + navigation. Anything with nothing to
# show is skipped entirely, so this stays a summary rather than turning
# into a copy of every other page.

from datetime import date

import streamlit as st

from shared import format_ist, get_client

client = get_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
is_host = st.session_state.is_host

today = date.today()
today_iso = today.isoformat()


# --- Gather everything first, then render ------------------------------------
# Done up front (rather than query-as-you-render) so the metrics at the top
# can count the same things the cards below list, without asking Supabase
# for any of it twice.

def _threads_awaiting_me(query_ids):
    # A thread "wants" you when its most recent message came from the other
    # side — the host waiting to reply, or a student with an unread answer.
    # No read/unread column needed for this; who sent last is enough.
    if not query_ids:
        return 0
    messages = (
        client.table("query_messages")
        .select("query_id, sender_id, created_at")
        .in_("query_id", query_ids)
        .order("created_at")
        .execute()
        .data
    )
    waiting = 0
    for qid in query_ids:
        thread_messages = [m for m in messages if m["query_id"] == qid]
        if thread_messages and thread_messages[-1]["sender_id"] != current_user_id:
            waiting += 1
    return waiting


# Part requests sitting on my approval, and parts I've borrowed from others.
pending_for_me = (
    client.table("requests")
    .select("*")
    .eq("owner_id", current_user_id)
    .eq("status", "pending")
    .execute()
    .data
)
my_borrowed = (
    client.table("requests")
    .select("*")
    .eq("requester_id", current_user_id)
    .eq("status", "approved")
    .execute()
    .data
)
part_by_id = {}
if my_borrowed or pending_for_me:
    part_ids = [r["part_id"] for r in my_borrowed + pending_for_me]
    part_by_id = {
        p["part_id"]: p
        for p in client.table("parts")
        .select("part_id, name, part_number")
        .in_("part_id", part_ids)
        .execute()
        .data
    }

# Queries: the host sees every thread, a student only their own.
if is_host:
    my_query_ids = [q["query_id"] for q in client.table("queries").select("query_id").execute().data]
else:
    my_query_ids = [
        q["query_id"]
        for q in client.table("queries").select("query_id").eq("student_id", current_user_id).execute().data
    ]
queries_waiting = _threads_awaiting_me(my_query_ids)

# Meetings: upcoming ones for the list, plus any already-happened ones I
# never checked into — check-in unlocks on the meeting's own day (see
# meetings.py), so "today" counts as both upcoming and checkable.
upcoming_meetings = (
    client.table("meetings").select("*").gte("meeting_date", today_iso).order("meeting_date").execute().data
)
my_rsvp_ids = {
    r["meeting_id"]
    for r in client.table("meeting_rsvps").select("meeting_id").eq("user_id", current_user_id).execute().data
}
my_attended_ids = {
    a["meeting_id"]
    for a in client.table("meeting_attendance").select("meeting_id").eq("user_id", current_user_id).execute().data
}
meetings_today = [
    m for m in upcoming_meetings
    if m["meeting_date"] == today_iso and m["meeting_id"] not in my_attended_ids
]

# Competitions I've put my hand up for that haven't happened yet.
my_volunteer_rows = (
    client.table("event_volunteers").select("*").eq("user_id", current_user_id).execute().data
)
upcoming_competitions = []
if my_volunteer_rows:
    event_ids = [v["event_id"] for v in my_volunteer_rows]
    my_events = client.table("competition_events").select("*").in_("event_id", event_ids).execute().data
    comp_ids = [e["competition_id"] for e in my_events]
    comp_by_id = {
        c["competition_id"]: c
        for c in client.table("competitions").select("*").in_("competition_id", comp_ids).execute().data
    }
    for v in my_volunteer_rows:
        event = next((e for e in my_events if e["event_id"] == v["event_id"]), None)
        comp = comp_by_id.get(event["competition_id"]) if event else None
        # Skip anything already run, cancelled-out, or whose competition was deleted.
        if not comp or comp.get("is_past") or comp.get("not_attending"):
            continue
        upcoming_competitions.append((comp, event, v))
    upcoming_competitions.sort(key=lambda row: row[0].get("competition_date") or "9999-99-99")

latest_announcements = (
    client.table("announcements").select("*").order("created_at", desc=True).limit(3).execute().data
)


# --- Header + metrics strip ---------------------------------------------------

st.title(f":material/waving_hand: Welcome back, {current_user_name.split()[0]}")
st.caption("Everything waiting on you, in one place.")

m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Part requests for me", len(pending_for_me), border=True,
    help="Requests from other members waiting on your approval",
)
m2.metric(
    "Upcoming meetings", len(upcoming_meetings), border=True,
    help="Meetings scheduled for today or later",
)
m3.metric(
    "Queries needing a reply" if is_host else "Queries with a new reply",
    queries_waiting, border=True,
    help="Threads where the last message wasn't yours",
)
m4.metric(
    "My competitions", len(upcoming_competitions), border=True,
    help="Upcoming events you've volunteered for or been selected for",
)

st.divider()

feed_col, side_col = st.columns([2, 1], gap="medium")


# --- Left: what actually needs you --------------------------------------------

with feed_col:
    st.subheader(":material/priority_high: Needs you right now")

    # Ordered by how time-sensitive each thing is: an overdue part first,
    # then approvals blocking someone else, then your own follow-ups.
    nothing_pending = True

    for r in sorted(my_borrowed, key=lambda r: r.get("due_date") or "9999-99-99"):
        due = r.get("due_date")
        if not due:
            continue  # no due date set — nothing to be late for
        days_left = (date.fromisoformat(due) - today).days
        if days_left > 2:
            continue  # not urgent yet; it's listed under "borrowed" below
        nothing_pending = False
        part = part_by_id.get(r["part_id"], {})
        label = f"**{part.get('name', 'Unknown')}** ({part.get('part_number', '?')})"
        with st.container(border=True, key=f"rkcard_home_due_{r['request_id']}"):
            if days_left < 0:
                st.markdown(f":material/warning: :red[Overdue by {-days_left} day(s)] — {label}")
            elif days_left == 0:
                st.markdown(f":material/schedule: :orange[Due today] — {label}")
            else:
                st.markdown(f":material/schedule: :orange[Due in {days_left} day(s)] — {label}")
            st.caption(f"Return it to its owner • was due {due}")

    if pending_for_me:
        nothing_pending = False
        with st.container(border=True, key="rkcard_home_approvals"):
            st.markdown(
                f":material/inventory_2: **{len(pending_for_me)}** part request(s) waiting on your approval"
            )
            for r in pending_for_me[:3]:
                part = part_by_id.get(r["part_id"], {})
                st.caption(f"{part.get('part_number', '?')} — {part.get('name', 'Unknown')}")
            st.page_link("app_pages/inventory.py", label="Review requests", icon=":material/arrow_forward:")

    if queries_waiting:
        nothing_pending = False
        with st.container(border=True, key="rkcard_home_queries"):
            if is_host:
                st.markdown(f":material/quiz: **{queries_waiting}** query thread(s) waiting on your reply")
            else:
                st.markdown(f":material/quiz: **{queries_waiting}** of your questions got a reply")
            st.page_link("app_pages/queries.py", label="Open Queries", icon=":material/arrow_forward:")

    if meetings_today:
        nothing_pending = False
        for m in meetings_today:
            with st.container(border=True, key=f"rkcard_home_checkin_{m['meeting_id']}"):
                st.markdown(f":material/how_to_reg: **{m['title']}** is today — check in when you're there")
                if m.get("meeting_time"):
                    st.caption(f"Starts {m['meeting_time']}")
                st.page_link("app_pages/meetings.py", label="Go to Meetings", icon=":material/arrow_forward:")

    if nothing_pending:
        with st.container(border=True, key="rkcard_home_allclear"):
            st.markdown(":material/check_circle: **All clear** — nothing needs your attention right now.")

    # --- Your commitments (context, not action) ------------------------------

    if upcoming_competitions:
        st.subheader(":material/emoji_events: Your upcoming competitions")
        for comp, event, v in upcoming_competitions:
            with st.container(border=True, key=f"rkcard_home_comp_{v['volunteer_id']}"):
                head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
                head_col.markdown(f"**{event['name']}** — {comp['name']}")
                if v.get("selected"):
                    badge_col.badge("Selected", color="green", icon=":material/verified:")
                else:
                    badge_col.badge("Volunteered", color="orange", icon=":material/front_hand:")
                comp_date = comp.get("competition_date")
                date_label = (
                    date.fromisoformat(comp_date).strftime("%d %b %Y") if comp_date else "date TBD"
                )
                st.caption(f":material/event: {date_label}   •   :material/location_on: {comp.get('venue') or 'venue TBD'}")
        st.page_link("app_pages/competitions.py", label="All competitions", icon=":material/arrow_forward:")

    if my_borrowed:
        st.subheader(":material/inventory_2: Parts you've borrowed")
        for r in sorted(my_borrowed, key=lambda r: r.get("due_date") or "9999-99-99"):
            part = part_by_id.get(r["part_id"], {})
            due = r.get("due_date")
            due_label = (
                f"due {date.fromisoformat(due).strftime('%d %b %Y')}" if due else "no due date set"
            )
            with st.container(border=True, key=f"rkcard_home_borrowed_{r['request_id']}"):
                st.markdown(f"**{part.get('name', 'Unknown')}** ({part.get('part_number', '?')})")
                st.caption(f":material/schedule: {due_label}")

    if upcoming_meetings:
        st.subheader(":material/groups: Upcoming meetings")
        for m in upcoming_meetings[:5]:
            with st.container(border=True, key=f"rkcard_home_meeting_{m['meeting_id']}"):
                head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
                head_col.markdown(f"**{m['title']}**")
                if m["meeting_id"] in my_rsvp_ids:
                    badge_col.badge("Going", color="green", icon=":material/check:")
                else:
                    badge_col.badge("No RSVP", color="grey", icon=":material/help:")
                when = date.fromisoformat(m["meeting_date"]).strftime("%d %b %Y")
                if m.get("meeting_time"):
                    when += f" at {m['meeting_time']}"
                st.caption(f":material/event: {when}")
        st.page_link("app_pages/meetings.py", label="All meetings", icon=":material/arrow_forward:")


# --- Right: announcements + navigation ----------------------------------------

with side_col:
    st.subheader(":material/campaign: Latest announcements")
    if not latest_announcements:
        st.caption("Nothing posted yet.")
    else:
        for a in latest_announcements:
            with st.container(border=True, key=f"rkcard_home_ann_{a['announcement_id']}"):
                st.markdown(f"**{a['subject']}**")
                st.caption(f":material/schedule: {format_ist(a['created_at'])}")
                body = a["body"]
                # Keep the sidebar column skimmable — the full text is one
                # click away on the Announcements page.
                st.write(body if len(body) <= 180 else body[:177] + "...")
        st.page_link("app_pages/announcements.py", label="All announcements", icon=":material/arrow_forward:")

    st.subheader(":material/link: Quick links")
    with st.container(border=True, key="rkcard_home_links"):
        st.page_link("app_pages/inventory.py", label="Inventory", icon=":material/inventory_2:")
        st.page_link("app_pages/competitions.py", label="Competitions", icon=":material/emoji_events:")
        st.page_link("app_pages/meetings.py", label="Meetings", icon=":material/groups:")
        st.page_link("app_pages/achievements.py", label="Achievements", icon=":material/military_tech:")
        st.page_link("app_pages/queries.py", label="Queries", icon=":material/quiz:")
        if is_host:
            st.page_link("app_pages/members.py", label="Members", icon=":material/badge:")

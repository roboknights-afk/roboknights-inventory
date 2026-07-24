# Landing page after login — a quick "what's relevant to you right now"
# instead of making everyone click through every one of the app's other
# pages just to check for anything new. Every section here only reads data
# those pages already write; nothing new is tracked to get this. In
# particular, a query thread's "new reply" isn't a stored read/unread flag
# — it's just whether the thread's last message wasn't sent by you.
# Sections with nothing to show just don't render, so this stays a tight
# summary instead of growing into a copy of every other page.

from datetime import date

import streamlit as st

from shared import format_ist, get_client

client = get_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
is_host = st.session_state.is_host

st.title(f":material/waving_hand: Welcome back, {current_user_name.split()[0]}")

today = date.today().isoformat()

# --- Needs your attention ----------------------------------------------------
pending_for_me = (
    client.table("requests").select("*").eq("owner_id", current_user_id).eq("status", "pending").execute().data
)

awaiting_host_reply = 0
if is_host:
    my_query_threads = client.table("queries").select("query_id").execute().data
    if my_query_threads:
        thread_ids = [q["query_id"] for q in my_query_threads]
        all_msgs = (
            client.table("query_messages")
            .select("query_id, sender_id, created_at")
            .in_("query_id", thread_ids)
            .order("created_at")
            .execute()
            .data
        )
        for qid in thread_ids:
            thread_msgs = [m for m in all_msgs if m["query_id"] == qid]
            if thread_msgs and thread_msgs[-1]["sender_id"] != current_user_id:
                awaiting_host_reply += 1

st.subheader(":material/priority_high: Needs your attention")
if not pending_for_me and not awaiting_host_reply:
    st.caption("Nothing needs your attention right now.")
else:
    if pending_for_me:
        st.info(f":material/inventory_2: **{len(pending_for_me)}** part request(s) waiting on your approval.")
        st.page_link("app_pages/inventory.py", label="Go to Inventory", icon=":material/arrow_forward:")
    if awaiting_host_reply:
        st.info(f":material/quiz: **{awaiting_host_reply}** query thread(s) waiting on your reply.")
        st.page_link("app_pages/queries.py", label="Go to Queries", icon=":material/arrow_forward:")

# --- What you've borrowed ----------------------------------------------------
my_borrowed = (
    client.table("requests").select("*").eq("requester_id", current_user_id).eq("status", "approved").execute().data
)
if my_borrowed:
    part_ids = [r["part_id"] for r in my_borrowed]
    part_by_id = {
        p["part_id"]: p
        for p in client.table("parts").select("part_id, name, part_number").in_("part_id", part_ids).execute().data
    }
    st.subheader(":material/inventory_2: What you've borrowed")
    for r in sorted(my_borrowed, key=lambda r: r.get("due_date") or "9999-99-99"):
        part = part_by_id.get(r["part_id"], {})
        part_label = f"**{part.get('name', 'Unknown')}** ({part.get('part_number', '?')})"
        due = r.get("due_date")
        if not due:
            st.caption(f"{part_label} — no due date set")
        else:
            days_left = (date.fromisoformat(due) - date.today()).days
            if days_left < 0:
                st.warning(f":material/warning: {part_label} — overdue by {-days_left} day(s) (was due {due})")
            elif days_left <= 2:
                st.warning(f":material/schedule: {part_label} — due in {days_left} day(s) ({due})")
            else:
                st.caption(f"{part_label} — due {due}")

# --- Upcoming meetings --------------------------------------------------------
upcoming_meetings = (
    client.table("meetings").select("*").gte("meeting_date", today).order("meeting_date").execute().data
)
if upcoming_meetings:
    my_rsvp_ids = {
        r["meeting_id"]
        for r in client.table("meeting_rsvps").select("meeting_id").eq("user_id", current_user_id).execute().data
    }
    st.subheader(":material/groups: Upcoming meetings")
    for m in upcoming_meetings[:5]:
        rsvped = m["meeting_id"] in my_rsvp_ids
        status = ":material/check_circle: You're going" if rsvped else ":material/help: You haven't RSVP'd yet"
        st.caption(f"**{m['title']}** — {m['meeting_date']}  •  {status}")
    st.page_link("app_pages/meetings.py", label="Go to Meetings", icon=":material/arrow_forward:")

# --- Your upcoming competitions ----------------------------------------------
my_volunteer_rows = client.table("event_volunteers").select("*").eq("user_id", current_user_id).execute().data
if my_volunteer_rows:
    event_ids = [v["event_id"] for v in my_volunteer_rows]
    my_events = client.table("competition_events").select("*").in_("event_id", event_ids).execute().data
    comp_ids = [e["competition_id"] for e in my_events]
    comp_by_id = {
        c["competition_id"]: c
        for c in client.table("competitions").select("*").in_("competition_id", comp_ids).execute().data
    }

    upcoming_rows = []
    for v in my_volunteer_rows:
        event = next((e for e in my_events if e["event_id"] == v["event_id"]), None)
        comp = comp_by_id.get(event["competition_id"]) if event else None
        if not comp or comp.get("is_past"):
            continue  # skip events whose competition is done or was deleted
        upcoming_rows.append((comp, event, v))

    if upcoming_rows:
        upcoming_rows.sort(key=lambda row: row[0].get("competition_date") or "9999-99-99")
        st.subheader(":material/emoji_events: Your upcoming competitions")
        for comp, event, v in upcoming_rows:
            status = "Selected" if v.get("selected") else "Volunteered (not finalized yet)"
            date_str = comp.get("competition_date") or "date TBD"
            st.caption(f"**{event['name']}** at {comp['name']} — {date_str}  •  {status}")
        st.page_link("app_pages/competitions.py", label="Go to Competitions", icon=":material/arrow_forward:")

# --- Latest announcements -----------------------------------------------------
latest_announcements = (
    client.table("announcements").select("*").order("created_at", desc=True).limit(3).execute().data
)
if latest_announcements:
    st.subheader(":material/campaign: Latest announcements")
    for a in latest_announcements:
        with st.container(border=True):
            st.markdown(f"**{a['subject']}**")
            st.caption(format_ist(a["created_at"]))
            st.write(a["body"])
    st.page_link("app_pages/announcements.py", label="Go to Announcements", icon=":material/arrow_forward:")

# --- Your query threads (students only — host side is in "Needs your attention") ---
if not is_host:
    my_threads = client.table("queries").select("query_id").eq("student_id", current_user_id).execute().data
    if my_threads:
        thread_ids = [q["query_id"] for q in my_threads]
        all_msgs = (
            client.table("query_messages")
            .select("query_id, sender_id, created_at")
            .in_("query_id", thread_ids)
            .order("created_at")
            .execute()
            .data
        )
        new_replies = 0
        for qid in thread_ids:
            thread_msgs = [m for m in all_msgs if m["query_id"] == qid]
            if thread_msgs and thread_msgs[-1]["sender_id"] != current_user_id:
                new_replies += 1
        if new_replies:
            st.subheader(":material/quiz: Query replies")
            st.info(f"You have **{new_replies}** thread(s) with a new reply.")
            st.page_link("app_pages/queries.py", label="Go to Queries", icon=":material/arrow_forward:")

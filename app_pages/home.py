# Landing page after login — a quick "what's relevant to you right now"
# instead of making everyone click through every one of the app's other
# pages just to check for anything new. Every section here only reads data
# those pages already write; nothing new is tracked to get this. A query
# thread's "new reply" uses the real host_read_at/student_read_at columns
# queries.py maintains (see _threads_awaiting_me below).
#
# Layout: a metrics strip across the top (the "how many things want me?"
# glance), then a wide left column for things needing action and a narrow
# right column for announcements + navigation. Anything with nothing to
# show is skipped entirely, so this stays a summary rather than turning
# into a copy of every other page.

from datetime import date

import streamlit as st

from shared import (
    cached_table, discord_id_problem, format_ist, get_client, invalidate_cache, is_meeting_visible,
    meeting_invited_ids, meeting_invitee_rows, relative_days_label, safe_write, today_ist,
)

# Read-only page — every table it needs goes through the shared 8-second
# cache instead of a fresh Supabase round trip per query, so landing here
# doesn't cost 11 network calls every time.
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
is_host = st.session_state.is_host
# Exun (sister club) accounts don't get Inventory, Announcements, or Queries
# in the nav at all (see app.py's st.navigation list) — st.page_link to any
# of those crashes the whole page for them if not guarded the same way here.
is_exun = st.session_state.is_exun
is_viewer = st.session_state.is_viewer
is_read_only = st.session_state.is_read_only

# IST "today", not the UTC server's — otherwise everything date-sensitive
# here (overdue warnings, "meeting is today" check-ins) runs up to 5.5
# hours behind real IST mornings.
today = today_ist()
today_iso = today.isoformat()


# --- Gather everything first, then render ------------------------------------
# Done up front (rather than query-as-you-render) so the metrics at the top
# can count the same things the cards below list, without asking Supabase
# for any of it twice.

def _threads_awaiting_me(queries_for_me):
    # A thread "wants" you when the other side has sent something since you
    # last actually opened it — using the real host_read_at/student_read_at
    # read-receipt columns, not just "who sent the last message" (that older
    # heuristic couldn't tell an already-read thread from a genuinely new one
    # once you'd replied and the other side hadn't come back yet).
    if not queries_for_me:
        return 0
    query_ids = {q["query_id"] for q in queries_for_me}
    messages = sorted(
        (m for m in cached_table("query_messages") if m["query_id"] in query_ids),
        key=lambda m: m["created_at"],
    )
    read_field = "host_read_at" if is_host else "student_read_at"
    waiting = 0
    for q in queries_for_me:
        thread_messages = [m for m in messages if m["query_id"] == q["query_id"]]
        others_messages = [m for m in thread_messages if m["sender_id"] != current_user_id]
        if not others_messages:
            continue
        latest_other = others_messages[-1]["created_at"]
        my_read_at = q.get(read_field)
        if not my_read_at or latest_other > my_read_at:
            waiting += 1
    return waiting


# Part requests sitting on my approval, and parts I've borrowed from others.
all_requests = cached_table("requests")
pending_for_me = [
    r for r in all_requests if r["owner_id"] == current_user_id and r["status"] == "pending"
]
my_borrowed = [
    r for r in all_requests if r["requester_id"] == current_user_id and r["status"] == "approved"
]
part_by_id = {p["part_id"]: p for p in cached_table("parts")}

# Queries: the host sees every thread, a student only their own.
all_queries = cached_table("queries")
if is_host:
    my_queries = all_queries
else:
    my_queries = [q for q in all_queries if q["student_id"] == current_user_id]
queries_waiting = _threads_awaiting_me(my_queries)

# Meetings: upcoming ones for the list, plus any already-happened ones I
# never checked into — check-in unlocks on the meeting's own day (see
# meetings.py), so "today" counts as both upcoming and checkable.
_invited_by_meeting = meeting_invited_ids(meeting_invitee_rows())
upcoming_meetings = sorted(
    (
        m for m in cached_table("meetings")
        if m["meeting_date"] >= today_iso
        # Same visibility rule the Meetings page uses - a host-only
        # meeting shouldn't surface here either.
        and is_meeting_visible(m, _invited_by_meeting, current_user_id, is_host, is_exun)
    ),
    key=lambda m: (m["meeting_date"], m["meeting_id"]),
)
my_rsvp_ids = {
    r["meeting_id"] for r in cached_table("meeting_rsvps") if r["user_id"] == current_user_id
}
my_attended_ids = {
    a["meeting_id"] for a in cached_table("meeting_attendance") if a["user_id"] == current_user_id
}
meetings_today = [
    m for m in upcoming_meetings
    if m["meeting_date"] == today_iso and m["meeting_id"] not in my_attended_ids
]

# Competitions I've put my hand up for that haven't happened yet.
my_volunteer_rows = [v for v in cached_table("event_volunteers") if v["user_id"] == current_user_id]
upcoming_competitions = []
if my_volunteer_rows:
    event_ids = {v["event_id"] for v in my_volunteer_rows}
    my_events = [e for e in cached_table("competition_events") if e["event_id"] in event_ids]
    comp_ids = {e["competition_id"] for e in my_events}
    comp_by_id = {
        c["competition_id"]: c for c in cached_table("competitions") if c["competition_id"] in comp_ids
    }
    for v in my_volunteer_rows:
        event = next((e for e in my_events if e["event_id"] == v["event_id"]), None)
        comp = comp_by_id.get(event["competition_id"]) if event else None
        # Skip anything already run, cancelled-out, or whose competition was deleted.
        if not comp or comp.get("is_past") or comp.get("not_attending"):
            continue
        upcoming_competitions.append((comp, event, v))
    upcoming_competitions.sort(
        key=lambda row: (row[0].get("competition_date") or "9999-99-99", row[0]["competition_id"])
    )

latest_announcements = sorted(
    cached_table("announcements"), key=lambda a: a["created_at"], reverse=True
)[:3]

my_row = next((u for u in cached_table("users") if u["user_id"] == current_user_id), None)


def _n(count, word):
    # "1 day", "2 days" - instead of "day(s)" everywhere.
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _date_badge(day):
    # Orange when it's within three days, so the close ones stand out.
    days_away = (day - today).days
    colour = "orange" if 0 <= days_away <= 3 else "blue"
    return f":{colour}-badge[:material/event: {day.strftime('%d %b %Y')} · {relative_days_label(days_away)}]"


# The single nearest thing on my calendar, for the card at the top. A
# competition wins a tie with a meeting on the same day.
next_up = None
for comp, event, v in upcoming_competitions:
    if comp.get("competition_date") and comp["competition_date"] >= today_iso:
        next_up = {
            "day": date.fromisoformat(comp["competition_date"]),
            "kind": "Competition", "icon": ":material/emoji_events:",
            "title": comp["name"],
            "detail": comp.get("venue") or "Venue to be announced",
            "page": "app_pages/competitions.py",
        }
        break
if upcoming_meetings:
    m = upcoming_meetings[0]
    meeting_day = date.fromisoformat(m["meeting_date"])
    if next_up is None or meeting_day < next_up["day"]:
        next_up = {
            "day": meeting_day, "kind": "Meeting", "icon": ":material/groups:",
            "title": m["title"],
            "detail": f"Starts {m['meeting_time']}" if m.get("meeting_time") else "Time to be announced",
            "page": "app_pages/meetings.py",
        }


# --- Header + metrics strip ---------------------------------------------------

st.title(f"Welcome back, {current_user_name.split()[0]}")
st.caption(f":material/calendar_today: {today.strftime('%A, %d %B %Y')}")

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
    "My competitions", len({c["competition_id"] for c, _, _ in upcoming_competitions}), border=True,
    help="Upcoming competitions you've volunteered for or been selected for",
)

feed_col, side_col = st.columns([2, 1], gap="medium")


# --- Left: what actually needs you --------------------------------------------

with feed_col:
    if next_up:
        days_to_go = (next_up["day"] - today).days
        with st.container(border=True, key="rkcard_home_next"):
            text_col, count_col = st.columns([3, 1], vertical_alignment="center")
            with text_col:
                st.caption(f"{next_up['icon']} Next up · {next_up['kind']}")
                st.subheader(next_up["title"], anchor=False)
                st.markdown(
                    f":blue-badge[:material/event: {next_up['day'].strftime('%A, %d %B')}] "
                    f":gray-badge[{next_up['detail']}]"
                )
            with count_col:
                if days_to_go == 0:
                    st.metric("Happening", "Today")
                else:
                    st.metric("Days to go", days_to_go)
                st.page_link(next_up["page"], label="Open", icon=":material/arrow_forward:")

    st.subheader(":material/priority_high: Needs you right now", anchor=False)

    # Ordered by how time-sensitive each thing is: an overdue part first,
    # then approvals blocking someone else, then your own follow-ups.
    nothing_pending = True

    for r in sorted(my_borrowed, key=lambda r: (r.get("due_date") or "9999-99-99", r["request_id"])):
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
                st.markdown(f":red-badge[:material/warning: Overdue by {_n(-days_left, 'day')}] {label}")
            elif days_left == 0:
                st.markdown(f":orange-badge[:material/schedule: Due today] {label}")
            else:
                st.markdown(f":orange-badge[:material/schedule: Due in {_n(days_left, 'day')}] {label}")
            st.caption(f"Return it to its owner · was due {date.fromisoformat(due).strftime('%d %b %Y')}")

    if pending_for_me and not is_read_only:
        nothing_pending = False
        with st.container(border=True, key="rkcard_home_approvals"):
            st.markdown(
                f":material/inventory_2: **{_n(len(pending_for_me), 'part request')}** waiting on your approval"
            )
            for r in pending_for_me[:3]:
                part = part_by_id.get(r["part_id"], {})
                st.caption(f"{part.get('part_number', '?')} · {part.get('name', 'Unknown')}")
            st.page_link("app_pages/inventory.py", label="Review requests", icon=":material/arrow_forward:")

    if queries_waiting and not is_read_only:
        nothing_pending = False
        with st.container(border=True, key="rkcard_home_queries"):
            if is_host:
                st.markdown(f":material/quiz: **{_n(queries_waiting, 'query thread')}** waiting on your reply")
            else:
                st.markdown(f":material/quiz: **{_n(queries_waiting, 'question')}** of yours got a reply")
            st.page_link("app_pages/queries.py", label="Open Queries", icon=":material/arrow_forward:")

    if meetings_today:
        nothing_pending = False
        for m in meetings_today:
            with st.container(border=True, key=f"rkcard_home_checkin_{m['meeting_id']}"):
                st.markdown(f":material/how_to_reg: **{m['title']}** is today. Check in when you're there.")
                if m.get("meeting_time"):
                    st.caption(f"Starts {m['meeting_time']}")
                st.page_link("app_pages/meetings.py", label="Go to Meetings", icon=":material/arrow_forward:")

    if nothing_pending:
        with st.container(border=True, key="rkcard_home_allclear"):
            st.markdown(":green-badge[:material/check_circle: All clear] Nothing needs your attention right now.")

    # --- Your commitments (context, not action) ------------------------------
    # Tabs, and one card per COMPETITION rather than per event (2026-11-13):
    # someone in nine NYRC events used to get nine near-identical cards and
    # a very long scroll. The three lists share one space now, and each
    # competition lists its events as badges.

    comps_grouped = {}
    for comp, event, v in upcoming_competitions:
        comps_grouped.setdefault(comp["competition_id"], (comp, []))[1].append((event, v))

    tab_labels = []
    if comps_grouped:
        tab_labels.append(f":material/emoji_events: Competitions ({len(comps_grouped)})")
    if upcoming_meetings:
        tab_labels.append(f":material/groups: Meetings ({len(upcoming_meetings)})")
    if my_borrowed:
        tab_labels.append(f":material/inventory_2: Borrowed parts ({len(my_borrowed)})")

    if tab_labels:
        st.subheader(":material/event_upcoming: Coming up for you", anchor=False)
        tabs = iter(st.tabs(tab_labels))

    if comps_grouped:
        with next(tabs):
            for comp_id, (comp, entries) in comps_grouped.items():
                with st.container(border=True, key=f"rkcard_home_comp_{comp_id}"):
                    comp_date = comp.get("competition_date")
                    days_away = None
                    date_badge = ":gray-badge[:material/event: Date to be announced]"
                    if comp_date:
                        comp_day = date.fromisoformat(comp_date)
                        days_away = (comp_day - today).days
                        date_badge = _date_badge(comp_day)
                    st.markdown(
                        f"**{comp['name']}** {date_badge} "
                        f":gray-badge[:material/location_on: {comp.get('venue') or 'Venue to be announced'}]"
                    )
                    # Selected events first, then the ones still only volunteered for.
                    entries.sort(key=lambda e: (not e[1].get("selected"), e[0]["name"]))
                    st.markdown(" ".join(
                        f":green-badge[:material/verified: {event['name']}]" if v.get("selected")
                        else f":violet-badge[:material/front_hand: {event['name']}]"
                        for event, v in entries
                    ))
                    # The consent form opens 2 days before and members kept
                    # asking where it was - so say so right here, for the ones
                    # it applies to (selected, form open, a real student account).
                    if (
                        any(v.get("selected") for _, v in entries)
                        and days_away is not None and 0 <= days_away <= 2
                        and not (is_read_only or is_exun or is_viewer)
                    ):
                        st.page_link(
                            "app_pages/consent_form.py", label="Your consent form is open",
                            icon=":material/description:",
                        )
            st.caption(":green-badge[:material/verified: Selected] :violet-badge[:material/front_hand: Volunteered]")
            st.page_link("app_pages/competitions.py", label="All competitions", icon=":material/arrow_forward:")

    if upcoming_meetings:
        with next(tabs):
            for m in upcoming_meetings[:5]:
                with st.container(border=True, key=f"rkcard_home_meeting_{m['meeting_id']}"):
                    rsvp_badge = (
                        ":green-badge[:material/check: Going]" if m["meeting_id"] in my_rsvp_ids
                        else ":gray-badge[:material/help: No RSVP yet]"
                    )
                    time_badge = (
                        f" :gray-badge[:material/schedule: {m['meeting_time']}]" if m.get("meeting_time") else ""
                    )
                    st.markdown(
                        f"**{m['title']}** {rsvp_badge} "
                        f"{_date_badge(date.fromisoformat(m['meeting_date']))}{time_badge}"
                    )
            st.page_link("app_pages/meetings.py", label="All meetings", icon=":material/arrow_forward:")

    if my_borrowed:
        with next(tabs):
            for r in sorted(my_borrowed, key=lambda r: (r.get("due_date") or "9999-99-99", r["request_id"])):
                part = part_by_id.get(r["part_id"], {})
                due = r.get("due_date")
                due_badge = (
                    f":gray-badge[:material/schedule: Due {date.fromisoformat(due).strftime('%d %b %Y')}]"
                    if due else ":gray-badge[:material/schedule: No due date]"
                )
                with st.container(border=True, key=f"rkcard_home_borrowed_{r['request_id']}"):
                    st.markdown(f"**{part.get('name', 'Unknown')}** ({part.get('part_number', '?')}) {due_badge}")


# --- Right: announcements + navigation ----------------------------------------

with side_col:
    st.subheader(":material/campaign: Latest announcements", anchor=False)
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
        if not is_exun:
            st.page_link("app_pages/announcements.py", label="All announcements", icon=":material/arrow_forward:")

    st.subheader(":material/link: Quick links", anchor=False)
    # A wrapping row of links rather than a tall list: same pages, a third
    # of the height.
    with st.container(border=True, key="rkcard_home_links", horizontal=True):
        if not is_exun:
            st.page_link("app_pages/inventory.py", label="Inventory", icon=":material/inventory_2:", width="content")
        st.page_link("app_pages/competitions.py", label="Competitions", icon=":material/emoji_events:", width="content")
        st.page_link("app_pages/meetings.py", label="Meetings", icon=":material/groups:", width="content")
        st.page_link("app_pages/achievements.py", label="Achievements", icon=":material/military_tech:", width="content")
        if not is_read_only:
            st.page_link("app_pages/queries.py", label="Queries", icon=":material/quiz:", width="content")
        if is_host or is_exun or is_viewer:
            st.page_link("app_pages/members.py", label="Members", icon=":material/badge:", width="content")

    # Settings live in expanders (2026-11-13): they're set once and then
    # rarely touched, so they shouldn't take up the page every visit. The
    # Discord one stays open until an ID is linked.
    #
    # The linked ID is how the RoboKnights bot knows who you are: /mycomps,
    # "which comps am I in", meeting and consent-form reminder DMs, and
    # whether you count as a host. Checked before saving (discord_id_problem,
    # 2026-11-05) after one member's ID was saved on another member's account.
    if not is_read_only:
        st.subheader(":material/settings: Your settings", anchor=False)
        linked_id = (my_row or {}).get("discord_user_id") or ""
        with st.expander(
            "Discord · linked" if linked_id else "Discord · not linked yet",
            icon=":material/forum:", expanded=not linked_id,
        ):
            st.caption(
                "Link your Discord account so the RoboKnights bot knows it's you: "
                "`/mycomps`, questions like \"which comps am I in\", and reminder "
                "DMs for your meetings and competitions all use it. Only put in "
                "your own ID."
            )
            new_discord_id = st.text_input(
                "Your Discord User ID",
                value=linked_id,
                key="home_discord_id_input",
                placeholder="e.g. 123456789012345678",
                help="In Discord: Settings → Advanced → turn on Developer Mode, then "
                     "right-click your own name anywhere and choose Copy User ID.",
            )
            if st.button("Save", icon=":material/check:", key="save_discord_id_btn", type="primary"):
                problem = discord_id_problem(new_discord_id, current_user_id, cached_table("users"))
                if problem:
                    st.error(problem, icon=":material/error:")
                else:
                    with safe_write("link your Discord account"):
                        get_client().table("users").update({
                            "discord_user_id": new_discord_id.strip() or None,
                        }).eq("user_id", current_user_id).execute()
                        invalidate_cache()
                    st.toast(
                        "Discord linked!" if new_discord_id.strip() else "Discord unlinked.",
                        icon=":material/check_circle:",
                    )
                    st.rerun()

    # Opting out of the bulk competition emails (2026-11-10, host's
    # request). Only the two emails that go to everyone in a grade range
    # or the whole club stop: "new event you're eligible for" and
    # competition date changes (still sent if you're in that competition).
    # Anything about YOU - selected, team changes, your parts, your
    # queries - plus announcements and meetings always come through.
    if not is_read_only and my_row is not None:
        with st.expander("Emails", icon=":material/mail:"):
            if "unsubscribed_nonessential" not in my_row:
                st.caption("Email preferences aren't switched on yet - a host needs to run a database update.")
            else:
                currently_off = bool(my_row.get("unsubscribed_nonessential"))
                off = st.toggle(
                    "Unsubscribe from non-essential emails",
                    value=currently_off,
                    key="home_unsub_nonessential",
                )
                st.caption(
                    "Stops the general competition emails (new events you could join, date changes "
                    "for competitions you're not in). You'll still get emails about you - being "
                    "selected, team changes, your parts and queries - plus announcements and meetings."
                )
                if off != currently_off:
                    with safe_write("change your email preferences"):
                        get_client().table("users").update({
                            "unsubscribed_nonessential": off,
                        }).eq("user_id", current_user_id).execute()
                        invalidate_cache()
                    st.toast(
                        "Unsubscribed from non-essential emails." if off
                        else "You'll get every email again.",
                        icon=":material/check_circle:",
                    )
                    st.rerun()

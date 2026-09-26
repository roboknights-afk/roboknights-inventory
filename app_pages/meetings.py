# Meeting scheduler: host schedules a meeting (title, agenda, date/time,
# and an external join link — Google Meet, Jitsi, whatever the club
# actually uses, since building real video calling from scratch isn't
# realistic for a Streamlit app). Everyone can RSVP, and once the meeting
# has happened, self-check-in for attendance, same as the "Request this"
# pattern elsewhere — host can review and correct the final attendance
# list afterward the same way volunteers get finalized on Competitions.
# Host can also edit or delete an existing meeting (RSVPs/attendance
# cascade-delete with it, same as deleting a part removes its request
# history).

from datetime import date, time

import streamlit as st

from shared import (
    EXUN_EMAILS, MEETING_EXCLUDED_STAFF_EMAILS, cached_table, get_client, google_calendar_link,
    invalidate_cache, is_meeting_visible, meeting_email_body, meeting_invited_ids,
    meeting_invitee_rows, notify_meeting_discord, plain_text_from_rich_html, render_rich_html_editor,
    safe_write, sanitize_rich_html, send_email, today_ist, wrap_rich_html_for_storage,
)

client = get_client()
is_host = st.session_state.is_host
is_exun = st.session_state.is_exun
is_read_only = st.session_state.is_read_only
current_user_id = st.session_state.current_user_id
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id


def _notify_meeting(meeting, invitee_ids, intro, subject):
    # Who gets told: exactly the named people if it's a private meeting,
    # otherwise the whole club. Exun and the two staff/host accounts
    # (Hema Jain - HOD, Computer Science; Ajith Kumar - Robotics
    # In-Charge, though he currently has no users row to even be in
    # user_email_by_id) are excluded from a general meeting's mailing
    # UNLESS this specific meeting opted them in (meetings.include_exun_staff,
    # 2026-09-23) - they can view meetings in the app but aren't part of
    # the club's routine mailing by default. Read straight off the
    # meeting row rather than taking this as a separate argument, so a
    # caller can never pass a value that disagrees with what was actually
    # saved. A NAMED invitee list always means exactly those people
    # regardless of this flag - opting someone in by name is a stronger
    # signal than the blanket toggle.
    include_exun_staff = bool(meeting.get("include_exun_staff"))
    if invitee_ids:
        emails = [user_email_by_id.get(uid) for uid in invitee_ids]
    else:
        emails = list(user_email_by_id.values())
        if not include_exun_staff:
            emails = [e for e in emails if e not in MEETING_EXCLUDED_STAFF_EMAILS]
    if not include_exun_staff:
        emails = [e for e in emails if e and e not in EXUN_EMAILS]
    else:
        emails = [e for e in emails if e]

    link = google_calendar_link(
        title=meeting["title"],
        meeting_date=date.fromisoformat(meeting["meeting_date"]),
        meeting_time=(
            time.fromisoformat(meeting["meeting_time"]) if meeting.get("meeting_time") else None
        ),
        details=plain_text_from_rich_html(meeting.get("agenda") or ""),
        location=meeting.get("join_link") or "",
    )
    body = meeting_email_body(meeting, link, intro)
    for email in emails:
        send_email(email, subject, body)
    return len(emails)

st.title(":material/groups: Meetings")

if "meeting_message" not in st.session_state:
    st.session_state.meeting_message = None
if "editing_meeting_id" not in st.session_state:
    st.session_state.editing_meeting_id = None
# Held open by a flag rather than being called straight from its button: a
# dialog only stays on screen while something re-calls its function each run,
# and the st.rerun() below is a full-app rerun. Clearing the flag closes it.
if "show_schedule_meeting" not in st.session_state:
    st.session_state.show_schedule_meeting = False


def _close_schedule_meeting():
    st.session_state.show_schedule_meeting = False


def _open_schedule_meeting():
    st.session_state.show_schedule_meeting = True


@st.dialog("Schedule a meeting", on_dismiss=_close_schedule_meeting)
def render_schedule_meeting():
    # At the top, on purpose - this is a decision about who a general
    # meeting reaches at all, not a detail to bury next to the invitee
    # picker below. Off by default: Exun and these two staff accounts
    # aren't part of the club's routine meetings unless a host says so
    # for this specific one.
    include_exun_staff = st.toggle(
        "Also include Exun & specific staff (Ajith Kumar, Hema Jain)",
        key="new_meeting_include_exun_staff",
        help="Off by default — a routine club meeting doesn't reach Exun or "
             "these two staff accounts unless you turn this on for this "
             "specific meeting.",
    )
    title = st.text_input("Title", key="new_meeting_title")
    st.caption("Agenda")
    render_rich_html_editor("new_meeting_agenda_rich", height=120, placeholder="Agenda...")
    mcol1, mcol2 = st.columns(2)
    meeting_date = mcol1.date_input("Date", key="new_meeting_date", value=None)
    meeting_time = mcol2.time_input("Time", key="new_meeting_time", value=None)
    join_link = st.text_input(
        "Join link (Google Meet, Jitsi, etc.)", key="new_meeting_link", placeholder="https://..."
    )
    # Both optional — plenty of meetings don't need a separate ID/password
    # (e.g. a plain Jitsi link), so nothing here is required to schedule.
    idcol, pwcol = st.columns(2)
    meeting_id_code = idcol.text_input("Meeting ID (optional)", key="new_meeting_id_code")
    meeting_password = pwcol.text_input("Meeting password (optional)", key="new_meeting_password")
    # Empty = the whole club, which is both the common case and what every
    # meeting scheduled before this feature existed already is. Naming
    # anyone here is what makes it private.
    invitees = st.multiselect(
        "Only for (leave empty for everyone)",
        options=list(user_name_by_id.keys()),
        format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
        key="new_meeting_invitees",
        help="Pick people to make this a private meeting — only they (and hosts) "
             "will see it anywhere in the app. Leave empty and the whole club sees it.",
    )
    if st.button("Schedule", icon=":material/check:", type="primary", key="confirm_schedule_meeting"):
        # Validation errors show here inside the dialog — stashing them for the
        # page behind would mean closing the form and losing what was typed.
        if not title.strip():
            st.error("Title is required.")
        elif not meeting_date:
            st.error("Date is required.")
        else:
            link = join_link.strip()
            if link and not link.startswith(("http://", "https://")):
                link = "https://" + link
            with safe_write("schedule this meeting"):
                created = client.table("meetings").insert({
                    "title": title.strip(),
                    "agenda": wrap_rich_html_for_storage("new_meeting_agenda_rich"),
                    "meeting_date": meeting_date.isoformat(),
                    "meeting_time": meeting_time.isoformat() if meeting_time else None,
                    "join_link": link or None,
                    "meeting_id_code": meeting_id_code.strip() or None,
                    "meeting_password": meeting_password.strip() or None,
                    "include_exun_staff": include_exun_staff,
                }).execute()
                if invitees:
                    new_id = created.data[0]["meeting_id"]
                    # upsert + ignore_duplicates, not insert - a double
                    # click on Schedule (this dialog has no debounce) can
                    # fire this twice before the first request's row
                    # exists to be deduped against, which hit the
                    # (meeting_id, user_id) unique constraint and crashed
                    # the whole save on a genuinely brand-new meeting.
                    client.table("meeting_invitees").upsert(
                        [{"meeting_id": new_id, "user_id": uid} for uid in invitees],
                        on_conflict="meeting_id,user_id", ignore_duplicates=True,
                    ).execute()
                invalidate_cache()
            # Meetings never emailed anyone before this - every other part
            # of the app (announcements, competitions, queries) notified,
            # so a scheduled meeting silently sitting on a page nobody had
            # a reason to open was the odd one out.
            sent_to = _notify_meeting(
                created.data[0], invitees,
                f"A meeting has been scheduled{' for you' if invitees else ''}.",
                f"Meeting: {title.strip()}",
            )
            # Discord: the whole club gets pinged in #announcements, or
            # (for a named-invitee meeting) each invited person with a
            # linked Discord account gets a DM instead — never both, and
            # never the join link either way. Best-effort inside
            # notify_meeting_discord itself, so a Discord hiccup can't
            # turn a successful "Schedule" into an error.
            notify_meeting_discord(created.data[0], invitees, "new")
            st.session_state.meeting_message = (
                "success", f"Scheduled {title.strip()} — emailed {sent_to} member(s)."
            )
            # Clear the form so reopening the dialog starts blank.
            for k in (
                "new_meeting_title", "new_meeting_agenda_rich", "new_meeting_agenda_rich_size",
                "new_meeting_link", "new_meeting_id_code", "new_meeting_password",
                "new_meeting_invitees",
            ):
                st.session_state.pop(k, None)
            _close_schedule_meeting()
            st.rerun()


if is_host:
    st.button(
        "Schedule a meeting", icon=":material/add_box:", type="primary",
        key="open_schedule_meeting", on_click=_open_schedule_meeting,
    )
    if st.session_state.show_schedule_meeting:
        render_schedule_meeting()

# Success pops as a toast; errors stay inline so they can't be missed.
# "celebrate" is the same as "success" plus balloons - reserved for a
# member's own delighted moment (RSVPing yes), not routine host edits.
if st.session_state.meeting_message:
    kind, text = st.session_state.meeting_message
    if kind in ("success", "celebrate"):
        st.toast(text, icon=":material/check_circle:")
        if kind == "celebrate":
            st.balloons()
    else:
        st.error(text)
    st.session_state.meeting_message = None

# meeting_id as a tiebreak alongside meeting_date, so two meetings on the
# same day sort deterministically rather than by undefined storage order.
all_invitees = meeting_invitee_rows()
invited_by_meeting = meeting_invited_ids(all_invitees)
# Filtered before anything else touches it - the counts, the tabs, and
# every card all work off this, so a private meeting can't leak through
# whichever one got overlooked.
meetings = sorted(
    (
        m for m in cached_table("meetings")
        if is_meeting_visible(m, invited_by_meeting, current_user_id, is_host, is_exun)
    ),
    key=lambda m: (m["meeting_date"], m["meeting_id"]),
)
all_rsvps = cached_table("meeting_rsvps")
all_attendance = cached_table("meeting_attendance")
# IST "today", not the UTC server's — otherwise check-in for a meeting
# happening today wouldn't unlock until 5:30 AM IST, and the upcoming/past
# split would lag the same way.
today_iso = today_ist().isoformat()

# --- At-a-glance numbers -----------------------------------------------------
upcoming_meetings = [m for m in meetings if m["meeting_date"] >= today_iso]
past_meetings = [m for m in meetings if m["meeting_date"] < today_iso]
my_rsvp_count = sum(1 for r in all_rsvps if r["user_id"] == current_user_id)
my_attended_count = sum(1 for a in all_attendance if a["user_id"] == current_user_id)

m1, m2, m3 = st.columns(3)
m1.metric("Upcoming", len(upcoming_meetings), border=True)
m2.metric("I'm going to", my_rsvp_count, border=True, help="Meetings you've RSVP'd yes to")
m3.metric("Attended", my_attended_count, border=True, help="Meetings you were marked present at")


def render_meeting_card(m):
    rsvps = [r for r in all_rsvps if r["meeting_id"] == m["meeting_id"]]
    rsvp_names = [user_name_by_id.get(r["user_id"], "Unknown") for r in rsvps]
    already_rsvpd = any(r["user_id"] == current_user_id for r in rsvps)

    attendance = [a for a in all_attendance if a["meeting_id"] == m["meeting_id"]]
    attended_ids = {a["user_id"] for a in attendance}
    already_checked_in = current_user_id in attended_ids

    is_past_or_today = m["meeting_date"] <= today_iso
    editing_this = is_host and st.session_state.editing_meeting_id == m["meeting_id"]

    with st.container(border=True, key=f"rkcard_meeting_{m['meeting_id']}"):
        if editing_this:
            # --- Host: edit this meeting ------------------------------
            # Same toggle as the Schedule dialog, top of the form for the
            # same reason - see the comment there.
            edit_include_exun_staff = st.toggle(
                "Also include Exun & specific staff (Ajith Kumar, Hema Jain)",
                value=bool(m.get("include_exun_staff")),
                key=f"edit_include_exun_staff_{m['meeting_id']}",
            )
            edit_title = st.text_input("Title", value=m["title"], key=f"edit_title_{m['meeting_id']}")
            st.caption("Agenda")
            render_rich_html_editor(
                f"edit_agenda_rich_{m['meeting_id']}", height=120,
                initial_html=m.get("agenda"),
            )
            ecol1, ecol2 = st.columns(2)
            edit_date = ecol1.date_input(
                "Date", value=date.fromisoformat(m["meeting_date"]), key=f"edit_date_{m['meeting_id']}"
            )
            edit_time = ecol2.time_input(
                "Time",
                value=time.fromisoformat(m["meeting_time"]) if m.get("meeting_time") else None,
                key=f"edit_time_{m['meeting_id']}",
            )
            edit_link = st.text_input(
                "Join link", value=m.get("join_link") or "", key=f"edit_link_{m['meeting_id']}"
            )
            eidcol, epwcol = st.columns(2)
            edit_id_code = eidcol.text_input(
                "Meeting ID (optional)", value=m.get("meeting_id_code") or "",
                key=f"edit_id_code_{m['meeting_id']}",
            )
            edit_password = epwcol.text_input(
                "Meeting password (optional)", value=m.get("meeting_password") or "",
                key=f"edit_password_{m['meeting_id']}",
            )
            edit_invitees = st.multiselect(
                "Only for (leave empty for everyone)",
                options=list(user_name_by_id.keys()),
                default=sorted(invited_by_meeting.get(m["meeting_id"], set())),
                format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                key=f"edit_invitees_{m['meeting_id']}",
                help="Clearing this makes the meeting visible to the whole club again.",
            )
            save_col, cancel_col = st.columns(2)
            if save_col.button(
                "Save changes", key=f"save_meeting_{m['meeting_id']}", icon=":material/check:", type="primary"
            ):
                edit_agenda_html = wrap_rich_html_for_storage(f"edit_agenda_rich_{m['meeting_id']}")
                if not edit_title.strip():
                    st.session_state.meeting_message = ("error", "Title is required.")
                else:
                    link = edit_link.strip()
                    if link and not link.startswith(("http://", "https://")):
                        link = "https://" + link
                    # Computed before the write so it can also decide
                    # whether to reset the 24h/1h reminder flags below —
                    # a rescheduled meeting whose reminders already fired
                    # for the OLD time needs a clean slate for the new one,
                    # same idea as the "moved" re-email a few lines down.
                    old_time = (m.get("meeting_time") or "")[:5]
                    new_time = edit_time.strftime("%H:%M") if edit_time else ""
                    moved = (
                        m["meeting_date"] != edit_date.isoformat() or old_time != new_time
                    )
                    with safe_write(f"update {edit_title.strip()}"):
                        client.table("meetings").update({
                            "title": edit_title.strip(),
                            "agenda": edit_agenda_html,
                            "meeting_date": edit_date.isoformat(),
                            "meeting_time": edit_time.isoformat() if edit_time else None,
                            "join_link": link or None,
                            "meeting_id_code": edit_id_code.strip() or None,
                            "meeting_password": edit_password.strip() or None,
                            "include_exun_staff": edit_include_exun_staff,
                            **(
                                {"reminder_24h_sent": False, "reminder_1h_sent": False}
                                if moved else {}
                            ),
                        }).eq("meeting_id", m["meeting_id"]).execute()
                        # Only the difference is written, so re-saving a
                        # meeting without touching this list doesn't churn
                        # rows (and an unchanged list costs no writes).
                        # invited_by_meeting comes from cached_table(),
                        # up to 8s stale - a double click on Save changes
                        # within that window (or two hosts editing the
                        # same meeting close together) can compute the
                        # same "add this uid" diff twice, and the second
                        # insert then hit the (meeting_id, user_id)
                        # unique constraint before its own row existed to
                        # be diffed against. upsert + ignore_duplicates
                        # makes a repeat add a harmless no-op instead of
                        # crashing the whole save.
                        was_invited = invited_by_meeting.get(m["meeting_id"], set())
                        now_invited = set(edit_invitees)
                        for uid in now_invited - was_invited:
                            client.table("meeting_invitees").upsert({
                                "meeting_id": m["meeting_id"], "user_id": uid,
                            }, on_conflict="meeting_id,user_id", ignore_duplicates=True).execute()
                        for uid in was_invited - now_invited:
                            client.table("meeting_invitees").delete().eq(
                                "meeting_id", m["meeting_id"]
                            ).eq("user_id", uid).execute()
                        invalidate_cache()
                    # Only a moved meeting emails again. Fixing a typo in
                    # the agenda shouldn't put a message in 53 inboxes,
                    # but a changed date or time is exactly the thing
                    # people need to be told about - same rule the
                    # Competitions page already uses for date changes.
                    # (moved was computed above, before the write.)
                    msg = f"Updated {edit_title.strip()}."
                    if moved:
                        updated = dict(m)
                        updated.update({
                            "title": edit_title.strip(),
                            "agenda": edit_agenda_html,
                            "meeting_date": edit_date.isoformat(),
                            "meeting_time": edit_time.isoformat() if edit_time else None,
                            "join_link": link or None,
                            "meeting_id_code": edit_id_code.strip() or None,
                            "meeting_password": edit_password.strip() or None,
                            "include_exun_staff": edit_include_exun_staff,
                        })
                        sent_to = _notify_meeting(
                            updated, list(now_invited),
                            "A meeting you're part of has been moved. The new details:",
                            f"Meeting moved: {edit_title.strip()}",
                        )
                        msg += f" Emailed {sent_to} member(s) about the new time."
                    st.session_state.meeting_message = ("success", msg)
                    st.session_state.editing_meeting_id = None
                st.rerun()
            if cancel_col.button("Cancel", key=f"cancel_meeting_{m['meeting_id']}", icon=":material/close:"):
                st.session_state.editing_meeting_id = None
                st.rerun()

        else:
            title_col, badge_col, edit_col, delete_col = st.columns(
                [3, 1, 1, 1], vertical_alignment="center"
            )
            title_col.markdown(f"### {m['title']}")
            # Where you stand on this one, without reading the names list —
            # plus a loud "Today" flag so the one meeting that matters right
            # now stands out from the rest of the list.
            if m["meeting_date"] == today_iso:
                badge_col.badge("Today", color="primary", icon=":material/today:")
            if already_checked_in:
                badge_col.badge("Attended", color="green", icon=":material/how_to_reg:")
            elif already_rsvpd:
                badge_col.badge("Going", color="green", icon=":material/check:")
            else:
                badge_col.badge("Not RSVP'd", color="blue", icon=":material/help:")
            if is_host:
                if edit_col.button("Edit", key=f"edit_btn_meeting_{m['meeting_id']}", icon=":material/edit:"):
                    st.session_state.editing_meeting_id = m["meeting_id"]
                    st.rerun()
                if delete_col.button(
                    "Delete", key=f"delete_meeting_{m['meeting_id']}", icon=":material/delete:"
                ):
                    with safe_write(f"delete {m['title']}"):
                        client.table("meetings").delete().eq("meeting_id", m["meeting_id"]).execute()
                        invalidate_cache()
                    st.session_state.meeting_message = ("success", f"Deleted {m['title']}.")
                    st.rerun()

            info_bits = [
                f":material/event: {date.fromisoformat(m['meeting_date']).strftime('%d %b %Y')}"
            ]
            if m.get("meeting_time"):
                # Comes back from Postgres as "HH:MM:SS".
                info_bits.append(f":material/schedule: {m['meeting_time'][:5]}")
            st.caption("  •  ".join(info_bits))

            # Shown to everyone who can see the meeting, not just the host:
            # if you're in a small invited group it matters that you know
            # the rest of the club isn't.
            this_meeting_invitees = invited_by_meeting.get(m["meeting_id"])
            if this_meeting_invitees:
                invited_names = ", ".join(
                    sorted(user_name_by_id.get(uid, "Unknown") for uid in this_meeting_invitees)
                )
                st.caption(f":material/lock: Private — only for {invited_names}")

            if m.get("agenda"):
                st.markdown(sanitize_rich_html(m["agenda"]), unsafe_allow_html=True)

            if m.get("join_link"):
                # A real button, not a bare text link — this is the single
                # most important action on a meeting card.
                st.link_button(
                    "Join meeting", m["join_link"],
                    icon=":material/videocam:", type="primary",
                )

            if m.get("meeting_id_code") or m.get("meeting_password"):
                detail_bits = []
                if m.get("meeting_id_code"):
                    detail_bits.append(f"Meeting ID: {m['meeting_id_code']}")
                if m.get("meeting_password"):
                    detail_bits.append(f"Password: {m['meeting_password']}")
                st.caption(" · ".join(detail_bits))

            # Loud prompt on the day itself, since that's the only window where
            # checking in is the thing you're meant to do.
            if m["meeting_date"] == today_iso and not already_checked_in:
                st.info(":material/how_to_reg: **This meeting is today** — check in below once you're there.")

            if rsvp_names:
                st.caption(f":material/group: Going ({len(rsvp_names)}): " + ", ".join(rsvp_names))
            else:
                st.caption(":material/group: No one has RSVP'd yet")

            # Exun can see everything above (details, join link, who's
            # going) but never RSVPs or checks in themselves — view only.
            if not is_read_only:
                rcol1, rcol2 = st.columns(2)
                if already_rsvpd:
                    if rcol1.button(
                        "Can't make it", key=f"withdraw_rsvp_{m['meeting_id']}", icon=":material/close:"
                    ):
                        with safe_write("withdraw your RSVP"):
                            client.table("meeting_rsvps").delete().eq(
                                "meeting_id", m["meeting_id"]
                            ).eq("user_id", current_user_id).execute()
                            invalidate_cache()
                        st.session_state.meeting_message = ("success", "RSVP withdrawn.")
                        st.rerun()
                else:
                    if rcol1.button(
                        "I'm going", key=f"rsvp_{m['meeting_id']}", icon=":material/event_available:",
                        type="primary",
                    ):
                        with safe_write("RSVP to this meeting"):
                            client.table("meeting_rsvps").insert({
                                "meeting_id": m["meeting_id"], "user_id": current_user_id,
                            }).execute()
                            invalidate_cache()
                        st.session_state.meeting_message = ("celebrate", "RSVP'd! See you there.")
                        st.rerun()

                # Self check-in only opens up once the meeting's actually
                # happening or has passed — no point checking in for the future.
                if is_past_or_today:
                    if already_checked_in:
                        rcol2.caption(":material/check_circle: You checked in")
                    else:
                        if rcol2.button(
                            "I attended", key=f"checkin_{m['meeting_id']}", icon=":material/how_to_reg:"
                        ):
                            with safe_write("check yourself in"):
                                client.table("meeting_attendance").insert({
                                    "meeting_id": m["meeting_id"], "user_id": current_user_id,
                                }).execute()
                                invalidate_cache()
                            st.session_state.meeting_message = ("success", "Checked in!")
                            st.rerun()

            # Host: review and correct the final attendance list — anyone
            # can be added or removed here, not just people who checked
            # themselves in, same "self-report then host override"
            # pattern as the Members directory edit.
            if is_host and is_past_or_today:
                # For a private meeting the picker is scoped to the people
                # actually invited (plus anyone already marked present, so
                # a mistaken entry can still be removed rather than being
                # stuck in the list with no way to deselect it).
                if this_meeting_invitees:
                    all_member_ids = sorted(set(this_meeting_invitees) | attended_ids)
                else:
                    all_member_ids = list(user_name_by_id.keys())
                final_attendees = st.multiselect(
                    "Attendance (host can correct)",
                    options=all_member_ids,
                    default=[uid for uid in all_member_ids if uid in attended_ids],
                    format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                    key=f"attendance_{m['meeting_id']}",
                )
                if st.button(
                    "Save attendance", key=f"save_attendance_{m['meeting_id']}", icon=":material/check:"
                ):
                    newly_added = set(final_attendees) - attended_ids
                    newly_removed = attended_ids - set(final_attendees)
                    with safe_write("save the attendance list"):
                        for uid in newly_added:
                            client.table("meeting_attendance").insert({
                                "meeting_id": m["meeting_id"], "user_id": uid,
                            }).execute()
                        for uid in newly_removed:
                            client.table("meeting_attendance").delete().eq(
                                "meeting_id", m["meeting_id"]
                            ).eq("user_id", uid).execute()
                        if newly_added or newly_removed:
                            invalidate_cache()
                    st.session_state.meeting_message = ("success", "Attendance saved.")
                    st.rerun()


# --- Layout ------------------------------------------------------------------

if not meetings:
    st.caption("No meetings scheduled yet.")
else:
    tab_upcoming, tab_past = st.tabs(
        [f"Upcoming ({len(upcoming_meetings)})", f"Past ({len(past_meetings)})"]
    )
    with tab_upcoming:
        if not upcoming_meetings:
            st.caption("Nothing scheduled right now.")
        for m in upcoming_meetings:
            render_meeting_card(m)
    with tab_past:
        if not past_meetings:
            st.caption("No meetings have happened yet.")
        for m in reversed(past_meetings):  # most recent first
            render_meeting_card(m)

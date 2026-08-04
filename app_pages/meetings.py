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

from shared import cached_table, get_client, invalidate_cache

client = get_client()
is_host = st.session_state.is_host
is_exun = st.session_state.is_exun
current_user_id = st.session_state.current_user_id
user_name_by_id = st.session_state.user_name_by_id

st.title("Meetings")

if "meeting_message" not in st.session_state:
    st.session_state.meeting_message = None
if "editing_meeting_id" not in st.session_state:
    st.session_state.editing_meeting_id = None

if is_host:
    with st.expander(":material/add_box: Schedule a meeting"):
        title = st.text_input("Title", key="new_meeting_title")
        agenda = st.text_area("Agenda", key="new_meeting_agenda")
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
        if st.button("Schedule", icon=":material/check:", type="primary"):
            if not title.strip():
                st.session_state.meeting_message = ("error", "Title is required.")
            elif not meeting_date:
                st.session_state.meeting_message = ("error", "Date is required.")
            else:
                link = join_link.strip()
                if link and not link.startswith(("http://", "https://")):
                    link = "https://" + link
                client.table("meetings").insert({
                    "title": title.strip(),
                    "agenda": agenda.strip(),
                    "meeting_date": meeting_date.isoformat(),
                    "meeting_time": meeting_time.isoformat() if meeting_time else None,
                    "join_link": link or None,
                    "meeting_id_code": meeting_id_code.strip() or None,
                    "meeting_password": meeting_password.strip() or None,
                }).execute()
                invalidate_cache()
                st.session_state.meeting_message = ("success", f"Scheduled {title.strip()}.")
                del st.session_state["new_meeting_title"]
                del st.session_state["new_meeting_agenda"]
                del st.session_state["new_meeting_link"]
                del st.session_state["new_meeting_id_code"]
                del st.session_state["new_meeting_password"]
                st.rerun()

# Success pops as a toast; errors stay inline so they can't be missed.
if st.session_state.meeting_message:
    kind, text = st.session_state.meeting_message
    if kind == "success":
        st.toast(text, icon=":material/check_circle:")
    else:
        st.error(text)
    st.session_state.meeting_message = None

# meeting_id as a tiebreak alongside meeting_date, so two meetings on the
# same day sort deterministically rather than by undefined storage order.
meetings = sorted(cached_table("meetings"), key=lambda m: (m["meeting_date"], m["meeting_id"]))
all_rsvps = cached_table("meeting_rsvps")
all_attendance = cached_table("meeting_attendance")
today_iso = date.today().isoformat()

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
            edit_title = st.text_input("Title", value=m["title"], key=f"edit_title_{m['meeting_id']}")
            edit_agenda = st.text_area(
                "Agenda", value=m.get("agenda") or "", key=f"edit_agenda_{m['meeting_id']}"
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
            save_col, cancel_col = st.columns(2)
            if save_col.button(
                "Save changes", key=f"save_meeting_{m['meeting_id']}", icon=":material/check:", type="primary"
            ):
                if not edit_title.strip():
                    st.session_state.meeting_message = ("error", "Title is required.")
                else:
                    link = edit_link.strip()
                    if link and not link.startswith(("http://", "https://")):
                        link = "https://" + link
                    client.table("meetings").update({
                        "title": edit_title.strip(),
                        "agenda": edit_agenda.strip(),
                        "meeting_date": edit_date.isoformat(),
                        "meeting_time": edit_time.isoformat() if edit_time else None,
                        "join_link": link or None,
                        "meeting_id_code": edit_id_code.strip() or None,
                        "meeting_password": edit_password.strip() or None,
                    }).eq("meeting_id", m["meeting_id"]).execute()
                    invalidate_cache()
                    st.session_state.meeting_message = ("success", f"Updated {edit_title.strip()}.")
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
            # Where you stand on this one, without reading the names list.
            if already_checked_in:
                badge_col.badge("Attended", color="green", icon=":material/how_to_reg:")
            elif already_rsvpd:
                badge_col.badge("Going", color="green", icon=":material/check:")
            else:
                badge_col.badge("Not RSVP'd", color="grey", icon=":material/help:")
            if is_host:
                if edit_col.button("Edit", key=f"edit_btn_meeting_{m['meeting_id']}", icon=":material/edit:"):
                    st.session_state.editing_meeting_id = m["meeting_id"]
                    st.rerun()
                if delete_col.button(
                    "Delete", key=f"delete_meeting_{m['meeting_id']}", icon=":material/delete:"
                ):
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

            if m.get("agenda"):
                st.write(m["agenda"])

            if m.get("join_link"):
                st.markdown(f"[Join meeting]({m['join_link']})")

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
            if not is_exun:
                rcol1, rcol2 = st.columns(2)
                if already_rsvpd:
                    if rcol1.button(
                        "Can't make it", key=f"withdraw_rsvp_{m['meeting_id']}", icon=":material/close:"
                    ):
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
                        client.table("meeting_rsvps").insert({
                            "meeting_id": m["meeting_id"], "user_id": current_user_id,
                        }).execute()
                        invalidate_cache()
                        st.session_state.meeting_message = ("success", "RSVP'd!")
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

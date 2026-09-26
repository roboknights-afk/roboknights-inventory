# Its own page (not a section on Competitions) so it reads like Inventory
# and Competitions do — a distinct place, not buried inside another screen.
# Sending is host-only; every logged-in member can view what's been sent.

from datetime import datetime, timedelta, timezone

import streamlit as st

from shared import (
    EXUN_EMAILS, cached_table, format_ist, format_relative, get_client, invalidate_cache,
    plain_text_from_rich_html, render_rich_html_editor, safe_write, sanitize_rich_html, send_email,
    wrap_rich_html_for_storage,
)

client = get_client()
is_host = st.session_state.is_host
user_email_by_id = st.session_state.user_email_by_id

st.title(":material/campaign: Announcements")

if "announcement_message" not in st.session_state:
    st.session_state.announcement_message = None
# See the Inventory/Competitions pages for why this is a flag rather than a
# straight call from the button: a dialog only stays up while something
# re-calls its function, and the st.rerun() below is a full-app rerun.
if "show_send_announcement" not in st.session_state:
    st.session_state.show_send_announcement = False


def _close_send_announcement():
    st.session_state.show_send_announcement = False


def _open_send_announcement():
    st.session_state.show_send_announcement = True


@st.dialog("Send an announcement", on_dismiss=_close_send_announcement)
def render_send_announcement():
    st.caption("Goes to every member's inbox as well as this page.")
    announcement_subject = st.text_input("Subject", key="announcement_subject")
    render_rich_html_editor("announcement_body_rich", placeholder="Write your announcement...")
    if st.button("Send to everyone", key="send_announcement", icon=":material/send:", type="primary"):
        body_html = wrap_rich_html_for_storage("announcement_body_rich")
        if not announcement_subject.strip() or not body_html:
            # Inline, so the dialog doesn't close and discard the draft.
            st.error("Subject and message are both required.")
        else:
            with safe_write("send this announcement"):
                client.table("announcements").insert({
                    "subject": announcement_subject.strip(),
                    "body": body_html,
                }).execute()
                invalidate_cache()

                # Exun deliberately excluded: announcements are an
                # internal RoboKnights channel they don't have access to
                # in the app, so they shouldn't get the emails either.
                all_emails = [
                    email for email in user_email_by_id.values()
                    if email and email not in EXUN_EMAILS
                ]
                # Plain-text email — the rich formatting only means
                # something rendered on this page itself.
                plain_body = plain_text_from_rich_html(body_html)
                for email in all_emails:
                    send_email(email, announcement_subject.strip(), plain_body)
                st.session_state.announcement_message = ("success", f"Sent to {len(all_emails)} member(s).")
                # Clear the form for next time — deleting the keys before
                # the widgets are recreated on rerun resets them to blank.
                st.session_state.pop("announcement_subject", None)
                st.session_state.pop("announcement_body_rich", None)
                st.session_state.pop("announcement_body_rich_size", None)
                _close_send_announcement()
                st.rerun()


if is_host:
    st.button(
        "Send an announcement", icon=":material/campaign:", type="primary",
        key="open_send_announcement", on_click=_open_send_announcement,
    )
    if st.session_state.show_send_announcement:
        render_send_announcement()

# Success pops as a toast; errors stay inline so they can't be missed.
if st.session_state.announcement_message:
    kind, text = st.session_state.announcement_message
    if kind == "success":
        st.toast(text, icon=":material/campaign:")
    else:
        st.error(text)
    st.session_state.announcement_message = None

announcements = sorted(
    cached_table("announcements"), key=lambda a: a["created_at"], reverse=True
)[:20]
if not announcements:
    st.caption("No announcements yet.")
else:
    st.subheader(":material/feed: Recent announcements")
    now_utc = datetime.now(timezone.utc)
    for a in announcements:
        # key= gives the card a stable "st-key-rkcard_..." CSS class, which
        # the hover animation in app.py targets.
        posted = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        is_new = (now_utc - posted) < timedelta(hours=48)

        with st.container(border=True, key=f"rkcard_ann_{a['announcement_id']}"):
            head_col, del_col = st.columns([5, 1], vertical_alignment="center")
            head_col.markdown(f"### {a['subject']}")

            if is_host:
                if del_col.button(
                    "Delete", key=f"delete_announcement_{a['announcement_id']}", icon=":material/delete:"
                ):
                    with safe_write("delete this announcement"):
                        client.table("announcements").delete().eq("announcement_id", a["announcement_id"]).execute()
                        invalidate_cache()
                        st.session_state.announcement_message = ("success", f"Deleted \"{a['subject']}\".")
                        st.rerun()

            # Announcements can only be sent by a host, and the table doesn't
            # record which one, so the tag says the role rather than inventing
            # a name for it. Anything under 48 hours old also gets a "New"
            # flag so a fresh notice stands out from the archive below it.
            badge_col, date_col = st.columns([1, 4], vertical_alignment="center")
            with badge_col:
                st.badge("Host", color="primary", icon=":material/shield_person:")
                if is_new:
                    st.badge("New", color="green", icon=":material/fiber_new:")
            date_col.caption(
                f":material/schedule: {format_relative(a['created_at'])}",
                help=format_ist(a["created_at"]),
            )

            st.markdown(sanitize_rich_html(a["body"]), unsafe_allow_html=True)

# Its own page (not a section on Competitions) so it reads like Inventory
# and Competitions do — a distinct place, not buried inside another screen.
# Sending is host-only; every logged-in member can view what's been sent.

import streamlit as st

from shared import cached_table, format_ist, get_client, invalidate_cache, send_email

client = get_client()
is_host = st.session_state.is_host
user_email_by_id = st.session_state.user_email_by_id

st.title("Announcements")

if "announcement_message" not in st.session_state:
    st.session_state.announcement_message = None

if is_host:
    with st.expander(":material/campaign: Send an announcement"):
        announcement_subject = st.text_input("Subject", key="announcement_subject")
        announcement_body = st.text_area("Message", key="announcement_body")
        if st.button("Send to everyone", key="send_announcement", icon=":material/send:", type="primary"):
            if not announcement_subject.strip() or not announcement_body.strip():
                st.session_state.announcement_message = ("error", "Subject and message are both required.")
            else:
                client.table("announcements").insert({
                    "subject": announcement_subject.strip(),
                    "body": announcement_body.strip(),
                }).execute()
                invalidate_cache()

                all_emails = [email for email in user_email_by_id.values() if email]
                for email in all_emails:
                    send_email(email, announcement_subject.strip(), announcement_body.strip())
                st.session_state.announcement_message = ("success", f"Sent to {len(all_emails)} member(s).")
                # Clear the form for next time — deleting the keys before
                # the widgets are recreated on rerun resets them to blank.
                del st.session_state["announcement_subject"]
                del st.session_state["announcement_body"]
            st.rerun()

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
    for a in announcements:
        # key= gives the card a stable "st-key-rkcard_..." CSS class, which
        # the hover animation in app.py targets.
        with st.container(border=True, key=f"rkcard_ann_{a['announcement_id']}"):
            head_col, del_col = st.columns([5, 1], vertical_alignment="center")
            head_col.markdown(f"### {a['subject']}")

            if is_host:
                if del_col.button(
                    "Delete", key=f"delete_announcement_{a['announcement_id']}", icon=":material/delete:"
                ):
                    client.table("announcements").delete().eq("announcement_id", a["announcement_id"]).execute()
                    invalidate_cache()
                    st.session_state.announcement_message = ("success", f"Deleted \"{a['subject']}\".")
                    st.rerun()

            # Announcements can only be sent by a host, and the table doesn't
            # record which one, so the tag says the role rather than inventing
            # a name for it.
            badge_col, date_col = st.columns([1, 4], vertical_alignment="center")
            badge_col.badge("Host", color="primary", icon=":material/shield_person:")
            date_col.caption(f":material/schedule: {format_ist(a['created_at'])}")

            st.write(a["body"])

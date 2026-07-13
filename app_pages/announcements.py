# Its own page (not a section on Competitions) so it reads like Inventory
# and Competitions do — a distinct place, not buried inside another screen.
# Sending is host-only; every logged-in member can view what's been sent.

from datetime import date

import streamlit as st

from shared import get_client, send_email

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

                all_emails = [email for email in user_email_by_id.values() if email]
                for email in all_emails:
                    send_email(email, announcement_subject.strip(), announcement_body.strip())
                st.session_state.announcement_message = ("success", f"Sent to {len(all_emails)} member(s).")
                # Clear the form for next time — deleting the keys before
                # the widgets are recreated on rerun resets them to blank.
                del st.session_state["announcement_subject"]
                del st.session_state["announcement_body"]
            st.rerun()

if st.session_state.announcement_message:
    kind, text = st.session_state.announcement_message
    (st.success if kind == "success" else st.error)(text)
    st.session_state.announcement_message = None

announcements = (
    client.table("announcements").select("*").order("created_at", desc=True).limit(20).execute().data
)
if not announcements:
    st.caption("No announcements yet.")
else:
    for a in announcements:
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            col1.markdown(f"**{a['subject']}**")
            st.write(a["body"])
            posted_at = date.fromisoformat(a["created_at"][:10]).strftime("%d %b %Y")
            st.caption(posted_at)

            if is_host:
                if col2.button("Delete", key=f"delete_announcement_{a['announcement_id']}", icon=":material/delete:"):
                    client.table("announcements").delete().eq("announcement_id", a["announcement_id"]).execute()
                    st.session_state.announcement_message = ("success", f"Deleted \"{a['subject']}\".")
                    st.rerun()

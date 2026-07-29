# Private student queries, as a real back-and-forth thread now (not just
# one question + one answer). `queries` is just the thread container;
# every message — the original question, replies, answers, from either
# side — lives in query_messages. Either side can edit their own past
# messages. Everyone gets this same page — what you see depends on
# is_host, same as the Add-a-competition pattern (no separate host-only
# page needed since students need this page too).

from datetime import datetime, timezone
from urllib.parse import quote

import streamlit as st

from shared import (
    APP_URL, HOST_EMAILS, WHATSAPP_HELP_NUMBER, cached_table, format_ist, get_client,
    invalidate_cache, send_email,
)

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

st.title("Queries")

if "query_message" not in st.session_state:
    st.session_state.query_message = None
if "editing_message_id" not in st.session_state:
    st.session_state.editing_message_id = None

if st.session_state.query_message:
    st.toast(st.session_state.query_message, icon=":material/check_circle:")
    st.session_state.query_message = None


def render_thread(query, all_messages, notify_email=None):
    # Shared by both the host and student views — a thread looks the same
    # either way, just filtered to different threads before this is called.
    # notify_email: who to email when the CURRENT viewer sends a reply here.
    # Only set by the host view (to email the student) — a student's own
    # follow-up doesn't email the host, since the host is checking this
    # page directly rather than waiting on a notification for every
    # message in an ongoing conversation.
    query_id = query["query_id"]
    thread_messages = [m for m in all_messages if m["query_id"] == query_id]

    # Read receipts: host and student each have their own "last read" column,
    # since they read the thread independently. Only WRITE when there's
    # something new from the other side to acknowledge — not on every rerun
    # of an already-read thread — so opening a stale thread doesn't spam
    # updates + cache invalidations for no reason.
    my_read_field = "host_read_at" if is_host else "student_read_at"
    other_read_field = "student_read_at" if is_host else "host_read_at"
    my_read_at = query.get(my_read_field)
    other_read_at = query.get(other_read_field)
    latest_from_other = max(
        (m["created_at"] for m in thread_messages if m["sender_id"] != current_user_id),
        default=None,
    )
    if latest_from_other and (not my_read_at or latest_from_other > my_read_at):
        client.table("queries").update({
            my_read_field: datetime.now(timezone.utc).isoformat(),
        }).eq("query_id", query_id).execute()
        invalidate_cache()

    for msg in thread_messages:
        mine = msg["sender_id"] == current_user_id
        sender_name = "You" if mine else user_name_by_id.get(msg["sender_id"], "Unknown")
        # st.chat_message gives real conversation bubbles for free, so the
        # back-and-forth reads like a chat rather than a stack of boxes.
        # Your own messages sit on the "user" side, the other person's on
        # the "assistant" side, whichever way round host/student happens to be.
        with st.chat_message("user" if mine else "assistant"):
            if st.session_state.editing_message_id == msg["message_id"]:
                edited_body = st.text_area(
                    "Edit message", value=msg["body"], key=f"edit_msg_{msg['message_id']}",
                    label_visibility="collapsed",
                )
                save_col, cancel_col = st.columns([1, 1])
                if save_col.button("Save", key=f"save_msg_{msg['message_id']}", icon=":material/check:"):
                    client.table("query_messages").update({
                        "body": edited_body.strip(),
                        "edited_at": datetime.now().isoformat(),
                    }).eq("message_id", msg["message_id"]).execute()
                    invalidate_cache()
                    st.session_state.editing_message_id = None
                    st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_msg_{msg['message_id']}", icon=":material/close:"):
                    st.session_state.editing_message_id = None
                    st.rerun()
            else:
                col1, col2 = st.columns([5, 1], vertical_alignment="center")
                label = f"**{sender_name}**"
                if msg.get("edited_at"):
                    label += " _(edited)_"
                col1.markdown(label)
                st.write(msg["body"])
                timestamp_line = f":material/schedule: {format_ist(msg['created_at'])}"
                # Blue ticks, WhatsApp-style — only shown on your OWN messages
                # (there's no such thing as a read receipt on a message you
                # received), comparing against the other side's read_at.
                if mine:
                    if other_read_at and msg["created_at"] <= other_read_at:
                        timestamp_line += "  •  :blue[✓✓ Read]"
                    else:
                        timestamp_line += "  •  ✓ Sent"
                st.caption(timestamp_line)
                # You can only edit your own messages, not the other side's.
                if mine:
                    if col2.button("Edit", key=f"edit_btn_{msg['message_id']}", icon=":material/edit:"):
                        st.session_state.editing_message_id = msg["message_id"]
                        st.rerun()

    # st.chat_input submits on Enter and clears itself afterwards, so there's
    # no separate send button and no need to reset the field by hand the way
    # the old text_input did.
    reply_text = st.chat_input("Type a reply...", key=f"reply_{query_id}")
    if reply_text and reply_text.strip():
        client.table("query_messages").insert({
            "query_id": query_id,
            "sender_id": current_user_id,
            "body": reply_text.strip(),
        }).execute()
        invalidate_cache()

        if notify_email:
            send_email(
                notify_email,
                "New reply to your question",
                f"{current_user_name} replied:\n\n{reply_text.strip()}\n\n"
                f"Log in to the app to see it: {APP_URL}",
            )

        st.session_state.query_message = "Reply sent."
        # Rerun so the thread re-fetches and shows the message just sent.
        st.rerun()


if is_host:
    # --- Host view: every thread from every student ---------------------
    st.caption("Only you can see who asked what — students only see their own threads.")

    all_queries = sorted(
        cached_table("queries"), key=lambda q: q["created_at"], reverse=True
    )
    all_messages = sorted(cached_table("query_messages"), key=lambda m: m["created_at"])

    if not all_queries:
        st.caption("No queries yet.")
    else:
        for q in all_queries:
            asker_name = user_name_by_id.get(q["student_id"], "Unknown")
            thread_messages = [m for m in all_messages if m["query_id"] == q["query_id"]]
            latest_from_student = max(
                (m["created_at"] for m in thread_messages if m["sender_id"] == q["student_id"]),
                default=None,
            )
            # Same student can have several threads, which used to all show
            # up with the identical expander label ("Naitik Jindal", say) —
            # making it impossible to tell which one still needs a look. This
            # badge is what actually fixes that, not just the read-column math.
            label = asker_name
            if latest_from_student and (
                not q.get("host_read_at") or latest_from_student > q["host_read_at"]
            ):
                label += "  🔵 New"
            with st.expander(label):
                render_thread(q, all_messages, notify_email=user_email_by_id.get(q["student_id"]))
else:
    # --- Student view: start a thread, see your own past ones ----------
    # A faster, live option for something urgent — asking below still works,
    # but a host might not see it right away. Styled as a floating pill like
    # the chat widgets on most websites — Streamlit has no built-in
    # component for that, so this is raw HTML/CSS (one of the few
    # deliberate exceptions to "no custom CSS" in this app, same reasoning
    # as the login wordmark card and gear splash). Kept on one line since
    # st.markdown turns an indented multi-line string into a code block,
    # and st.html would silently strip the inline <svg> icon.
    whatsapp_text = quote("Hi, I need help with something in the RoboKnights app.")
    whatsapp_url = f"https://wa.me/{WHATSAPP_HELP_NUMBER}?text={whatsapp_text}"
    st.markdown(f'<a href="{whatsapp_url}" target="_blank" style="position: fixed; bottom: 24px; right: 24px; z-index: 9999; display: flex; align-items: center; gap: 10px; background: #1f2b2b; color: white; padding: 10px 20px 10px 10px; border-radius: 999px; text-decoration: none; box-shadow: 0 4px 14px rgba(0,0,0,0.35); font-weight: 600;"><span style="background: #25D366; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91C2.13 13.66 2.59 15.36 3.46 16.86L2.05 22L7.3 20.62C8.75 21.41 10.38 21.83 12.04 21.83C17.5 21.83 21.95 17.38 21.95 11.92C21.95 9.27 20.92 6.78 19.05 4.91C17.18 3.03 14.69 2 12.04 2M12.05 3.67C14.25 3.67 16.31 4.53 17.87 6.09C19.42 7.65 20.28 9.72 20.28 11.92C20.28 16.46 16.58 20.15 12.04 20.15C10.56 20.15 9.11 19.76 7.85 19L7.55 18.83L4.43 19.65L5.26 16.61L5.06 16.29C4.24 15 3.8 13.47 3.8 11.91C3.81 7.37 7.5 3.67 12.05 3.67M8.53 6.75C8.37 6.75 8.1 6.81 7.87 7.06C7.65 7.31 7 7.91 7 9.14C7 10.37 7.89 11.56 8 11.72C8.14 11.88 9.76 14.44 12.31 15.47C13.5 15.97 13.83 15.9 14.19 15.87C14.72 15.82 15.4 15.44 15.56 15C15.72 14.6 15.72 14.25 15.68 14.18C15.63 14.11 15.5 14.07 15.28 13.96C15.06 13.85 14 13.32 13.8 13.25C13.6 13.18 13.45 13.14 13.31 13.36C13.16 13.58 12.75 14.07 12.63 14.21C12.5 14.36 12.37 14.38 12.15 14.27C11.94 14.16 11.23 13.92 10.39 13.18C9.73 12.6 9.29 11.87 9.16 11.65C9.04 11.44 9.14 11.32 9.25 11.21C9.35 11.11 9.5 10.94 9.6 10.82C9.71 10.7 9.75 10.61 9.82 10.46C9.9 10.32 9.86 10.19 9.81 10.08C9.75 9.97 9.32 8.9 9.13 8.47C8.95 8.05 8.77 8.11 8.63 8.1C8.5 8.1 8.35 8.09 8.19 8.09Z"/></svg></span><span>Chat with us</span></a>', unsafe_allow_html=True)

    with st.expander(":material/add_box: Ask a question"):
        new_question = st.text_area("Your question", key="new_question")
        if st.button("Submit", icon=":material/send:", type="primary"):
            if not new_question.strip():
                st.error("Question can't be empty.")
            else:
                new_query = client.table("queries").insert({"student_id": current_user_id}).execute()
                query_id = new_query.data[0]["query_id"]
                client.table("query_messages").insert({
                    "query_id": query_id,
                    "sender_id": current_user_id,
                    "body": new_question.strip(),
                }).execute()
                invalidate_cache()

                host_emails = [
                    email for uid, email in user_email_by_id.items()
                    if email in HOST_EMAILS
                ]
                for email in host_emails:
                    send_email(
                        email,
                        f"New question from {current_user_name}",
                        f"{current_user_name} asked:\n\n{new_question.strip()}\n\n"
                        f"Reply here: {APP_URL}",
                    )

                st.session_state.query_message = "Question submitted — a host will reply soon."
                del st.session_state["new_question"]
                st.rerun()

    st.subheader(":material/list_alt: Your questions")
    my_queries = sorted(
        (q for q in cached_table("queries") if q["student_id"] == current_user_id),
        key=lambda q: q["created_at"], reverse=True,
    )
    if not my_queries:
        st.caption("You haven't asked anything yet.")
    else:
        my_query_ids = {q["query_id"] for q in my_queries}
        all_messages = sorted(
            (m for m in cached_table("query_messages") if m["query_id"] in my_query_ids),
            key=lambda m: m["created_at"],
        )
        for q in my_queries:
            first_message = next(
                (m["body"] for m in all_messages if m["query_id"] == q["query_id"]), ""
            ).strip()
            # A thread with no messages, or whose first one is blank, would
            # otherwise give the expander an empty title and look broken.
            if not first_message:
                first_message = "Your question"
            preview = first_message if len(first_message) <= 50 else first_message[:47] + "..."
            with st.expander(preview, expanded=len(my_queries) == 1):
                render_thread(q, all_messages)

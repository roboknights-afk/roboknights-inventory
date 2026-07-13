# Private student queries, as a real back-and-forth thread now (not just
# one question + one answer). `queries` is just the thread container;
# every message — the original question, replies, answers, from either
# side — lives in query_messages. Either side can edit their own past
# messages. Everyone gets this same page — what you see depends on
# is_host, same as the Add-a-competition pattern (no separate host-only
# page needed since students need this page too).

from datetime import datetime

import streamlit as st

from shared import APP_URL, HOST_EMAILS, format_ist, get_client, send_email

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
    st.success(st.session_state.query_message)
    st.session_state.query_message = None


def render_thread(query_id, all_messages, notify_email=None):
    # Shared by both the host and student views — a thread looks the same
    # either way, just filtered to different threads before this is called.
    # notify_email: who to email when the CURRENT viewer sends a reply here.
    # Only set by the host view (to email the student) — a student's own
    # follow-up doesn't email the host, since the host is checking this
    # page directly rather than waiting on a notification for every
    # message in an ongoing conversation.
    thread_messages = [m for m in all_messages if m["query_id"] == query_id]
    for msg in thread_messages:
        sender_name = "You" if msg["sender_id"] == current_user_id else user_name_by_id.get(
            msg["sender_id"], "Unknown"
        )
        with st.container(border=True):
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
                st.caption(format_ist(msg["created_at"]))
                # You can only edit your own messages, not the other side's.
                if msg["sender_id"] == current_user_id:
                    if col2.button("Edit", key=f"edit_btn_{msg['message_id']}", icon=":material/edit:"):
                        st.session_state.editing_message_id = msg["message_id"]
                        st.rerun()

    reply_text = st.text_input(
        "Reply", key=f"reply_{query_id}", label_visibility="collapsed", placeholder="Type a reply...",
    )
    if st.button("Reply", key=f"send_reply_{query_id}", icon=":material/send:"):
        if reply_text.strip():
            client.table("query_messages").insert({
                "query_id": query_id,
                "sender_id": current_user_id,
                "body": reply_text.strip(),
            }).execute()

            if notify_email:
                send_email(
                    notify_email,
                    "New reply to your question",
                    f"{current_user_name} replied:\n\n{reply_text.strip()}\n\n"
                    f"Log in to the app to see it: {APP_URL}",
                )

            st.session_state.query_message = "Reply sent."
            del st.session_state[f"reply_{query_id}"]
            st.rerun()


if is_host:
    # --- Host view: every thread from every student ---------------------
    st.caption("Only you can see who asked what — students only see their own threads.")

    all_queries = client.table("queries").select("*").order("created_at", desc=True).execute().data
    all_messages = client.table("query_messages").select("*").order("created_at").execute().data

    if not all_queries:
        st.caption("No queries yet.")
    else:
        for q in all_queries:
            asker_name = user_name_by_id.get(q["student_id"], "Unknown")
            with st.expander(asker_name):
                render_thread(q["query_id"], all_messages, notify_email=user_email_by_id.get(q["student_id"]))
else:
    # --- Student view: start a thread, see your own past ones ----------
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
    my_queries = (
        client.table("queries")
        .select("*")
        .eq("student_id", current_user_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    if not my_queries:
        st.caption("You haven't asked anything yet.")
    else:
        my_query_ids = [q["query_id"] for q in my_queries]
        all_messages = (
            client.table("query_messages")
            .select("*")
            .in_("query_id", my_query_ids)
            .order("created_at")
            .execute()
            .data
        )
        for q in my_queries:
            first_message = next(
                (m["body"] for m in all_messages if m["query_id"] == q["query_id"]), "Your question"
            )
            preview = first_message if len(first_message) <= 50 else first_message[:47] + "..."
            with st.expander(preview, expanded=len(my_queries) == 1):
                render_thread(q["query_id"], all_messages)

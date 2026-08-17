# Member-to-member chat: 1:1 DMs and group chats. Both are the same thing
# with a different number of people in them, so they share one set of
# tables and `is_group` tells them apart — a DM is just a 2-person thread
# with no title and nobody added to it after the fact.
#
# Not the same as Queries (private student<>host, and a host sees EVERY
# thread there): here nobody sees a thread they aren't actually in, hosts
# included. Read receipts are one row per person per thread, since each
# member reads each of their threads independently.

from datetime import datetime, timezone

import streamlit as st

from shared import (
    cached_table, format_ist, format_relative, get_client, invalidate_cache, safe_write,
)

client = get_client()
current_user_id = st.session_state.current_user_id
user_name_by_id = st.session_state.user_name_by_id
is_read_only = st.session_state.is_read_only

st.title("Messages")

# A read-only visitor account has no business in members' private
# conversations — same reasoning that keeps them out of Queries.
if is_read_only:
    st.info(":material/lock: Read-only accounts can't use messages.")
    st.stop()

if "chat_message" not in st.session_state:
    st.session_state.chat_message = None
if "editing_chat_message_id" not in st.session_state:
    st.session_state.editing_chat_message_id = None
# Held open by a flag, same reasoning as every other dialog in this app:
# the st.rerun() after submitting is a full-app rerun, so the dialog has
# to be re-called each run to stay up, and clearing the flag closes it.
if "show_new_chat" not in st.session_state:
    st.session_state.show_new_chat = False
if "open_thread_id" not in st.session_state:
    st.session_state.open_thread_id = None

if st.session_state.chat_message:
    st.toast(st.session_state.chat_message, icon=":material/check_circle:")
    st.session_state.chat_message = None


def _close_new_chat():
    st.session_state.show_new_chat = False


def _open_new_chat():
    st.session_state.show_new_chat = True


def _my_thread_ids():
    return {
        p["thread_id"] for p in cached_table("chat_participants")
        if p["user_id"] == current_user_id
    }


def _participants_by_thread():
    by_thread = {}
    for p in cached_table("chat_participants"):
        by_thread.setdefault(p["thread_id"], []).append(p["user_id"])
    return by_thread


def _thread_label(thread, participant_ids):
    # A group shows its title; a DM shows the OTHER person's name, since
    # "chat with yourself and Aryamman" is just "Aryamman" from where
    # you're sitting.
    if thread.get("is_group"):
        return thread.get("title") or "Group chat"
    other = next((uid for uid in participant_ids if uid != current_user_id), None)
    return user_name_by_id.get(other, "Unknown")


@st.dialog("New chat", on_dismiss=_close_new_chat)
def render_new_chat():
    # Everyone except yourself — you're added to your own thread
    # automatically below, and picking yourself would make a "DM" with one
    # person in it.
    others = sorted(
        ((uid, name) for uid, name in user_name_by_id.items() if uid != current_user_id),
        key=lambda pair: pair[1].lower(),
    )
    if not others:
        st.caption("There's nobody else signed up yet.")
        return

    picked = st.multiselect(
        "Who do you want to talk to?",
        options=[uid for uid, _ in others],
        format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
        key="new_chat_people",
    )
    # One person picked = a DM, more than one = a group. Naming it is only
    # offered for a group, since a DM's name is just the other person.
    is_group = len(picked) > 1
    title = ""
    if is_group:
        title = st.text_input(
            "Group name", key="new_chat_title", placeholder="e.g. Line follower team",
        )

    if st.button("Start chat", icon=":material/send:", type="primary", key="confirm_new_chat"):
        if not picked:
            st.error("Pick at least one person.")
            return
        if is_group and not title.strip():
            st.error("Give the group a name.")
            return

        members = set(picked) | {current_user_id}

        # An existing DM with the same person is reused rather than making
        # a second parallel thread with them — two separate "chats with
        # Aryamman" would just split the conversation in half.
        if not is_group:
            participants_by_thread = _participants_by_thread()
            for t in cached_table("chat_threads"):
                if t.get("is_group"):
                    continue
                if set(participants_by_thread.get(t["thread_id"], [])) == members:
                    st.session_state.open_thread_id = t["thread_id"]
                    st.session_state.chat_message = "You already had a chat with them — opened it."
                    _close_new_chat()
                    st.rerun()

        with safe_write("start this chat"):
            new_thread = client.table("chat_threads").insert({
                "is_group": is_group,
                "title": title.strip() if is_group else None,
                "created_by": current_user_id,
            }).execute()
            thread_id = new_thread.data[0]["thread_id"]
            client.table("chat_participants").insert(
                [{"thread_id": thread_id, "user_id": uid} for uid in members]
            ).execute()
            invalidate_cache()

        st.session_state.open_thread_id = thread_id
        st.session_state.chat_message = "Chat started."
        st.session_state.pop("new_chat_people", None)
        st.session_state.pop("new_chat_title", None)
        _close_new_chat()
        st.rerun()


def render_thread(thread, participant_ids, thread_messages):
    thread_id = thread["thread_id"]

    if thread.get("is_group"):
        names = ", ".join(
            sorted(user_name_by_id.get(uid, "Unknown") for uid in participant_ids)
        )
        st.caption(f":material/group: {len(participant_ids)} people — {names}")

    # Read receipt: only WRITE when there's actually something newer from
    # someone else than what's on record, so re-opening an already-read
    # thread doesn't spam updates and cache invalidations for no reason.
    my_read_row = next(
        (r for r in cached_table("chat_reads")
         if r["thread_id"] == thread_id and r["user_id"] == current_user_id),
        None,
    )
    my_read_at = my_read_row.get("last_read_at") if my_read_row else None
    latest_from_other = max(
        (m["created_at"] for m in thread_messages if m["sender_id"] != current_user_id),
        default=None,
    )
    if latest_from_other and (not my_read_at or latest_from_other > my_read_at):
        client.table("chat_reads").upsert({
            "thread_id": thread_id,
            "user_id": current_user_id,
            "last_read_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        invalidate_cache()

    # Everyone else's last-read time, so a sent message can show whether
    # it's been read. In a group that means "read by everyone", which is
    # the only reading of it that doesn't need a per-person breakdown.
    other_ids = [uid for uid in participant_ids if uid != current_user_id]
    other_reads = [
        r.get("last_read_at") for r in cached_table("chat_reads")
        if r["thread_id"] == thread_id and r["user_id"] in other_ids
    ]
    everyone_read_at = (
        min(other_reads) if other_reads and len(other_reads) == len(other_ids)
        and all(other_reads) else None
    )

    if not thread_messages:
        st.caption("No messages yet — say something.")

    for msg in thread_messages:
        mine = msg["sender_id"] == current_user_id
        sender_name = "You" if mine else user_name_by_id.get(msg["sender_id"], "Unknown")
        with st.chat_message("user" if mine else "assistant", avatar=":material/person:"):
            if st.session_state.editing_chat_message_id == msg["message_id"]:
                edited_body = st.text_area(
                    "Edit message", value=msg["body"], key=f"edit_chat_{msg['message_id']}",
                    label_visibility="collapsed",
                )
                save_col, cancel_col = st.columns([1, 1])
                if save_col.button("Save", key=f"save_chat_{msg['message_id']}", icon=":material/check:"):
                    with safe_write("save this edit"):
                        client.table("chat_messages").update({
                            "body": edited_body.strip(),
                            "edited_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("message_id", msg["message_id"]).execute()
                        invalidate_cache()
                    st.session_state.editing_chat_message_id = None
                    st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_chat_{msg['message_id']}", icon=":material/close:"):
                    st.session_state.editing_chat_message_id = None
                    st.rerun()
            else:
                col1, col2 = st.columns([5, 1], vertical_alignment="center")
                label = f"**{sender_name}**"
                if msg.get("edited_at"):
                    label += " _(edited)_"
                col1.markdown(label)
                st.write(msg["body"])
                timestamp_line = f":material/schedule: {format_relative(msg['created_at'])}"
                if mine:
                    if everyone_read_at and msg["created_at"] <= everyone_read_at:
                        timestamp_line += "  •  :blue[✓✓ Read]"
                    else:
                        timestamp_line += "  •  ✓ Sent"
                st.caption(timestamp_line, help=format_ist(msg["created_at"]))
                # You can only edit your own messages, same as Queries.
                if mine:
                    if col2.button("Edit", key=f"edit_chat_btn_{msg['message_id']}", icon=":material/edit:"):
                        st.session_state.editing_chat_message_id = msg["message_id"]
                        st.rerun()

    new_text = st.chat_input("Type a message...", key=f"chat_input_{thread_id}")
    if new_text and new_text.strip():
        with safe_write("send this message"):
            client.table("chat_messages").insert({
                "thread_id": thread_id,
                "sender_id": current_user_id,
                "body": new_text.strip(),
            }).execute()
            invalidate_cache()
        st.rerun()


st.button(
    "New chat", icon=":material/add_comment:", type="primary",
    key="open_new_chat", on_click=_open_new_chat,
)
if st.session_state.show_new_chat:
    render_new_chat()

my_thread_ids = _my_thread_ids()
my_threads = [t for t in cached_table("chat_threads") if t["thread_id"] in my_thread_ids]

if not my_threads:
    st.caption("No chats yet — start one above.")
    st.stop()

participants_by_thread = _participants_by_thread()
messages_by_thread = {}
for m in sorted(cached_table("chat_messages"), key=lambda m: m["created_at"]):
    if m["thread_id"] in my_thread_ids:
        messages_by_thread.setdefault(m["thread_id"], []).append(m)

# Most recently active first — a chat someone just wrote in is what you're
# most likely here for. Threads with nothing in them yet fall back to when
# they were created, so a brand-new chat doesn't sink to the bottom.
def _last_activity(thread):
    msgs = messages_by_thread.get(thread["thread_id"], [])
    return msgs[-1]["created_at"] if msgs else thread["created_at"]


my_threads.sort(key=_last_activity, reverse=True)

reads_by_thread = {
    r["thread_id"]: r.get("last_read_at") for r in cached_table("chat_reads")
    if r["user_id"] == current_user_id
}

for t in my_threads:
    thread_id = t["thread_id"]
    participant_ids = participants_by_thread.get(thread_id, [])
    thread_messages = messages_by_thread.get(thread_id, [])

    label = _thread_label(t, participant_ids)
    latest_from_other = max(
        (m["created_at"] for m in thread_messages if m["sender_id"] != current_user_id),
        default=None,
    )
    my_read_at = reads_by_thread.get(thread_id)
    if latest_from_other and (not my_read_at or latest_from_other > my_read_at):
        label += "  🔵 New"

    with st.expander(label, expanded=st.session_state.open_thread_id == thread_id):
        render_thread(t, participant_ids, thread_messages)

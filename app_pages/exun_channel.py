# Private RoboKnights <> Exun channel: one shared thread, visible only to
# the specific hand-picked people in shared.py's EXUN_CHANNEL_MEMBERS
# (both clubs' leadership, plus a few named RoboKnights members) — not
# every host, not every member. app.py only adds this page to the nav for
# those exact people, but it's guarded here too since that's the one
# thing st.navigation's page list alone doesn't stop for a direct URL hit.

from datetime import datetime, timezone

import streamlit as st

from shared import (
    APP_URL, EXUN_CHANNEL_MEMBERS, EXUN_EMAILS, HOST_EMAILS, cached_table, format_ist,
    format_relative, get_client, invalidate_cache, safe_write, send_email,
)

client = get_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
current_email = st.session_state.auth_user["email"]

if current_email not in EXUN_CHANNEL_MEMBERS:
    st.error("This channel is only open to specific RoboKnights and Exun members.")
    st.stop()

st.title(":material/handshake: Exun Channel")
st.caption("Private — only visible to the specific people listed below, not the whole club.")

all_users = cached_table("users")
member_names = sorted(
    u["name"] for u in all_users if u["email"] in EXUN_CHANNEL_MEMBERS
)
with st.expander(f":material/group: Who has access ({len(EXUN_CHANNEL_MEMBERS)})"):
    if member_names:
        st.write(", ".join(member_names))
    else:
        st.caption("No one on this list has signed up yet.")

user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

messages = sorted(cached_table("exun_channel_messages"), key=lambda m: m["created_at"])

# Read receipt: one row per member (not per-thread — this is a single flat
# channel everyone reads independently). Only WRITE when there's actually
# something newer than what's on record, same "don't spam updates on an
# already-read channel" guard Queries uses for its own read_at columns.
latest_message_at = max((m["created_at"] for m in messages), default=None)
my_read_row = next(
    (r for r in cached_table("exun_channel_reads") if r["user_id"] == current_user_id), None
)
my_read_at = my_read_row.get("last_read_at") if my_read_row else None
if latest_message_at and (not my_read_at or latest_message_at > my_read_at):
    with safe_write("mark this channel read"):
        client.table("exun_channel_reads").upsert({
            "user_id": current_user_id,
            "last_read_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        invalidate_cache()

if not messages:
    st.caption("No messages yet — say hello.")
else:
    for msg in messages:
        mine = msg["sender_id"] == current_user_id
        sender_name = "You" if mine else user_name_by_id.get(msg["sender_id"], "Unknown")
        # Avatar shows which side of the partnership (and what role) the
        # sender is: handshake for Exun, shield for a RoboKnights host,
        # plain person for the named RoboKnights members.
        sender_email = user_email_by_id.get(msg["sender_id"])
        if sender_email in EXUN_EMAILS:
            avatar = ":material/handshake:"
        elif sender_email in HOST_EMAILS:
            avatar = ":material/shield_person:"
        else:
            avatar = ":material/person:"
        with st.chat_message("user" if mine else "assistant", avatar=avatar):
            st.markdown(f"**{sender_name}**")
            st.write(msg["body"])
            st.caption(
                f":material/schedule: {format_relative(msg['created_at'])}",
                help=format_ist(msg["created_at"]),
            )

reply_text = st.chat_input("Message the channel...")
if reply_text and reply_text.strip():
    with safe_write("send this message"):
        client.table("exun_channel_messages").insert({
            "sender_id": current_user_id,
            "body": reply_text.strip(),
        }).execute()
        invalidate_cache()

        # Only the RoboKnights side notifies Exun — Exun posting their own
        # message doesn't need to email themselves.
        if current_email not in EXUN_EMAILS:
            for exun_email in EXUN_EMAILS:
                send_email(
                    exun_email,
                    f"New message from {current_user_name} in the Exun channel",
                    f"{current_user_name} posted in the Exun channel:\n\n{reply_text.strip()}\n\n"
                    f"Reply here: {APP_URL}",
                )

        st.rerun()

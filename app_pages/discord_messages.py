# Host-only hub for everything this app sends to Discord, across BOTH
# channels: the original competitions channel, and the private Exun<>RK
# channel added alongside the "all event names finalized" notification.
# Used to live scattered across the Competitions page (custom message,
# vacant-events reminder, recent-messages list) — moved here so a host has
# one place for Discord messaging instead of hunting for it inside a
# competitions-specific tab.

import os

import streamlit as st

from shared import (
    CUSTOM_DISCORD_MESSAGE_HOURLY_LIMIT, DISCORD_CHANNELS, build_vacant_events_message, cached_table,
    custom_discord_send_allowed, delete_discord_message, discord_message_suffix, edit_discord_message,
    format_ist, invalidate_cache, send_discord_message,
)

is_host = st.session_state.is_host

st.title("Discord Messages")

if not is_host:
    st.info(":material/lock: Host-only page.")
    st.stop()

st.caption(
    "Send, preview, and manage every message this app posts to Discord. Every "
    "message automatically ends with a bold-italic \"automated\" line; "
    "competitions-channel messages also get the app link (the other channels' "
    "are FYI-only, not a \"go do something\" prompt)."
)

CHANNEL_LABELS = {
    "competitions": "Competitions channel",
    "exun_rk": "Exun RK channel",
    "dashboard": "Dashboard updates",
}

for _channel in DISCORD_CHANNELS:
    st.session_state.setdefault(f"custom_discord_preview_{_channel}", None)
    st.session_state.setdefault(f"editing_custom_discord_preview_{_channel}", False)
    st.session_state.setdefault(f"editing_discord_msg_id_{_channel}", None)
st.session_state.setdefault("vacant_events_preview", None)
st.session_state.setdefault("editing_vacant_events_preview", False)


def render_recent_messages(channel):
    st.markdown("**Recent messages**")
    recent_messages = sorted(
        (m for m in cached_table("discord_messages") if m.get("channel", "competitions") == channel),
        key=lambda m: m["sent_at"], reverse=True,
    )[:20]
    if not recent_messages:
        st.caption("Nothing sent yet.")
    editing_key = f"editing_discord_msg_id_{channel}"
    for m in recent_messages:
        row_col, edit_col, delete_col = st.columns([5, 1, 1], vertical_alignment="center")
        preview = m["content"] if len(m["content"]) <= 150 else m["content"][:147] + "..."
        row_col.caption(f":material/schedule: {format_ist(m['sent_at'])} — {preview}")
        if edit_col.button("Edit", key=f"edit_discord_msg_{channel}_{m['id']}", icon=":material/edit:"):
            st.session_state[editing_key] = m["id"]
            st.rerun()
        if delete_col.button("Delete", key=f"delete_discord_msg_{channel}_{m['id']}", icon=":material/delete:"):
            delete_discord_message(channel, m["message_id"])
            invalidate_cache()
            st.toast("Deleted from Discord.", icon=":material/delete:")
            st.rerun()

        if st.session_state[editing_key] == m["id"]:
            # Edited body only — the suffix (app link on the competitions
            # channel, just the automated-message marker on exun_rk) is
            # stripped for editing and re-appended on save, so it can't be
            # accidentally edited out.
            suffix = discord_message_suffix(channel)
            current_body = m["content"][: -len(suffix)] if m["content"].endswith(suffix) else m["content"]
            new_body = st.text_area(
                "Edit message", value=current_body, height=140,
                key=f"discord_edit_box_{channel}_{m['id']}", label_visibility="collapsed",
            )
            save_col, cancel_col = st.columns([1, 1])
            if save_col.button(
                "Save changes", key=f"save_discord_edit_{channel}_{m['id']}",
                icon=":material/check:", type="primary",
            ):
                if not new_body.strip():
                    st.error("Message can't be empty.")
                else:
                    with st.spinner("Updating this message on Discord…"):
                        ok = edit_discord_message(channel, m["message_id"], new_body.strip())
                    if ok:
                        invalidate_cache()
                        st.session_state[editing_key] = None
                        st.toast("Updated on Discord.", icon=":material/check_circle:")
                        st.rerun()
                    else:
                        st.error("Couldn't update that message on Discord — try again.")
            if cancel_col.button("Cancel", key=f"cancel_discord_edit_{channel}_{m['id']}", icon=":material/close:"):
                st.session_state[editing_key] = None
                st.rerun()


def render_custom_message_section(channel):
    label = CHANNEL_LABELS[channel]
    webhook_configured = bool(os.environ.get(DISCORD_CHANNELS[channel]))
    st.subheader(f":material/forum: {label}")
    if not webhook_configured:
        st.info(
            f":material/key_off: No `{DISCORD_CHANNELS[channel]}` is set yet — "
            "see DEPLOY.md for how to create that webhook in Discord."
        )
        return

    st.markdown("**Send a custom message**")
    st.caption(
        "Discord markdown works (**bold**, *italic*, etc.), and you can "
        "@mention someone by hand with `<@their_discord_user_id>` or a role "
        "with `<@&role_id>`."
    )
    custom_message = st.text_area(
        "Message", key=f"custom_discord_message_{channel}", label_visibility="collapsed",
        placeholder="Type your message...",
    )
    preview_key = f"custom_discord_preview_{channel}"
    editing_key = f"editing_custom_discord_preview_{channel}"
    if st.button("Preview", icon=":material/visibility:", key=f"preview_custom_discord_btn_{channel}"):
        if not custom_message.strip():
            st.error("Message can't be empty.")
        else:
            st.session_state[preview_key] = custom_message.strip()
            st.session_state[editing_key] = False
            st.rerun()

    preview = st.session_state.get(preview_key)
    if preview:
        footer_note = (
            "app link + automated-message footer" if channel == "competitions"
            else "automated-message footer (no app link on this channel)"
        )
        st.markdown(f"**Preview** (with the {footer_note} that gets added):")
        if st.session_state.get(editing_key):
            edited = st.text_area(
                "Edit custom preview", value=preview, height=140,
                key=f"custom_discord_edit_box_{channel}", label_visibility="collapsed",
            )
            save_col, cancel_col = st.columns([1, 1])
            if save_col.button(
                "Save edits", icon=":material/check:", type="primary", key=f"save_custom_discord_edit_btn_{channel}",
            ):
                st.session_state[preview_key] = edited
                st.session_state[editing_key] = False
                st.rerun()
            if cancel_col.button("Cancel", icon=":material/close:", key=f"cancel_custom_discord_edit_btn_{channel}"):
                st.session_state[editing_key] = False
                st.rerun()
        else:
            st.text_area(
                "Custom preview", value=preview + discord_message_suffix(channel), height=140,
                key=f"custom_discord_preview_box_{channel}", label_visibility="collapsed", disabled=True,
            )
            edit_col, send_col = st.columns([1, 1])
            if edit_col.button("Edit", icon=":material/edit:", key=f"edit_custom_discord_btn_{channel}"):
                st.session_state[editing_key] = True
                st.rerun()
            if send_col.button(
                "Send to Discord", icon=":material/send:", type="primary", key=f"send_custom_discord_btn_{channel}",
            ):
                if not custom_discord_send_allowed():
                    st.error(
                        f"Rate limit reached — max {CUSTOM_DISCORD_MESSAGE_HOURLY_LIMIT} custom "
                        "messages per hour (shared across every channel/host), to stop accidental "
                        "spam. Try again in a bit."
                    )
                else:
                    send_discord_message(preview, channel=channel)
                    invalidate_cache()
                    st.session_state[preview_key] = None
                    st.toast("Sent to Discord!", icon=":material/check_circle:")
                    del st.session_state[f"custom_discord_message_{channel}"]
                    st.rerun()

    st.divider()
    render_recent_messages(channel)


def render_vacant_events_reminder():
    # Competitions-channel only: pings the @member / @adhoc server roles
    # (see DEPLOY.md) once with every upcoming event, across every
    # competition, that still has open slots.
    with st.container(border=True):
        st.markdown("**Vacant events reminder**")
        webhook_configured = bool(os.environ.get(DISCORD_CHANNELS["competitions"]))
        if not webhook_configured:
            return
        st.caption(
            "Builds a message listing every upcoming event (across every "
            "competition) that still has open slots, pinging the @member / "
            "@adhoc server roles. Review it below before it actually posts."
        )
        if st.button("Generate message", icon=":material/auto_awesome:", key="gen_vacant_events_msg"):
            message = build_vacant_events_message()
            if message is None:
                st.session_state.vacant_events_preview = None
                st.toast("No vacant events right now — nothing to send.", icon=":material/info:")
            else:
                st.session_state.vacant_events_preview = message
            st.rerun()

        preview = st.session_state.get("vacant_events_preview")
        if preview:
            st.markdown("**Preview** (exactly what will post, before the app link + automated-message footer):")
            if st.session_state.get("editing_vacant_events_preview"):
                edited = st.text_area(
                    "Edit preview", value=preview, height=220,
                    key="vacant_events_edit_box", label_visibility="collapsed",
                )
                save_col, cancel_col = st.columns([1, 1])
                if save_col.button(
                    "Save edits", icon=":material/check:", type="primary", key="save_vacant_events_edit_btn",
                ):
                    st.session_state.vacant_events_preview = edited
                    st.session_state.editing_vacant_events_preview = False
                    st.rerun()
                if cancel_col.button("Cancel", icon=":material/close:", key="cancel_vacant_events_edit_btn"):
                    st.session_state.editing_vacant_events_preview = False
                    st.rerun()
            else:
                st.text_area(
                    "Preview", value=preview, height=220, key="vacant_events_preview_box",
                    label_visibility="collapsed", disabled=True,
                )
                edit_col, send_col, discard_col = st.columns([1, 1, 1])
                if edit_col.button("Edit", icon=":material/edit:", key="edit_vacant_events_btn"):
                    st.session_state.editing_vacant_events_preview = True
                    st.rerun()
                if send_col.button(
                    "Send to Discord", icon=":material/send:", type="primary", key="send_vacant_events_btn",
                ):
                    send_discord_message(preview, channel="competitions")
                    invalidate_cache()
                    st.session_state.vacant_events_preview = None
                    st.toast("Sent to Discord!", icon=":material/check_circle:")
                    st.rerun()
                if discard_col.button("Discard", icon=":material/close:", key="discard_vacant_events_btn"):
                    st.session_state.vacant_events_preview = None
                    st.rerun()


tab_competitions, tab_exun_rk, tab_dashboard = st.tabs(
    [CHANNEL_LABELS["competitions"], CHANNEL_LABELS["exun_rk"], CHANNEL_LABELS["dashboard"]]
)

with tab_competitions:
    st.caption(
        "New-event notifications post here automatically whenever a "
        "competition event is added — nothing to do for those."
    )
    render_vacant_events_reminder()
    render_custom_message_section("competitions")

with tab_exun_rk:
    st.caption(
        "\"Team names finalized\" posts here automatically once every event "
        "under a competition has its full SELECTED roster — nothing to do "
        "for that either."
    )
    render_custom_message_section("exun_rk")

with tab_dashboard:
    st.caption(
        "Posted automatically by GitHub every time the dashboard itself is "
        "updated — a short summary of what changed for members. That comes "
        "from send_dashboard_update.py, not from this app, so there's nothing "
        "to trigger here; the box below is only for a one-off message, and "
        "the list under it can edit or delete anything already posted."
    )
    render_custom_message_section("dashboard")

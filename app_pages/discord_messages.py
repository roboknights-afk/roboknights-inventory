# Host-only hub for everything this app sends to Discord, across BOTH
# channels: the original competitions channel, and the private Exun<>RK
# channel added alongside the "all event names finalized" notification.
# Used to live scattered across the Competitions page (custom message,
# vacant-events reminder, recent-messages list) — moved here so a host has
# one place for Discord messaging instead of hunting for it inside a
# competitions-specific tab.

import os

import streamlit as st
from groq import Groq

from shared import (
    CUSTOM_DISCORD_MESSAGE_HOURLY_LIMIT, DISCORD_CHANNELS, ROAST_REFUSAL, _roast_request,
    build_vacant_events_message, cached_table, custom_discord_send_allowed, delete_discord_message,
    discord_message_suffix, edit_discord_message, format_ist, invalidate_cache, send_discord_message,
)

# Same model app_pages/assistant.py uses (each AI-having surface in this
# project keeps its own copy of this rather than sharing one - see that
# file, discord_bot/bot.py, and send_dashboard_update.py for the others).
GROQ_MODEL = "openai/gpt-oss-120b"

DRAFT_SYSTEM_PROMPT = """You draft a single Discord message for RoboKnights, a school robotics club, on behalf of a host who reviews and can edit every draft before it's actually sent - nothing you write goes out unseen.

Write only the message itself: no meta-commentary, no "Here's a draft:", no quotation marks wrapping it, no explanation of your choices. Discord markdown (**bold**, *italic*, etc.) is fine to use.

Match the tone of a real host writing to real students, aged 11-18: direct, honest, and human - not corporate, not preachy, not overly formal. Long is fine when the host's instructions call for it, short is fine too.

Never insult, mock, roast, or disrespect anyone - a member, a group, staff, or an outsider - even if asked to. Never discuss school staff/administration, Exun, Domain Square, or DPSRKP by name or description. If the host's instructions ask for either of those, do not write the message - instead write only: "I can't draft that - it would roast/disrespect someone or discuss staff/off-limits topics. Write this one yourself."."""


def draft_discord_message(prompt):
    """Turns a host's short instruction ("write a long reply to X
    justifying...") into a full message draft, using the same Groq model
    the AI Assistant uses. The host still reviews it in the normal preview
    box below before anything sends - this only saves typing the whole
    thing by hand, the same trade a host already makes asking an assistant
    to draft an email they'll still read before hitting send.

    _roast_request() runs BEFORE any API call, same reasoning as every
    other call site with this guard: a host typing "roast Arnav" should be
    refused for free, not sent to a model that might comply anyway.
    Returns (text, None) on success or (None, message) to show instead.
    """
    if _roast_request(prompt):
        return None, ROAST_REFUSAL
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None, (
            "No `GROQ_API_KEY` is set, so AI drafting can't run yet. Get a "
            "free key (no card required) at console.groq.com/keys and add "
            "it to `.env` as `GROQ_API_KEY=...`."
        )
    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None, "The draft came back empty — try rephrasing what you want."
        return text, None
    except Exception as exc:
        return None, f"Couldn't generate a draft: {exc}"

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
    "competitions": "Competitions",
    "exun_rk": "Exun RK",
    "dashboard": "Dashboard updates",
    "announcements": "Announcements",
}

CHANNEL_INTRO = {
    "competitions": (
        "New-event notifications post here automatically whenever a "
        "competition event is added — nothing to do for those."
    ),
    "exun_rk": (
        "\"Team names finalized\" posts here automatically once every event "
        "under a competition has its full SELECTED roster — nothing to do "
        "for that either."
    ),
    "dashboard": (
        "Posted automatically by GitHub every time the dashboard itself is "
        "updated — a short summary of what changed for members. That comes "
        "from send_dashboard_update.py, not from this app, so there's nothing "
        "to trigger here; the box below is only for a one-off message, and "
        "the list under it can edit or delete anything already posted."
    ),
    "announcements": (
        "Nothing posts here automatically — this is the club's #announcements "
        "channel, for one-off messages a host writes by hand."
    ),
}

for _channel in DISCORD_CHANNELS:
    st.session_state.setdefault(f"custom_discord_preview_{_channel}", None)
    st.session_state.setdefault(f"editing_custom_discord_preview_{_channel}", False)
    st.session_state.setdefault(f"editing_discord_msg_id_{_channel}", None)
st.session_state.setdefault("vacant_events_preview", None)
st.session_state.setdefault("editing_vacant_events_preview", False)


def render_recent_messages(channel):
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
            with st.container(horizontal=True):
                if st.button(
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
                if st.button("Cancel", key=f"cancel_discord_edit_{channel}_{m['id']}", icon=":material/close:"):
                    st.session_state[editing_key] = None
                    st.rerun()


def render_draft_popover(channel):
    with st.popover("Draft with AI", icon=":material/auto_awesome:"):
        st.caption(
            "Describe what you want — it fills the message box below for "
            "you to review and edit. Nothing posts from this on its own."
        )
        draft_prompt = st.text_input(
            "What should the message say?", key=f"discord_draft_prompt_{channel}",
            label_visibility="collapsed",
            placeholder='e.g. "a long reply to Arnav justifying why message logging exists"',
        )
        if st.button("Generate draft", icon=":material/auto_awesome:", key=f"gen_discord_draft_btn_{channel}"):
            if not draft_prompt.strip():
                st.error("Describe what you want first.")
            else:
                with st.spinner("Drafting…"):
                    drafted, error = draft_discord_message(draft_prompt.strip())
                if error:
                    st.error(error)
                else:
                    st.session_state[f"custom_discord_message_{channel}"] = drafted
                    st.toast("Draft ready in the message box.", icon=":material/auto_awesome:")
                    st.rerun()


def render_custom_message_section(channel):
    webhook_configured = bool(os.environ.get(DISCORD_CHANNELS[channel]))
    if not webhook_configured:
        st.warning(
            f":material/key_off: No `{DISCORD_CHANNELS[channel]}` is set yet — "
            "see DEPLOY.md for how to create that webhook in Discord."
        )
        return

    with st.container(border=True):
        header_col, popover_col = st.columns([3, 1], vertical_alignment="center")
        header_col.markdown("**Compose a message**")
        with popover_col:
            render_draft_popover(channel)

        custom_message = st.text_area(
            "Message", key=f"custom_discord_message_{channel}", label_visibility="collapsed",
            placeholder="Type your message, or use Draft with AI above...", height=120,
        )
        st.caption(
            "Discord markdown works (**bold**, *italic*, etc.), and you can "
            "@mention someone by hand with `<@their_discord_user_id>` or a role "
            "with `<@&role_id>`."
        )

        preview_key = f"custom_discord_preview_{channel}"
        editing_key = f"editing_custom_discord_preview_{channel}"
        if st.button(
            "Preview", icon=":material/visibility:", type="primary", key=f"preview_custom_discord_btn_{channel}",
        ):
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
            st.markdown(f"**Preview** — exactly what posts, with the {footer_note}:")
            if st.session_state.get(editing_key):
                edited = st.text_area(
                    "Edit custom preview", value=preview, height=140,
                    key=f"custom_discord_edit_box_{channel}", label_visibility="collapsed",
                )
                with st.container(horizontal=True):
                    if st.button(
                        "Save edits", icon=":material/check:", type="primary",
                        key=f"save_custom_discord_edit_btn_{channel}",
                    ):
                        st.session_state[preview_key] = edited
                        st.session_state[editing_key] = False
                        st.rerun()
                    if st.button(
                        "Cancel", icon=":material/close:", key=f"cancel_custom_discord_edit_btn_{channel}",
                    ):
                        st.session_state[editing_key] = False
                        st.rerun()
            else:
                st.text_area(
                    "Custom preview", value=preview + discord_message_suffix(channel), height=140,
                    key=f"custom_discord_preview_box_{channel}", label_visibility="collapsed", disabled=True,
                )
                with st.container(horizontal=True):
                    if st.button("Edit", icon=":material/edit:", key=f"edit_custom_discord_btn_{channel}"):
                        st.session_state[editing_key] = True
                        st.rerun()
                    if st.button(
                        "Send to Discord", icon=":material/send:", type="primary",
                        key=f"send_custom_discord_btn_{channel}",
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

    with st.expander("Recent messages", icon=":material/history:"):
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
                with st.container(horizontal=True):
                    if st.button(
                        "Save edits", icon=":material/check:", type="primary", key="save_vacant_events_edit_btn",
                    ):
                        st.session_state.vacant_events_preview = edited
                        st.session_state.editing_vacant_events_preview = False
                        st.rerun()
                    if st.button("Cancel", icon=":material/close:", key="cancel_vacant_events_edit_btn"):
                        st.session_state.editing_vacant_events_preview = False
                        st.rerun()
            else:
                st.text_area(
                    "Preview", value=preview, height=220, key="vacant_events_preview_box",
                    label_visibility="collapsed", disabled=True,
                )
                with st.container(horizontal=True):
                    if st.button("Edit", icon=":material/edit:", key="edit_vacant_events_btn"):
                        st.session_state.editing_vacant_events_preview = True
                        st.rerun()
                    if st.button(
                        "Send to Discord", icon=":material/send:", type="primary", key="send_vacant_events_btn",
                    ):
                        send_discord_message(preview, channel="competitions")
                        invalidate_cache()
                        st.session_state.vacant_events_preview = None
                        st.toast("Sent to Discord!", icon=":material/check_circle:")
                        st.rerun()
                    if st.button("Discard", icon=":material/close:", key="discard_vacant_events_btn"):
                        st.session_state.vacant_events_preview = None
                        st.rerun()


# st.segmented_control, not st.tabs(): tabs reset back to the FIRST one on
# any rerun triggered from inside a tab's own content (confirmed live
# 2026-09-15 — Generate/Preview/Edit/Send all call st.rerun(), so a host
# working in, say, the Dashboard channel got silently bounced back to
# Competitions, with their generated draft written into the channel
# they'd left rather than the one now showing). segmented_control is a
# real widget whose selection lives in session_state, so — like
# st.selectbox, which this replaced as a first fix — it survives a rerun
# triggered by anything else on the page. Chosen over selectbox here
# since there are only 4 channels: seeing all of them at once and
# switching in one click reads clearer than a dropdown for that few.
selected_channel = st.segmented_control(
    "Channel", list(CHANNEL_LABELS.keys()), format_func=lambda c: CHANNEL_LABELS[c],
    default="competitions", required=True, key="discord_messages_selected_channel",
)

st.caption(CHANNEL_INTRO[selected_channel])
if selected_channel == "competitions":
    render_vacant_events_reminder()
render_custom_message_section(selected_channel)

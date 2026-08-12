# Host-only viewer for what the AI actually said — both surfaces, in one
# place. The two logs it reads answer different questions, so they're
# separate tabs rather than one merged list:
#
#   ai_chat_messages   — the AI's own conversations (question + reply),
#                        from the Discord bot AND the dashboard's AI
#                        Assistant page. This is the one for "the bot gave
#                        a wrong/weird answer, what exactly did it say?"
#   discord_channel_log — every message the bot can SEE in a channel,
#                        whether or not it was talking. This is the one
#                        for "what led up to that answer?"
#
# Both tables already existed and were being written to; without this page
# reviewing them meant opening Supabase directly, which is exactly the
# friction that stops anyone actually checking the bot's output.
#
# Read-only by design: nothing here edits or deletes. Deleting a Discord
# message the app SENT is a different thing and already lives on the
# Discord Messages page.

from datetime import timedelta

import streamlit as st

from shared import IST, cached_table, format_ist, today_ist

is_host = st.session_state.is_host
user_name_by_id = st.session_state.user_name_by_id

st.title("AI Logs")

if not is_host:
    st.info(":material/lock: Host-only page.")
    st.stop()

st.caption(
    "Everything the AI has said, on Discord and on the AI Assistant page — "
    "so a wrong or odd answer can actually be looked at rather than just "
    "reported second-hand."
)


def _safe_table(name):
    # Same reasoning as shared.meeting_invitee_rows: app code and SQL
    # migrations ship separately, so a table that hasn't been created yet
    # should show an empty tab, not crash the page.
    try:
        return cached_table(name)
    except Exception:
        return None


def _display_names_by_discord_id(channel_rows):
    # ai_chat_messages stores only a numeric discord_user_id, so a Discord
    # question from anyone who hasn't linked their account would otherwise
    # show as a bare 18-digit number. discord_channel_log DOES capture the
    # display name, and the same person appears in both, so it's used as a
    # lookup here rather than adding a column and backfilling.
    names = {}
    for r in channel_rows or []:
        if r.get("discord_user_id") and r.get("discord_display_name"):
            names[r["discord_user_id"]] = r["discord_display_name"]
    return names


def _who(row, discord_names=None):
    # The two tables name the linked account differently: ai_chat_messages
    # uses user_id, discord_channel_log uses linked_user_id. Both are only
    # set for members who've linked their Discord account, which most
    # haven't — hence the display-name fallbacks after it.
    named = user_name_by_id.get(row.get("user_id") or row.get("linked_user_id"))
    if named:
        return named
    if row.get("discord_display_name"):
        return row["discord_display_name"]
    discord_id = row.get("discord_user_id")
    if discord_id and discord_names:
        return discord_names.get(discord_id, discord_id)
    return discord_id or "Unknown"


tab_chats, tab_channel = st.tabs(["AI conversations", "Discord channel log"])


with tab_chats:
    rows = _safe_table("ai_chat_messages")
    if rows is None:
        st.warning(
            ":material/database: The `ai_chat_messages` table doesn't exist yet — "
            "run the block for it in `supabase_schema.sql` in Supabase's SQL editor."
        )
    elif not rows:
        st.caption("Nothing logged yet.")
    else:
        discord_names = _display_names_by_discord_id(_safe_table("discord_channel_log"))
        fcol1, fcol2 = st.columns([1, 2])
        source = fcol1.segmented_control(
            "Where", ["All", "Discord", "Dashboard"], default="All", key="ai_logs_source",
        ) or "All"
        search = fcol2.text_input(
            "Search", key="ai_logs_search", placeholder="Find a word in a question or reply…",
        ).strip().lower()

        shown = rows
        if source != "All":
            shown = [r for r in shown if r.get("source") == source.lower()]
        if search:
            shown = [r for r in shown if search in (r.get("content") or "").lower()]

        # Newest first: a host opening this is almost always chasing
        # something that just happened, not reading from the beginning.
        shown = sorted(shown, key=lambda r: r.get("created_at") or "", reverse=True)
        st.caption(f"{len(shown)} of {len(rows)} messages")

        for r in shown[:200]:
            is_assistant = r.get("role") == "assistant"
            with st.container(border=True):
                head = (
                    f":material/smart_toy: **AI** · {r.get('source', '?')}"
                    if is_assistant
                    else f":material/person: **{_who(r, discord_names)}** · {r.get('source', '?')}"
                )
                st.caption(f"{head} · {format_ist(r['created_at'])}")
                st.write(r.get("content") or "")
        if len(shown) > 200:
            st.caption("Showing the 200 most recent — narrow it down with the search box.")


with tab_channel:
    rows = _safe_table("discord_channel_log")
    if rows is None:
        st.warning(
            ":material/database: The `discord_channel_log` table doesn't exist yet — "
            "run the block for it in `supabase_schema.sql` in Supabase's SQL editor."
        )
    elif not rows:
        st.caption("Nothing logged yet.")
    else:
        st.caption(
            "Every message the bot can see in a channel — not just ones aimed at it. "
            "Useful for seeing the conversation around a bad answer."
        )
        dcol1, dcol2 = st.columns([1, 2])
        days = dcol1.selectbox(
            "Last", [1, 3, 7, 30, 365], index=2,
            format_func=lambda d: "24 hours" if d == 1 else (f"{d} days" if d < 365 else "year"),
            key="ai_logs_days",
        )
        search = dcol2.text_input(
            "Search", key="ai_logs_channel_search", placeholder="Find a word in a message…",
        ).strip().lower()

        cutoff = (today_ist() - timedelta(days=days)).isoformat()
        shown = [r for r in rows if (r.get("created_at") or "") >= cutoff]
        if search:
            shown = [r for r in shown if search in (r.get("content") or "").lower()]
        shown = sorted(shown, key=lambda r: r.get("created_at") or "", reverse=True)

        st.caption(f"{len(shown)} messages")
        for r in shown[:300]:
            edited = " *(edited)*" if r.get("was_edited") else ""
            st.markdown(
                f"**{_who(r)}** · {format_ist(r['created_at'])}{edited}  \n{r.get('content') or ''}"
            )
        if len(shown) > 300:
            st.caption("Showing the 300 most recent — narrow it down with the search box.")

# Achievements: any member logs their own result for a competition event
# they went to — position, and optionally a link to photos/video (not a
# real file upload; this app has no file storage set up, just links like
# every other link field in this app). Self-reported, like most of this
# app; the poster or a host can delete an entry.
#
# The reminder that nudges people to actually do this lives in
# send_achievement_reminders.py, run by GitHub Actions at 4pm IST on the
# competition's own day — this page only has to display things, not chase
# anyone.

import streamlit as st

from shared import get_client

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
user_name_by_id = st.session_state.user_name_by_id

st.title("Achievements")

if "achievement_message" not in st.session_state:
    st.session_state.achievement_message = None

if st.session_state.achievement_message:
    st.toast(st.session_state.achievement_message, icon=":material/emoji_events:")
    st.session_state.achievement_message = None

# --- Add my achievement ------------------------------------------------------
# Pick a competition, then an event under it — same two-level structure as
# browsing on the Competitions page.

competitions = client.table("competitions").select("competition_id, name").order("name").execute().data

with st.expander(":material/add_box: Add my achievement"):
    if not competitions:
        st.caption("No competitions to pick from yet.")
    else:
        comp_options = [c["competition_id"] for c in competitions]
        comp_name_by_id = {c["competition_id"]: c["name"] for c in competitions}
        chosen_comp_id = st.selectbox(
            "Competition",
            comp_options,
            format_func=lambda cid: comp_name_by_id.get(cid, "Unknown"),
            key="new_achievement_comp",
        )

        events = (
            client.table("competition_events")
            .select("event_id, name")
            .eq("competition_id", chosen_comp_id)
            .order("name")
            .execute()
            .data
        )
        if not events:
            st.caption("This competition has no events yet.")
        else:
            event_options = [e["event_id"] for e in events]
            event_name_by_id = {e["event_id"]: e["name"] for e in events}
            chosen_event_id = st.selectbox(
                "Event",
                event_options,
                format_func=lambda eid: event_name_by_id.get(eid, "Unknown"),
                key="new_achievement_event",
            )

            position = st.text_input(
                "Position (e.g. 1st place, Finalist)", key="new_achievement_position"
            )
            media_link = st.text_input(
                "Attachment/link (optional)", key="new_achievement_media",
                placeholder="https://... (photo, video, drive folder, etc.)",
            )
            if st.button("Save", icon=":material/check:", type="primary"):
                link = media_link.strip()
                if link and not link.startswith(("http://", "https://")):
                    link = "https://" + link
                client.table("achievements").insert({
                    "user_id": current_user_id,
                    "competition_id": chosen_comp_id,
                    "event_id": chosen_event_id,
                    "position": position.strip() or None,
                    "media_link": link or None,
                }).execute()
                st.session_state.achievement_message = "Achievement added!"
                del st.session_state["new_achievement_position"]
                del st.session_state["new_achievement_media"]
                st.rerun()

# --- Browse all achievements -------------------------------------------------

st.subheader(":material/emoji_events: All achievements")

achievements = client.table("achievements").select("*").order("created_at", desc=True).execute().data

if not achievements:
    st.caption("No achievements logged yet.")
else:
    all_events = client.table("competition_events").select("event_id, name").execute().data
    event_name_by_id_all = {e["event_id"]: e["name"] for e in all_events}
    comp_name_by_id_all = {c["competition_id"]: c["name"] for c in competitions}

    for a in achievements:
        with st.container(border=True, key=f"rkcard_achievement_{a['achievement_id']}"):
            col1, col2 = st.columns([5, 1], vertical_alignment="center")
            member_name = user_name_by_id.get(a["user_id"], "Unknown")
            comp_name = comp_name_by_id_all.get(a["competition_id"], "Unknown competition")
            event_name = event_name_by_id_all.get(a["event_id"], "Unknown event")
            col1.markdown(f"**{member_name}** — {comp_name} ({event_name})")

            if a.get("position"):
                st.write(a["position"])
            if a.get("media_link"):
                st.markdown(f"[View attachment]({a['media_link']})")

            # Only the person who logged it, or a host, can remove it.
            if is_host or a["user_id"] == current_user_id:
                if col2.button("Delete", key=f"delete_achievement_{a['achievement_id']}", icon=":material/delete:"):
                    client.table("achievements").delete().eq("achievement_id", a["achievement_id"]).execute()
                    st.session_state.achievement_message = "Deleted."
                    st.rerun()

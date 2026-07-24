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

                # Auto-log the same result for every teammate (same team_no
                # on this event, from the E2C import's team grouping) who
                # hasn't already logged one themselves — so one person
                # reporting a team result doesn't mean everyone has to
                # separately do the same thing. Nothing to do if this
                # person isn't grouped into a team at all yet.
                my_row = (
                    client.table("event_volunteers")
                    .select("team_no")
                    .eq("event_id", chosen_event_id)
                    .eq("user_id", current_user_id)
                    .execute()
                    .data
                )
                team_no = my_row[0]["team_no"] if my_row else None

                auto_logged = 0
                if team_no:
                    teammates = (
                        client.table("event_volunteers")
                        .select("user_id")
                        .eq("event_id", chosen_event_id)
                        .eq("team_no", team_no)
                        .neq("user_id", current_user_id)
                        .execute()
                        .data
                    )
                    already_logged = {
                        a["user_id"]
                        for a in client.table("achievements")
                        .select("user_id").eq("event_id", chosen_event_id).execute().data
                    }
                    for t in teammates:
                        if t["user_id"] in already_logged:
                            continue
                        client.table("achievements").insert({
                            "user_id": t["user_id"],
                            "competition_id": chosen_comp_id,
                            "event_id": chosen_event_id,
                            "position": position.strip() or None,
                            "media_link": link or None,
                        }).execute()
                        auto_logged += 1

                msg = "Achievement added!"
                if auto_logged:
                    msg += f" Also logged for {auto_logged} teammate(s)."
                st.session_state.achievement_message = msg
                del st.session_state["new_achievement_position"]
                del st.session_state["new_achievement_media"]
                st.rerun()

# --- Browse all achievements -------------------------------------------------

st.subheader(":material/emoji_events: All achievements")

achievements = client.table("achievements").select("*").order("created_at", desc=True).execute().data

achievement_search = st.text_input(
    "Search achievements",
    key="achievement_search",
    placeholder="Search by member, competition, event, or position",
    icon=":material/search:",
    label_visibility="collapsed",
)

if not achievements:
    st.caption("No achievements logged yet.")
else:
    all_events = client.table("competition_events").select("event_id, name").execute().data
    event_name_by_id_all = {e["event_id"]: e["name"] for e in all_events}
    comp_name_by_id_all = {c["competition_id"]: c["name"] for c in competitions}

    # Teammates (same team_no on the same event, from the E2C import) get
    # grouped into one shared card instead of one card per person — since
    # they're the same result, logged for each of them automatically.
    # Anyone without a team_no (a manually-added competition, or before
    # Chunk 3) just gets their own card, same as before.
    team_no_by_event_user = {
        (v["event_id"], v["user_id"]): v["team_no"]
        for v in client.table("event_volunteers").select("event_id, user_id, team_no").execute().data
    }

    groups = {}
    for a in achievements:
        team_no = team_no_by_event_user.get((a["event_id"], a["user_id"]))
        key = (a["event_id"], team_no) if team_no else ("solo", a["achievement_id"])
        groups.setdefault(key, []).append(a)

    if achievement_search:
        needle = achievement_search.lower()
        groups = {
            key: group for key, group in groups.items()
            if any(
                needle in " ".join([
                    user_name_by_id.get(a["user_id"], ""),
                    comp_name_by_id_all.get(a["competition_id"], ""),
                    event_name_by_id_all.get(a["event_id"], ""),
                    a.get("position") or "",
                ]).lower()
                for a in group
            )
        }

    if not groups:
        st.caption("No achievements match your search.")

    for group in groups.values():
        first = group[0]
        with st.container(border=True, key=f"rkcard_achievement_{first['achievement_id']}"):
            col1, col2 = st.columns([5, 1], vertical_alignment="center")
            member_names = ", ".join(user_name_by_id.get(a["user_id"], "Unknown") for a in group)
            comp_name = comp_name_by_id_all.get(first["competition_id"], "Unknown competition")
            event_name = event_name_by_id_all.get(first["event_id"], "Unknown event")
            col1.markdown(f"**{member_names}** — {comp_name} ({event_name})")

            if first.get("position"):
                st.write(first["position"])
            if first.get("media_link"):
                st.markdown(f"[View attachment]({first['media_link']})")

            # Only a host, or anyone actually in this result, can remove it
            # — removing it takes out every teammate's entry together.
            can_delete = is_host or any(a["user_id"] == current_user_id for a in group)
            if can_delete:
                if col2.button("Delete", key=f"delete_achievement_{first['achievement_id']}", icon=":material/delete:"):
                    for a in group:
                        client.table("achievements").delete().eq("achievement_id", a["achievement_id"]).execute()
                    st.session_state.achievement_message = "Deleted."
                    st.rerun()

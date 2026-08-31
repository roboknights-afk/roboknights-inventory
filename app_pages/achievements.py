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

from shared import (
    cached_table, get_client, invalidate_cache, safe_write, send_discord_message,
)

client = get_client()

# Interschool / National / International / Regional - the same buckets the
# website's homepage already counts by (see data/record.ts's INTERNATIONAL
# figure there). Asked for at the same time as position now; before this,
# nothing anywhere in this schema tracked it at all.
LEVEL_OPTIONS = ["Interschool", "National", "International", "Regional"]

is_host = st.session_state.is_host
is_exun = st.session_state.is_exun
is_read_only = st.session_state.is_read_only
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

competitions = sorted(cached_table("competitions"), key=lambda c: (c["name"], c["competition_id"]))

# --- At-a-glance numbers -----------------------------------------------------
all_achievements = cached_table("achievements")
my_achievement_count = sum(1 for a in all_achievements if a["user_id"] == current_user_id)

m1, m2, m3 = st.columns(3)
m1.metric("Logged results", len(all_achievements), border=True)
m2.metric("Mine", my_achievement_count, border=True)
m3.metric(
    "Events represented",
    len({a["event_id"] for a in all_achievements}),
    border=True,
    help="Distinct competition events with a result logged",
)

# Logging a result used to be a second tab sitting permanently beside the
# browse list. It's a dialog now, so this page is just the list plus one
# button. Held open by a flag rather than called straight from the button:
# the st.rerun() at the end is a full-app rerun, so the dialog has to be
# re-called each run to stay up, and clearing the flag is what closes it.
if "show_log_achievement" not in st.session_state:
    st.session_state.show_log_achievement = False


def _close_log_achievement():
    st.session_state.show_log_achievement = False


def _open_log_achievement():
    st.session_state.show_log_achievement = True


def _notify_achievement(user_ids, comp_name, event_name, position, link):
    # Posted to the club's #wins-and-appreciation Discord channel the
    # moment a result is logged. ONE message per result, not per person — a team
    # that logged together is one achievement with several names on it,
    # and three identical posts would just be noise.
    #
    # Best-effort like every other Discord sender in this app: a webhook
    # failure (or the webhook simply not being configured yet) must never
    # turn a successfully-logged achievement into an error on screen.
    try:
        if not user_ids:
            return
        names = ", ".join(
            sorted(user_name_by_id.get(uid, "Unknown") for uid in user_ids)
        )
        lines = [
            ":trophy: **New achievement logged**",
            "",
            f"**{names}**",
            f"{comp_name} — {event_name}",
        ]
        if position:
            lines.append(f"Position: **{position}**")
        if link:
            # Wrapped in <> so Discord doesn't expand it into a big
            # preview embed, same as the bot's search-source links.
            lines.append(f"Attachment: <{link}>")
        send_discord_message("\n".join(lines), channel="wins")
    except Exception:
        pass


@st.dialog("Log a result", width="large", on_dismiss=_close_log_achievement)
def render_log_achievement():
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

        events = sorted(
            (e for e in cached_table("competition_events") if e["competition_id"] == chosen_comp_id),
            key=lambda e: (e["name"], e["event_id"]),
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

            # Hosts log on behalf of the people who actually competed —
            # names pulled automatically from the event's own volunteer
            # list (selected people pre-picked), not typed by hand, so
            # there's no name to get wrong. Falls back to the full member
            # list only when an event has no volunteers recorded at all
            # (e.g. a manually-added competition that never used the
            # volunteer flow).
            log_for_ids = None
            if is_host:
                event_vols = [
                    v for v in cached_table("event_volunteers")
                    if v["event_id"] == chosen_event_id
                ]
                if event_vols:
                    onbehalf_options = [v["user_id"] for v in event_vols]
                    onbehalf_default = [v["user_id"] for v in event_vols if v.get("selected")]
                    st.caption(
                        ":material/group: Names pulled automatically from this "
                        "event's volunteers — selected people are pre-picked."
                    )
                else:
                    onbehalf_options = list(user_name_by_id.keys())
                    onbehalf_default = []
                    st.caption(
                        ":material/info: No volunteers recorded for this event — "
                        "pick from the full member list instead."
                    )
                log_for_ids = st.multiselect(
                    "Log this result for",
                    options=onbehalf_options,
                    default=onbehalf_default,
                    format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                    key=f"ach_log_for_{chosen_event_id}",
                )

            pos_col, level_col = st.columns([2, 1])
            position = pos_col.text_input(
                "Position (e.g. 1st place, Finalist)", key="new_achievement_position"
            )
            level = level_col.selectbox(
                "Level", LEVEL_OPTIONS, key="new_achievement_level",
            )
            media_link = st.text_input(
                "Attachment/link (optional)", key="new_achievement_media",
                placeholder="https://... (photo, video, drive folder, etc.)",
            )
            if st.button("Save", icon=":material/check:", type="primary"):
                if is_host and not log_for_ids:
                    st.error("Pick at least one person to log this result for.")
                else:
                    link = media_link.strip()
                    if link and not link.startswith(("http://", "https://")):
                        link = "https://" + link

                    # Host path: one row per chosen person, skipping
                    # anyone who already has a result for this exact
                    # event (so re-logging can't duplicate). Member
                    # path: yourself, same skip rule.
                    #
                    # This read is deliberately NOT cached_table: it
                    # decides who to skip as already-logged, so it needs
                    # the true current state rather than up to 8s old.
                    targets = log_for_ids if is_host else [current_user_id]

                    with safe_write("log this achievement"):
                        already_logged_ids = {
                            a["user_id"]
                            for a in client.table("achievements")
                            .select("user_id").eq("event_id", chosen_event_id).execute().data
                        }
                        # Tracked as ids, not just a count, so the Discord
                        # post below can name exactly who ended up on this
                        # result — skipped duplicates included in neither.
                        logged_ids = []
                        for uid in targets:
                            if uid in already_logged_ids:
                                continue
                            client.table("achievements").insert({
                                "user_id": uid,
                                "competition_id": chosen_comp_id,
                                "event_id": chosen_event_id,
                                "position": position.strip() or None,
                                "level": level,
                                "media_link": link or None,
                            }).execute()
                            logged_ids.append(uid)
                        logged = len(logged_ids)

                        # Auto-log the same result for every teammate (same
                        # team_no on this event, from the E2C import's team
                        # grouping) who hasn't already logged one themselves —
                        # so one person reporting a team result doesn't mean
                        # everyone has to separately do the same thing. Only
                        # applies to a member logging their OWN result — a
                        # host already picked the exact people above, so
                        # nothing extra should be implied.
                        auto_logged_ids = []
                        if not is_host:
                            my_row = (
                                client.table("event_volunteers")
                                .select("team_no")
                                .eq("event_id", chosen_event_id)
                                .eq("user_id", current_user_id)
                                .execute()
                                .data
                            )
                            team_no = my_row[0]["team_no"] if my_row else None
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
                                        "level": level,
                                        "media_link": link or None,
                                    }).execute()
                                    auto_logged_ids.append(t["user_id"])
                        auto_logged = len(auto_logged_ids)

                        invalidate_cache()

                        # Everyone who actually ended up on this result —
                        # the people logged for, plus any teammates it was
                        # auto-logged for — in one post.
                        _notify_achievement(
                            logged_ids + auto_logged_ids,
                            comp_name_by_id.get(chosen_comp_id, "Unknown competition"),
                            event_name_by_id.get(chosen_event_id, "Unknown event"),
                            position.strip(),
                            link,
                        )
                    skipped = len(targets) - logged
                    if is_host:
                        msg = f"Logged this result for {logged} member(s)."
                    elif logged:
                        msg = "Achievement added!"
                    else:
                        msg = "You already have a result for this event — nothing new added."
                    if auto_logged:
                        msg += f" Also logged for {auto_logged} teammate(s)."
                    if skipped and is_host:
                        msg += f" Skipped {skipped} who already had a result for this event."
                    st.session_state.achievement_message = msg
                    # Clear the form so reopening the dialog starts blank.
                    st.session_state.pop("new_achievement_position", None)
                    st.session_state.pop("new_achievement_media", None)
                    _close_log_achievement()
                    st.rerun()


# Exun can browse, but never logs a result themselves — so they don't get the
# button at all, rather than one that refuses to do anything.
if not is_read_only:
    st.button(
        "Log a result", icon=":material/add_box:", type="primary",
        key="open_log_achievement", on_click=_open_log_achievement,
    )
    if st.session_state.show_log_achievement:
        render_log_achievement()

# Plain container (not a tab any more) so the browse list below keeps its
# indentation now that "Log a result" has moved into a dialog.
browse = st.container()

with browse:
    # --- Browse all achievements -------------------------------------------------

    st.subheader(":material/emoji_events: All achievements")

    achievements = sorted(all_achievements, key=lambda a: a["created_at"], reverse=True)

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
        event_name_by_id_all = {e["event_id"]: e["name"] for e in cached_table("competition_events")}
        comp_name_by_id_all = {c["competition_id"]: c["name"] for c in competitions}

        # Teammates (same team_no on the same event, from the E2C import) get
        # grouped into one shared card instead of one card per person — since
        # they're the same result, logged for each of them automatically.
        # Anyone without a team_no (a manually-added competition, or before
        # Chunk 3) just gets their own card, same as before.
        team_no_by_event_user = {
            (v["event_id"], v["user_id"]): v["team_no"] for v in cached_table("event_volunteers")
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
                    # A medal-colored badge reads much faster than plain text
                    # when scanning the list — gold (our primary) for a win,
                    # then per-place colors, grey for everything else
                    # (finalist, participation, etc.).
                    pos_lower = first["position"].lower()
                    if "1st" in pos_lower or "first" in pos_lower or "winner" in pos_lower:
                        st.badge(first["position"], color="primary", icon=":material/emoji_events:")
                    elif "2nd" in pos_lower or "second" in pos_lower:
                        st.badge(first["position"], color="blue", icon=":material/military_tech:")
                    elif "3rd" in pos_lower or "third" in pos_lower:
                        st.badge(first["position"], color="orange", icon=":material/military_tech:")
                    else:
                        st.badge(first["position"], color="grey", icon=":material/military_tech:")
                if first.get("media_link"):
                    st.markdown(f"[View attachment]({first['media_link']})")

                # Only a host, or anyone actually in this result, can remove it
                # — removing it takes out every teammate's entry together.
                can_delete = is_host or any(a["user_id"] == current_user_id for a in group)
                if can_delete:
                    if col2.button("Delete", key=f"delete_achievement_{first['achievement_id']}", icon=":material/delete:"):
                        with safe_write("delete this achievement"):
                            for a in group:
                                client.table("achievements").delete().eq("achievement_id", a["achievement_id"]).execute()
                            invalidate_cache()
                        st.session_state.achievement_message = "Deleted."
                        st.rerun()

                # Host-only: level + whether this is allowed onto the public
                # website. Applies to the whole group at once (a team's
                # result is one fact, not one per person) — same reasoning
                # as delete already grouping them. published_on_website is
                # a host's explicit call, not something logging a result
                # grants on its own — see the schema migration's comment on
                # why that's deliberate.
                if is_host:
                    already_published = bool(first.get("published_on_website"))
                    with st.expander(
                        ":material/public: Published on website" if already_published
                        else ":material/public_off: Not on website yet"
                    ):
                        lcol, pcol = st.columns([2, 1], vertical_alignment="bottom")
                        chosen_level = lcol.selectbox(
                            "Level", LEVEL_OPTIONS,
                            index=(
                                LEVEL_OPTIONS.index(first["level"])
                                if first.get("level") in LEVEL_OPTIONS else 0
                            ),
                            key=f"ach_level_{first['achievement_id']}",
                        )
                        publish = pcol.checkbox(
                            "On website", value=already_published,
                            key=f"ach_publish_{first['achievement_id']}",
                        )
                        if st.button(
                            "Save", icon=":material/save:",
                            key=f"ach_save_{first['achievement_id']}",
                        ):
                            with safe_write("update this result's website status"):
                                for a in group:
                                    client.table("achievements").update({
                                        "level": chosen_level,
                                        "published_on_website": publish,
                                    }).eq("achievement_id", a["achievement_id"]).execute()
                                invalidate_cache()
                            st.session_state.achievement_message = "Saved."
                            st.rerun()

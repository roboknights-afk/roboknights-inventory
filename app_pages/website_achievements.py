# Half of the host review queue for the public website — the other half
# is app_pages/website_members.py. Split into two pages rather than one
# long page with two sections, so reviewing results and reviewing member
# profiles don't sit in each other's way.
#
# Host-only. Not linked from anywhere a non-host would see it — app.py
# only adds this page to the nav for hosts — but guarded here too, same
# reasoning as the Members page: st.navigation's page list isn't the only
# way in if someone hits the URL directly.

import streamlit as st

from shared import cached_table, get_client, invalidate_cache, safe_write

is_host = st.session_state.is_host
if not is_host:
    st.error("Access only for hosts.")
    st.stop()

client = get_client()
LEVEL_OPTIONS = ["Interschool", "National", "International", "Regional"]

st.title(":material/emoji_events: Website: Results")
st.caption(
    "Every result a member has logged, waiting on a level, position and a "
    "yes or no before it can reach roboknights.in."
)

if "website_review_message" not in st.session_state:
    st.session_state.website_review_message = None
if st.session_state.website_review_message:
    st.toast(st.session_state.website_review_message, icon=":material/emoji_events:")
    st.session_state.website_review_message = None

all_achievements = cached_table("achievements")
comp_name_by_id = {c["competition_id"]: c["name"] for c in cached_table("competitions")}
event_name_by_id = {e["event_id"]: e["name"] for e in cached_table("competition_events")}
user_name_by_id = st.session_state.user_name_by_id

# One card per actual result, not per person - the same (competition,
# event, position) grouping export_achievements.py uses, so what a host
# reviews here is exactly what would become one website entry.
ach_groups = {}
for a in all_achievements:
    key = (a["competition_id"], a["event_id"], a.get("position"))
    ach_groups.setdefault(key, []).append(a)

ach_pending = [g for g in ach_groups.values() if (g[0].get("website_status") or "pending") == "pending"]
ach_decided = [g for g in ach_groups.values() if (g[0].get("website_status") or "pending") != "pending"]

m1, m2, m3 = st.columns(3)
m1.metric("Waiting for review", len(ach_pending), border=True)
m2.metric("Approved", sum(1 for g in ach_decided if g[0]["website_status"] == "approved"), border=True)
m3.metric("Declined", sum(1 for g in ach_decided if g[0]["website_status"] == "declined"), border=True)

show_all = st.toggle("Show already-reviewed results too", key="ach_show_reviewed")
ach_list = ach_groups.values() if show_all else ach_pending

if not ach_list:
    st.caption("Nothing waiting on a result right now.")
else:
    for group in ach_list:
        first = group[0]
        status = first.get("website_status") or "pending"
        names = ", ".join(user_name_by_id.get(a["user_id"], "Unknown") for a in group)
        comp_name = comp_name_by_id.get(first["competition_id"], "Unknown competition")
        event_name = event_name_by_id.get(first["event_id"], "Unknown event")

        with st.container(border=True, key=f"rkcard_review_ach_{first['achievement_id']}"):
            st.markdown(f"**{names}** — {comp_name} ({event_name})")

            pcol, lcol = st.columns([2, 1])
            new_position = pcol.text_input(
                "Position", value=first.get("position") or "",
                key=f"review_pos_{first['achievement_id']}",
                disabled=status != "pending",
            )
            new_level = lcol.selectbox(
                "Level", LEVEL_OPTIONS,
                index=LEVEL_OPTIONS.index(first["level"]) if first.get("level") in LEVEL_OPTIONS else 0,
                key=f"review_level_{first['achievement_id']}",
                disabled=status != "pending",
            )

            if status == "pending":
                note = st.text_input(
                    "Note (only needed if declining — shown back to whoever logged it)",
                    key=f"review_note_ach_{first['achievement_id']}",
                )
                bcol1, bcol2 = st.columns(2)
                if bcol1.button(
                    "Approve", icon=":material/check_circle:", type="primary",
                    key=f"review_approve_ach_{first['achievement_id']}",
                ):
                    with safe_write("approve this result for the website"):
                        for a in group:
                            client.table("achievements").update({
                                "position": new_position.strip() or None,
                                "level": new_level,
                                "website_status": "approved",
                                "website_note": note.strip() or None,
                            }).eq("achievement_id", a["achievement_id"]).execute()
                        invalidate_cache()
                    st.session_state.website_review_message = f"Approved {event_name}."
                    st.rerun()
                if bcol2.button(
                    "Decline", icon=":material/cancel:",
                    key=f"review_decline_ach_{first['achievement_id']}",
                ):
                    with safe_write("decline this result for the website"):
                        for a in group:
                            client.table("achievements").update({
                                "website_status": "declined",
                                "website_note": note.strip() or None,
                            }).eq("achievement_id", a["achievement_id"]).execute()
                        invalidate_cache()
                    st.session_state.website_review_message = f"Declined {event_name}."
                    st.rerun()
            else:
                badge = (
                    (":material/public: Approved — will appear on the site")
                    if status == "approved"
                    else ":material/public_off: Declined"
                )
                st.caption(badge + (f" — {first['website_note']}" if first.get("website_note") else ""))

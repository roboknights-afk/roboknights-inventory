# The one place a host reviews everything a member has submitted for the
# public website — a logged result, or a photo + social handles — before
# any of it reaches roboknights.in. Same approve/decline/edit model for
# both, side by side, rather than scattered across the pages members
# actually use.
#
# Host-only. Not linked from anywhere a non-host would see it — app.py
# only adds this page to the nav for hosts — but guarded here too, same
# reasoning as the Members page: st.navigation's page list isn't the only
# way in if someone hits the URL directly.

import streamlit as st

from shared import cached_table, get_client, get_storage_client, invalidate_cache, safe_write

is_host = st.session_state.is_host
if not is_host:
    st.error("Access only for hosts.")
    st.stop()

client = get_client()
# NOT client.storage - see get_storage_client()'s own comment in shared.py
# for the full explanation, but the short version: this page needs to read
# OTHER people's photos, not your own, and the storage bucket's policies
# only ever let someone read their own file. Even a correctly-working
# per-user identity wouldn't be enough here - only the service-role client
# can see everyone's. Same client profile.py uses for its own upload/
# download, for the same underlying reason.
storage = get_storage_client()
LEVEL_OPTIONS = ["Interschool", "National", "International", "Regional"]

st.title("Website")
st.caption(
    "Everything here started as a member submitting something — a result, "
    "or their photo and links. Nothing reaches roboknights.in until it's "
    "approved here."
)

if "website_review_message" not in st.session_state:
    st.session_state.website_review_message = None
if st.session_state.website_review_message:
    st.toast(st.session_state.website_review_message, icon=":material/public:")
    st.session_state.website_review_message = None


# --- Achievements ------------------------------------------------------------

st.subheader(":material/emoji_events: Results")

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

am1, am2, am3 = st.columns(3)
am1.metric("Waiting for review", len(ach_pending), border=True)
am2.metric("Approved", sum(1 for g in ach_decided if g[0]["website_status"] == "approved"), border=True)
am3.metric("Declined", sum(1 for g in ach_decided if g[0]["website_status"] == "declined"), border=True)

ach_show_all = st.toggle("Show already-reviewed results too", key="ach_show_reviewed")
ach_list = ach_groups.values() if ach_show_all else ach_pending

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


# --- Member profiles -----------------------------------------------------

st.divider()
st.subheader(":material/badge: Member photos & links")

all_users = cached_table("users")
# Only people who actually asked to be shown - a member who never ticked
# the box on Your profile never appears here at all.
requested = [u for u in all_users if u.get("photo_public")]
mem_pending = [u for u in requested if (u.get("website_status") or "pending") == "pending"]
mem_decided = [u for u in requested if (u.get("website_status") or "pending") != "pending"]

mm1, mm2, mm3 = st.columns(3)
mm1.metric("Waiting for review", len(mem_pending), border=True)
mm2.metric("Approved", sum(1 for u in mem_decided if u["website_status"] == "approved"), border=True)
mm3.metric("Declined", sum(1 for u in mem_decided if u["website_status"] == "declined"), border=True)

mem_show_all = st.toggle("Show already-reviewed profiles too", key="mem_show_reviewed")
mem_list = requested if mem_show_all else mem_pending

if not mem_list:
    st.caption("Nothing waiting on a member profile right now.")
else:
    BUCKET = "member-photos"
    if storage is None:
        st.warning(
            "Can't load photos here — SUPABASE_SERVICE_KEY is missing on "
            "this server. Everything else on this page still works."
        )
    for u in mem_list:
        status = u.get("website_status") or "pending"
        with st.container(border=True, key=f"rkcard_review_user_{u['user_id']}"):
            pcol, dcol = st.columns([1, 4])
            if u.get("photo_path") and storage is not None:
                try:
                    pcol.image(storage.storage.from_(BUCKET).download(u["photo_path"]), width=90)
                except Exception as error:
                    pcol.caption(f"Photo unavailable ({error})")
            with dcol:
                st.markdown(f"**{u['name']}**")
                ig = st.text_input(
                    "Instagram", value=u.get("instagram") or "",
                    key=f"review_ig_{u['user_id']}", disabled=status != "pending",
                )
                li = st.text_input(
                    "LinkedIn", value=u.get("linkedin") or "",
                    key=f"review_li_{u['user_id']}", disabled=status != "pending",
                )
                gh = st.text_input(
                    "GitHub", value=u.get("github") or "",
                    key=f"review_gh_{u['user_id']}", disabled=status != "pending",
                )

            if status == "pending":
                note = st.text_input(
                    "Note (only needed if declining — shown back to them)",
                    key=f"review_note_user_{u['user_id']}",
                )
                bcol1, bcol2 = st.columns(2)
                if bcol1.button(
                    "Approve", icon=":material/check_circle:", type="primary",
                    key=f"review_approve_user_{u['user_id']}",
                ):
                    with safe_write("approve this profile for the website"):
                        client.table("users").update({
                            "instagram": ig.strip(),
                            "linkedin": li.strip(),
                            "github": gh.strip(),
                            "website_status": "approved",
                            "website_note": note.strip() or None,
                        }).eq("user_id", u["user_id"]).execute()
                        invalidate_cache()
                    st.session_state.website_review_message = f"Approved {u['name']}."
                    st.rerun()
                if bcol2.button(
                    "Decline", icon=":material/cancel:",
                    key=f"review_decline_user_{u['user_id']}",
                ):
                    with safe_write("decline this profile for the website"):
                        client.table("users").update({
                            "website_status": "declined",
                            "website_note": note.strip() or None,
                        }).eq("user_id", u["user_id"]).execute()
                        invalidate_cache()
                    st.session_state.website_review_message = f"Declined {u['name']}."
                    st.rerun()
            else:
                badge = (
                    ":material/public: Approved — will appear on the site"
                    if status == "approved"
                    else ":material/public_off: Declined"
                )
                st.caption(badge + (f" — {u['website_note']}" if u.get("website_note") else ""))

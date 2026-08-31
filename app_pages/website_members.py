# Half of the host review queue for the public website — the other half
# is app_pages/website_achievements.py. Split into two pages rather than
# one long page with two sections, so reviewing photos/links and
# reviewing results don't sit in each other's way.
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
# NOT client.storage - this page needs to read OTHER people's photos, not
# your own, and the storage bucket's policies only ever let someone read
# their own file. Even a correctly-working per-user identity wouldn't be
# enough here - only the service-role client, which bypasses those
# policies by design, can see everyone's. Full explanation in
# get_storage_client()'s own comment in shared.py. Same client
# app_pages/profile.py uses for its own upload/download.
storage = get_storage_client()
BUCKET = "member-photos"

st.title("Website: Members")
st.caption(
    "Every member who has asked to be shown on roboknights.in — their "
    "photo and social handles, waiting for a yes or no."
)

if "website_review_message" not in st.session_state:
    st.session_state.website_review_message = None
if st.session_state.website_review_message:
    st.toast(st.session_state.website_review_message, icon=":material/badge:")
    st.session_state.website_review_message = None

all_users = cached_table("users")
# Only people who actually asked to be shown - a member who never ticked
# the box on Your profile never appears here at all.
requested = [u for u in all_users if u.get("photo_public")]
mem_pending = [u for u in requested if (u.get("website_status") or "pending") == "pending"]
mem_decided = [u for u in requested if (u.get("website_status") or "pending") != "pending"]

m1, m2, m3 = st.columns(3)
m1.metric("Waiting for review", len(mem_pending), border=True)
m2.metric("Approved", sum(1 for u in mem_decided if u["website_status"] == "approved"), border=True)
m3.metric("Declined", sum(1 for u in mem_decided if u["website_status"] == "declined"), border=True)

show_all = st.toggle("Show already-reviewed profiles too", key="mem_show_reviewed")
mem_list = requested if show_all else mem_pending

if not mem_list:
    st.caption("Nothing waiting on a member profile right now.")
else:
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

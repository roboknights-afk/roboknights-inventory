# Host-only member directory — Exun (RoboKnights' sister club) also gets
# read-only access to the same full data, per an explicit call, but can't
# edit anything. Not linked from anywhere a non-host/non-Exun would see
# it — app.py only adds this page to the nav at all for those two groups —
# but guarded here too in case someone hits the URL directly, since that's
# the one thing st.navigation's page list alone doesn't stop.

import streamlit as st

from shared import cached_table, get_client, invalidate_cache, safe_write

is_host = st.session_state.is_host
is_exun = st.session_state.is_exun

if not is_host and not is_exun:
    st.error("Access only for hosts and Exun.")
    st.stop()

client = get_client()

st.title("Members")

if "member_edit_message" not in st.session_state:
    st.session_state.member_edit_message = None
if "confirming_delete_member_id" not in st.session_state:
    st.session_state.confirming_delete_member_id = None

if st.session_state.member_edit_message:
    st.toast(st.session_state.member_edit_message, icon=":material/check_circle:")
    st.session_state.member_edit_message = None

# Sorted by name with user_id as a tiebreak — two members can share a name
# (there are duplicates in the real data), and without a deterministic
# secondary key which one shows first would be an unreliable storage-order
# artifact rather than something predictable.
users = sorted(cached_table("users"), key=lambda u: (u["name"], u["user_id"]))

# --- At-a-glance numbers -----------------------------------------------------
GRADE_OPTIONS = [7, 8, 9, 10, 11, 12]
# Maps the raw DB value (see supabase_schema.sql's users_role_check) to
# what's actually shown/picked in the UI, and back. "" is its own option
# (not just "no selection") since a member with no standing set yet is a
# real, common state — most members had none until a host went through
# and set them by hand — not something to force a choice on immediately.
STANDING_LABELS = {"core_member": "Core member", "member": "Member", "adhoc": "Ad hoc", None: ""}
STANDING_VALUES = {v: k for k, v in STANDING_LABELS.items()}
m1, m2, m3, m4 = st.columns(4)
m1.metric("Members", len(users), border=True)
m2.metric(
    "Grades represented", len({u.get("grade") for u in users if u.get("grade")}), border=True
)
m3.metric(
    "Missing details",
    sum(
        1 for u in users
        if not u.get("is_staff")
        and not (u.get("grade") and u.get("section") and u.get("admission_no") and u.get("phone_no"))
    ),
    border=True,
    help="Students with at least one blank field (staff accounts aren't expected to have grade/section/admission no.)",
)
m4.metric(
    "Verified",
    sum(1 for u in users if u.get("details_verified")),
    border=True,
    help="Students who've confirmed their details in the mandatory \"Verify your details\" popup.",
)

with st.expander(":material/info: About this page"):
    st.markdown(
        "Grade, section, admission no., and phone no. are private to each member "
        "everywhere else in the app — this directory (visible to hosts and Exun) "
        "is the one place they're shown together."
    )
    if not is_exun:
        st.warning(
            "Editing **Institutional email** here only updates this profile record. "
            "It does **not** change their actual login email in Supabase Auth, which "
            "is a separate system this app doesn't have admin access to."
        )

if not users:
    st.caption("No members yet.")
else:
    disabled_users = [u for u in users if u.get("is_disabled")]
    if disabled_users:
        st.warning(
            "**Account(s) disabled:** " + ", ".join(u["name"] for u in disabled_users)
            + " — blocked from logging in until re-enabled below."
        )

    # Search + grade + standing filters above the table.
    search_col, grade_col, standing_col = st.columns([2, 1, 1], vertical_alignment="center")
    member_search = search_col.text_input(
        "Search members",
        key="member_search",
        placeholder="Search by name, email, section or admission no.",
        icon=":material/search:",
        label_visibility="collapsed",
    )
    grade_filter = grade_col.multiselect(
        "Filter by grade", GRADE_OPTIONS, key="member_grade_filter",
        placeholder="All grades", label_visibility="collapsed",
    )
    standing_filter = standing_col.multiselect(
        "Filter by standing",
        ["Core member", "Member", "Ad hoc", "Not set"],
        key="member_standing_filter",
        placeholder="All standings", label_visibility="collapsed",
    )

    visible_users = []
    for u in users:
        haystack = " ".join([
            u["name"], u["email"], str(u.get("section") or ""), str(u.get("admission_no") or ""),
        ]).lower()
        if member_search and member_search.lower() not in haystack:
            continue
        if grade_filter and u.get("grade") not in grade_filter:
            continue
        if standing_filter:
            label = STANDING_LABELS.get(u.get("role")) or "Not set"
            if label not in standing_filter:
                continue
        visible_users.append(u)

    if not visible_users:
        st.caption("No members match your search or filter.")
        st.stop()

    st.caption(f"Showing {len(visible_users)} of {len(users)} member(s).")

    table_rows = [
        {
            "Name": u["name"],
            "Role": "Staff" if u.get("is_staff") else "Student",
            "Standing": STANDING_LABELS.get(u.get("role"), u.get("role") or ""),
            "Institutional email": u["email"],
            "Grade": u.get("grade"),
            "Section": u.get("section") or "",
            "Admission no.": u.get("admission_no") or "",
            "Phone no.": u.get("phone_no") or "",
            "Discord ID": u.get("discord_user_id") or "",
            "Verified": bool(u.get("details_verified")),
            "Note": "⚠️ Account disabled" if u.get("is_disabled") else "",
            "Disabled": bool(u.get("is_disabled")),
        }
        for u in visible_users
    ]

    if is_exun:
        # Read-only for Exun — same data a host sees, but no editing.
        st.dataframe(table_rows, hide_index=True, width="stretch")
    else:
        # Editable straight in the table. num_rows="fixed" so hosts can't
        # add/delete rows here — a "member" only ever comes from someone
        # actually signing up.
        edited_rows = st.data_editor(
            table_rows,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Name": st.column_config.TextColumn("Name", width="medium"),
                "Role": st.column_config.TextColumn("Role", width="small", disabled=True),
                "Standing": st.column_config.SelectboxColumn(
                    "Standing", width="small",
                    options=["", "Core member", "Member", "Ad hoc"],
                    help="Host-set club standing — separate from Role above, which is "
                         "just staff vs. student.",
                ),
                "Institutional email": st.column_config.TextColumn("Institutional email", width="medium"),
                "Grade": st.column_config.SelectboxColumn("Grade", options=GRADE_OPTIONS, width="small"),
                "Section": st.column_config.TextColumn("Section", width="small"),
                "Admission no.": st.column_config.TextColumn("Admission no.", width="small"),
                "Phone no.": st.column_config.TextColumn("Phone no.", width="small"),
                "Discord ID": st.column_config.TextColumn(
                    "Discord ID", width="small",
                    help="Their numeric Discord User ID — normally self-linked on the Home page.",
                ),
                "Verified": st.column_config.CheckboxColumn(
                    "Verified", width="small",
                    help="Whether they've confirmed their details in the mandatory popup. "
                         "Uncheck to make the popup reappear for them next login (e.g. so "
                         "they can add something they missed).",
                ),
                "Note": st.column_config.TextColumn("Note", width="medium", disabled=True),
                "Disabled": st.column_config.CheckboxColumn(
                    "Disabled", width="small",
                    help="Blocks them from logging in entirely (they see \"account disabled, "
                         "contact the admin\"). Doesn't touch their data — check again to "
                         "re-enable.",
                ),
            },
            key="members_editor",
        )

        if st.button("Save changes", icon=":material/check:", type="primary"):
            # Row order is preserved by data_editor, so we can zip the rows
            # that were actually shown back up with the edited ones and write
            # only what changed. Zipping against the FILTERED list matters —
            # pairing edits with the unfiltered list would write them to the
            # wrong people whenever a search or grade filter is active.
            changed = 0
            with safe_write("save member changes"):
                for original, edited in zip(visible_users, edited_rows):
                    updates = {}
                    if edited["Name"].strip() != original["name"]:
                        updates["name"] = edited["Name"].strip()
                    if edited["Institutional email"].strip() != original["email"]:
                        updates["email"] = edited["Institutional email"].strip()
                    if edited["Grade"] != original.get("grade"):
                        updates["grade"] = edited["Grade"]
                    if STANDING_VALUES.get(edited["Standing"], edited["Standing"]) != original.get("role"):
                        updates["role"] = STANDING_VALUES.get(edited["Standing"]) or None
                    if edited["Section"].strip() != (original.get("section") or ""):
                        updates["section"] = edited["Section"].strip()
                    if edited["Admission no."].strip() != (original.get("admission_no") or ""):
                        updates["admission_no"] = edited["Admission no."].strip()
                    if edited["Phone no."].strip() != (original.get("phone_no") or ""):
                        updates["phone_no"] = edited["Phone no."].strip()
                    if edited["Discord ID"].strip() != (original.get("discord_user_id") or ""):
                        updates["discord_user_id"] = edited["Discord ID"].strip() or None
                    if edited["Verified"] != bool(original.get("details_verified")):
                        updates["details_verified"] = edited["Verified"]
                    if edited["Disabled"] != bool(original.get("is_disabled")):
                        updates["is_disabled"] = edited["Disabled"]

                    if updates:
                        client.table("users").update(updates).eq("user_id", original["user_id"]).execute()
                        changed += 1

                if changed:
                    invalidate_cache()
            st.session_state.member_edit_message = (
                f"Updated {changed} member(s)." if changed else "No changes to save."
            )
            st.rerun()

        st.divider()
        with st.expander(":material/person_remove: Delete a member"):
            st.caption(
                "Removes their profile from this app only — it does **not** delete their "
                "actual Supabase Auth login (a separate system this app has no admin access "
                "to, same limitation as editing Institutional email above). They could still "
                "log in afterward, just with no profile data."
            )
            delete_target_id = st.selectbox(
                "Member to delete",
                options=[u["user_id"] for u in users],
                format_func=lambda uid: next(
                    (u["name"] for u in users if u["user_id"] == uid), "Unknown"
                ),
                key="delete_member_target",
                index=None,
                placeholder="Choose a member...",
            )
            if delete_target_id:
                target = next(u for u in users if u["user_id"] == delete_target_id)

                # Some tables don't cascade-delete on a user (see
                # supabase_schema.sql) — parts/requests deliberately, so
                # ownership/loan history can't silently vanish, and Exun
                # channel/query messages since those belong to a shared
                # thread, not just the sender. Deleting would otherwise
                # crash on a raw foreign-key error, so check first and
                # tell the host exactly what to resolve.
                owned_parts = [p for p in cached_table("parts") if p["owner_id"] == delete_target_id]
                their_requests = [
                    r for r in cached_table("requests")
                    if r["requester_id"] == delete_target_id or r["owner_id"] == delete_target_id
                ]
                their_exun_msgs = [
                    m for m in cached_table("exun_channel_messages")
                    if m["sender_id"] == delete_target_id
                ]
                their_query_msgs = [
                    m for m in cached_table("query_messages") if m["sender_id"] == delete_target_id
                ]

                blockers = []
                if owned_parts:
                    blockers.append(f"still owns {len(owned_parts)} part(s) — reassign or delete them on Inventory first")
                if their_requests:
                    blockers.append(f"has {len(their_requests)} borrow request(s) on record, as borrower or lender")
                if their_exun_msgs:
                    blockers.append(f"sent {len(their_exun_msgs)} message(s) in the Exun channel")
                if their_query_msgs:
                    blockers.append(f"sent {len(their_query_msgs)} message(s) in Queries")

                if blockers:
                    st.error(f"Can't delete **{target['name']}** — they " + "; ".join(blockers) + ".")
                elif st.session_state.confirming_delete_member_id == delete_target_id:
                    # Everything else DOES cascade-delete with the user row
                    # (event_volunteers, meeting_rsvps/attendance,
                    # achievements, their own query threads) — summarized
                    # here so the host knows what's actually going away.
                    vol_count = sum(
                        1 for v in cached_table("event_volunteers") if v["user_id"] == delete_target_id
                    )
                    rsvp_count = sum(
                        1 for r in cached_table("meeting_rsvps") if r["user_id"] == delete_target_id
                    )
                    achievement_count = sum(
                        1 for a in cached_table("achievements") if a["user_id"] == delete_target_id
                    )
                    query_thread_count = sum(
                        1 for q in cached_table("queries") if q["student_id"] == delete_target_id
                    )
                    st.warning(
                        f"Delete **{target['name']}**'s profile? This also removes their "
                        f"{vol_count} competition signup(s), {rsvp_count} meeting RSVP(s), "
                        f"{achievement_count} achievement(s), and {query_thread_count} query "
                        f"thread(s). This can't be undone."
                    )
                    confirm_col, cancel_col = st.columns([1, 1])
                    if confirm_col.button(
                        "Confirm delete", icon=":material/delete_forever:", type="primary",
                        key="confirm_delete_member",
                    ):
                        with safe_write(f"delete {target['name']}"):
                            client.table("users").delete().eq("user_id", delete_target_id).execute()
                            invalidate_cache()
                        st.session_state.confirming_delete_member_id = None
                        st.session_state.member_edit_message = f"Deleted {target['name']}."
                        st.rerun()
                    if cancel_col.button("Cancel", icon=":material/close:", key="cancel_delete_member"):
                        st.session_state.confirming_delete_member_id = None
                        st.rerun()
                else:
                    if st.button(
                        "Delete this member", icon=":material/delete:", key="delete_member_btn"
                    ):
                        st.session_state.confirming_delete_member_id = delete_target_id
                        st.rerun()

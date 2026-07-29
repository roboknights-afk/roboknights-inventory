# Host-only member directory. Not linked from anywhere a non-host would see
# it — app.py only adds this page to the nav at all when is_host is true —
# but guarded here too in case someone hits the URL directly, since that's
# the one thing st.navigation's page list alone doesn't stop.

import streamlit as st

from shared import cached_table, get_client, invalidate_cache

is_host = st.session_state.is_host

if not is_host:
    st.error("Host access only.")
    st.stop()

client = get_client()

st.title("Members")

if "member_edit_message" not in st.session_state:
    st.session_state.member_edit_message = None

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
m1, m2, m3 = st.columns(3)
m1.metric("Members", len(users), border=True)
m2.metric(
    "Grades represented", len({u.get("grade") for u in users if u.get("grade")}), border=True
)
m3.metric(
    "Missing details",
    sum(
        1 for u in users
        if not (u.get("grade") and u.get("section") and u.get("admission_no") and u.get("phone_no"))
    ),
    border=True,
    help="Members with at least one blank field",
)

with st.expander(":material/info: About this page"):
    st.markdown(
        "Grade, section, admission no., and phone no. are private to each member "
        "everywhere else in the app — this directory is the one place a host can "
        "see everyone's details together."
    )
    st.warning(
        "Editing **Institutional email** here only updates this profile record. "
        "It does **not** change their actual login email in Supabase Auth, which "
        "is a separate system this app doesn't have admin access to."
    )

if not users:
    st.caption("No members yet.")
else:
    # Search + grade filter above the table.
    search_col, grade_col = st.columns([2, 2], vertical_alignment="center")
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

    visible_users = []
    for u in users:
        haystack = " ".join([
            u["name"], u["email"], str(u.get("section") or ""), str(u.get("admission_no") or ""),
        ]).lower()
        if member_search and member_search.lower() not in haystack:
            continue
        if grade_filter and u.get("grade") not in grade_filter:
            continue
        visible_users.append(u)

    if not visible_users:
        st.caption("No members match your search or filter.")
        st.stop()

    st.caption(f"Showing {len(visible_users)} of {len(users)} member(s).")

    # Editable straight in the table. num_rows="fixed" so hosts can't
    # add/delete rows here — a "member" only ever comes from someone
    # actually signing up.
    edited_rows = st.data_editor(
        [
            {
                "Name": u["name"],
                "Institutional email": u["email"],
                "Grade": u.get("grade"),
                "Section": u.get("section") or "",
                "Admission no.": u.get("admission_no") or "",
                "Phone no.": u.get("phone_no") or "",
            }
            for u in visible_users
        ],
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            "Name": st.column_config.TextColumn("Name", width="medium"),
            "Institutional email": st.column_config.TextColumn("Institutional email", width="medium"),
            "Grade": st.column_config.SelectboxColumn("Grade", options=GRADE_OPTIONS, width="small"),
            "Section": st.column_config.TextColumn("Section", width="small"),
            "Admission no.": st.column_config.TextColumn("Admission no.", width="small"),
            "Phone no.": st.column_config.TextColumn("Phone no.", width="small"),
        },
        key="members_editor",
    )

    if st.button("Save changes", icon=":material/check:", type="primary"):
        # Row order is preserved by data_editor, so we can zip the rows that
        # were actually shown back up with the edited ones and write only
        # what changed. Zipping against the FILTERED list matters — pairing
        # edits with the unfiltered list would write them to the wrong people
        # whenever a search or grade filter is active.
        changed = 0
        for original, edited in zip(visible_users, edited_rows):
            updates = {}
            if edited["Name"].strip() != original["name"]:
                updates["name"] = edited["Name"].strip()
            if edited["Institutional email"].strip() != original["email"]:
                updates["email"] = edited["Institutional email"].strip()
            if edited["Grade"] != original.get("grade"):
                updates["grade"] = edited["Grade"]
            if edited["Section"].strip() != (original.get("section") or ""):
                updates["section"] = edited["Section"].strip()
            if edited["Admission no."].strip() != (original.get("admission_no") or ""):
                updates["admission_no"] = edited["Admission no."].strip()
            if edited["Phone no."].strip() != (original.get("phone_no") or ""):
                updates["phone_no"] = edited["Phone no."].strip()

            if updates:
                client.table("users").update(updates).eq("user_id", original["user_id"]).execute()
                changed += 1

        if changed:
            invalidate_cache()
        st.session_state.member_edit_message = (
            f"Updated {changed} member(s)." if changed else "No changes to save."
        )
        st.rerun()

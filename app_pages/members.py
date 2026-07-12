# Host-only member directory. Not linked from anywhere a non-host would see
# it — app.py only adds this page to the nav at all when is_host is true —
# but guarded here too in case someone hits the URL directly, since that's
# the one thing st.navigation's page list alone doesn't stop.

import streamlit as st

from shared import get_client

is_host = st.session_state.is_host

if not is_host:
    st.error("Host access only.")
    st.stop()

client = get_client()

st.title("Members")
st.caption(
    "Grade, section, admission no., and phone no. are private to each member "
    "everywhere else in the app — this directory is the one place a host can "
    "see everyone's details together."
)
st.caption(
    ":material/info: Editing \"Institutional email\" here only updates this "
    "profile record — it does NOT change their actual login email in "
    "Supabase Auth, which is a separate system this app doesn't have "
    "admin access to."
)

if "member_edit_message" not in st.session_state:
    st.session_state.member_edit_message = None

if st.session_state.member_edit_message:
    st.success(st.session_state.member_edit_message)
    st.session_state.member_edit_message = None

users = (
    client.table("users")
    .select("user_id, name, email, grade, section, admission_no, phone_no")
    .order("name")
    .execute()
    .data
)

if not users:
    st.caption("No members yet.")
else:
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
            for u in users
        ],
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            "Grade": st.column_config.SelectboxColumn("Grade", options=[7, 8, 9, 10, 11, 12]),
        },
        key="members_editor",
    )

    if st.button("Save changes", icon=":material/check:", type="primary"):
        # Row order is preserved by data_editor, so we can zip the original
        # rows back up with the edited ones and only write what changed.
        changed = 0
        for original, edited in zip(users, edited_rows):
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

        st.session_state.member_edit_message = (
            f"Updated {changed} member(s)." if changed else "No changes to save."
        )
        st.rerun()

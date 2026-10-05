# Parent's consent form for inter-school events. A student SELECTED for any
# event at a competition gets a printable, pre-filled copy of the school's
# own form starting 2 days before the competition. They check the details
# (only the date is editable), download it, have a guardian fill in and sign
# the rest by hand, and bring it on the day. The fill-in itself lives in
# consent_docx.py; this page is just the gate, the confirm step and the
# download.

from datetime import date, timedelta

import streamlit as st

from consent_docx import build_consent_docx
from shared import cached_table, today_ist

OPENS_DAYS_BEFORE = 2

is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
user_name_by_id = st.session_state.user_name_by_id

st.title(":material/description: Consent form")

# Belt and braces alongside the nav gating in app.py — Exun and the
# read-only viewer have no users row and never compete for us.
if st.session_state.is_exun or st.session_state.is_viewer:
    st.warning("This page isn't available for your account.")
    st.stop()

today = today_ist()
users = {u["user_id"]: u for u in cached_table("users")}
competitions = {
    c["competition_id"]: c for c in cached_table("competitions")
    if not c.get("is_past") and not c.get("not_attending") and c.get("competition_date")
}
event_to_competition = {e["event_id"]: e["competition_id"] for e in cached_table("competition_events")}

# {user_id: {competition_id, ...}} for everyone SELECTED (not just
# volunteered) for at least one event at an upcoming competition.
selected_competitions = {}
for v in cached_table("event_volunteers"):
    comp_id = event_to_competition.get(v["event_id"])
    if v.get("selected") and comp_id in competitions:
        selected_competitions.setdefault(v["user_id"], set()).add(comp_id)

if is_host:
    # Hosts can open the form for any selected student, at any time, so it
    # can be checked and reprinted for someone who lost theirs. Students
    # themselves only ever see their own.
    student_ids = sorted(selected_competitions, key=lambda uid: user_name_by_id.get(uid, "").lower())
    if not student_ids:
        st.caption("Nobody is selected for an upcoming competition yet.")
        st.stop()
    target_id = st.selectbox(
        "Student", student_ids, format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
        key="consent_target_student",
    )
else:
    target_id = current_user_id

st.caption(
    "Check the details below (the date is the only thing you can change), download the form, print it, have your guardian complete "
    "and sign the rest, and **bring it with you on the day of the event**."
)

mine = sorted(
    (competitions[cid] for cid in selected_competitions.get(target_id, ())),
    key=lambda c: c["competition_date"],
)
if not mine:
    st.info(
        "You're not selected for an upcoming competition yet. Once you are, your consent form "
        f"opens {OPENS_DAYS_BEFORE} days before the event."
    )
    st.stop()

student = users.get(target_id) or {}
for comp in mine:
    comp_date = date.fromisoformat(comp["competition_date"])
    opens_on = comp_date - timedelta(days=OPENS_DAYS_BEFORE)
    is_open = today >= opens_on
    with st.container(border=True, key=f"rkcard_consent_{comp['competition_id']}_{target_id}"):
        head_col, badge_col = st.columns([4, 1], vertical_alignment="center")
        head_col.markdown(f"### {comp['name']}")
        head_col.caption(f":material/event: {comp_date.strftime('%A, %d %b %Y')}")
        if is_open:
            badge_col.badge("Open", color="green", icon=":material/check_circle:")
        else:
            badge_col.badge(f"Opens {opens_on.strftime('%d %b')}", color="grey", icon=":material/lock:")

        if not is_open and not is_host:
            st.caption(
                f"Your form opens on {opens_on.strftime('%A, %d %b')}, {OPENS_DAYS_BEFORE} days before the event."
            )
            continue
        if not is_open:
            st.caption(f"Host preview — it opens for the student on {opens_on.strftime('%d %b')}.")

        key = f"{comp['competition_id']}_{target_id}"
        # Fixed, not editable: these come straight from the student's own
        # account and the competition record, so a form can't be made out
        # for a different name, class or event. Only the date can change.
        name = student.get("name") or ""
        grade = str(student.get("grade") or "")
        section = student.get("section") or ""
        admission_no = student.get("admission_no") or ""
        competition_name = comp["name"]
        venue = (comp.get("venue") or "").strip()
        st.text_input("Student name", value=name, disabled=True, key=f"consent_name_{key}")
        grade_col, section_col, adm_col = st.columns(3)
        grade_col.text_input("Class", value=grade, disabled=True, key=f"consent_grade_{key}")
        section_col.text_input("Section", value=section, disabled=True, key=f"consent_section_{key}")
        adm_col.text_input("Admission no.", value=admission_no, disabled=True, key=f"consent_adm_{key}")
        st.text_input("Name of the competition", value=competition_name, disabled=True, key=f"consent_comp_{key}")
        st.text_input("Venue", value=venue, disabled=True, key=f"consent_venue_{key}")
        chosen_date = st.date_input(
            "Date you're competing", value=comp_date, key=f"consent_date_{key}",
            help="Competing on a different day (for example day 2 of a two-day event)? Change it here.",
        )
        st.caption(f"That's a **{chosen_date.strftime('%A')}**.")

        missing = [label for label, value in (
            ("name", name), ("class", grade), ("section", section), ("admission number", admission_no),
        ) if not value.strip()]
        if missing:
            st.warning(
                f"Your account is missing: {', '.join(missing)}. Ask a host to update your details "
                "on the Members page, then reload — until then it'll be blank on the form."
            )

        confirmed = st.checkbox(
            "I've checked these details and the date are correct", key=f"consent_confirm_{key}",
        )
        st.download_button(
            "Download consent form", icon=":material/download:", type="primary",
            data=build_consent_docx(
                name.strip(), admission_no.strip(), grade.strip(), section.strip(),
                competition_name.strip(), chosen_date.strftime("%A"), chosen_date.strftime("%d %b %Y"), venue,
            ),
            file_name=f"Consent form - {competition_name.strip() or comp['name']} - {name.strip() or 'student'}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            disabled=not confirmed, key=f"consent_download_{key}",
        )
        st.caption(
            "Opens in Word or Google Docs. Your guardian fills in their own name, relation, mobile, "
            "email, the date of consent, and signs."
        )

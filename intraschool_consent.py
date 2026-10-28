# Intra-school "stay back" consent forms (2026-10-28), shown on the Consent
# form page (app_pages/consent_form.py) under "Intra-school (stay back)".
#
# A host picks the members who have to stay back in school after school hours,
# gives the reason, the date and a TENTATIVE time they should be free by, and
# chooses how guardians consent:
#   physical - each member downloads the school's consent form, reworded for
#              staying back (consent_docx.build_stayback_docx), and hands it in
#              signed, the same way as the inter-school form;
#   online   - the guardian fills in their name, relation, phone and email on
#              the member's account and accepts an undertaking. The exact text
#              they accepted is saved with their details as the record.
# Both say the time is tentative (they might be delayed) and that no mobile
# phones are allowed.
#
# Needs the intraschool_forms / intraschool_form_members migration at the end
# of supabase_schema.sql. Until it is run the section says so and the rest of
# the page is unaffected.

import re
from datetime import date, datetime, time, timezone

import streamlit as st

from consent_docx import build_stayback_docx
from shared import APP_URL, cached_table, format_ist, get_client, invalidate_cache, safe_write, send_email, today_ist

MODE_LABELS = {
    "physical": "Physical form, submitted in school",
    "online": "Online, the guardian fills it in here",
}
TENTATIVE_NOTE = (
    "The time is **tentative** - they might be delayed past it. **No mobile phones are allowed.**"
)


def time_text(value):
    # "17:30:00" -> "5:30 p.m.", the way the school's own form writes 1:40 p.m.
    t = value if isinstance(value, time) else time.fromisoformat(value)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'a.m.' if t.hour < 12 else 'p.m.'}"


def undertaking_text(form, student, guardian_name=None, relation=None):
    # With no guardian given yet it reads "I, the guardian named above", for
    # showing before the form is sent; the saved copy has their real name.
    who = f"{guardian_name}, {relation} of" if guardian_name else "the guardian named above, guardian of"
    stay_date = date.fromisoformat(form["stay_date"])
    return (
        f"I, {who} {student.get('name') or 'my ward'} (Adm. No. {student.get('admission_no') or '-'}, "
        f"Class {student.get('grade') or '-'}-{student.get('section') or '-'}), allow my ward to stay back in "
        f"school after school hours on {stay_date.strftime('%A, %d %b %Y')} for {form['reason']}, till "
        f"{time_text(form['until_time'])} (tentative time). I understand that this is only a tentative time and there is no "
        "assurance that my ward will be free by then; they might be delayed. I confirm that this undertaking "
        "is given by me and will be saved in the school's records, and that my ward will not bring a mobile phone."
    )


def stayback_email(form, student):
    # (subject, body) of the email each member gets when a form is made for
    # them. Plain text, like every other email this app sends.
    stay_date = date.fromisoformat(form["stay_date"])
    when = stay_date.strftime("%A, %d %b %Y")
    until = time_text(form["until_time"])
    if form["mode"] == "physical":
        how = "on a signed paper form"
        steps = [
            'Open the Consent form page on the dashboard and choose "Intra-school (stay back)".',
            "Download your consent form and print it.",
            "Have your parent/guardian fill in their details and sign it.",
            "Submit the signed form in school.",
        ]
    else:
        how = "online, on the dashboard"
        steps = [
            'Open the Consent form page on the dashboard and choose "Intra-school (stay back)".',
            "Ask your parent/guardian to fill in their name, relation to you, phone number and email.",
            "They read the undertaking, tick to accept it, and submit. It is saved in the school's records.",
        ]
    lines = [
        f"Hi {student.get('name') or 'there'},",
        "",
        f"You need to stay back in school after school hours on {when} for {form['reason']}, "
        f"till {until} (tentative time).",
        "",
        "Please note:",
        "- This is a tentative time. You might be delayed past it.",
        "- No mobile phones are allowed.",
        "",
        f"Your parent/guardian's consent is needed {how}:",
        *(f"{i}. {step}" for i, step in enumerate(steps, 1)),
        "",
        f"Dashboard: {APP_URL}",
        "",
        "- RoboKnights",
    ]
    subject = f"Stay-back consent form: {form['reason']} on {stay_date.strftime('%d %b')}"
    return subject, "\n".join(lines)


def _stayback_docx(form, student):
    stay_date = date.fromisoformat(form["stay_date"])
    return build_stayback_docx(
        (student.get("name") or "").strip(), (student.get("admission_no") or "").strip(),
        str(student.get("grade") or "").strip(), (student.get("section") or "").strip(),
        stay_date.strftime("%A"), stay_date.strftime("%d %b %Y"),
        form["reason"].strip(), time_text(form["until_time"]),
    )


def _docx_name(form, student):
    return f"Stay-back consent - {date.fromisoformat(form['stay_date']).strftime('%d %b')} - {student.get('name') or 'student'}.docx"


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def render():
    is_host = st.session_state.is_host
    me = st.session_state.current_user_id

    try:
        forms = cached_table("intraschool_forms")
        recipients = cached_table("intraschool_form_members")
    except Exception:
        st.warning(
            "Intra-school forms aren't set up yet. A host needs to run the intraschool_forms migration "
            "(at the end of supabase_schema.sql) in Supabase's SQL editor."
        )
        return

    flash = st.session_state.pop("intraschool_flash", None)
    if flash:
        st.success(flash)

    users = {u["user_id"]: u for u in cached_table("users")}
    rows_by_form = {}
    for r in recipients:
        rows_by_form.setdefault(r["form_id"], []).append(r)
    forms = sorted(forms, key=lambda f: (f["stay_date"], f["created_at"]), reverse=True)

    if is_host:
        _create_form(users)
        _host_list(forms, rows_by_form, users)
    _my_forms(forms, rows_by_form, users, me, is_host)


# ---------- host: create ----------

def _create_form(users):
    members = sorted(
        (u for u in users.values() if not u.get("is_staff") and not u.get("is_disabled")),
        key=lambda u: (u.get("name") or "").lower(),
    )
    with st.expander("Create an intra-school consent form", icon=":material/add:"):
        with st.form("new_intraschool_form", clear_on_submit=True, border=False):
            chosen = st.multiselect(
                "Members who get this form", [u["user_id"] for u in members],
                format_func=lambda uid: users[uid].get("name") or "Unknown",
            )
            reason = st.text_input("Reason for staying back", placeholder="e.g. robot build for Compufest")
            date_col, time_col = st.columns(2)
            stay_date = date_col.date_input("Date", value=today_ist(), min_value=today_ist())
            until = time_col.time_input("Tentative time they'll be free by", value=time(16, 0), step=900)
            mode = st.radio("How guardians give consent", list(MODE_LABELS), format_func=MODE_LABELS.get)
            st.caption(
                "Either way the form says the time is tentative and they might be delayed, "
                "and that no mobile phones are allowed."
            )
            if st.form_submit_button("Create form", type="primary", icon=":material/check:"):
                if not chosen:
                    st.error("Pick at least one member.")
                elif not reason.strip():
                    st.error("Give the reason they're staying back.")
                else:
                    with safe_write("create this form"):
                        client = get_client()
                        form = client.table("intraschool_forms").insert({
                            "reason": reason.strip(), "stay_date": stay_date.isoformat(),
                            "until_time": until.strftime("%H:%M"), "mode": mode,
                            "created_by": st.session_state.current_user_id,
                        }).execute().data[0]
                        client.table("intraschool_form_members").insert(
                            [{"form_id": form["form_id"], "user_id": uid} for uid in chosen]
                        ).execute()
                        invalidate_cache()
                        # Best-effort like every email here: the form is saved
                        # either way, a failed send just means no email.
                        emailed = 0
                        for uid in chosen:
                            to_email = users[uid].get("email")
                            if to_email:
                                send_email(to_email, *stayback_email(form, users[uid]))
                                emailed += 1
                        st.session_state.intraschool_flash = (
                            f"Form created for {len(chosen)} member{'s' if len(chosen) != 1 else ''}, "
                            f"and {emailed} emailed about it."
                        )
                        st.rerun()


# ---------- host: every form and who has done what ----------

def _host_list(forms, rows_by_form, users):
    if not forms:
        st.caption("No intra-school forms yet.")
        return
    today = today_ist()
    for form in forms:
        fid = form["form_id"]
        stay_date = date.fromisoformat(form["stay_date"])
        rows = sorted(rows_by_form.get(fid, []), key=lambda r: (users.get(r["user_id"], {}).get("name") or "").lower())
        with st.container(border=True, key=f"rkcard_intra_{fid}"):
            head_col, badge_col = st.columns([4, 1], vertical_alignment="center")
            head_col.markdown(f"### {form['reason']}")
            head_col.caption(
                f":material/event: {stay_date.strftime('%A, %d %b %Y')} · till {time_text(form['until_time'])} "
                f"(tentative) · {MODE_LABELS[form['mode']]}"
            )
            if form["mode"] == "online":
                done = sum(1 for r in rows if r.get("submitted_at"))
                badge_col.badge(f"{done}/{len(rows)} submitted", color="green" if done == len(rows) else "orange")
            elif stay_date < today:
                badge_col.badge("Past", color="grey")

            if form["mode"] == "online":
                st.dataframe(
                    [{
                        "Member": users.get(r["user_id"], {}).get("name") or "Unknown",
                        "Status": "Submitted" if r.get("submitted_at") else "Waiting",
                        "Guardian": r.get("guardian_name") or "",
                        "Relation": r.get("guardian_relation") or "",
                        "Phone": r.get("guardian_phone") or "",
                        "Email": r.get("guardian_email") or "",
                        "Submitted": format_ist(r["submitted_at"]) if r.get("submitted_at") else "",
                    } for r in rows],
                    hide_index=True, width="stretch",
                )
                submitted = [r for r in rows if r.get("submitted_at")]
                if submitted:
                    with st.expander("Read the undertakings"):
                        for r in submitted:
                            st.markdown(f"**{users.get(r['user_id'], {}).get('name') or 'Unknown'}**")
                            st.caption(r.get("undertaking") or "")
            else:
                st.caption("For: " + ", ".join(users.get(r["user_id"], {}).get("name") or "Unknown" for r in rows))
                pick_col, dl_col = st.columns([3, 2], vertical_alignment="bottom")
                pick = pick_col.selectbox(
                    "Download a member's form", [r["user_id"] for r in rows],
                    format_func=lambda uid: users.get(uid, {}).get("name") or "Unknown", key=f"intra_pick_{fid}",
                )
                if pick:
                    student = users.get(pick, {})
                    dl_col.download_button(
                        "Download", icon=":material/download:", data=_stayback_docx(form, student),
                        file_name=_docx_name(form, student), mime=DOCX_MIME, key=f"intra_host_dl_{fid}",
                    )

            with st.popover("Delete", icon=":material/delete:"):
                st.caption("Deletes the form for everyone, including any online submissions.")
                if st.button("Yes, delete it", type="primary", key=f"intra_delete_{fid}"):
                    with safe_write("delete this form"):
                        get_client().table("intraschool_forms").delete().eq("form_id", fid).execute()
                        invalidate_cache()
                        st.session_state.intraschool_flash = "Form deleted."
                        st.rerun()


# ---------- member: their own forms ----------

def _my_forms(forms, rows_by_form, users, me, is_host):
    today = today_ist()
    mine = []
    for form in forms:
        row = next((r for r in rows_by_form.get(form["form_id"], []) if r["user_id"] == me), None)
        # Past forms drop off, except an online one still waiting, which a
        # guardian may need to finish late.
        if row and (date.fromisoformat(form["stay_date"]) >= today or (form["mode"] == "online" and not row.get("submitted_at"))):
            mine.append((form, row))
    if not mine:
        if not is_host:
            st.info("You don't have any intra-school forms right now.")
        return
    if is_host:
        st.subheader("Your own forms")

    student = users.get(me) or {}
    for form, row in sorted(mine, key=lambda pair: pair[0]["stay_date"]):
        fid = form["form_id"]
        stay_date = date.fromisoformat(form["stay_date"])
        with st.container(border=True, key=f"rkcard_intra_mine_{fid}"):
            st.markdown(f"### {form['reason']}")
            st.caption(
                f":material/event: Stay back on {stay_date.strftime('%A, %d %b %Y')}, "
                f"till {time_text(form['until_time'])} (tentative)"
            )
            st.warning(TENTATIVE_NOTE, icon=":material/schedule:")

            if form["mode"] == "physical":
                missing = [label for label, key in (
                    ("name", "name"), ("class", "grade"), ("section", "section"), ("admission number", "admission_no"),
                ) if not str(student.get(key) or "").strip()]
                if missing:
                    st.warning(
                        f"Your account is missing: {', '.join(missing)}. Ask a host to update your details "
                        "on the Members page - until then it'll be blank on the form."
                    )
                st.download_button(
                    "Download consent form", icon=":material/download:", type="primary",
                    data=_stayback_docx(form, student), file_name=_docx_name(form, student),
                    mime=DOCX_MIME, key=f"intra_dl_{fid}",
                )
                st.caption(
                    "Print it, have your guardian fill in their details and sign, and **submit it in school**."
                )
            elif row.get("submitted_at"):
                st.success(
                    f"Submitted by {row.get('guardian_name')} ({row.get('guardian_relation')}) "
                    f"on {format_ist(row['submitted_at'])}.",
                    icon=":material/check_circle:",
                )
                with st.expander("The undertaking they gave"):
                    st.caption(row.get("undertaking") or "")
            else:
                _online_form(form, student, me)


def _online_form(form, student, me):
    fid = form["form_id"]
    st.caption("To be filled in by your parent or guardian.")
    with st.form(f"intra_online_{fid}", border=False):
        guardian_name = st.text_input("Guardian's full name")
        relation = st.text_input("Relation to student", placeholder="e.g. Father, Mother")
        phone_col, email_col = st.columns(2)
        phone = phone_col.text_input("Guardian's phone number", placeholder="10-digit mobile number")
        email = email_col.text_input("Guardian's email")
        st.markdown("**Undertaking**")
        st.info(undertaking_text(form, student))
        agreed = st.checkbox("I am the guardian named above, and I give this undertaking.")
        if st.form_submit_button("Submit", type="primary", icon=":material/send:"):
            digits = re.sub(r"\D", "", phone)
            if digits.startswith("91") and len(digits) == 12:
                digits = digits[2:]
            if not guardian_name.strip() or not relation.strip():
                st.error("Fill in the guardian's name and their relation to the student.")
            elif len(digits) != 10:
                st.error("Enter a 10-digit phone number.")
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
                st.error("Enter a valid email address.")
            elif not agreed:
                st.error("Tick the box to give the undertaking.")
            else:
                with safe_write("submit this form"):
                    get_client().table("intraschool_form_members").update({
                        "guardian_name": guardian_name.strip(),
                        "guardian_relation": relation.strip(),
                        "guardian_phone": digits,
                        "guardian_email": email.strip(),
                        "undertaking": undertaking_text(form, student, guardian_name.strip(), relation.strip()),
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("form_id", fid).eq("user_id", me).execute()
                    invalidate_cache()
                    st.session_state.intraschool_flash = "Submitted. Thank you!"
                    st.rerun()

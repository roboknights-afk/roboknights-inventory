# Intra-school consent forms (2026-10-28), shown on the Consent form page
# (app_pages/consent_form.py) under SECTION_LABEL. Two kinds (2026-10-29), the
# host picks one per form:
#   stayback - stay back in school after school hours on a school day;
#   saturday - come to school on a Saturday, from one time till another.
# Every email, undertaking, caption and paper form words itself by the kind,
# so a Saturday form never says "stay back".
#
# A host picks the members, gives the reason, the date and a TENTATIVE time
# they should be free by, and chooses how guardians consent:
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
# the page is unaffected. Saturday forms also need the later kind/from_time
# migration; until that is run only stay-back forms can be made.

import csv
import io
import re
from datetime import date, datetime, time, timedelta, timezone

import streamlit as st

from consent_docx import build_stayback_docx
from shared import (
    APP_URL, HOST_ROLES, cached_table, format_ist, get_client, invalidate_cache, safe_write, send_email, today_ist,
)

SECTION_LABEL = "Intra-school (stay back / Saturday)"
KIND_LABELS = {
    "stayback": "Stay back after school",
    "saturday": "Come to school on Saturday",
}
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


def kind_of(form):
    # Forms made before Saturday forms existed have no kind: they're stay-backs.
    return form.get("kind") or "stayback"


def is_saturday(form):
    return kind_of(form) == "saturday"


def noun(form):
    # "stay-back consent form" / "Saturday consent form"
    return "Saturday" if is_saturday(form) else "stay-back"


def timing(form):
    # "from 9:00 a.m. till 1:00 p.m." / "till 6:00 p.m."
    until = f"till {time_text(form['until_time'])}"
    return f"from {time_text(form['from_time'])} {until}" if is_saturday(form) and form.get("from_time") else until


def plan(form):
    # What the member is being asked to do, after "to": "stay back in school
    # after school hours on ... for ..." or "come to school on ... for ...".
    when = date.fromisoformat(form["stay_date"]).strftime("%A, %d %b %Y")
    if is_saturday(form):
        return f"come to school on {when} for {form['reason']}, {timing(form)} (tentative time)"
    return f"stay back in school after school hours on {when} for {form['reason']}, {timing(form)} (tentative time)"


def undertaking_text(form, student, guardian_name=None, relation=None):
    # With no guardian given yet it reads "I, the guardian named above", for
    # showing before the form is sent; the saved copy has their real name.
    who = f"{guardian_name}, {relation} of" if guardian_name else "the guardian named above, guardian of"
    return (
        f"I, {who} {student.get('name') or 'my ward'} (Adm. No. {student.get('admission_no') or '-'}, "
        f"Class {student.get('grade') or '-'}-{student.get('section') or '-'}), allow my ward to {plan(form)}. "
        "I understand that this is only a tentative time and there is no "
        "assurance that my ward will be free by then; they might be delayed. I confirm that this undertaking "
        "is given by me and will be saved in the school's records, and that my ward will not bring a mobile phone."
    )


def form_email(form, student):
    # (subject, body) of the email each member gets when a form is made for
    # them. Plain text, like every other email this app sends.
    stay_date = date.fromisoformat(form["stay_date"])
    if form["mode"] == "physical":
        how = "on a signed paper form"
        steps = [
            f'Open the Consent form page on the dashboard and choose "{SECTION_LABEL}".',
            "Download your consent form and print it.",
            "Have your parent/guardian fill in their details and sign it.",
            "Submit the signed form in school.",
        ]
    else:
        how = "online, on the dashboard"
        steps = [
            f'Open the Consent form page on the dashboard and choose "{SECTION_LABEL}".',
            "Ask your parent/guardian to fill in their name, relation to you, phone number and email.",
            "They read the undertaking, tick to accept it, and submit. It is saved in the school's records.",
        ]
    lines = [
        f"Hi {student.get('name') or 'there'},",
        "",
        f"You need to {plan(form)}.",
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
    subject = f"{noun(form).capitalize()} consent form: {form['reason']} on {stay_date.strftime('%d %b')}"
    return subject, "\n".join(lines)


def paper_copy_email(form, student):
    # (subject, body) of the email a member gets once their guardian has
    # submitted online (host's request, 2026-10-29): the school still wants
    # a signed paper form, so the member's own form is attached to print.
    stay_date = date.fromisoformat(form["stay_date"])
    lines = [
        f"Hi {student.get('name') or 'there'},",
        "",
        f"Your parent/guardian's undertaking for {'coming to school' if is_saturday(form) else 'staying back'} "
        f"on {stay_date.strftime('%A, %d %b %Y')} for {form['reason']} has been received online. Thank you!",
        "",
        "The school also needs a signed paper copy. Your consent form is attached:",
        "1. Print the attached form.",
        "2. Have your parent/guardian sign it. Their details are already filled in from what they gave online.",
        f"3. Bring it to school and submit it on or before {stay_date.strftime('%d %b')}.",
        "",
        "- RoboKnights",
    ]
    subject = f"Bring your signed {noun(form)} form to school: {form['reason']} on {stay_date.strftime('%d %b')}"
    return subject, "\n".join(lines)


def send_paper_copy(form, student, row):
    # False if there's no address or the send fails (2026-10-29: one member's
    # copy went missing with nobody told), so the guardian is told to download
    # it instead. `row` is their submitted intraschool_form_members row, so the
    # form comes with the guardian's details printed in - only the signature blank.
    if not student.get("email"):
        return False
    try:
        send_email(student["email"], *paper_copy_email(form, student), raise_errors=True,
                   attachments=[(_docx_name(form, student), _stayback_docx(form, student, row))])
    except Exception as e:
        print(f"intra-school paper copy email failed: {e!r}")
        return False
    return True


def _stayback_docx(form, student, row=None):
    stay_date = date.fromisoformat(form["stay_date"])
    guardian = None
    if row and row.get("submitted_at"):
        guardian = {
            "name": row.get("guardian_name"), "relation": row.get("guardian_relation"),
            "phone": row.get("guardian_phone"), "email": row.get("guardian_email"),
            "date": format_ist(row["submitted_at"]).split(",")[0],
        }
    return build_stayback_docx(
        (student.get("name") or "").strip(), (student.get("admission_no") or "").strip(),
        str(student.get("grade") or "").strip(), (student.get("section") or "").strip(),
        stay_date.strftime("%A"), stay_date.strftime("%d %b %Y"),
        form["reason"].strip(), time_text(form["until_time"]), guardian=guardian,
        kind=kind_of(form), from_text=time_text(form["from_time"]) if form.get("from_time") else None,
    )


def _docx_name(form, student):
    return (f"{noun(form).capitalize()} consent - {date.fromisoformat(form['stay_date']).strftime('%d %b')} - "
            f"{student.get('name') or 'student'}.docx")


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Who "Forward undertakings" emails (host's request, 2026-10-29: Ajith sir).
# Found by his HOST_ROLES title, not a hardcoded address - this repo is public.
UNDERTAKINGS_TO_TITLE = "Robotics In-Charge"
# ...with the club's shared inbox copied in, so the club keeps the same record.
UNDERTAKINGS_CC = "roboknights@dpsrkp.net"


def _class_of(s):
    return f"{s.get('grade') or '-'}-{s.get('section') or '-'}" if s.get("section") else str(s.get("grade") or "-")


def undertakings_email(form, rows, users):
    # (subject, body, attachments) of ONE email with every undertaking given
    # online for this form, or None if there are none yet. Kept short for a
    # big form (2026-10-29: 47 people on the Exun event): every guardian
    # accepted the same wording, so it's quoted once and the people are a
    # table; each one's exact saved text, with their email, is in the
    # attached spreadsheet.
    rows = sorted(rows, key=lambda r: (users.get(r["user_id"], {}).get("name") or "").lower())
    submitted = [r for r in rows if r.get("submitted_at")]
    if form["mode"] != "online" or not submitted:
        return None
    stay_date = date.fromisoformat(form["stay_date"])
    placeholder = {"name": "[student]", "admission_no": "[adm. no.]", "grade": "[class]", "section": "[section]"}
    wording = undertaking_text(form, placeholder, "[guardian]", "[relation]")
    waiting = [users.get(r["user_id"], {}).get("name") or "Unknown" for r in rows if not r.get("submitted_at")]
    total = len(submitted)

    lines = [
        "Dear Sir,",
        "",
        (f"{total} guardians have" if total != 1 else "1 guardian has")
        + f" given the {noun(form)} undertaking online on the RoboKnights dashboard for:",
        "",
        f"**{form['reason']}**: {stay_date.strftime('%A, %d %b %Y')}, {timing(form)} (tentative)",
        f"Submitted: {total} of {len(rows)}",
        "",
        "Each guardian accepted this undertaking, with their own and their ward's details:",
        f'"{wording}"',
        "",
        "# | Student | Class | Guardian | Mobile",
        *(f"{i} | {users.get(r['user_id'], {}).get('name') or 'Unknown'} | {_class_of(users.get(r['user_id'], {}))} | "
          f"{r.get('guardian_name')} ({r.get('guardian_relation')}) | {r.get('guardian_phone')}"
          for i, r in enumerate(submitted, 1)),
        "",
    ]
    if waiting:
        lines += [f"Still waiting ({len(waiting)}): " + ", ".join(waiting), ""]
    lines += [
        "The attached spreadsheet has every guardian's email, admission numbers, when they submitted, "
        "and the exact undertaking each one accepted.",
        "",
        "- RoboKnights",
    ]

    sheet = io.StringIO()
    writer = csv.writer(sheet)
    writer.writerow(["Student", "Class", "Adm. No.", "Guardian", "Relation", "Mobile", "Email", "Submitted", "Undertaking"])
    for r in submitted:
        s = users.get(r["user_id"], {})
        writer.writerow([
            s.get("name") or "", _class_of(s), s.get("admission_no") or "", r.get("guardian_name") or "",
            r.get("guardian_relation") or "", r.get("guardian_phone") or "", r.get("guardian_email") or "",
            format_ist(r["submitted_at"]), r.get("undertaking") or "",
        ])
    # utf-8-sig so Excel opens it with names intact.
    filename = f"{noun(form).capitalize()} undertakings - {form['reason']} - {stay_date.strftime('%d %b')}.csv"
    attachments = [(re.sub(r'[\\/:*?"<>|]', "-", filename), sheet.getvalue().encode("utf-8-sig"))]

    subject = (f"{noun(form).capitalize()} undertakings: {form['reason']} on {stay_date.strftime('%d %b')} "
               f"({total} of {len(rows)} submitted)")
    return subject, "\n".join(lines), attachments


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
    warning = st.session_state.pop("intraschool_warning", None)
    if warning:
        st.warning(warning, icon=":material/mail:")

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
        # Outside the form, so choosing Saturday shows the extra "from" time.
        kind = st.segmented_control(
            "Type of form", list(KIND_LABELS), format_func=KIND_LABELS.get, default="stayback",
            key="intra_new_kind",
        ) or "stayback"
        saturday = kind == "saturday"
        if saturday and not _saturday_columns_ready():
            st.warning(
                "Saturday forms aren't set up yet. A host needs to run the intraschool_forms kind/from_time "
                "migration (at the end of supabase_schema.sql) in Supabase's SQL editor."
            )
            return
        today = today_ist()
        next_saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        with st.form(f"new_intraschool_form_{kind}", clear_on_submit=True, border=False):
            chosen = st.multiselect(
                "Members who get this form", [u["user_id"] for u in members],
                format_func=lambda uid: users[uid].get("name") or "Unknown",
            )
            reason = st.text_input(
                "Reason for coming in on Saturday" if saturday else "Reason for staying back",
                placeholder="e.g. robot build for Compufest",
            )
            if saturday:
                date_col, from_col, time_col = st.columns(3)
                stay_date = date_col.date_input("Saturday", value=next_saturday, min_value=today)
                start = from_col.time_input("Time they come in", value=time(9, 0), step=900)
                until = time_col.time_input("Tentative time they'll be free by", value=time(13, 0), step=900)
            else:
                date_col, time_col = st.columns(2)
                stay_date = date_col.date_input("Date", value=today, min_value=today)
                start = None
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
                    st.error("Give the reason they're coming in." if saturday else "Give the reason they're staying back.")
                elif saturday and stay_date.weekday() != 5:
                    st.error(f"{stay_date.strftime('%d %b %Y')} is a {stay_date.strftime('%A')}, not a Saturday.")
                elif saturday and start >= until:
                    st.error("The time they come in has to be before the time they'll be free by.")
                else:
                    with safe_write("create this form"):
                        client = get_client()
                        row = {
                            "reason": reason.strip(), "stay_date": stay_date.isoformat(),
                            "until_time": until.strftime("%H:%M"), "mode": mode,
                            "created_by": st.session_state.current_user_id,
                        }
                        # Only a Saturday form writes the new columns, so
                        # stay-back forms work whether or not that migration ran.
                        if saturday:
                            row.update(kind="saturday", from_time=start.strftime("%H:%M"))
                        form = client.table("intraschool_forms").insert(row).execute().data[0]
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
                                send_email(to_email, *form_email(form, users[uid]))
                                emailed += 1
                        st.session_state.intraschool_flash = (
                            f"Form created for {len(chosen)} member{'s' if len(chosen) != 1 else ''}, "
                            f"and {emailed} emailed about it."
                        )
                        st.rerun()


def _saturday_columns_ready():
    # True once the kind/from_time migration has been run.
    try:
        get_client().table("intraschool_forms").select("kind,from_time").limit(1).execute()
        return True
    except Exception:
        return False


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
                f":material/event: {stay_date.strftime('%A, %d %b %Y')} · {timing(form)} "
                f"(tentative) · {KIND_LABELS[kind_of(form)]} · {MODE_LABELS[form['mode']]}"
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
                _forward_undertakings(form, rows, users)
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


# ---------- host: forward a form's undertakings in one email ----------

def _forward_undertakings(form, rows, users):
    # On each online form's card (2026-10-29: one email per form, not one for
    # every form at once, which got too long with 47 people on one).
    email = undertakings_email(form, rows, users)
    if not email:
        return
    to_email = next((e for e, title in HOST_ROLES.items() if title == UNDERTAKINGS_TO_TITLE), None)
    if not to_email:
        st.caption(f"No account has the title {UNDERTAKINGS_TO_TITLE}, so undertakings can't be forwarded.")
        return
    count = sum(1 for r in rows if r.get("submitted_at"))
    with st.popover("Forward undertakings to Ajith sir", icon=":material/forward_to_inbox:"):
        st.caption(
            f"Emails the {count} submitted undertaking{'s' if count != 1 else ''} for this form to Ajith sir "
            f"in one email - a short table, with a spreadsheet of the full details attached - "
            f"with {UNDERTAKINGS_CC} in CC."
        )
        if st.button("Send", type="primary", icon=":material/send:", key=f"intra_forward_{form['form_id']}"):
            # Not the usual best-effort send: if the mail server refuses or
            # can't be reached, safe_write shows "Couldn't send..." instead of
            # a false "Sent".
            with safe_write("send the undertakings to Ajith sir"):
                subject, body, attachments = email
                send_email(to_email, subject, body, raise_errors=True, cc=[UNDERTAKINGS_CC], attachments=attachments)
                st.session_state.intraschool_flash = (
                    f"Sent {count} undertaking{'s' if count != 1 else ''} for {form['reason']} to Ajith sir."
                )
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
                f":material/event: {'Come to school' if is_saturday(form) else 'Stay back'} on "
                f"{stay_date.strftime('%A, %d %b %Y')}, {timing(form)} (tentative)"
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
                st.download_button(
                    "Download paper copy to sign", icon=":material/download:",
                    data=_stayback_docx(form, student, row), file_name=_docx_name(form, student),
                    mime=DOCX_MIME, key=f"intra_paper_{fid}",
                )
                st.caption("Print it, have your guardian sign it, and **bring it to school**.")
                with st.expander("The undertaking they gave"):
                    st.caption(row.get("undertaking") or "")
            else:
                _online_form(form, student, me)


def _online_form(form, student, me):
    fid = form["form_id"]
    st.caption("To be filled in by your parent or guardian. You'll then be emailed a paper copy to bring to school, signed.")
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
                    saved = get_client().table("intraschool_form_members").update({
                        "guardian_name": guardian_name.strip(),
                        "guardian_relation": relation.strip(),
                        "guardian_phone": digits,
                        "guardian_email": email.strip(),
                        "undertaking": undertaking_text(form, student, guardian_name.strip(), relation.strip()),
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("form_id", fid).eq("user_id", me).execute().data[0]
                    invalidate_cache()
                    if send_paper_copy(form, student, saved):
                        st.session_state.intraschool_flash = (
                            "Submitted. Thank you! A paper copy has been emailed to you - "
                            "print it, get it signed and bring it to school."
                        )
                    else:
                        st.session_state.intraschool_flash = "Submitted. Thank you!"
                        st.session_state.intraschool_warning = (
                            "We couldn't email you the paper copy. Use **Download paper copy to sign** below, "
                            "get it signed and bring it to school."
                        )
                    st.rerun()

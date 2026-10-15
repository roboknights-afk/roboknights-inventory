# The host's "Make permission slip" dialog on the Competitions page. Kept out
# of app_pages/competitions.py (already 2,300 lines); the page only owns the
# button and one session flag, `slip_competition_id`.
#
# The flow, top to bottom:
#   1. Each event on the competition's E2C listing gets a mode (offline /
#      hybrid / online). Online events are left off the slip, since nobody
#      leaves school for them. Modes start from what was saved last time, else
#      the event's own name ("(Online)"), else the competition's mode.
#      A competition that runs over two days (E2C's date says so) also gets a
#      Day column, filled from what was saved last time (read off the
#      brochure), else "Both days".
#   2. "Build student list" gathers everyone CONFIRMED (green on E2C; white
#      cells are listed but not confirmed and are left out) for the kept events
#      and looks each up (slip_roster.py): Exun's student sheet first, then the
#      RoboKnights members. Anyone it can't find stays in the table with blanks.
#      A two-day competition gets one list per day, from that day's events.
#   3. The host fixes anything wrong right in the tables, then "Add to the
#      document" writes ONE SLIP PER DAY at the top of the permission-slip
#      Google Doc (perm_slip_writer.py), Day 1 above Day 2. All or nothing: if a
#      later day fails, the ones already added are taken back out.
#
#   4. Once they are in the document, #e2c-announcements is told ("the automatic
#      slip is done, kindly check") and the person who checks slips is pinged
#      (notify_slip_done).
#
# Nothing is written or posted anywhere until that last button. Native
# Streamlit only.

import os
from datetime import date

import pandas as pd
import streamlit as st

import perm_slip_check
import perm_slip_writer
import slip_roster
from e2c_import import fetch_event_rosters
from shared import DISCORD_CHANNELS, get_client, invalidate_cache, log_action, send_discord_message

STATE_KEY = "slip_state"
FLAG_KEY = "slip_competition_id"
STUDENT_COLUMNS = ["Adm. No.", "Name", "Events", "Class-Sec", "Mobile", "Check"]

_CHECK_TEXT = {
    "found": "",
    "missing": "Not found - fill in",
    "ambiguous": "Two students share this name - fill in",
}


def _day_names(numbers):
    # [1] -> "Day 1", [1, 2] -> "Day 1 and Day 2", [1, 2, 3] -> "Day 1, Day 2 and Day 3"
    names = [f"Day {n}" for n in numbers]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def notify_slip_done(competition_name, days_written=None):
    """Tell #e2c-announcements the slip is made, pinging whoever checks slips
    (the SLIP_PING_DISCORD_USER_ID secret - one Discord user id, kept out of
    the public repo like the registration-deadline ping's). `days_written` is
    the day numbers of a two-day competition's slips ("the slips for X (Day 1
    and Day 2) are done"); None for an ordinary one-day slip. Best-effort: the
    slips are already in the document, so this never raises. -> (ok, note for
    the host)."""
    if not os.environ.get(DISCORD_CHANNELS["e2c_announcements"]):
        return False, "The E2C announcements webhook isn't set up, so nobody was told on Discord."
    ping_id = (os.environ.get("SLIP_PING_DISCORD_USER_ID") or "").strip()
    mention = f"<@{ping_id}> " if ping_id.isdigit() else ""
    if days_written and len(days_written) > 1:
        text = f"The automatic slips for **{competition_name}** ({_day_names(days_written)}) are done, kindly check."
    elif days_written:
        text = f"The automatic slip for **{competition_name}** ({_day_names(days_written)}) is done, kindly check."
    else:
        text = f"The automatic slip for **{competition_name}** is done, kindly check."
    try:
        message_id = send_discord_message(f"{mention}{text}", channel="e2c_announcements")
    except Exception:
        message_id = None
    if not message_id:
        return False, "The slip is made, but the note to #e2c-announcements didn't go through."
    if not mention:
        return True, "Posted in #e2c-announcements, but nobody was pinged (SLIP_PING_DISCORD_USER_ID isn't set)."
    return True, "Posted in #e2c-announcements and pinged sid."


def close_slip():
    st.session_state[FLAG_KEY] = None
    st.session_state.pop(STATE_KEY, None)


def open_slip(competition_id):
    # A dialog stays up only while something re-calls it each run, so the page
    # drives it from this flag (same pattern as its other dialogs). Only one
    # dialog can be open per run, so the others are closed here.
    st.session_state[FLAG_KEY] = competition_id
    st.session_state.pop(STATE_KEY, None)
    st.session_state.show_add_competition = False
    st.session_state.show_e2c_import = False
    st.session_state.editing_competition_id = None
    st.session_state.finalizing_event_id = None


# --- slow lookups, cached so the dialog's reruns don't repeat them ---------------

@st.cache_data(ttl=600, show_spinner=False)
def _exun_students(sheet_url, service_account_b64):
    return slip_roster.load_exun_students(sheet_url, service_account_b64)


@st.cache_data(ttl=60, show_spinner=False)
def _e2c_rosters(competition_name):
    return fetch_event_rosters(competition_name)


@st.cache_data(ttl=30, show_spinner=False)
def _doc_blocks(doc_id, service_account_b64):
    headers, email = perm_slip_writer._headers(service_account_b64)
    return perm_slip_check.parse_blocks(perm_slip_writer.get_doc(doc_id, headers, email))


def _rk_students():
    return slip_roster.students_from_users(
        [u for u in get_client().table("users").select("name,grade,section,admission_no,phone_no").execute().data]
    )


def _student_table(rows):
    return pd.DataFrame({
        "Adm. No.": [r["adm"] for r in rows],
        "Name": [r["name"] for r in rows],
        "Events": [", ".join(r["events"]) for r in rows],
        "Class-Sec": [r["class_sec"] for r in rows],
        "Mobile": [r["mobile"] for r in rows],
        "Check": [_CHECK_TEXT[r["status"]] or f"Found in {r['source']}" for r in rows],
    })


def _edit_students(cid, day_no, frame, builds):
    """One day's student table, editable. -> (the students as edited, blank rows
    dropped; the sign-offs they need), after showing what is still blank."""
    students = st.data_editor(
        frame, hide_index=True, num_rows="dynamic", key=f"slip_students_{cid}_{day_no}_{builds}",
        disabled=["Check"], width="stretch",
    ).fillna("")
    students = students[students["Name"].astype(str).str.strip() != ""]
    if students.empty:
        st.caption("Nobody is listed, so no slip will be made.")
        return students, []
    blank = {c: int((students[c].astype(str).str.strip() == "").sum()) for c in ("Adm. No.", "Class-Sec", "Mobile")}
    if any(blank.values()):
        st.warning("Still blank: " + ", ".join(f"{n} {c.lower()}" for c, n in blank.items() if n) + ".")
    approvers = slip_roster.approvers_for(students["Class-Sec"].astype(str).tolist())
    unknown_class = blank["Class-Sec"]
    st.caption(
        "Sign-offs on this slip: " + ", ".join(perm_slip_writer.APPROVER_LABELS[a] for a in approvers) + "."
        + (f" ({unknown_class} student(s) have no class yet, so a Headmistress box may be missing.)" if unknown_class else "")
    )
    return students, approvers


# --- the dialog --------------------------------------------------------------------

@st.dialog("Make permission slip", width="large", on_dismiss=close_slip)
def render_slip_dialog(comp):
    if not st.session_state.get("is_host"):
        st.error("Only hosts can make permission slips.")
        return
    cid = comp["competition_id"]
    state = st.session_state.get(STATE_KEY)
    if not state or state["cid"] != cid:
        state = st.session_state[STATE_KEY] = {"cid": cid, "students": None, "built_for": None, "builds": 0, "done": None}

    doc_url = os.environ.get("PERM_SLIP_DOC_URL", "")
    sa_b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "")
    exun_url = os.environ.get("EXUN_DATA_SHEET_URL", "")
    st.markdown(f"**{comp['name']}**")
    if not doc_url or not sa_b64:
        st.error("The permission-slip document isn't connected (PERM_SLIP_DOC_URL / GOOGLE_SERVICE_ACCOUNT_JSON_B64 "
                 "missing from the app's secrets).")
        return
    doc_id = perm_slip_check.doc_id_from_url(doc_url)

    if state["done"]:
        done = state["done"]    # [(day label or "", students)]
        if len(done) == 1:
            label, count = done[0]
            st.success(f"Added the slip{f' for {label}' if label else ''}: {count} students, at the top of the document.")
        else:
            st.success(f"Added {len(done)} slips at the top of the document: "
                       + "; ".join(f"{label} ({count} students)" for label, count in done) + ".")
        notice_ok, notice_text = state.get("notice", (True, ""))
        (st.info if notice_ok else st.warning)(notice_text)
        st.link_button("Open the document", doc_url, icon=":material/open_in_new:")
        if st.button("Close", key="slip_close"):
            close_slip()
            st.rerun()
        return

    comp_day = date.fromisoformat(comp["competition_date"])

    try:
        with st.spinner("Reading the E2C sheet..."):
            rosters = _e2c_rosters(comp["name"])
    except Exception as e:
        st.error(f"Couldn't read the E2C sheet: {e}")
        return
    if rosters is None:
        st.error("This competition isn't on the E2C sheet, so there are no names to put on a slip.")
        return

    # A competition over two days gets a slip for EACH day (the host, 2026-10-15).
    days = slip_roster.parse_days(comp_day, rosters.get("date_text"))
    n_days = len(days)
    multi = n_days > 1
    day_numbers = list(range(1, n_days + 1))

    # Is it already in the document? A second copy would be signed twice. Each
    # day is looked for on its own: Day 1 may be there while Day 2 isn't.
    found = {}
    try:
        blocks = _doc_blocks(doc_id, sa_b64)
        found = {i: perm_slip_check.judge(comp["name"], d, blocks) for i, d in zip(day_numbers, days)}
    except Exception as e:
        st.warning(f"Couldn't check whether the document already has this slip: {e}")
    in_doc = [i for i, (status, _) in found.items() if status in ("ready", "incomplete")]
    add_anyway = True
    if in_doc:
        where = " and ".join(slip_roster.day_label(i, days[i - 1]) for i in in_doc) if multi else "this competition"
        lacking = "; ".join(
            (f"{slip_roster.day_label(i, days[i - 1])}: " if multi else "") + ", ".join(found[i][1])
            for i in in_doc if found[i][0] == "incomplete"
        )
        st.warning(
            f"The document already has a slip for {where}"
            + (f" (not complete yet: {lacking})" if lacking else "")
            + ". Adding another would duplicate it."
        )
        add_anyway = st.checkbox("Add another one anyway", key="slip_add_anyway")

    exun_rows = []
    if exun_url:
        try:
            exun_rows = _exun_students(exun_url, sa_b64)
        except Exception as e:
            st.warning(f"Exun's student sheet couldn't be read, so only RoboKnights members will be filled in. ({e})")
    else:
        st.warning("Exun's student sheet isn't connected (EXUN_DATA_SHEET_URL), so only RoboKnights members "
                   "will be filled in.")

    # --- 1. which events go on the slip, and on which day -----------------------------
    st.markdown("##### 1. Events")
    stored = {k.lower(): v for k, v in (comp.get("event_modes") or {}).items()}
    stored_days = {k.lower(): v for k, v in (comp.get("event_days") or {}).items()}
    # Only names E2C shades GREEN (confirmed) count. White ones are listed but
    # not confirmed, so they are left off and an event with none confirmed is skipped.
    with_names = [ev for ev in rosters["events"] if slip_roster.confirmed_names(ev)]
    skipped = len(rosters["events"]) - len(with_names)
    unconfirmed = sum(len(ev["names"]) - len(slip_roster.confirmed_names(ev)) for ev in rosters["events"])
    if not with_names:
        st.info("Nobody is confirmed (green) for any event of this competition on the E2C sheet yet.")
        return
    columns = {
        "Event": [ev["name"] for ev in with_names],
        "Mode": [slip_roster.default_mode(ev["name"], comp.get("mode"), stored) for ev in with_names],
    }
    column_config = {"Mode": st.column_config.SelectboxColumn("Mode", options=list(slip_roster.MODES), required=True)}
    if multi:
        columns["Day"] = [slip_roster.default_day(ev["name"], n_days, stored_days) for ev in with_names]
        column_config["Day"] = st.column_config.SelectboxColumn(
            "Day", options=slip_roster.day_choices(n_days), required=True,
            help="The day the brochure puts the event on. An event on both days goes on both slips.",
        )
    columns["Confirmed names"] = [len(slip_roster.confirmed_names(ev)) for ev in with_names]
    st.caption(
        "Only names shaded green (confirmed) on E2C are used"
        + (f"; {unconfirmed} white (unconfirmed) name(s) are left out" if unconfirmed else "")
        + (f", and {skipped} event(s) with nobody confirmed are skipped" if skipped else "")
        + ". Offline and hybrid events go on the slip; online ones are left off. Change a mode if the brochure "
        "says otherwise."
    )
    if multi:
        st.caption(
            "This runs over " + " and ".join(slip_roster.format_date(d) for d in days) + ", so each day gets its own "
            "slip with just that day's events. Set each event's Day from the brochure."
        )
    edited_events = st.data_editor(
        pd.DataFrame(columns), hide_index=True, key=f"slip_events_{cid}", disabled=["Event", "Confirmed names"],
        width="stretch", column_config=column_config,
    )
    modes = tuple(edited_events["Mode"])
    choices = tuple(edited_events["Day"]) if multi else ("Day 1",) * len(with_names)
    wanted_days = [slip_roster.days_of(c, n_days) for c in choices]
    on_slip = [m in slip_roster.ON_SLIP_MODES for m in modes]
    kept_on = {i: [ev for ev, ok, ds in zip(with_names, on_slip, wanted_days) if ok and i in ds] for i in day_numbers}
    st.caption(
        f"{sum(on_slip)} of {len(with_names)} events on the slip"
        + (": " + ", ".join(f"{len(kept_on[i])} on Day {i}" for i in day_numbers) if multi else "") + "."
    )

    if st.button("Build student list", key="slip_build", icon=":material/groups:", disabled=not sum(on_slip)):
        exun_dir, rk_dir = slip_roster.Directory(exun_rows), slip_roster.Directory(_rk_students())
        state["students"] = {
            i: _student_table(slip_roster.students_for_events(kept_on[i], exun_dir, rk_dir)) for i in day_numbers
        }
        state["built_for"] = (modes, choices)
        state["builds"] += 1
    if state["students"] is None:
        return
    if state["built_for"] != (modes, choices):
        st.warning("The event modes or days changed since this list was built. Build it again to match (any edits "
                   "below are lost).")

    # --- 2. the slips' details ----------------------------------------------------------
    st.markdown("##### 2. Slip details")
    c1, c2 = st.columns(2)
    event_name = c1.text_input("Event", value=comp["name"], key=f"slip_event_{cid}")
    organising = c2.text_input("Organising", value=comp.get("venue") or "", key=f"slip_org_{cid}")
    mode_help = "What the document's Mode box says. Existing slips say Offline."
    if multi:
        mode_text = st.text_input("Mode", value="Offline", key=f"slip_mode_{cid}", help=mode_help)
        date_texts = {
            i: col.text_input(f"Date, Day {i}", value=slip_roster.format_date(d), key=f"slip_date_{cid}_{i}")
            for i, d, col in zip(day_numbers, days, st.columns(n_days))
        }
    else:
        c3, c4 = st.columns(2)
        mode_text = c3.text_input("Mode", value="Offline", key=f"slip_mode_{cid}", help=mode_help)
        date_texts = {1: c4.text_input("Date", value=slip_roster.format_date(comp_day), key=f"slip_date_{cid}")}
    st.caption("On-Duty Staff is left blank for the teacher to fill in, and Department says Computer Science."
               + (" Event, Organising and Mode are the same on every day's slip." if multi else ""))

    # --- 3. students ----------------------------------------------------------------------
    st.markdown("##### 3. Students")
    st.caption("Fix or fill in anything below, remove a row by selecting it and pressing Delete, or add a student at "
               "the bottom. Admission numbers that start with E, R or V are the school's own.")
    tabs = st.tabs([slip_roster.day_label(i, d) for i, d in zip(day_numbers, days)]) if multi else None
    edited = {}
    for i in day_numbers:
        with (tabs[i - 1] if multi else st.container()):
            edited[i] = _edit_students(cid, i, state["students"][i], state["builds"])

    # --- 4. write it ------------------------------------------------------------------------
    to_write = [i for i in day_numbers if not edited[i][0].empty and (i not in in_doc or add_anyway)]
    if multi and in_doc and not add_anyway:
        st.caption("Already in the document, so left out: " + ", ".join(f"Day {i}" for i in in_doc)
                   + ". Tick \"Add another one anyway\" to add them again.")
    if st.button("Add to the document", key="slip_write", type="primary", icon=":material/note_add:",
                 disabled=not to_write):
        slips = {}
        for i in to_write:
            students, approvers = edited[i]
            slips[i] = {
                "event": event_name, "organising": organising, "mode": mode_text, "date_text": date_texts[i],
                "department": "Computer Science", "approvers": approvers,
                "students": [
                    {"adm": str(r["Adm. No."]), "name": str(r["Name"]), "sub_event": str(r["Events"]),
                     "class_sec": str(r["Class-Sec"]), "mobile": str(r["Mobile"])}
                    for _, r in students.iterrows()
                ],
            }
        written = []
        try:
            with st.spinner("Adding the slip to the document..." if len(to_write) == 1 else "Adding the slips to the document..."):
                # Every slip goes in at the TOP, so the last one added is the one
                # read first: the last day goes in first and Day 1 ends up above Day 2.
                for i in sorted(to_write, reverse=True):
                    perm_slip_writer.write_slip(doc_id, sa_b64, slips[i])
                    written.append(i)
        except Exception as e:
            for _ in written:     # take back out what already went in (each is the newest block in turn)
                perm_slip_writer.remove_block(doc_id, sa_b64)
            undone = " The other day's slip was taken back out, so nothing was left in the document." if written else ""
            if isinstance(e, RuntimeError):
                st.error(str(e) + undone)     # our own messages: never carry the document id
            else:
                st.error(f"Couldn't add the slip ({type(e).__name__}). Nothing was left half-made in the document.")
            return
        _doc_blocks.clear()
        for i in sorted(written):
            log_action(
                "slip.created", "permission_slip", comp["name"],
                f"Added a permission slip for {event_name}"
                + (f" ({date_texts[i]})" if multi else "")
                + f" to the document ({len(slips[i]['students'])} students)",
                {"students": len(slips[i]["students"]), "events": len(kept_on[i]),
                 "approvers": slips[i]["approvers"], **({"day": i} if multi else {})},
            )
        # Remember the modes (and days) for next time. One update per column, so
        # a column whose migration hasn't been run yet doesn't lose the other.
        remembered = [("event_modes", {ev["name"].lower(): m for ev, m in zip(with_names, modes)})]
        if multi:
            remembered.append(("event_days", {ev["name"].lower(): ds for ev, ds in zip(with_names, wanted_days)}))
        for column, value in remembered:
            try:
                get_client().table("competitions").update({column: value}).eq("competition_id", cid).execute()
            except Exception:
                pass
        invalidate_cache()
        state["notice"] = notify_slip_done(comp["name"], sorted(written) if multi else None)
        state["done"] = [(slip_roster.day_label(i, days[i - 1]) if multi else "", len(slips[i]["students"]))
                         for i in sorted(written)]
        st.rerun()

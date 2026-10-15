# The host's "Make permission slip" dialog on the Competitions page. Kept out
# of app_pages/competitions.py (already 2,300 lines); the page only owns the
# button and one session flag, `slip_competition_id`.
#
# The flow, top to bottom:
#   1. Each event on the competition's E2C listing gets a mode (offline /
#      hybrid / online). Online events are left off the slip, since nobody
#      leaves school for them. Modes start from what was saved last time, else
#      the event's own name ("(Online)"), else the competition's mode.
#   2. "Build student list" gathers everyone CONFIRMED (green on E2C; white
#      cells are listed but not confirmed and are left out) for the kept events
#      and looks each up (slip_roster.py): Exun's student sheet first, then the
#      RoboKnights members. Anyone it can't find stays in the table with blanks.
#   3. The host fixes anything wrong right in the table, then "Add to the
#      document" writes the slip at the top of the permission-slip Google Doc
#      (perm_slip_writer.py).
#
# Nothing is written anywhere until that last button. Native Streamlit only.

import os
from datetime import date

import pandas as pd
import streamlit as st

import perm_slip_check
import perm_slip_writer
import slip_roster
from e2c_import import fetch_event_rosters
from shared import get_client, invalidate_cache, log_action

STATE_KEY = "slip_state"
FLAG_KEY = "slip_competition_id"
STUDENT_COLUMNS = ["Adm. No.", "Name", "Events", "Class-Sec", "Mobile", "Check"]

_CHECK_TEXT = {
    "found": "",
    "missing": "Not found - fill in",
    "ambiguous": "Two students share this name - fill in",
}


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


# --- the dialog --------------------------------------------------------------------

@st.dialog("Make permission slip", width="large", on_dismiss=close_slip)
def render_slip_dialog(comp):
    if not st.session_state.get("is_host"):
        st.error("Only hosts can make permission slips.")
        return
    cid = comp["competition_id"]
    state = st.session_state.get(STATE_KEY)
    if not state or state["cid"] != cid:
        state = st.session_state[STATE_KEY] = {"cid": cid, "students": None, "built_modes": None, "builds": 0, "done": None}

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
        st.success(f"Added the slip: {state['done']} students, at the top of the document.")
        st.link_button("Open the document", doc_url, icon=":material/open_in_new:")
        if st.button("Close", key="slip_close"):
            close_slip()
            st.rerun()
        return

    comp_day = date.fromisoformat(comp["competition_date"])

    # Is it already in the document? A second copy would be signed twice.
    try:
        status, missing = perm_slip_check.judge(comp["name"], comp_day, _doc_blocks(doc_id, sa_b64))
    except Exception as e:
        status, missing = "unknown", []
        st.warning(f"Couldn't check whether the document already has this slip: {e}")
    already_ok = True
    if status in ("ready", "incomplete"):
        st.warning(
            "The document already has a slip for this competition"
            + (f" (not complete yet: {', '.join(missing)})." if status == "incomplete" else ".")
            + " Adding another would duplicate it."
        )
        already_ok = st.checkbox("Add another one anyway", key="slip_add_anyway")

    try:
        with st.spinner("Reading the E2C sheet..."):
            rosters = _e2c_rosters(comp["name"])
    except Exception as e:
        st.error(f"Couldn't read the E2C sheet: {e}")
        return
    if rosters is None:
        st.error("This competition isn't on the E2C sheet, so there are no names to put on a slip.")
        return

    exun_rows = []
    if exun_url:
        try:
            exun_rows = _exun_students(exun_url, sa_b64)
        except Exception as e:
            st.warning(f"Exun's student sheet couldn't be read, so only RoboKnights members will be filled in. ({e})")
    else:
        st.warning("Exun's student sheet isn't connected (EXUN_DATA_SHEET_URL), so only RoboKnights members "
                   "will be filled in.")

    # --- 1. which events go on the slip -------------------------------------------
    st.markdown("##### 1. Events")
    stored = {k.lower(): v for k, v in (comp.get("event_modes") or {}).items()}
    # Only names E2C shades GREEN (confirmed) count. White ones are listed but
    # not confirmed, so they are left off and an event with none confirmed is skipped.
    with_names = [ev for ev in rosters["events"] if slip_roster.confirmed_names(ev)]
    skipped = len(rosters["events"]) - len(with_names)
    unconfirmed = sum(len(ev["names"]) - len(slip_roster.confirmed_names(ev)) for ev in rosters["events"])
    if not with_names:
        st.info("Nobody is confirmed (green) for any event of this competition on the E2C sheet yet.")
        return
    events_df = pd.DataFrame({
        "Event": [ev["name"] for ev in with_names],
        "Mode": [slip_roster.default_mode(ev["name"], comp.get("mode"), stored) for ev in with_names],
        "Confirmed names": [len(slip_roster.confirmed_names(ev)) for ev in with_names],
    })
    st.caption(
        "Only names shaded green (confirmed) on E2C are used"
        + (f"; {unconfirmed} white (unconfirmed) name(s) are left out" if unconfirmed else "")
        + (f", and {skipped} event(s) with nobody confirmed are skipped" if skipped else "")
        + ". Offline and hybrid events go on the slip; online ones are left off. Change a mode if the brochure "
        "says otherwise."
    )
    edited_events = st.data_editor(
        events_df, hide_index=True, key=f"slip_events_{cid}", disabled=["Event", "Confirmed names"], width="stretch",
        column_config={"Mode": st.column_config.SelectboxColumn("Mode", options=list(slip_roster.MODES), required=True)},
    )
    modes = tuple(edited_events["Mode"])
    kept = [ev for ev, m in zip(with_names, modes) if m in slip_roster.ON_SLIP_MODES]
    st.caption(f"{len(kept)} of {len(with_names)} events on the slip.")

    if st.button("Build student list", key="slip_build", icon=":material/groups:", disabled=not kept):
        rows = slip_roster.students_for_events(kept, slip_roster.Directory(exun_rows), slip_roster.Directory(_rk_students()))
        state["students"] = pd.DataFrame({
            "Adm. No.": [r["adm"] for r in rows],
            "Name": [r["name"] for r in rows],
            "Events": [", ".join(r["events"]) for r in rows],
            "Class-Sec": [r["class_sec"] for r in rows],
            "Mobile": [r["mobile"] for r in rows],
            "Check": [_CHECK_TEXT[r["status"]] or f"Found in {r['source']}" for r in rows],
        })
        state["built_modes"] = modes
        state["builds"] += 1
    if state["students"] is None:
        return
    if state["built_modes"] != modes:
        st.warning("The event modes changed since this list was built. Build it again to match (any edits below are lost).")

    # --- 2. the slip's details ----------------------------------------------------------
    st.markdown("##### 2. Slip details")
    c1, c2 = st.columns(2)
    event_name = c1.text_input("Event", value=comp["name"], key=f"slip_event_{cid}")
    organising = c2.text_input("Organising", value=comp.get("venue") or "", key=f"slip_org_{cid}")
    c3, c4 = st.columns(2)
    mode_text = c3.text_input("Mode", value="Offline", key=f"slip_mode_{cid}",
                              help="What the document's Mode box says. Existing slips say Offline.")
    date_text = c4.text_input("Date", value=slip_roster.format_date(comp_day, rosters.get("date_text")), key=f"slip_date_{cid}")
    st.caption("On-Duty Staff is left blank for the teacher to fill in, and Department says Computer Science.")

    # --- 3. students ----------------------------------------------------------------------
    st.markdown("##### 3. Students")
    st.caption("Fix or fill in anything below, remove a row by selecting it and pressing Delete, or add a student at "
               "the bottom. Admission numbers that start with E, R or V are the school's own.")
    students = st.data_editor(
        state["students"], hide_index=True, num_rows="dynamic", key=f"slip_students_{cid}_{state['builds']}",
        disabled=["Check"], width="stretch",
    ).fillna("")
    students = students[students["Name"].astype(str).str.strip() != ""]

    blank = {c: int((students[c].astype(str).str.strip() == "").sum()) for c in ("Adm. No.", "Class-Sec", "Mobile")}
    if any(blank.values()):
        st.warning("Still blank: " + ", ".join(f"{n} {c.lower()}" for c, n in blank.items() if n) + ".")
    approvers = slip_roster.approvers_for(students["Class-Sec"].astype(str).tolist())
    unknown_class = blank["Class-Sec"]
    st.caption(
        "Sign-offs on this slip: " + ", ".join(perm_slip_writer.APPROVER_LABELS[a] for a in approvers) + "."
        + (f" ({unknown_class} student(s) have no class yet, so a Headmistress box may be missing.)" if unknown_class else "")
    )

    # --- 4. write it ------------------------------------------------------------------------
    if st.button(
        "Add to the document", key="slip_write", type="primary", icon=":material/note_add:",
        disabled=students.empty or not already_ok,
    ):
        slip = {
            "event": event_name, "organising": organising, "mode": mode_text, "date_text": date_text,
            "department": "Computer Science", "approvers": approvers,
            "students": [
                {"adm": str(r["Adm. No."]), "name": str(r["Name"]), "sub_event": str(r["Events"]),
                 "class_sec": str(r["Class-Sec"]), "mobile": str(r["Mobile"])}
                for _, r in students.iterrows()
            ],
        }
        try:
            with st.spinner("Adding the slip to the document..."):
                perm_slip_writer.write_slip(doc_id, sa_b64, slip)
        except RuntimeError as e:
            st.error(str(e))      # our own messages: never carry the document id
            return
        except Exception as e:
            st.error(f"Couldn't add the slip ({type(e).__name__}). Nothing was left half-made in the document.")
            return
        _doc_blocks.clear()
        log_action(
            "slip.created", "permission_slip", comp["name"],
            f"Added a permission slip for {event_name} to the document ({len(slip['students'])} students)",
            {"students": len(slip["students"]), "events": len(kept), "approvers": approvers},
        )
        try:   # remember the modes for next time; harmless if the column isn't there yet
            get_client().table("competitions").update(
                {"event_modes": {ev["name"].lower(): m for ev, m in zip(with_names, modes)}}
            ).eq("competition_id", cid).execute()
            invalidate_cache()
        except Exception:
            pass
        state["done"] = len(slip["students"])
        st.rerun()

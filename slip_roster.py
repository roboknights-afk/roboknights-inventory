# The data side of a permission slip: WHO is on it and WHICH sign-offs it
# needs. perm_slip_writer.py puts the result into the Google Doc. Its own
# module with no Streamlit import of its own, so it can be tested without the
# app.
#
# Where the names come from (host's rules, 2026-10-15):
#   - the E2C sheet lists who is registered, event by event (e2c_import.
#     fetch_event_rosters). Only CONFIRMED names count (cell shaded green; a
#     white cell is listed but not confirmed). Only offline and hybrid events
#     go on the slip: an online event means nobody leaves school.
#   - each name's admission no., class, section and mobile come from the
#     Exun club's student sheet first, then from the RoboKnights `users`
#     table for anyone not in it.
#   - a name found in neither is NEVER guessed: it goes on the slip with the
#     blanks left for the host to fill in.
#
# PRIVACY: these are students' names and phone numbers and the repo is public.
# Nothing here prints one.

import base64
import json
import re
from datetime import date

import requests

from e2c_import import _normalize_name, _tokens_match

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
MODES = ("offline", "hybrid", "online")
ON_SLIP_MODES = ("offline", "hybrid")

# Exun's sheet has one members tab per school year, named M<start><end>
# (M20262027), next to the E<years> events tabs. The newest is the one.
_MEMBERS_TAB = re.compile(r"^M(\d{4})(\d{4})$")


# --- Exun's student sheet --------------------------------------------------------

def sheet_id_from_url(value):
    match = re.search(r"/spreadsheets/d/([\w-]+)", value or "")
    return match.group(1) if match else (value or "").strip()


def clean_mobile(raw):
    # "9812345678", "98123 45678", "9812345678 (Father)" -> "9812345678".
    # No ten-digit number in it: keep nothing rather than a half number.
    text = re.sub(r"(?<=\d)[ -](?=\d)", "", raw or "")
    match = re.search(r"(?<!\d)(\d{10})(?!\d)", text)
    return match.group(1) if match else ""


def class_sec(cls, sec):
    cls, sec = str(cls or "").strip(), str(sec or "").strip().upper()
    return f"{cls}-{sec}" if cls and sec else cls


def load_exun_students(sheet_value, service_account_b64):
    """Every student on the newest members tab of Exun's sheet, as
    {name, adm, cls, sec, mobile}. Read-only access is enough."""
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    sheet_id = sheet_id_from_url(sheet_value)
    info = json.loads(base64.b64decode(service_account_b64))
    creds = Credentials.from_service_account_info(info, scopes=[SHEETS_SCOPE])
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}

    def get(path, **params):
        response = requests.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}{path}",
                                headers=headers, params=params, timeout=60)
        if not response.ok:
            # not raise_for_status(): its message would carry the sheet id into logs
            raise RuntimeError(
                f"Google Sheets refused to read the Exun data ({response.status_code}). Share the sheet with "
                f"{info.get('client_email', 'the service account')} and check the Sheets API is enabled."
            )
        return response.json()

    tabs = [s["properties"]["title"] for s in get("", fields="sheets.properties.title")["sheets"]]
    members = [t for t in tabs if _MEMBERS_TAB.match(t)]
    if not members:
        raise RuntimeError("No members tab (like M20262027) found in the Exun data sheet.")
    tab = max(members, key=lambda t: int(_MEMBERS_TAB.match(t).group(1)))
    # Quoted: a tab called M20262027 would otherwise read as the CELL M20262027.
    rows = get(f"/values/{requests.utils.quote(chr(39) + tab + chr(39))}").get("values", [])
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    c_name, c_cls, c_sec = col("student name", "name"), col("class"), col("sec", "section")
    c_adm, c_m1, c_m2 = col("studentid", "student id", "adm. no.", "admission no"), col("mobile1", "mobile"), col("mobile2")
    if c_name is None or c_adm is None:
        raise RuntimeError("The Exun data sheet's columns changed: need 'Student Name' and 'STUDENTID'.")

    def cell(row, i):
        return row[i].strip() if i is not None and i < len(row) else ""

    students = []
    for row in rows[1:]:
        name = cell(row, c_name)
        if not name:
            continue
        students.append({
            "name": name, "adm": cell(row, c_adm).upper(), "cls": cell(row, c_cls), "sec": cell(row, c_sec),
            "mobile": clean_mobile(cell(row, c_m1)) or clean_mobile(cell(row, c_m2)),
        })
    return students


def students_from_users(users):
    # The RoboKnights `users` rows, in the same shape as Exun's.
    out = []
    for u in users:
        if not (u.get("name") or "").strip():
            continue
        out.append({
            "name": u["name"], "adm": (u.get("admission_no") or "").strip().upper(),
            "cls": str(u.get("grade") or "").strip(), "sec": (u.get("section") or "").strip(),
            "mobile": clean_mobile(u.get("phone_no")),
        })
    return out


# --- matching a name from E2C to a student -----------------------------------------

class Directory:
    """A list of students searchable by the name E2C shows. Same rule as the
    rest of the app (e2c_import._NameMatcher): the words must match exactly,
    except that a lone initial may stand in for a full word - so "Medhansh T.
    Pandya" finds "Medhansh Tanmay Pandya" but "Ashray" alone finds nobody
    with a longer name."""

    def __init__(self, students):
        self._rows = [(_normalize_name(s["name"]).lower().split(), s) for s in students]

    def find(self, name):
        tokens = _normalize_name(name).lower().split()
        if not tokens:
            return []
        return [s for toks, s in self._rows if toks == tokens or _tokens_match(tokens, toks)]


def resolve(name, exun, rk):
    """-> {"status": "found" | "ambiguous" | "missing", "student": {...} | None, "source": str}.

    Exun's sheet is tried first, then the RoboKnights members; anything the
    first lacks (say a mobile number) is filled from the second when it is
    clearly the same person. Two different students with the same name make
    it "ambiguous": nothing is filled in, the host picks."""
    first = exun.find(name)
    second = rk.find(name)
    if len(first) > 1 or (not first and len(second) > 1):
        # two students with one name, unless every hit shares an admission no.
        pool = first or second
        if len({s["adm"] for s in pool if s["adm"]}) > 1 or any(not s["adm"] for s in pool):
            return {"status": "ambiguous", "student": None, "source": ""}
        first, second = [pool[0]], []
    if first:
        student, source = dict(first[0]), "Exun"
        match = next((s for s in second if s["adm"] and s["adm"] == student["adm"]), None) or (
            second[0] if len(second) == 1 and not student["adm"] else None)
        if match:
            for key in ("adm", "cls", "sec", "mobile"):
                if not student[key] and match[key]:
                    student[key] = match[key]
        return {"status": "found", "student": student, "source": source}
    if second:
        return {"status": "found", "student": dict(second[0]), "source": "RoboKnights"}
    return {"status": "missing", "student": None, "source": ""}


# --- events and their modes ---------------------------------------------------------

def default_mode(event_name, competition_mode, stored_modes=None):
    """The mode to start from for an event: what the host saved for it, else
    "online" if its own name says so ("Virtual Warriors (Online)"), else the
    competition's mode. The host can change it in the dialog before anything
    is written."""
    stored = (stored_modes or {}).get((event_name or "").strip().lower())
    if stored in MODES:
        return stored
    if re.search(r"\(\s*online\s*\)", event_name or "", re.I):
        return "online"
    mode = (competition_mode or "").strip().lower()
    return mode if mode in MODES else "offline"


def confirmed_names(event):
    # E2C shades a registration green once it is confirmed; a white cell is
    # someone listed but not (yet) confirmed. Only green ones go on a slip
    # (host, 2026-10-15: "why is it taking names from white cells").
    return [n for n in event["names"] if n.get("confirmed")]


def students_for_events(selected_events, exun, rk):
    """selected_events: [{"name": event, "names": [{"name", "confirmed"}]}].
    Only CONFIRMED (green on E2C) names are used - see confirmed_names.
    One row per STUDENT (a student in two events appears once, with both
    events). Order is first appearance. Each row: {name, adm, class_sec,
    mobile, events, status, source}. Unfound / ambiguous students keep the
    name as E2C shows it and blanks for the rest."""
    rows, index = [], {}
    for ev in selected_events:
        for entry in confirmed_names(ev):
            shown = _normalize_name(entry["name"])
            if not shown:
                continue
            key = shown.lower()
            if key in index:
                row = rows[index[key]]
                if ev["name"] not in row["events"]:
                    row["events"].append(ev["name"])
                continue
            res = resolve(shown, exun, rk)
            s = res["student"] or {}
            index[key] = len(rows)
            # E2C often has names in all lower case; a slip shouldn't.
            typed = shown.title() if (shown.islower() or shown.isupper()) else shown
            rows.append({
                "name": s.get("name") or typed,
                "adm": s.get("adm", ""), "class_sec": class_sec(s.get("cls"), s.get("sec")),
                "mobile": s.get("mobile", ""), "events": [ev["name"]],
                "status": res["status"], "source": res["source"], "e2c_name": shown,
            })
    return rows


# --- who has to sign ------------------------------------------------------------------

def grade_of(class_text):
    match = re.match(r"\s*(\d{1,2})", class_text or "")
    return int(match.group(1)) if match else None


def approvers_for(class_secs):
    """The sign-offs a slip needs, in the school's order: the HOD, then each
    Headmistress only if a student from her classes is on the slip, then the
    Vice Principal and the Principal. A student whose class isn't known adds
    nothing here - the dialog warns about those."""
    grades = {grade_of(c) for c in class_secs}
    out = ["hod"]
    if any(g is not None and 6 <= g <= 8 for g in grades):
        out.append("hm68")
    if any(g is not None and 9 <= g <= 10 for g in grades):
        out.append("hm910")
    return out + ["vp", "principal"]


# --- the slip's date ---------------------------------------------------------------------

def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def format_date(day, e2c_date_text=""):
    """The doc writes dates as "Friday, 16th October 2026". A two-day event
    ("October 27 & 28, 2026" on E2C) becomes "Tuesday, 27th & Wednesday, 28th
    October 2026"; anything else falls back to the single competition date."""
    text = e2c_date_text or ""
    # "October 27 & 28, 2026" (month first) or "23-24 October 2026" (day first)
    sep = r"\s*(?:&|and|-|–|to)\s*"
    month_first = re.search(rf"([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}}){sep}(\d{{1,2}}),?\s+(\d{{4}})", text)
    day_first = re.search(rf"(\d{{1,2}}){sep}(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?,?\s+(\d{{4}})", text)
    if month_first:
        month_word, a, b, year = month_first.group(1), month_first.group(2), month_first.group(3), month_first.group(4)
    elif day_first:
        a, b, month_word, year = day_first.group(1), day_first.group(2), day_first.group(3), day_first.group(4)
    else:
        month_word = None
    if month_word:
        months = ["january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december"]
        m = next((i for i, name in enumerate(months, 1) if name.startswith(month_word.lower()[:3])), None)
        try:
            d1, d2 = date(int(year), m, int(a)), date(int(year), m, int(b))
            if d1 < d2 and (d2 - d1).days <= 6:
                return (f"{d1.strftime('%A')}, {_ordinal(d1.day)} & {d2.strftime('%A')}, {_ordinal(d2.day)} "
                        f"{d1.strftime('%B %Y')}")
        except (TypeError, ValueError):
            pass
    return f"{day.strftime('%A')}, {_ordinal(day.day)} {day.strftime('%B %Y')}"

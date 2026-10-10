# Reads the club's "Inter School Event Permissions" Google Doc (the perm slip
# the school signs) and says whether a competition's slip is ready. Used by
# send_slip_reminders.py. Its own module with no Streamlit import, like
# consent_docx.py, so it can be tested without the app.
#
# The doc is ONE document for every competition. Each competition is a block:
#   - a small two-column table:  Event / Organising / Mode / Date / On-Duty Staff / Department
#   - then a participants table: S No | Adm. No. | Name | Sub-Event | Class-Sec | Mobile
#     (a second participants table can follow for the same competition)
#
# A competition's slip is "ready" when its block has Event, Organising, Mode
# and Date filled in and at least MIN_NAMES students listed (host's
# definition, 2026-10-11). Signatures are NOT checked - a Google Doc has no
# reliable signature field.
#
# The bot reads the doc as the Google service account (view-only access: the
# doc is shared with it as Viewer, scope documents.readonly).
#
# PRIVACY: this doc is full of students' names and phone numbers and the repo
# is public, so nothing here may print or return a participant's details -
# only counts and which headline fields are missing.

import base64
import json
import re
from datetime import date
from difflib import SequenceMatcher

import requests

MIN_NAMES = 2
NAME_MATCH = 0.6  # how alike the doc's event name and the dashboard's must be

_UNFILLED = {"", "-", "--", "—", "–", "tba", "tbd", "tbc", "n/a", "na", "nil", "?"}
_MONTHS = [
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
]


def doc_id_from_url(value):
    match = re.search(r"/document/d/([\w-]+)", value or "")
    return match.group(1) if match else (value or "").strip()


def fetch_doc(doc_id, service_account_b64):
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    info = json.loads(base64.b64decode(service_account_b64))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/documents.readonly"]
    )
    creds.refresh(Request())
    response = requests.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {creds.token}"}, timeout=30,
    )
    if not response.ok:
        # Deliberately not response.raise_for_status(): its message carries
        # the full url, i.e. the document id, into public Actions logs.
        try:
            reason = response.json().get("error", {}).get("status", "")
        except Exception:
            reason = ""
        raise RuntimeError(
            f"Google Docs refused the read ({response.status_code} {reason}). A 403/404 usually means "
            f"the doc isn't shared with {info.get('client_email', 'the service account')} or the "
            f"Google Docs API isn't enabled."
        )
    return response.json()


def _all_content(doc):
    if doc.get("body"):
        return doc["body"].get("content", [])
    out = []

    def walk(tabs):
        for tab in tabs:
            out.extend(tab.get("documentTab", {}).get("body", {}).get("content", []))
            walk(tab.get("childTabs", []))

    walk(doc.get("tabs", []))
    return out


def _cell_text(cell):
    parts = []
    for item in cell.get("content", []):
        paragraph = item.get("paragraph")
        if paragraph:
            parts.append("".join(e.get("textRun", {}).get("content", "") for e in paragraph.get("elements", [])))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _table_rows(table):
    return [[_cell_text(c) for c in row.get("tableCells", [])] for row in table.get("tableRows", [])]


def is_filled(value):
    value = (value or "").strip()
    return value.lower() not in _UNFILLED and any(ch.isalnum() for ch in value)


def _details(rows):
    found = {}
    for row in rows:
        if len(row) < 2:
            continue
        label = row[0].lower()
        value = " ".join(x for x in row[1:] if x).strip()
        for key, prefix in (("event", "event"), ("organising", "organi"), ("mode", "mode"), ("date", "date")):
            if label.startswith(prefix):
                found[key] = value
    return found if "event" in found and ("date" in found or "mode" in found) else None


def _name_count(rows):
    if not rows:
        return None
    header = [h.lower().strip() for h in rows[0]]
    name_col = next((i for i, h in enumerate(header) if h.startswith("name")), None)
    has_id_col = any(h.startswith("adm") or h.replace(".", "").replace(" ", "") in ("sno", "srno") for h in header)
    if name_col is None or not has_id_col:
        return None
    return sum(1 for row in rows[1:] if len(row) > name_col and is_filled(row[name_col]))


def parse_blocks(doc):
    # One dict per competition block, in document order. Participants are
    # only ever COUNTED.
    blocks = []
    for element in _all_content(doc):
        table = element.get("table")
        if not table:
            continue
        rows = _table_rows(table)
        details = _details(rows)
        if details is not None:
            blocks.append({
                "event": details.get("event", ""), "organising": details.get("organising", ""),
                "mode": details.get("mode", ""), "date_text": details.get("date", ""), "names": 0,
            })
            continue
        count = _name_count(rows)
        if count is not None and blocks:
            blocks[-1]["names"] += count
    return blocks


def parse_doc_date(text):
    # The doc's dates are typed by hand: "Saturday, 10th October 2026",
    # "10th October 2026", "Friday, August 21st, 2026"...
    text = (text or "").lower()
    for pattern, day_first in (
        (r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?([a-z]{3,9})\.?,?\s*(\d{4})", True),
        (r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})", False),
    ):
        for match in re.finditer(pattern, text):
            day, word, year = (match.group(1), match.group(2), match.group(3)) if day_first else (
                match.group(2), match.group(1), match.group(3))
            month = next((i for i, m in enumerate(_MONTHS, 1) if len(word) >= 3 and m.startswith(word)), None)
            if month:
                try:
                    return date(int(year), month, int(day))
                except ValueError:
                    pass
    return None


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def name_score(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def judge(comp_name, comp_date, blocks, needed=MIN_NAMES):
    # -> ("ready", []) | ("incomplete", [what is missing]) | ("missing", [])
    # A block belongs to the competition when its Event reads like the
    # dashboard's name and its Date (if it has a readable one) is the
    # competition's date.
    matches = []
    for block in blocks:
        if name_score(block["event"], comp_name) < NAME_MATCH:
            continue
        doc_date = parse_doc_date(block["date_text"])
        if doc_date and doc_date != comp_date:
            continue
        matches.append(block)
    if not matches:
        return "missing", []

    fewest = None
    for block in matches:
        missing = []
        if not is_filled(block["organising"]):
            missing.append("organising")
        if not is_filled(block["mode"]):
            missing.append("mode")
        if not is_filled(block["date_text"]):
            missing.append("date")
        if block["names"] < needed:
            missing.append(f"names, need at least {needed}")
        if not missing:
            return "ready", []
        if fewest is None or len(missing) < len(fewest):
            fewest = missing
    return "incomplete", fewest

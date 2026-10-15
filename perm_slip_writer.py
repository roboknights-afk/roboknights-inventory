# Adds a competition's permission slip to the club's "Inter School Event
# Permissions" Google Doc, in the doc's own format. Its own module with no
# Streamlit import, like perm_slip_check.py (which READS the same doc), so it
# can be tested without the app.
#
# The doc is one block per competition, newest first (see perm_slip_check.py
# for the shape): a details table, a students table, then two small sign-off
# tables, with blank "signing space" paragraphs in between. Rather than
# describing that format in code, this module REPLAYS a snapshot of one real
# block's formatting - `slip_template.json`, made by `extract_template()` from
# the doc itself with every student's text stripped (so nothing personal is in
# the repo, which is public). To change how slips look, restyle a block in the
# doc and regenerate the snapshot (`python make_slip_template.py`).
#
# How a block is written (Docs API, three rounds, all at the TOP of the doc):
#   1. insert the empty skeleton: the tables and the spacer paragraphs
#   2. re-read the doc, then set every style (column widths, borders, fonts...)
#      using the real positions of what round 1 made
#   3. fill in the text, from the BOTTOM of the block up, so each insert leaves
#      the positions of the cells above it unchanged
#
# Needs the doc shared with the Google service account as EDITOR (the check in
# perm_slip_check.py only needs Viewer).
#
# PRIVACY: this doc holds students' names and phone numbers and the repo and
# its logs are public. Nothing here prints or returns a student's details, and
# errors carry Google's status, never the document id.

import base64
import json
import os
import re

import requests

DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slip_template.json")

# The sign-off boxes, in the order the school wants them.
APPROVER_LABELS = {
    "hod": "HOD - Computer Science",
    "hm68": "Headmistress, 6-8",
    "hm910": "Headmistress, 9-10",
    "vp": "Vice Principal",
    "principal": "Principal",
}

# What the Docs API lets us set, per kind of style. Anything else in a
# snapshot (read-only fields like rowSpan) is dropped.
_PARA_KEYS = (
    "namedStyleType", "alignment", "lineSpacing", "direction", "spacingMode", "spaceAbove",
    "spaceBelow", "indentFirstLine", "indentStart", "indentEnd", "keepLinesTogether",
    "keepWithNext", "avoidWidowAndOrphan",
)
_TEXT_KEYS = (
    "bold", "italic", "underline", "strikethrough", "smallCaps", "backgroundColor",
    "foregroundColor", "fontSize", "weightedFontFamily", "baselineOffset",
)
_CELL_KEYS = (
    "backgroundColor", "borderBottom", "borderLeft", "borderRight", "borderTop",
    "paddingBottom", "paddingLeft", "paddingRight", "paddingTop", "contentAlignment",
)
_ROW_KEYS = ("minRowHeight", "preventOverflow")   # tableHeader is pinned with its own request


def _keep(style, keys):
    return {k: v for k, v in (style or {}).items() if k in keys}


# --- talking to Google --------------------------------------------------------

def _headers(service_account_b64):
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    info = json.loads(base64.b64decode(service_account_b64))
    creds = Credentials.from_service_account_info(info, scopes=[DOCS_SCOPE])
    creds.refresh(Request())
    return {"Authorization": f"Bearer {creds.token}"}, info.get("client_email", "the service account")


def _fail(response, what, email):
    # Deliberately not raise_for_status(): its message carries the full url,
    # i.e. the document id, into logs.
    try:
        reason = response.json().get("error", {}).get("status", "")
    except Exception:
        reason = ""
    hint = ""
    if response.status_code in (403, 404):
        hint = f" The doc must be shared with {email} as an Editor, and the Google Docs API enabled."
    raise RuntimeError(f"Google Docs refused to {what} ({response.status_code} {reason}).{hint}")


def get_doc(doc_id, headers, email="the service account"):
    response = requests.get(f"https://docs.googleapis.com/v1/documents/{doc_id}", headers=headers, timeout=60)
    if not response.ok:
        _fail(response, "read the document", email)
    return response.json()


def _batch_update(doc_id, headers, requests_list, email, chunk=250):
    # Chunked: one huge request can time out. Order is kept, and each chunk is
    # applied atomically by Google.
    for i in range(0, len(requests_list), chunk):
        response = requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers=headers, json={"requests": requests_list[i:i + chunk]}, timeout=120,
        )
        if not response.ok:
            try:
                detail = response.json().get("error", {}).get("message", "")[:200]
            except Exception:
                detail = ""
            _fail_detail = f" ({detail})" if detail and response.status_code == 400 else ""
            try:
                _fail(response, "edit the document", email)
            except RuntimeError as e:
                raise RuntimeError(str(e) + _fail_detail) from None


def _body(doc):
    if doc.get("body"):
        return doc["body"].get("content", [])
    for tab in doc.get("tabs", []):
        return tab.get("documentTab", {}).get("body", {}).get("content", [])
    return []


# --- the snapshot of one block's formatting -----------------------------------

def _cell_text(cell):
    parts = []
    for item in cell.get("content", []):
        for e in item.get("paragraph", {}).get("elements", []):
            parts.append(e.get("textRun", {}).get("content", ""))
    return "".join(parts).rstrip("\n")


def _first_run_style(paragraph):
    for e in paragraph.get("elements", []):
        if "textRun" in e:
            return _keep(e["textRun"].get("textStyle", {}), _TEXT_KEYS)
    return {}


def _snapshot_cell(cell):
    paragraphs = [c["paragraph"] for c in cell.get("content", []) if "paragraph" in c]
    p = paragraphs[0] if paragraphs else {}
    return {
        "cell": _keep(cell.get("tableCellStyle", {}), _CELL_KEYS),
        "para": _keep(p.get("paragraphStyle", {}), _PARA_KEYS),
        "text": _first_run_style(p),
        "label": _cell_text(cell),
    }


def _snapshot_table(table, keep_rows=None):
    rows = table["tableRows"]
    if keep_rows is not None:
        rows = [rows[i] for i in keep_rows]
    return {
        "kind": "table",
        "cols": table["columns"],
        "widths": [c.get("width") for c in table.get("tableStyle", {}).get("tableColumnProperties", [])],
        "rows": [
            {"row": _keep(r.get("tableRowStyle", {}), _ROW_KEYS),
             "header": bool(r.get("tableRowStyle", {}).get("tableHeader")),
             "cells": [_snapshot_cell(c) for c in r["tableCells"]]}
            for r in rows
        ],
    }


def _is_details_table(el):
    t = el.get("table")
    if not t or t["columns"] != 2:
        return False
    first = _cell_text(t["tableRows"][0]["tableCells"][0]).strip().lower()
    return first == "event"


def _is_people_table(el):
    t = el.get("table")
    if not t or t["columns"] < 5:
        return False
    return _cell_text(t["tableRows"][0]["tableCells"][0]).strip().lower().replace(".", "").replace(" ", "") in ("sno", "srno")


def extract_template(doc):
    """Snapshot the doc's NEWEST block (the first details table onwards) as a
    list of items. Student text is never kept: only the people table's header
    labels and ONE blank sample row (its formatting) are stored."""
    body = _body(doc)
    first = next((i for i, el in enumerate(body) if _is_details_table(el)), None)
    if first is None:
        raise RuntimeError("No permission-slip block found in the document to copy the format from.")
    # the paragraph just before the first table is part of the block (see below)
    start = first - 1 if first > 0 and "paragraph" in body[first - 1] else first
    nxt = next((i for i in range(first + 1, len(body)) if _is_details_table(body[i])), len(body))
    items = []
    for el in body[start:nxt]:
        if "paragraph" in el:
            p = el["paragraph"]
            items.append({"kind": "para", "para": _keep(p.get("paragraphStyle", {}), _PARA_KEYS), "text": _first_run_style(p)})
        elif "table" in el:
            if _is_people_table(el):
                snap = _snapshot_table(el["table"], keep_rows=[0, 1])      # header + one sample row
                snap["role"] = "people"
                for cell in snap["rows"][1]["cells"]:
                    cell["label"] = ""
            elif _is_details_table(el):
                snap = _snapshot_table(el["table"])
                snap["role"] = "details"
                for row in snap["rows"]:
                    # keep the labels (Event, Organising...), blank every value
                    row["cells"][1]["label"] = ""
            else:
                snap = _snapshot_table(el["table"])
                snap["role"] = "approvers"
                for row in snap["rows"]:
                    for cell in row["cells"]:
                        cell["label"] = ""
            items.append(snap)
        elif "sectionBreak" in el:
            continue
    return {"version": 1, "items": items}


def load_template(path=TEMPLATE_PATH):
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


# --- writing a block ------------------------------------------------------------

def _clean(value):
    # One line, no tabs: a newline inside a value would split a table cell's paragraph.
    return re.sub(r"\s+", " ", str(value or "")).strip()


def approver_slots(approvers):
    """Where each sign-off goes in the two small tables. `approvers` is the
    ordered list of keys from APPROVER_LABELS. The Principal always takes the
    last box of the second table; everyone before them fills the boxes in
    reading order. With all five that is exactly the newest block in the doc
    (HOD | Hm 6-8 | Hm 9-10 / VP | - | Principal); with four it is
    (HOD | Hm 9-10 | VP / - | - | Principal), as on the older blocks."""
    slots = [""] * 6                       # table 1 boxes 0-2, table 2 boxes 0-2
    others = [a for a in approvers if a != "principal"]
    for i, key in enumerate(others[:4]):
        slots[i] = APPROVER_LABELS[key]
    if "principal" in approvers:
        slots[5] = APPROVER_LABELS["principal"]
    return slots


_DETAIL_ROWS = ("event", "organising", "mode", "date", "on-duty", "department")


def _details_values(slip):
    return {
        "event": slip["event"], "organising": slip["organising"], "mode": slip.get("mode") or "Offline",
        "date": slip["date_text"], "on-duty": slip.get("staff", ""),
        "department": slip.get("department") or "Computer Science",
    }


def _student_cell(student, row_no, col):
    return (
        str(row_no), student.get("adm", ""), student.get("name", ""), student.get("sub_event", ""),
        student.get("class_sec", ""), student.get("mobile", ""),
    )[col]


def _para_requests(start, end, para, text):
    # Full masks on purpose: a key absent from the snapshot must come out
    # unset, not left holding whatever the paragraph had.
    return [
        {"updateParagraphStyle": {"range": {"startIndex": start, "endIndex": end},
                                  "paragraphStyle": para, "fields": ",".join(_PARA_KEYS)}},
        {"updateTextStyle": {"range": {"startIndex": start, "endIndex": end},
                             "textStyle": text, "fields": ",".join(_TEXT_KEYS)}},
    ]


def _plan_items(items):
    # Which template items must be inserted as paragraphs. The paragraph right
    # before a table is created by insertTable itself ("a newline is inserted
    # before the table"), and the LAST paragraph is the doc's existing one.
    insert_para = []
    for i, it in enumerate(items):
        if it["kind"] != "para":
            continue
        if i == len(items) - 1:
            continue
        if items[i + 1]["kind"] == "table":
            continue
        insert_para.append(i)
    return insert_para


def _table_shape(item, n_students):
    rows = len(item["rows"])
    if item["role"] == "people":
        rows = 1 + n_students
    return rows, item["cols"]


def _skeleton_requests(items, n_students):
    # Everything goes in at index 1 (the very top), last item first, so each
    # insert pushes the earlier ones down and the finished order is the
    # template's.
    reqs = []
    para_ids = set(_plan_items(items))
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if it["kind"] == "table":
            rows, cols = _table_shape(it, n_students)
            reqs.append({"insertTable": {"rows": rows, "columns": cols, "location": {"index": 1}}})
        elif i in para_ids:
            reqs.append({"insertText": {"text": "\n", "location": {"index": 1}}})
    return reqs


def _check_skeleton(body, items, n_students):
    """The new block should be body[1:1+len(items)], same kinds and table sizes
    as the template. Raise (so the caller can clean up) if not."""
    if len(body) < 1 + len(items):
        raise RuntimeError("The document came back shorter than expected after inserting the slip.")
    for i, it in enumerate(items):
        el = body[1 + i]
        if it["kind"] == "para" and "paragraph" not in el:
            raise RuntimeError(f"Unexpected layout after inserting the slip (item {i}).")
        if it["kind"] == "table":
            if "table" not in el:
                raise RuntimeError(f"Unexpected layout after inserting the slip (item {i}).")
            rows, cols = _table_shape(it, n_students)
            if el["table"]["rows"] != rows or el["table"]["columns"] != cols:
                raise RuntimeError(f"Unexpected table size after inserting the slip (item {i}).")


def _style_and_text_requests(body, items, slip):
    """Rounds 2 and 3: styles first (positions don't move), then text from the
    bottom of the block up (so positions above never move)."""
    style_reqs, texts = [], []
    students = slip["students"]
    details = _details_values(slip)
    slots = approver_slots(slip["approvers"])
    approver_tables_seen = 0

    for i, it in enumerate(items):
        el = body[1 + i]
        if it["kind"] == "para":
            style_reqs += _para_requests(el["startIndex"], el["endIndex"], it["para"], it["text"])
            continue

        table, start = el["table"], el["startIndex"]
        loc = {"index": start}
        for c, w in enumerate(it["widths"]):
            if w:
                style_reqs.append({"updateTableColumnProperties": {
                    "tableStartLocation": loc, "columnIndices": [c],
                    "tableColumnProperties": {"widthType": "FIXED_WIDTH", "width": w}, "fields": "width,widthType"}})

        # Header rows (repeat on each page): the leading rows the snapshot flags.
        pinned = 0
        for tmpl_row in it["rows"]:
            if not tmpl_row.get("header"):
                break
            pinned += 1
        if pinned:
            style_reqs.append({"pinTableHeaderRows": {"tableStartLocation": loc, "pinnedHeaderRowsCount": pinned}})

        role = it["role"]
        if role == "approvers":
            table_slots = slots[approver_tables_seen * 3:(approver_tables_seen + 1) * 3]
            approver_tables_seen += 1

        for r, trow in enumerate(table["tableRows"]):
            # The people table has a header row, then one sample row's
            # formatting repeated for every student.
            tmpl_row = it["rows"][min(r, len(it["rows"]) - 1)]
            style_reqs.append({"updateTableRowStyle": {
                "tableStartLocation": loc, "rowIndices": [r],
                "tableRowStyle": tmpl_row["row"], "fields": ",".join(_ROW_KEYS)}})
            for c, tcell in enumerate(trow["tableCells"]):
                tmpl_cell = tmpl_row["cells"][c]
                cell_style = tmpl_cell["cell"]
                if cell_style:
                    style_reqs.append({"updateTableCellStyle": {
                        "tableRange": {"tableCellLocation": {"tableStartLocation": loc, "rowIndex": r, "columnIndex": c},
                                       "rowSpan": 1, "columnSpan": 1},
                        "tableCellStyle": cell_style, "fields": ",".join(cell_style)}})
                para = tcell["content"][0]
                p_start, p_end = para["startIndex"], para["endIndex"]
                style_reqs += _para_requests(p_start, p_end, tmpl_cell["para"], tmpl_cell["text"])

                if role == "details":
                    text = tmpl_cell["label"] if c == 0 else details[_DETAIL_ROWS[r]]
                elif role == "people":
                    text = tmpl_cell["label"] if r == 0 else _student_cell(students[r - 1], r, c)
                else:
                    text = table_slots[c]
                text = _clean(text)
                if text:
                    texts.append((p_start, text, tmpl_cell["text"]))

    text_reqs = []
    for idx, text, tstyle in sorted(texts, key=lambda t: -t[0]):
        text_reqs.append({"insertText": {"text": text, "location": {"index": idx}}})
        text_reqs.append({"updateTextStyle": {"range": {"startIndex": idx, "endIndex": idx + len(text)},
                                              "textStyle": tstyle, "fields": ",".join(_TEXT_KEYS)}})
    return style_reqs, text_reqs


def write_slip(doc_id, service_account_b64, slip, template=None):
    """Add `slip` as the newest block at the top of the doc.

    slip = {event, organising, mode, date_text, staff, department,
            students: [{adm, name, sub_event, class_sec, mobile}, ...],
            approvers: [keys of APPROVER_LABELS in order]}
    Returns {"students": n}. On a layout surprise it removes what it inserted
    before raising, so a failed attempt doesn't leave a half-made slip behind."""
    if not slip.get("students"):
        raise ValueError("A slip needs at least one student.")
    template = template or load_template()
    items = template["items"]
    headers, email = _headers(service_account_b64)

    _batch_update(doc_id, headers, _skeleton_requests(items, len(slip["students"])), email)
    try:
        body = _body(get_doc(doc_id, headers, email))
        _check_skeleton(body, items, len(slip["students"]))
        style_reqs, text_reqs = _style_and_text_requests(body, items, slip)
        _batch_update(doc_id, headers, style_reqs, email)
        _batch_update(doc_id, headers, text_reqs, email)
    except Exception:
        remove_block(doc_id, service_account_b64, len(items))
        raise
    return {"students": len(slip["students"])}


def remove_block(doc_id, service_account_b64, n_items=None):
    """Delete the newest block (the n_items-long run at the top of the doc),
    leaving the doc's own paragraph that follows it. Used to clean up after a
    failed write and by the tests; best-effort, never raises."""
    try:
        n_items = n_items or len(load_template()["items"])
        headers, email = _headers(service_account_b64)
        body = _body(get_doc(doc_id, headers, email))
        end = body[n_items]["startIndex"]          # start of the doc's last paragraph of the block
        if end > 1:
            _batch_update(doc_id, headers,
                          [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end}}}], email)
    except Exception:
        pass

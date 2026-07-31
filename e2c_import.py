# Reads the club's real E2C Google Sheet and pulls out just the robotics
# competitions, for the host to review and import on the Competitions page.
# This file is pure data-wrangling (no Streamlit) so the messy parsing logic
# stays separate from the page that displays it.
#
# Sheet shape (confirmed by actually reading the live sheet, not guessed):
# one tab per year ("Events and Reg 2026-27"), with every competition
# stacked vertically in the SAME tab. Column A holds a competition's info
# spread across several rows (name, venue, date, links, deadline,
# in-charge) with no fixed row count per competition. Column B is each
# event's name, column C is "<max teams>x<team size>, <grade range>".
#
# The only reliable "is this event robotics?" signal is the real Google
# Sheets NOTE on the event-name cell: its first line is a short category
# ("Robotics", "Gaming", "Quiz", ...). Plenty of non-robotics events also
# have notes, so "has a note" is NOT the signal — the category text is
# (matched as a substring, since some categories are compound, e.g.
# "Robotics and STEM"). A few real robotics events don't say "robotics"
# in their note at all (one just repeats its own event name) — a known,
# accepted gap the student chose to live with rather than guess around.
#
# Columns D onward hold registered participants — one row per registered
# TEAM (confirmed live: a "3x2" event genuinely has 3 separate rows of 2
# names each). A name's cell is shaded green if the sheet shows them as
# confirmed, plain white otherwise; only names that match a real member in
# our own `users` table (by name, ignoring tags like "[Ad-Hoc]") get
# auto-added at all.

import os
import re
from datetime import date

import requests

from shared import E2C_SHEET_ID

MONTHS = {
    name: i for i, name in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"],
        start=1,
    )
}
YEAR_RE = re.compile(r"(20\d{2})")
DAY_THEN_MONTH_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")", re.IGNORECASE
)
MONTH_THEN_DAY_RE = re.compile(
    r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?", re.IGNORECASE
)

TAB_YEAR_RE = re.compile(r"(\d{4})")
DEADLINE_RE = re.compile(r"^registration\s*deadline\s*:\s*(.*)$", re.IGNORECASE)
INCHARGE_RE = re.compile(r"^student\s*in-?charge\s*:\s*(.*)$", re.IGNORECASE)
GRADE_RANGE_RE = re.compile(r"(\d+)\D+(\d+)")
SINGLE_GRADE_RE = re.compile(r"(\d+)")
TEAM_FORMAT_RE = re.compile(r"^(\d+)\s*x\s*(\d+)$", re.IGNORECASE)
NAME_TAG_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]")  # strips "[Ad-Hoc]", "(adhoc)", etc.

NUM_COLUMNS = 20  # A-C (info/name/eligibility) + D onward (up to 17 participant slots — more than any real team needs)
GREEN_RED_CHANNEL = 0.4196  # the confirmed-registration green's red channel, from the live sheet


def _clean_participant_name(raw):
    name = NAME_TAG_RE.sub("", raw)
    return " ".join(name.split())  # collapses any doubled/odd whitespace too


def _api_get(path, api_key, **params):
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{path}",
        params={"key": api_key, **params},
    )
    resp.raise_for_status()
    return resp.json()


def _pick_current_tab(sheet_id, api_key):
    # Pick whichever "Events and Reg <year>" tab has the highest year in its
    # name, so a new tab added next year is picked up with no code change.
    data = _api_get(sheet_id, api_key, fields="sheets.properties")
    tabs = [
        s["properties"]["title"]
        for s in data["sheets"]
        if "Events and Reg" in s["properties"]["title"]
    ]
    if not tabs:
        raise ValueError("No 'Events and Reg' tab found in the sheet.")
    return max(tabs, key=lambda t: int(TAB_YEAR_RE.search(t).group(1)))


def _fetch_raw_rows(sheet_id, api_key, tab_name):
    # A-C (competition info, event name, eligibility) plus D onward
    # (registered participants, one row per registered team). Column A's
    # font size is fetched too: competition name/type/tagline lines are set
    # in a visibly bigger font (~19-22pt) than every other line in that
    # column (~10pt) — a far more reliable "new competition starts here"
    # signal than counting blank rows, which also occur *inside* a single
    # competition's own event list. Background color is fetched for every
    # column since a participant's cell is shaded green when the sheet
    # marks them confirmed.
    last_col = chr(ord("A") + NUM_COLUMNS - 1)
    data = _api_get(
        sheet_id, api_key,
        ranges=f"{tab_name}!A1:{last_col}2000",
        includeGridData="true",
        fields=(
            "sheets(data(rowData(values("
            "formattedValue,note,hyperlink,effectiveFormat(textFormat/fontSize,backgroundColor)"
            "))))"
        ),
    )
    row_data = data["sheets"][0]["data"][0].get("rowData", [])
    rows = []
    for row in row_data:
        values = row.get("values", [])
        cells = []
        for i in range(NUM_COLUMNS):
            cell = values[i] if i < len(values) else {}
            fmt = cell.get("effectiveFormat", {})
            bg_red = fmt.get("backgroundColor", {}).get("red", 1)
            cells.append({
                "text": (cell.get("formattedValue") or "").strip(),
                "note": cell.get("note"),
                "hyperlink": cell.get("hyperlink"),
                "font_size": fmt.get("textFormat", {}).get("fontSize"),
                "is_green": abs(bg_red - GREEN_RED_CHANNEL) < 0.02,
            })
        rows.append(cells)
    return rows


LARGE_FONT = 15  # competition name/type/tagline lines are ~19-22pt; everything else is ~10pt


def _split_into_blocks(rows):
    # A genuine competition boundary is a run of 2+ CONSECUTIVE big-font
    # column-A rows (name + type, at minimum) — found with a look-ahead
    # pass first, rather than reacting to the first big-font row seen.
    # A lone big-font row (e.g. a stray "Delayed: to be held in October"
    # annotation) isn't followed by another big-font row, so it's correctly
    # left as just another line inside the current competition instead of
    # being mistaken for the start of a new one.
    n = len(rows)
    is_large = [
        bool(rows[i][0]["text"]) and (rows[i][0]["font_size"] or 0) >= LARGE_FONT
        for i in range(n)
    ]
    boundary_starts = set()
    i = 0
    while i < n:
        if is_large[i]:
            run_start = i
            while i < n and is_large[i]:
                i += 1
            if i - run_start >= 2:
                boundary_starts.add(run_start)
        else:
            i += 1

    blocks = []
    current = []
    for idx, row in enumerate(rows):
        if idx in boundary_starts and current:
            blocks.append(current)
            current = []
        current.append(row)
    if current:
        blocks.append(current)
    return blocks


PAST_EVENTS_MARKER = "past events"


def _drop_past_events(blocks):
    # The sheet marks a hard divider ("PAST EVENTS", in a huge font) between
    # competitions still to come and ones already held — importing the
    # latter would just be clutter nobody can volunteer for.
    kept = []
    for block in blocks:
        first_text = next((row[0]["text"] for row in block if row[0]["text"]), "")
        if first_text.strip().lower() == PAST_EVENTS_MARKER:
            break
        kept.append(block)
    return kept


def _parse_grade_range(text):
    text = text.strip()
    if text.lower() == "open":
        return 6, 12  # "Open" = no restriction; 6-12 is the widest range seen
    m = GRADE_RANGE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = SINGLE_GRADE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def _parse_team_format(text):
    text = text.strip()
    m = TEAM_FORMAT_RE.match(text)
    if not m:
        return None, None  # covers "Open" and "∞x2" — no safe number to guess
    return int(m.group(1)), int(m.group(2))  # (max_teams, team_size)


def _parse_eligibility(text):
    left, _, right = text.partition(",")
    max_teams, team_size = _parse_team_format(left)
    min_grade, max_grade = _parse_grade_range(right) if right else (None, None)
    return {
        "team_size": team_size,
        "max_teams": max_teams,
        "min_grade": min_grade,
        "max_grade": max_grade,
        "flagged": max_teams is None or min_grade is None,
    }


def _split_note(note):
    # A note's first line is a short category ("Robotics", "Gaming", ...);
    # everything after the blank line is the actual rules text.
    if not note:
        return "", ""
    category, _, details = note.partition("\n\n")
    return category.strip(), details.strip()


def _parse_date_best_effort(text):
    # Real dates on the sheet are messy free text: multi-day ranges
    # ("22nd July - 1st August 2026"), no year at all ("27th July"), or
    # just "not confirmed". We only guess when a year is clearly present
    # and a day+month can be found — picking the FIRST date in a range,
    # since that's editable in the preview if it's the wrong one. No year
    # found means no guess at all (None), so the host has to pick a real
    # date rather than the app silently storing a wrong one.
    if not text:
        return None
    year_m = YEAR_RE.search(text)
    if not year_m:
        return None
    year = int(year_m.group(1))
    m = DAY_THEN_MONTH_RE.search(text)
    if m:
        day, month_name = int(m.group(1)), m.group(2).lower()
    else:
        m = MONTH_THEN_DAY_RE.search(text)
        if not m:
            return None
        month_name, day = m.group(1).lower(), int(m.group(2))
    try:
        return date(year, MONTHS[month_name], day)
    except ValueError:
        return None


def _parse_competition_info(block):
    a_entries = [
        (row[0]["text"], row[0]["hyperlink"], row[0]["font_size"])
        for row in block if row[0]["text"]
    ]
    if not a_entries:
        return {
            "name": "Untitled competition", "venue": "", "date_text": "",
            "deadline_text": None, "student_incharge": "", "links": [],
        }

    name = a_entries[0][0]

    # The header is however many big-font lines start the block (name,
    # type e.g. "Hybrid", tagline) — everything after that is venue, date,
    # links, deadline, in-charge, all in the smaller ~10pt font.
    header_len = 0
    for _, _, fs in a_entries:
        if (fs or 0) >= LARGE_FONT:
            header_len += 1
        else:
            break
    rest = a_entries[header_len:]

    links = [{"label": t, "url": h} for t, h, _ in rest if h]

    deadline_text = None
    student_incharge = ""
    for t, h, fs in rest:
        m = DEADLINE_RE.match(t)
        if m:
            deadline_text = m.group(1).strip()
            continue
        m = INCHARGE_RE.match(t)
        if m:
            student_incharge = m.group(1).strip()

    # Venue and date are free text with no keyword to anchor on, so we take
    # them positionally: the first two "plain" lines (not a link, not the
    # deadline/in-charge line) right after the header.
    plain = [
        t for t, h, _ in rest
        if h is None and not DEADLINE_RE.match(t) and not INCHARGE_RE.match(t)
    ]
    venue = plain[0] if plain else ""
    date_text = plain[1] if len(plain) > 1 else ""

    return {
        "name": name,
        "venue": venue,
        "date_text": date_text,
        "deadline_text": deadline_text,
        "student_incharge": student_incharge,
        "links": links,
    }


def _parse_teams(team_rows, user_id_by_name):
    # One team per row; columns D onward are that team's participants.
    # Only names that match a real member end up here at all — an ad-hoc
    # guest or a name we can't match just isn't ours to add.
    #
    # team_no is the row's OWN position within team_rows, not a position
    # within the (filtered) list this function returns. It has to be —
    # different members of the same event often get matched across
    # SEPARATE sync calls as people sign up over time, and rows with zero
    # matches at a given scan are silently skipped. Numbering off the
    # filtered list would let a newly-matched row land on whatever index a
    # completely different row happened to occupy the first time it was
    # matched, merging two real teams into one team_no (confirmed live:
    # this is exactly how Kyraan and Arhaan — different rows on the sheet
    # — ended up sharing team_no 2 on Acon's "Kinetic Chaos").
    teams = []
    for row_no, row in enumerate(team_rows, start=1):
        participants = []
        for cell in row[3:]:
            if not cell["text"]:
                continue
            cleaned = _clean_participant_name(cell["text"])
            user_id = user_id_by_name.get(cleaned.lower())
            if user_id:
                participants.append({"user_id": user_id, "name": cleaned, "selected": cell["is_green"]})
        if participants:
            teams.append({"team_no": row_no, "participants": participants})
    return teams


SR_JR_SUFFIX_RE = re.compile(r"\s*Sr\.?\s*&\s*Jr\.?\s*$", re.IGNORECASE)


def _parse_all_events(block, user_id_by_name):
    # Every event in the block, not just robotics ones — the host can
    # still pull in a specific non-robotics event by name (e.g. a
    # borderline AI/IoT one) even though it's not auto-included by default.
    events = []
    n = len(block)
    i = 0
    while i < n:
        b_cell = block[i][1]
        if not b_cell["text"]:
            i += 1
            continue

        # This row starts an event; every following row belongs to it
        # (participant/team rows) until the next row with its own event
        # name — that's the next event starting.
        j = i + 1
        while j < n and not block[j][1]["text"]:
            j += 1
        event_rows = block[i:j]

        # One event name can still cover more than one grade bracket (e.g.
        # "RoboWar Sr. & Jr." — a 6th-8th row, then a further-down row with
        # its own 9th-12th eligibility but a blank name cell, which would
        # otherwise look like just more team rows for the first bracket).
        # Each row with its own eligibility text starts a new bracket; team
        # rows in between belong to whichever bracket precedes them.
        bracket_starts = [0] + [
            k for k in range(1, len(event_rows)) if event_rows[k][2]["text"]
        ]
        bracket_row_groups = [
            event_rows[start:(bracket_starts[bi + 1] if bi + 1 < len(bracket_starts) else len(event_rows))]
            for bi, start in enumerate(bracket_starts)
        ]
        bracket_eligs = [_parse_eligibility(rows[0][2]["text"]) for rows in bracket_row_groups]

        category, details = _split_note(b_cell["note"])
        # "robo" (not "robotics") so compound/short categories like
        # "Robotics and STEM" and "Robo Soccer" both count. When there's no
        # note at all to check, fall back to the event's own name — this is
        # only a fallback (not applied when a note exists but doesn't
        # mention robo) so a genuinely different category isn't overridden
        # just because of a coincidental name.
        no_note = not category
        keyword_robotics = "robo" in category.lower() or (no_note and "robo" in b_cell["text"].lower())

        multi_bracket = len(bracket_row_groups) > 1
        if multi_bracket:
            base_name = SR_JR_SUFFIX_RE.sub("", b_cell["text"]).strip()
            # Label by actual grade order rather than assuming the sheet
            # always lists the junior bracket first.
            order = sorted(
                range(len(bracket_eligs)), key=lambda idx: bracket_eligs[idx]["min_grade"] or 7,
            )
            suffix_by_index = {}
            for rank, idx in enumerate(order):
                suffix_by_index[idx] = (
                    "(Jr.)" if rank == 0 else "(Sr.)" if rank == 1 else f"(Bracket {rank + 1})"
                )

        for bi, rows in enumerate(bracket_row_groups):
            elig = bracket_eligs[bi]
            teams = _parse_teams(rows, user_id_by_name)
            matched_member_ids = {p["user_id"] for team in teams for p in team["participants"]}
            # One matched name could be a coincidence (a common name shared
            # with someone unrelated), so that alone doesn't auto-include an
            # event with no note/keyword. Two or more distinct real members
            # already on the roster is a much stronger signal — still not
            # auto-included, but surfaced for the host to confirm by hand,
            # which is what actually catches oddly-named events like
            # "Rescue Maze" without silently importing a false positive.
            is_robotics = keyword_robotics
            needs_review = not keyword_robotics and len(matched_member_ids) >= 2
            name = f"{base_name} {suffix_by_index[bi]}" if multi_bracket else b_cell["text"]

            events.append({
                "name": name,
                "details": details,
                "raw_eligibility": rows[0][2]["text"],
                "team_size": elig["team_size"] or 1,
                "max_teams": elig["max_teams"] or 1,
                "min_grade": elig["min_grade"] or 7,
                "max_grade": elig["max_grade"] or 12,
                "flagged": elig["flagged"],
                "teams": teams,
                "is_robotics": is_robotics,
                "needs_review": needs_review,
            })
        i = j
    return events


def scan_e2c_sheet(client):
    # client is the Supabase client, used to match scanned competitions/
    # events against what's already in the database (by name) — both to
    # label a competition "already imported" and so an already-imported
    # competition's own NEW events can be offered individually, without
    # re-importing everything else about it.
    api_key = os.environ["GOOGLE_SHEETS_API_KEY"]
    tab = _pick_current_tab(E2C_SHEET_ID, api_key)
    rows = _fetch_raw_rows(E2C_SHEET_ID, api_key, tab)
    blocks = _split_into_blocks(rows[2:])  # rows[0:2] are the sheet's own title/header rows
    blocks = _drop_past_events(blocks)

    existing_id_by_name = {
        c["name"].strip().lower(): c["competition_id"]
        for c in client.table("competitions").select("competition_id, name").execute().data
    }
    existing_event_id_by_comp_and_name = {}
    for e in client.table("competition_events").select("event_id, competition_id, name").execute().data:
        existing_event_id_by_comp_and_name[(e["competition_id"], e["name"].strip().lower())] = e["event_id"]

    user_id_by_name = {
        u["name"].strip().lower(): u["user_id"]
        for u in client.table("users").select("user_id, name").execute().data
    }

    competitions = []
    for block in blocks:
        all_events = _parse_all_events(block, user_id_by_name)
        # Shown by default: real keyword-detected robotics events, PLUS
        # events that weren't keyword-detected but have 2+ real members
        # already on the roster — those are pre-unchecked in the UI and
        # need the host's confirmation, not silently imported (see
        # needs_review in _parse_all_events).
        events = [e for e in all_events if e["is_robotics"] or e["needs_review"]]
        if not events:
            continue  # nothing robotics-related, and nothing worth a manual look
        info = _parse_competition_info(block)
        existing_id = existing_id_by_name.get(info["name"].strip().lower())
        for e in all_events:
            e["existing_event_id"] = (
                existing_event_id_by_comp_and_name.get((existing_id, e["name"].strip().lower()))
                if existing_id else None
            )
            e["already_imported"] = e["existing_event_id"] is not None
        info["events"] = events  # robotics-only + needs_review, shown by default
        info["all_events"] = all_events  # every event, for adding a specific one by name
        info["existing_id"] = existing_id
        info["already_imported"] = existing_id is not None
        info["date_parsed"] = _parse_date_best_effort(info["date_text"])
        info["deadline_parsed"] = _parse_date_best_effort(info["deadline_text"] or "")
        competitions.append(info)
    return competitions

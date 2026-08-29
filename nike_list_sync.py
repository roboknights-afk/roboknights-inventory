"""Rebuild the "2026-27 (bot)" tab in Nike's List from the dashboard:
every competition, its links, events, finalised teams and volunteers.
Upcoming competitions first, then a PAST section (most recent first).

This started life as a scratchpad script somebody ran by hand, which is
exactly why Dynamix 26 sat in the database for days without ever
appearing on the sheet. It now runs on a schedule (see
.github/workflows/nike-list-sync.yml) and can be triggered manually from
the Actions tab, so the sheet stops depending on anyone remembering.

Deliberately does NOT import shared.py — same reasoning as
send_due_reminders.py: the Actions runner should not have to install
Streamlit to write a spreadsheet.

Design (unchanged from the hand-run version, which the club has already
seen and approved):
  font        Proxima Nova, bold, size 10 (22 for a competition's
              name / mode / status lines)
  align       centred, every cell, wrapped so nothing clips
  palette     roboknights.in's own - near-black #242424, ink #111827,
              greys #E5E7EB / #F3F4F6 - with the dashboard's knight gold
              as the single accent, since the site itself is monochrome
  gold        #E8B33D  a finalised team SLOT; an unfilled slot shows "-"
  near-white  #F3F4F6  the row under it, where people add their own names
  dark gold   #B8860B  status line, deadline, in-charge, links, section
                       titles (#E8B33D is too pale to read as text)
  separators  an 8px hairline in #E5E7EB between competitions, and a gold
              underline beneath each section title - no filled bands,
              which at full width read heavier than the data itself

Only this one tab is ever touched. Every other tab in Nike's List,
including the hand-maintained "2026-2027" one, is left completely alone.

Run with --dry-run to build the grid and print what it WOULD write
without touching the sheet at all.
"""
import base64
import json
import os
import sys
from datetime import date

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

NIKE_ID = "1056zsk6qjHg8TwSRMsnPlvdkzPY-6hsaekKL1jdF0Tc"
# The hand-maintained tab. Read only, and only for its column widths, so
# the generated tab lines up with the one people already know.
SRC_TAB_ID = 34193206
TAB = "2026-27 (bot)"
NCOLS = 18
FIRST_NAME_COL = 5
FONT = "Proxima Nova"
# Spare rows kept below the last written one. The sheet is resized to
# exactly this every run, so yesterday's longer version can't leave a
# tail of stale rows behind.
TRAILING_ROWS = 10

DRY_RUN = "--dry-run" in sys.argv


def rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255}


INK = rgb("#111827")
DARK = rgb("#242424")
GOLD = rgb("#E8B33D")
GOLD_DARK = rgb("#B8860B")
NEAR_WHITE = rgb("#F3F4F6")
RULE = rgb("#E5E7EB")
GREY = rgb("#9CA3AF")
WHITE = rgb("#FFFFFF")


def sheets_client():
    # Imported here, not at the top: --dry-run builds the whole grid and
    # prints it without ever talking to Google, and should not need
    # gspread installed to do that.
    import gspread
    from google.oauth2.service_account import Credentials

    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    if not b64:
        # Loud, not silent. A missing credential is the single most
        # common way a Google Sheets feature in this project has quietly
        # stopped working (three separate outages so far), and this job
        # has no user watching it - the exit code is the only signal.
        print("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is not set - cannot write "
              "the sheet.", flush=True)
        sys.exit(1)
    creds = Credentials.from_service_account_info(
        json.loads(base64.b64decode(b64)),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def pretty_date(iso):
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{ordinal(d.day)} {d.strftime('%B %Y')}"


# ---------------------------------------------------------------- data
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
comps = client.table("competitions").select("*").execute().data
all_links = client.table("competition_links").select("*").execute().data
all_events = client.table("competition_events").select("*").execute().data
vols = client.table("event_volunteers").select("*").execute().data
# Everyone, disabled accounts included. This sheet is the club's RECORD
# of who competed, and a member who has since left still went - filtering
# them out quietly deleted Kush Singh from a Mecha-Cup team he was on.
users = {u["user_id"]: u["name"]
         for u in client.table("users").select("user_id,name").execute().data}

LINK_ORDER = ["Website", "Discord", "Registration Link", "Brochure"]

# ------------------------------------------------------------- canvas
grid = {}
rule_rows, title_rows = [], []


def put(r, col, value, size=10, bg=None, fg=None, link=None):
    grid[(r, col)] = {"value": value, "link": link, "size": size,
                      "bg": bg or WHITE, "fg": fg or INK}


def rule(r):
    """A hairline between two competitions."""
    for col in range(NCOLS):
        put(r, col, "", bg=RULE)
    rule_rows.append(r)


def section(r, label):
    """A section title - text with an underline, not a filled band."""
    put(r, 0, label, size=22, fg=GOLD_DARK)
    title_rows.append(r)


def render_competition(top, comp):
    cid = comp["competition_id"]
    events = [e for e in all_events if e["competition_id"] == cid]
    links = [l for l in all_links if l["competition_id"] == cid]
    links.sort(key=lambda l: (LINK_ORDER.index(l["label"])
                              if l["label"] in LINK_ORDER else 99, l["label"]))

    status = comp.get("priority_label") or ("NOT ATTENDING" if comp.get("not_attending") else "")
    col_a = [(comp["name"], 22, INK), (comp.get("mode") or "", 22, INK),
             (status, 22, GOLD_DARK), None]
    for text, colour in [
        (comp.get("venue") or "", INK),
        (pretty_date(comp.get("competition_date")), INK),
        (f"Registration Deadline: {pretty_date(comp['registration_deadline'])}"
         if comp.get("registration_deadline") else "", GOLD_DARK),
        (f"Student in-charge: {comp.get('student_incharge') or 'TBD'}", GOLD_DARK),
    ]:
        col_a.append((text, 10, colour) if text else None)

    for i, item in enumerate(col_a):
        if item and item[0]:
            put(top + i, 0, item[0], size=item[1], fg=item[2])

    for i, l in enumerate(links):
        # A real cell link, not a =HYPERLINK() formula: a URL with a quote
        # in it would break out of the formula's own string (one such URL
        # was already sitting in the database).
        put(top + 3 + i, 1, l["label"], fg=GOLD_DARK, link=l["url"])

    row = top + 2
    for e in events:
        team_size = e.get("team_size") or 1
        max_teams = e.get("max_teams") or 1
        grades = (f", {ordinal(e['min_grade'])}-{ordinal(e['max_grade'])}"
                  if e.get("min_grade") and e.get("max_grade") else "")
        put(row, 2, e.get("name") or "")
        put(row, 3, f"{max_teams}x{team_size}{grades}")

        mine = [v for v in vols if v["event_id"] == e["event_id"] and v["user_id"] in users]
        others = [users[v["user_id"]] for v in mine if not v.get("selected")]

        # Who is on WHICH team is stored in event_volunteers.team_no -
        # group by it rather than dealing names out in row order, which
        # invented pairings that were never real. team_no is not 1,2,3:
        # the values seen are 1,3,5 (sheet row offsets), so the teams are
        # its distinct values in order, not its values used as indexes.
        teams, loose = {}, []
        for v in [x for x in mine if x.get("selected")]:
            if v.get("team_no") is None:
                loose.append(v)
            else:
                teams.setdefault(v["team_no"], []).append(v)
        ordered = [teams[k] for k in sorted(teams)]
        for v in loose:
            spot = next((g for g in ordered if len(g) < team_size), None)
            if spot is None:
                ordered.append([v])
            else:
                spot.append(v)

        for team in range(max(max_teams, len(ordered))):
            members = [users[v["user_id"]] for v in (ordered[team] if team < len(ordered) else [])]
            slots = (members + ["-"] * team_size)[:max(team_size, len(members))]
            for j, who in enumerate(slots):
                put(row, FIRST_NAME_COL + j, who, bg=GOLD, fg=DARK)
            if team == 0:
                for j, who in enumerate(others[:NCOLS - FIRST_NAME_COL]):
                    put(row + 1, FIRST_NAME_COL + j, who, bg=NEAR_WHITE)
            row += 2

    # Blocks used to end at whichever ran longer, the column-A info lines
    # or the event rows, so the gap before the next rule varied from none
    # to several rows. Measure what was ACTUALLY written and leave exactly
    # one blank row, so every block is spaced identically.
    return max(r for (r, _) in grid if r >= top) + 2


upcoming = sorted([x for x in comps if not x.get("is_past")],
                  key=lambda k: k.get("competition_date") or "9999")
past = sorted([x for x in comps if x.get("is_past")],
              key=lambda k: k.get("competition_date") or "", reverse=True)

for col in range(NCOLS):
    put(1, col, "", bg=NEAR_WHITE)
for i, h in enumerate(["Event name", "Important Links", "Event Name", "Eligibilty"]):
    put(1, i, h, bg=NEAR_WHITE)
put(1, 6, "Please put your names in the row below the gold boxes. "
          "The names in the gold boxes are the finalized names.", bg=NEAR_WHITE, fg=GREY)

row = 2
for group, label in ((upcoming, "UPCOMING"), (past, "PAST")):
    section(row, label)
    row += 1
    for i, comp in enumerate(group):
        row = render_competition(row, comp)
        if i < len(group) - 1:
            rule(row)
            row += 1
    row += 1

LAST = row
TOTAL_ROWS = LAST + TRAILING_ROWS

print(f"built {LAST} rows: {len(upcoming)} upcoming + {len(past)} past "
      f"competitions, {len(all_events)} events", flush=True)
if DRY_RUN:
    print("--dry-run: not writing to the sheet", flush=True)
    for c in upcoming:
        print(f"   upcoming: {c['name']}", flush=True)
    sys.exit(0)

# ------------------------------------------------------- write it out
sh = sheets_client().open_by_key(NIKE_ID)

# Reuse the tab rather than deleting and recreating it. The hand-run
# version deleted it every time, which handed the tab a NEW sheet id on
# every run - so anyone who had bookmarked or linked "#gid=..." was sent
# to a tab that no longer existed. Now the id is stable and only the
# contents change.
ws = next((w for w in sh.worksheets() if w.title == TAB), None)
if ws is None:
    ws = sh.add_worksheet(title=TAB, rows=TOTAL_ROWS, cols=NCOLS)
    print(f"created the {TAB!r} tab", flush=True)
else:
    print(f"reusing the existing {TAB!r} tab (gid {ws.id})", flush=True)
sid = ws.id

meta = sh.fetch_sheet_metadata(
    {"fields": "sheets(properties(sheetId,gridProperties),data/columnMetadata/pixelSize)"}
)
src_widths = next(
    (s["data"][0]["columnMetadata"] for s in meta["sheets"]
     if s["properties"]["sheetId"] == SRC_TAB_ID), []
)
old_rows = next(
    (s["properties"]["gridProperties"].get("rowCount", 0) for s in meta["sheets"]
     if s["properties"]["sheetId"] == sid), 0
)

# One updateCells for the whole grid - a request per cell would be
# thousands of operations for the same result. Every row up to
# TOTAL_ROWS is written, blanks included, so nothing from a longer
# previous run survives underneath.
payload = []
for r in range(1, TOTAL_ROWS + 1):
    values = []
    for col in range(NCOLS):
        cell = grid.get((r, col)) or {"value": "", "link": None, "size": 10,
                                      "bg": WHITE, "fg": INK}
        out = {"userEnteredFormat": {
            "backgroundColor": cell["bg"],
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            # WRAP, so a name longer than its column goes onto a second
            # line instead of being cut off at the column edge.
            "wrapStrategy": "WRAP",
            "textFormat": {"fontFamily": FONT, "bold": True, "fontSize": cell["size"],
                           "foregroundColor": cell["fg"],
                           **({"link": {"uri": cell["link"]}} if cell.get("link") else {})},
        }}
        # A cell with nothing in it is left genuinely BLANK rather than
        # given an empty string: Sheets only lets text spill over into a
        # truly empty neighbour, and writing "" everywhere is what was
        # clipping the long competition names.
        if cell["value"]:
            out["userEnteredValue"] = {"stringValue": cell["value"]}
        values.append(out)
    payload.append({"values": values})

requests = []

# Grow first if the sheet is too small to hold the write; shrink only
# AFTER writing (below), since you cannot write past the current end.
if old_rows and old_rows < TOTAL_ROWS:
    requests.append({"appendDimension": {
        "sheetId": sid, "dimension": "ROWS", "length": TOTAL_ROWS - old_rows}})

requests += [
    {"updateCells": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": TOTAL_ROWS,
                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
        "rows": payload, "fields": "userEnteredValue,userEnteredFormat",
    }},
    # The header stays put while you scroll 250 rows of competitions.
    {"updateSheetProperties": {
        "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount",
    }},
    {"updateBorders": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
        "bottom": {"style": "SOLID", "color": GREY},
    }},
]

for r in title_rows:
    requests.append({"updateBorders": {
        "range": {"sheetId": sid, "startRowIndex": r - 1, "endRowIndex": r,
                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
        "bottom": {"style": "SOLID_MEDIUM", "color": GOLD_DARK},
    }})

for i, w in enumerate(src_widths[:NCOLS]):
    if w.get("pixelSize"):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w["pixelSize"]}, "fields": "pixelSize",
        }})

# --------------------------------------------------------- row heights
# Every row's height is set explicitly, every run. Three things forced
# this, in the order they were learned:
#
# 1. Heights are set by row INDEX and survive a rewrite - the trap that
#    hid members' names in the Clio Alumni sheet when a layout shifted.
#    So they cannot simply be left alone.
# 2. Setting them all to 21px sliced every competition name in half:
#    those lines are 22pt and need about 30px. 40px is what the
#    hand-maintained "2026-2027" tab uses for its own 22pt rows.
# 3. autoResizeDimensions looks like the right answer and is a no-op on
#    ROWS through this API - tested directly on this tab, heights came
#    back unchanged at 21px.
#
# Worth knowing WHY the older hand-run version never hit any of this: it
# deleted and recreated the tab every time, and a freshly created row
# keeps Sheets' own auto-fit. Once a height is set explicitly it is
# pinned for good. Reusing the tab (so its gid stays stable) means that
# free auto-fit is gone and the heights are ours to get right.
BIG_PT = 22
BIG_ROW_PX = 40
DEFAULT_ROW_PX = 21
HAIRLINE_PX = 8

big_rows = sorted({r for (r, _), cell in grid.items()
                   if cell["size"] >= BIG_PT and cell["value"]})


def height_runs(rows, px):
    """Consecutive rows at the same height become one request instead of
    one each - a competition's name/mode/status are always three in a
    row, so this roughly thirds the op count."""
    out, start, prev = [], None, None
    for r in rows:
        if start is None:
            start = prev = r
        elif r == prev + 1:
            prev = r
        else:
            out.append({"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS",
                          "startIndex": start - 1, "endIndex": prev},
                "properties": {"pixelSize": px}, "fields": "pixelSize"}})
            start = prev = r
    if start is not None:
        out.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS",
                      "startIndex": start - 1, "endIndex": prev},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    return out


# Everything back to the default first, so yesterday's 40px title row
# can't sit in the middle of today's plain text, then the exceptions.
requests.append({"updateDimensionProperties": {
    "range": {"sheetId": sid, "dimension": "ROWS",
              "startIndex": 0, "endIndex": max(TOTAL_ROWS, old_rows)},
    "properties": {"pixelSize": DEFAULT_ROW_PX}, "fields": "pixelSize"}})
requests += height_runs(big_rows, BIG_ROW_PX)
requests += height_runs(sorted(rule_rows), HAIRLINE_PX)

# Trim the tail last, once everything above it has been written.
if old_rows > TOTAL_ROWS:
    requests.append({"deleteDimension": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": TOTAL_ROWS, "endIndex": old_rows}}})

sh.batch_update({"requests": requests})
print(f"wrote {TAB!r}: {len(upcoming)} upcoming + {len(past)} past, "
      f"{LAST} rows, {len(rule_rows)} hairlines, {len(requests)} ops", flush=True)

# ------------------------------------------------------- check itself
# This job writes something a person LOOKS at, and the first version of it
# shipped a change that read back perfectly - right competitions, right
# rows, right values - while every competition name was sliced in half on
# screen, because the rows were too short for 22pt text. Checking the data
# landed is not the same as checking the sheet is readable, so check the
# thing that actually broke: every row holding a big line has to be tall
# enough to show it.
MIN_BIG_ROW_PX = 28  # 22pt needs about 30px; below 28 is visibly clipped
after = sh.fetch_sheet_metadata(
    {"fields": "sheets(properties/sheetId,data/rowMetadata/pixelSize)"})
heights = next((s["data"][0].get("rowMetadata", []) for s in after["sheets"]
                if s["properties"]["sheetId"] == sid), [])


def height_of(grid_row):
    # grid rows are 1-based and row 1 is the header, so grid row r is
    # rowMetadata[r - 1].
    i = grid_row - 1
    return (heights[i].get("pixelSize") if i < len(heights) else None) or 0


clipped = [r for r in big_rows if height_of(r) < MIN_BIG_ROW_PX]
if clipped:
    print(f"PROBLEM: {len(clipped)} of {len(big_rows)} big-text rows are under "
          f"{MIN_BIG_ROW_PX}px and will be clipped on screen.", flush=True)
    for r in clipped[:10]:
        name = next((grid[(r, c)]["value"] for c in range(NCOLS)
                     if (r, c) in grid and grid[(r, c)]["value"]), "")
        print(f"   row {r}: {height_of(r)}px  {name!r}", flush=True)
    print(f"open: https://docs.google.com/spreadsheets/d/{NIKE_ID}/edit#gid={sid}",
          flush=True)
    sys.exit(1)

print(f"checked: all {len(big_rows)} big-text rows are tall enough "
      f"({min(height_of(r) for r in big_rows)}px smallest)", flush=True)
print(f"open: https://docs.google.com/spreadsheets/d/{NIKE_ID}/edit#gid={sid}",
      flush=True)

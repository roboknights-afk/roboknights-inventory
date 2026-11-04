# Paints the Clio roster in the RoboKnights theme (2026-11-04): the
# dashboard's near-black (#242424) and knight gold (#E8B33D), from
# .streamlit/config.toml. Run by the manual "Clio theme" workflow; safe to
# re-run (e.g. after a new year's tab is added) since it only sets colours.
#
# On every tab:
#   - header row: near-black fill, bold gold text;
#   - the ADHOC MEMBERS label row (if any): the same;
#   - data rows: white / light-gold alternating bands (existing bands and
#     Sheets tables are recoloured; a tab with neither gets new bands);
#   - the tab itself: gold.
# Values are never read out or printed - logs show counts only (this repo's
# Actions logs are public).

from clio_cleanup import CLIO_SHEET_ID, sheets_client_from_env


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return {"rgbColor": {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}}


DARK = _rgb("#242424")
GOLD = _rgb("#E8B33D")
WHITE = _rgb("#FFFFFF")
GOLD_TINT = _rgb("#FBF3DF")

HEADER_FORMAT = {
    "backgroundColorStyle": DARK,
    "textFormat": {"foregroundColorStyle": GOLD, "bold": True},
}
HEADER_FIELDS = "userEnteredFormat.backgroundColorStyle,userEnteredFormat.textFormat.foregroundColorStyle,userEnteredFormat.textFormat.bold"


def _used_columns(rows):
    return max((len(r) for r in rows), default=1) or 1


def main():
    gc = sheets_client_from_env()
    if gc is None:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON_B64 isn't set")
    sh = gc.open_by_key(CLIO_SHEET_ID)
    meta = sh.fetch_sheet_metadata({"fields": "sheets(properties,bandedRanges,tables)"})
    requests = []
    counts = {"tabs": 0, "bands recoloured": 0, "bands added": 0, "tables recoloured": 0, "adhoc rows": 0}
    for s in meta["sheets"]:
        props = s["properties"]
        if props.get("sheetType", "GRID") != "GRID":
            continue
        sid = props["sheetId"]
        counts["tabs"] += 1
        rows = sh.get_worksheet_by_id(sid).get_all_values()
        ncols = max(_used_columns(rows), 7)
        nrows = max(len(rows), 2)

        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sid, "tabColorStyle": GOLD}, "fields": "tabColorStyle",
        }})
        requests.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols},
            "cell": {"userEnteredFormat": HEADER_FORMAT}, "fields": HEADER_FIELDS,
        }})
        for i, r in enumerate(rows):
            if r and (r[0] or "").strip().upper() == "ADHOC MEMBERS":
                counts["adhoc rows"] += 1
                requests.append({"repeatCell": {
                    "range": {"sheetId": sid, "startRowIndex": i, "endRowIndex": i + 1, "startColumnIndex": 0, "endColumnIndex": ncols},
                    "cell": {"userEnteredFormat": HEADER_FORMAT}, "fields": HEADER_FIELDS,
                }})

        band_props = {"headerColorStyle": DARK, "firstBandColorStyle": WHITE, "secondBandColorStyle": GOLD_TINT}
        for b in s.get("bandedRanges", []):
            counts["bands recoloured"] += 1
            requests.append({"updateBanding": {
                "bandedRange": {"bandedRangeId": b["bandedRangeId"], "rowProperties": band_props},
                "fields": "rowProperties",
            }})
        for t in s.get("tables", []):
            counts["tables recoloured"] += 1
            requests.append({"updateTable": {
                "table": {"tableId": t["tableId"], "rowsProperties": band_props},
                "fields": "rowsProperties",
            }})
        if not s.get("bandedRanges") and not s.get("tables"):
            counts["bands added"] += 1
            requests.append({"addBanding": {"bandedRange": {
                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": nrows + 200, "startColumnIndex": 0, "endColumnIndex": ncols},
                "rowProperties": band_props,
            }}})

    sh.batch_update({"requests": requests})
    print("Clio restyled: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()

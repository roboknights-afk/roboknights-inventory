# Checks for the permission-slip pieces that need no network: who signs, how
# dates and mobiles are written, how a name is matched, where the sign-offs sit
# in the doc, and the slip checker's reading of a two-day date.
#
#   python test_slip.py
#
# Names below are made up. The live path (Google Docs/Sheets, E2C) is tested by
# hand against a COPY of the document, never the real one.

from datetime import date

import perm_slip_check
import perm_slip_writer
import slip_roster as sr

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, wanted {want!r}")


# --- who signs: HOD, Hm 6-8 only if a 6-8 student, Hm 9-10 only if a 9-10 student, VP, Principal
check("all grades", sr.approvers_for(["7-A", "10-B", "12-C"]), ["hod", "hm68", "hm910", "vp", "principal"])
check("no 6-8", sr.approvers_for(["9-A", "11-B"]), ["hod", "hm910", "vp", "principal"])
check("no 9-10", sr.approvers_for(["6-A", "12-B"]), ["hod", "hm68", "vp", "principal"])
check("only 11-12", sr.approvers_for(["11-A", "12-B"]), ["hod", "vp", "principal"])
check("unknown class adds nothing", sr.approvers_for(["", "11-A"]), ["hod", "vp", "principal"])
check("grade 8 and 9 edges", sr.approvers_for(["8 D", "9"]), ["hod", "hm68", "hm910", "vp", "principal"])

# --- where the sign-offs sit in the two small tables
L = perm_slip_writer.APPROVER_LABELS
check("slots, all five", perm_slip_writer.approver_slots(["hod", "hm68", "hm910", "vp", "principal"]),
      [L["hod"], L["hm68"], L["hm910"], L["vp"], "", L["principal"]])
check("slots, four", perm_slip_writer.approver_slots(["hod", "hm910", "vp", "principal"]),
      [L["hod"], L["hm910"], L["vp"], "", "", L["principal"]])
check("slots, three", perm_slip_writer.approver_slots(["hod", "vp", "principal"]),
      [L["hod"], L["vp"], "", "", "", L["principal"]])

# --- dates
check("single day", sr.format_date(date(2026, 10, 16)), "Friday, 16th October 2026")
check("single, e2c text ignored", sr.format_date(date(2026, 10, 24), "Saturday, 24th October 2026 (Offline)"),
      "Saturday, 24th October 2026")
check("two days, month first", sr.format_date(date(2026, 10, 27), "October 27 & 28, 2026"),
      "Tuesday, 27th & Wednesday, 28th October 2026")
check("two days, day first", sr.format_date(date(2026, 10, 23), "23-24 October 2026"),
      "Friday, 23rd & Saturday, 24th October 2026")
check("ordinals", [sr._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)],
      ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"])

# --- the slip checker reads a two-day slip as covering both days
check("doc dates, two days", perm_slip_check.parse_doc_dates("Tuesday, 27th & Wednesday, 28th October 2026"),
      [date(2026, 10, 27), date(2026, 10, 28)])
check("doc dates, one day", perm_slip_check.parse_doc_dates("Friday, 16th October 2026"), [date(2026, 10, 16)])
check("doc dates, unreadable", perm_slip_check.parse_doc_dates("soon"), [])
blocks = [{"event": "Force Fest 2K26", "organising": "Apeejay", "mode": "Offline",
           "date_text": "Tuesday, 27th & Wednesday, 28th October 2026", "names": 5}]
check("judge finds a two-day slip on day one", perm_slip_check.judge("Force Fest 2K26", date(2026, 10, 27), blocks)[0], "ready")
check("judge still rejects another day", perm_slip_check.judge("Force Fest 2K26", date(2026, 10, 29), blocks)[0], "missing")

# --- mobiles and classes
check("mobile plain", sr.clean_mobile("9812345678"), "9812345678")
check("mobile with a note", sr.clean_mobile("9812345678 (Father)"), "9812345678")
check("mobile with a space", sr.clean_mobile("98123 45678"), "9812345678")
check("mobile too short", sr.clean_mobile("981234"), "")
check("mobile empty", sr.clean_mobile(None), "")
check("class-sec", sr.class_sec("12", "b"), "12-B")
check("class only", sr.class_sec("12", ""), "12")
check("grade_of", [sr.grade_of(x) for x in ("12-B", "9 D", "", "x")], [12, 9, None, None])

# --- event modes
check("stored mode wins", sr.default_mode("nCinema", "Hybrid", {"ncinema": "online"}), "online")
check("(Online) in the name", sr.default_mode("Virtual Warriors (Online)", "Hybrid"), "online")
check("falls back to the competition", sr.default_mode("Robo Race", "Offline"), "offline")
check("nothing known -> offline", sr.default_mode("Robo Race", ""), "offline")
check("junk stored mode ignored", sr.default_mode("Robo Race", "Hybrid", {"robo race": "maybe"}), "hybrid")

# --- matching a name to a student
exun = sr.Directory([
    {"name": "Asha Verma", "adm": "R10001", "cls": "11", "sec": "A", "mobile": "9000000001"},
    {"name": "Ravi Kumar Singh", "adm": "E10002", "cls": "9", "sec": "C", "mobile": ""},
    {"name": "Meera Nair", "adm": "V10003", "cls": "12", "sec": "B", "mobile": "9000000003"},
    {"name": "Meera Nair", "adm": "V10004", "cls": "8", "sec": "D", "mobile": "9000000004"},   # same name, different student
])
rk = sr.Directory([
    {"name": "Ravi K. Singh", "adm": "E10002", "cls": "9", "sec": "C", "mobile": "9000000002"},
    {"name": "Tara Das", "adm": "R10005", "cls": "10", "sec": "F", "mobile": "9000000005"},
])
r = sr.resolve("asha verma", exun, rk)
check("exact, any case", (r["status"], r["source"], r["student"]["adm"]), ("found", "Exun", "R10001"))
r = sr.resolve("Ravi Kumar Singh", exun, rk)
check("gap filled from RoboKnights (same admission no.)", r["student"]["mobile"], "9000000002")
r = sr.resolve("Tara Das", exun, rk)
check("only in RoboKnights", (r["status"], r["source"]), ("found", "RoboKnights"))
r = sr.resolve("Meera Nair", exun, rk)
check("two students, one name", (r["status"], r["student"]), ("ambiguous", None))
r = sr.resolve("Ashray", exun, rk)
check("first name only is never guessed", r["status"], "missing")
r = sr.resolve("Asha V.", exun, rk)
check("an initial may stand in for a word", r["status"], "found")

# --- one row per student, events joined
rows = sr.students_for_events([
    {"name": "Robo Race", "names": [{"name": "Asha Verma", "confirmed": True}, {"name": "kiran rao", "confirmed": True},
                                    {"name": "Tara Das", "confirmed": False}]},      # white cell: listed, not confirmed
    {"name": "Quiz", "names": [{"name": "ASHA VERMA", "confirmed": True}]},
], exun, rk)
check("white (unconfirmed) cells never go on a slip", [r["name"] for r in rows if "Tara" in r["name"]], [])
check("confirmed_names keeps only green", [n["name"] for n in sr.confirmed_names(
    {"names": [{"name": "A", "confirmed": True}, {"name": "B", "confirmed": False}, {"name": "C"}]})], ["A"])
check("an event with nobody green gives no rows", sr.students_for_events(
    [{"name": "Quiz", "names": [{"name": "Asha Verma", "confirmed": False}]}], exun, rk), [])
check("one row per student", len(rows), 2)
check("events joined", rows[0]["events"], ["Robo Race", "Quiz"])
check("class-sec built", rows[0]["class_sec"], "11-A")
check("unfound name tidied, blanks kept", (rows[1]["name"], rows[1]["adm"], rows[1]["status"]), ("Kiran Rao", "", "missing"))

if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("all slip checks passed")

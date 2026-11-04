# Checks for the bot's rulebook lookup (discord_bot/rulebook_search.py): which
# rulebook and which pages a rules question gets. Made-up rulebooks only.
#
#   python test_rulebook_search.py

import sys

sys.path.insert(0, "discord_bot")
import rulebook_search as rs  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


COMPS = [{"competition_id": 1, "name": "Test Cup 2026"}, {"competition_id": 2, "name": "Other Fest"}]
EVENTS = [{"event_id": 10, "competition_id": 1, "name": "Robo War"}, {"event_id": 20, "competition_id": 2, "name": "Maze"}]
BOOKS = [
    {"rulebook_id": 1, "competition_id": 1, "event_id": 10, "file_name": "robowar_rules.pdf", "pages": [
        "Introduction. Welcome to Robo War at Test Cup.",
        "Bot specifications: the maximum weight of the bot is 8 kg. Weight is checked before every match.",
        "",
        "Arena: a 6 m x 6 m arena. Each match lasts 3 minutes.",
    ]},
    {"rulebook_id": 2, "competition_id": 2, "event_id": 20, "file_name": "maze_rules.pdf", "pages": [
        "Maze rules. The maximum weight of the bot is 1 kg. The maze is 16 x 16 cells.",
    ]},
]

text = rs.search(BOOKS, COMPS, EVENTS, "robo war", "max weight of the bot")
check("picks the named event's rulebook", "robowar_rules.pdf" in text and "maze_rules.pdf" not in text, True)
check("best page first, with its number", text.startswith(rs.HEADER + "\n\n[robowar_rules.pdf, page 2 (Test Cup 2026 - Robo War)]"), True)
check("the answer is in it", "8 kg" in text, True)

text = rs.search(BOOKS, COMPS, EVENTS, "test cup", "how long is a match")
check("match length page found", "page 4" in text and "3 minutes" in text, True)

text = rs.search(BOOKS, COMPS, EVENTS, "maze", "weight limit")
check("other competition's rulebook", "maze_rules.pdf" in text and "1 kg" in text and "8 kg" not in text, True)

text = rs.search(BOOKS, COMPS, EVENTS, "", "arena size")
check("no competition named: searches all", "6 m x 6 m" in text, True)

check("nothing uploaded", rs.search([], COMPS, EVENTS, "robo war", "weight"), rs.NONE_UPLOADED)
check("blank pages skipped", "page 3" in rs.search(BOOKS, COMPS, EVENTS, "robo war", "zzz"), False)

if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("all rulebook checks passed")

"""One-time migration: import the website's existing data/achievements.ts
into the new public_achievements Supabase table (see the comment above
that table in supabase_schema.sql for why this table exists). Run once,
right after that table has been created in Supabase's SQL editor.

    python import_public_achievements.py --dry-run     see what would be inserted
    python import_public_achievements.py                write it

Parses data/achievements.ts with Node (a real JS engine) rather than a
hand-rolled regex parser — the file's quoting is not uniform enough for
that to be safe. One entry has escaped quotes INSIDE a string
("\"Technovanza\" event of \"ATAL Tinkering Fest\"..."), and formatting
(line breaks, spacing) varies entry to entry throughout the file. Since
this is plain JS-compatible array/object literal syntax (no TS type
annotations in the data itself), Node can eval it directly and hand back
exactly what the site itself will render — the same trust boundary
export_achievements.py already carries (it writes to this same file).

Safe to run more than once by accident: it refuses to insert anything if
public_achievements already has rows. This is a one-time seed, not a
sync — if it ever needs re-running on purpose, empty the table in
Supabase's SQL editor first.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE = Path(os.environ.get("ROBOKNIGHTS_SITE_REPO",
                           Path.home() / "Documents/GitHub/RoboKnights-Clan.github.io"))
ACHIEVEMENTS_TS = SITE / "data" / "achievements.ts"

# Reads the file itself rather than trusting the array literal is on its
# own line — pull everything between the first "[" and the LAST "]", the
# same "don't guess the shape, just find the real boundary" approach
# export_achievements.py's append_to_file() already uses.
NODE_EXTRACT_SCRIPT = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const start = src.indexOf("[");
const end = src.lastIndexOf("]");
if (start === -1 || end === -1 || end < start) {
    console.error("Could not find an array literal in the file.");
    process.exit(1);
}
const arr = eval(src.slice(start, end + 1));
process.stdout.write(JSON.stringify(arr));
"""


# The real data has "Naitonal" (a typo), "Inter-School", trailing spaces
# ("National ", "Interschool "), and "Regional (Delhi)" as its own level
# distinct from plain "Regional" - the exact same set pages/achievements.tsx
# in the website repo already normalizes for DISPLAY via its own
# LEVEL_LABEL map. Reused here (same keys, same canonical spellings)
# rather than inventing a second, possibly-diverging normalization -
# this one actually fixes the stored value once, since public_achievements
# is now the editable source, not just a display pass.
LEVEL_LABEL = {
    "interschool": "Interschool",
    "inter-school": "Interschool",
    "national": "National",
    "naitonal": "National",
    "international": "International",
    "regional": "Regional",
    "regional (delhi)": "Regional (Delhi)",
}


def normalize_level(raw):
    key = (raw or "").strip().lower()
    return LEVEL_LABEL.get(key, (raw or "").strip())


def load_entries():
    if not ACHIEVEMENTS_TS.exists():
        sys.exit(f"{ACHIEVEMENTS_TS} not found. Set ROBOKNIGHTS_SITE_REPO if it's not a sibling folder.")
    result = subprocess.run(
        ["node", "-e", NODE_EXTRACT_SCRIPT, str(ACHIEVEMENTS_TS)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"Node couldn't parse {ACHIEVEMENTS_TS}:\n{result.stderr}")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = load_entries()
    print(f"Parsed {len(entries)} entries from {ACHIEVEMENTS_TS}")

    rows = [
        {
            "competition": e.get("competition") or "",
            "level": normalize_level(e.get("level")),
            "year": e.get("year") or "",
            "prize": e.get("prize") or "",
            "members": e.get("members") or [],
        }
        for e in entries
    ]

    if args.dry_run:
        print("\nFirst 3 rows as a sample:")
        for row in rows[:3]:
            print(f"   {row}")
        print(f"\n--dry-run: nothing written ({len(rows)} row(s) would be inserted)")
        return

    load_dotenv(HERE / ".env")
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    existing = client.table("public_achievements").select("public_achievement_id").limit(1).execute().data
    if existing:
        sys.exit(
            "public_achievements already has rows — refusing to import again. "
            "This is a one-time seed, not a sync. If you really mean to redo it, "
            "empty the table in Supabase's SQL editor first."
        )

    # Batched, not one call for all 198+ rows - keeps any single request
    # well under PostgREST's payload limits and gives a clearer failure
    # point if one batch has a bad row.
    BATCH = 50
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        client.table("public_achievements").insert(batch).execute()
        inserted += len(batch)
        print(f"   inserted {inserted}/{len(rows)}")

    print(f"\nDone — {inserted} row(s) imported into public_achievements.")
    print("This table is now the source of truth for data/achievements.ts — "
          "edit results on the Website: Results page from here on, not the file.")


if __name__ == "__main__":
    main()

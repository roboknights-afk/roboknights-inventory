"""One-time migration: import the website's existing data/alumni.ts into
the new public_alumni Supabase table (see its comment in
supabase_schema.sql). Run once, right after that table has been created
in Supabase's SQL editor.

    python import_public_alumni.py --dry-run     see what would be inserted
    python import_public_alumni.py                write it

Parses data/alumni.ts with Node (a real JS engine), same reasoning as
import_public_achievements.py - the file's formatting is not uniform
enough to trust a regex parser with.

Flattens the file's nested { batch, people: [...] } shape into one row
per PERSON, since that's the unit a host will actually add/edit/delete
going forward. export_public_alumni.py is what regroups rows back into
the nested shape pages/alumni.tsx expects.

Safe to run more than once by accident: refuses if public_alumni already
has rows.
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
ALUMNI_TS = SITE / "data" / "alumni.ts"

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


def load_batches():
    if not ALUMNI_TS.exists():
        sys.exit(f"{ALUMNI_TS} not found. Set ROBOKNIGHTS_SITE_REPO if it's not a sibling folder.")
    result = subprocess.run(
        ["node", "-e", NODE_EXTRACT_SCRIPT, str(ALUMNI_TS)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"Node couldn't parse {ALUMNI_TS}:\n{result.stderr}")
    return json.loads(result.stdout)


def flatten(batches):
    rows = []
    for batch in batches:
        for person in batch.get("people", []):
            rows.append({
                "batch": batch.get("batch") or "",
                "name": person.get("name") or "",
                "role": person.get("role") or None,
                "src": person.get("src") or None,
                "socials": person.get("socials") or [],
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batches = load_batches()
    rows = flatten(batches)
    print(f"Parsed {len(batches)} batch(es), {len(rows)} people, from {ALUMNI_TS}")

    if args.dry_run:
        print("\nFirst 3 rows as a sample:")
        for row in rows[:3]:
            print(f"   {row}")
        print(f"\n--dry-run: nothing written ({len(rows)} row(s) would be inserted)")
        return

    load_dotenv(HERE / ".env")
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    existing = client.table("public_alumni").select("public_alumni_id").limit(1).execute().data
    if existing:
        sys.exit(
            "public_alumni already has rows — refusing to import again. "
            "This is a one-time seed, not a sync. If you really mean to redo it, "
            "empty the table in Supabase's SQL editor first."
        )

    BATCH_SIZE = 50
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        client.table("public_alumni").insert(chunk).execute()
        inserted += len(chunk)
        print(f"   inserted {inserted}/{len(rows)}")

    print(f"\nDone — {inserted} row(s) imported into public_alumni.")
    print("This table is now the source of truth for data/alumni.ts — "
          "edit alumni on the Website page in the dashboard from here on, not the file.")


if __name__ == "__main__":
    main()

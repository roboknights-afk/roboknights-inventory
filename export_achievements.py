"""Append newly-published results onto the website's achievements list.

    data/achievements.ts   new entries added at the end, nothing removed

Only touches Supabase for two things: reading achievements.website_status
(a host's explicit "approved" - see the "Website" page in the app, and
supabase_schema.sql's comment on why logging a result does not mean it
is allowed onto the public site on its own) and, after a successful
write, stamping exported_at so the same result never gets appended twice.

    python export_achievements.py --dry-run     see what would be added
    python export_achievements.py               write it

This is a two-source problem, not a sync: data/achievements.ts is 189
hand-compiled historical results going back to 2002; the Supabase
achievements table is 13 distinct self-reported results, all from 2026,
with no overlap in HOW they are structured (the static file is one row
per result with a members list; Supabase is one row per person per
result). This script only ever APPENDS - it never rewrites or replaces
the 189 existing entries, and it inserts new ones as plain text right
before the array's closing bracket, so nothing already there is
reformatted or touched.

Known duplicate, excluded permanently below: achievement_ids 28/29/30
(competition_id 23, event_id 41, "Techस्पर्धा
· Steel Clash · 2026-04-17 · Aryamman/Naitik/Arhaan") is the
same real result as the static file's existing "Techspardha Steel Clash"
2026 entry, just self-reported independently under the competition's
Devanagari-script name. Confirmed by hand, 2026-09-01.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE = Path(os.environ.get("ROBOKNIGHTS_SITE_REPO",
                           Path.home() / "Documents/GitHub/RoboKnights-Clan.github.io"))
ACHIEVEMENTS_TS = SITE / "data" / "achievements.ts"

# See the module docstring - this is the confirmed Techspardha duplicate,
# not a placeholder to "figure out later". Never exported, regardless of
# its website_status.
PERMANENTLY_EXCLUDED = {(23, 41)}


def build_entries(rows, comp_by_id):
    """rows: achievements already filtered to published + not yet exported.
    Groups the one-row-per-person shape into one row per actual result."""
    groups = {}
    for row in rows:
        key = (row["competition_id"], row["event_id"], row.get("position"))
        groups.setdefault(key, []).append(row)

    entries, skipped = [], []
    for (competition_id, event_id, position), group in groups.items():
        if (competition_id, event_id) in PERMANENTLY_EXCLUDED:
            skipped.append((competition_id, event_id, "confirmed duplicate of an existing entry"))
            continue

        comp = comp_by_id.get(competition_id)
        if comp is None or not comp.get("competition_date"):
            skipped.append((competition_id, event_id, "competition has no date on record"))
            continue

        levels = {r.get("level") for r in group}
        if None in levels or not levels:
            skipped.append((competition_id, event_id, "level not set yet - a host needs to set it"))
            continue
        if len(levels) > 1:
            # Same event, same position, but the group disagrees on level -
            # a data problem worth surfacing rather than silently picking one.
            skipped.append((competition_id, event_id, f"group disagrees on level: {levels}"))
            continue

        entries.append({
            "competition": comp["name"],
            "level": next(iter(levels)),
            "year": comp["competition_date"][:4],
            "prize": (position or "").strip(),
            "members": [r["_name"] for r in group],
            "achievement_ids": [r["achievement_id"] for r in group],
        })

    return entries, skipped


def render_entry(entry):
    lines = ["  {"]
    lines.append(f'    competition: {json.dumps(entry["competition"], ensure_ascii=False)},')
    lines.append(f'    level: {json.dumps(entry["level"], ensure_ascii=False)},')
    lines.append(f'    year: {json.dumps(entry["year"])},')
    lines.append(f'    prize: {json.dumps(entry["prize"], ensure_ascii=False)},')
    members_json = ", ".join(json.dumps(m, ensure_ascii=False) for m in entry["members"])
    lines.append(f'    members: [{members_json}]')
    lines.append("  }")
    return "\n".join(lines)


def append_to_file(entries):
    text = ACHIEVEMENTS_TS.read_text(encoding="utf-8")
    marker = "];"
    if not text.rstrip().endswith(marker):
        raise SystemExit(
            f"{ACHIEVEMENTS_TS} does not end with '];' as expected - "
            "stopping rather than guessing where to insert."
        )
    # Insert right before the final "];", after adding a comma onto
    # whatever the current last entry is. Pure text surgery: nothing
    # before this point is touched, re-parsed, or reformatted.
    cut = text.rstrip().rfind(marker)
    head = text[:cut].rstrip()
    if not head.endswith("}"):
        raise SystemExit("Unexpected file shape right before '];' - stopping rather than guessing.")
    new_text = head + ",\n" + ",\n".join(render_entry(e) for e in entries) + "\n];\n"
    ACHIEVEMENTS_TS.write_text(new_text, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(HERE / ".env")
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    try:
        achievements = client.table("achievements").select(
            "achievement_id,user_id,competition_id,event_id,position,level,"
            "website_status,website_note,exported_at"
        ).eq("website_status", "approved").is_("exported_at", "null").execute().data
    except Exception as error:
        if "does not exist" not in str(error):
            raise
        print("NOTE: the level/website_status/exported_at columns are")
        print("      missing - run the migration at the end of")
        print("      supabase_schema.sql in Supabase's SQL editor first.")
        return

    if not achievements:
        print("Nothing new to export - either nothing is published yet, or")
        print("everything published has already been exported.")
        return

    users = {u["user_id"]: u["name"] for u in client.table("users").select("user_id,name").execute().data}
    comps = {c["competition_id"]: c for c in client.table("competitions").select("*").execute().data}

    for row in achievements:
        row["_name"] = users.get(row["user_id"], "Unknown")

    entries, skipped = build_entries(achievements, comps)

    print(f"{len(achievements)} published-but-unexported rows -> {len(entries)} new result(s)")
    for e in entries:
        print(f"   {e['competition']} · {e['level']} · {e['year']} · {e['prize']} · {', '.join(e['members'])}")
    if skipped:
        print("\nnot exported:")
        for cid, eid, why in skipped:
            print(f"   competition {cid}, event {eid}: {why}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return

    if not entries:
        return

    if not SITE.exists():
        sys.exit(f"Website repo not found at {SITE}. Set ROBOKNIGHTS_SITE_REPO.")

    append_to_file(entries)
    print(f"\nappended {len(entries)} entr{'y' if len(entries)==1 else 'ies'} to {ACHIEVEMENTS_TS}")

    # A real Python-computed timestamp, not the literal string "now()" -
    # PostgREST has no SQL function evaluation on an update payload, so
    # that string would just get stored as unparseable text.
    exported_ids = [aid for e in entries for aid in e["achievement_ids"]]
    stamp = datetime.now(timezone.utc).isoformat()
    client.table("achievements").update({"exported_at": stamp}).in_(
        "achievement_id", exported_ids
    ).execute()
    print(f"marked {len(exported_ids)} achievement row(s) as exported")
    print("\nNow look at the diff in the website repo and commit it if it is right.")


if __name__ == "__main__":
    main()

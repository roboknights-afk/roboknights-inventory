# Half of the host review queue for the public website — the other half
# is app_pages/website_members.py. Split into two pages rather than one
# long page with two sections, so reviewing results and reviewing member
# profiles don't sit in each other's way.
#
# Host-only. Not linked from anywhere a non-host would see it — app.py
# only adds this page to the nav for hosts — but guarded here too, same
# reasoning as the Members page: st.navigation's page list isn't the only
# way in if someone hits the URL directly.
#
# Three sections now (2026-09-22), not one:
#   1. Review queue (original) — a member self-reported a result for a
#      competition/event they attended; approving it now ALSO copies it
#      straight into public_achievements (below), rather than leaving
#      that for export_achievements.py to pick up later.
#   2. Add a result directly — for anything that didn't start as a
#      member's self-report: an old result being backfilled, something a
#      host heard about, a one-off. Free-text, no competition/event
#      record required.
#   3. All public achievements — every row in public_achievements,
#      editable and deletable right here. This table is what
#      export_public_achievements.py fully regenerates the website's
#      data/achievements.ts from — see the comment above that table in
#      supabase_schema.sql for why this replaced the old
#      export_achievements.py (which could only ever append, never edit
#      or remove).

from datetime import datetime, timezone

import streamlit as st

from shared import cached_table, get_client, invalidate_cache, safe_write, send_email

is_host = st.session_state.is_host
if not is_host:
    st.error("Access only for hosts.")
    st.stop()

client = get_client()
# "Regional (Delhi)" is a real, distinct level in the historical data -
# not the same as plain "Regional" - see LEVEL_LABEL in
# import_public_achievements.py, which this list matches exactly.
LEVEL_OPTIONS = ["Interschool", "National", "International", "Regional", "Regional (Delhi)"]


def _public_achievements():
    try:
        return cached_table("public_achievements")
    except Exception as e:
        if "does not exist" in str(e):
            return None
        raise


def _insert_public_achievement(competition, level, year, prize, members, source_ids=None):
    client.table("public_achievements").insert({
        "competition": competition,
        "level": level,
        "year": year,
        "prize": prize,
        "members": members,
        "source_achievement_ids": source_ids,
    }).execute()


st.title(":material/emoji_events: Website: Results")
st.caption(
    "Every result a member has logged, waiting on a level, position and a "
    "yes or no before it can reach roboknights.in — plus the full, "
    "editable list of everything already on the site."
)

if _public_achievements() is None:
    st.error(
        ":material/database_off: The `public_achievements` table doesn't exist yet. "
        "Run the migration at the end of `supabase_schema.sql` in Supabase's SQL "
        "editor, then run `python import_public_achievements.py` once to bring in "
        "the site's existing results, before using this page."
    )
    st.stop()

if "website_review_message" not in st.session_state:
    st.session_state.website_review_message = None
if st.session_state.website_review_message:
    st.toast(st.session_state.website_review_message, icon=":material/emoji_events:")
    st.session_state.website_review_message = None

all_achievements = cached_table("achievements")
competitions = cached_table("competitions")
comp_name_by_id = {c["competition_id"]: c["name"] for c in competitions}
comp_by_id = {c["competition_id"]: c for c in competitions}
event_name_by_id = {e["event_id"]: e["name"] for e in cached_table("competition_events")}
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

# One card per actual result, not per person - the same (competition,
# event, position) grouping export_achievements.py uses, so what a host
# reviews here is exactly what would become one website entry.
ach_groups = {}
for a in all_achievements:
    key = (a["competition_id"], a["event_id"], a.get("position"))
    ach_groups.setdefault(key, []).append(a)

ach_pending = [g for g in ach_groups.values() if (g[0].get("website_status") or "pending") == "pending"]
ach_decided = [g for g in ach_groups.values() if (g[0].get("website_status") or "pending") != "pending"]

m1, m2, m3 = st.columns(3)
m1.metric("Waiting for review", len(ach_pending), border=True)
m2.metric("Approved", sum(1 for g in ach_decided if g[0]["website_status"] == "approved"), border=True)
m3.metric("Declined", sum(1 for g in ach_decided if g[0]["website_status"] == "declined"), border=True)

show_all = st.toggle("Show already-reviewed results too", key="ach_show_reviewed")
ach_list = ach_groups.values() if show_all else ach_pending

if not ach_list:
    st.caption("Nothing waiting on a result right now.")
else:
    for group in ach_list:
        first = group[0]
        status = first.get("website_status") or "pending"
        names = ", ".join(user_name_by_id.get(a["user_id"], "Unknown") for a in group)
        comp_name = comp_name_by_id.get(first["competition_id"], "Unknown competition")
        event_name = event_name_by_id.get(first["event_id"], "Unknown event")

        with st.container(border=True, key=f"rkcard_review_ach_{first['achievement_id']}"):
            st.markdown(f"**{names}** — {comp_name} ({event_name})")

            pcol, lcol = st.columns([2, 1])
            new_position = pcol.text_input(
                "Position", value=first.get("position") or "",
                key=f"review_pos_{first['achievement_id']}",
                disabled=status != "pending",
            )
            new_level = lcol.selectbox(
                "Level", LEVEL_OPTIONS,
                index=LEVEL_OPTIONS.index(first["level"]) if first.get("level") in LEVEL_OPTIONS else 0,
                key=f"review_level_{first['achievement_id']}",
                disabled=status != "pending",
            )

            if status == "pending":
                note = st.text_input(
                    "Note (only needed if declining — shown back to whoever logged it)",
                    key=f"review_note_ach_{first['achievement_id']}",
                )
                bcol1, bcol2 = st.columns(2)
                if bcol1.button(
                    "Approve", icon=":material/check_circle:", type="primary",
                    key=f"review_approve_ach_{first['achievement_id']}",
                ):
                    with safe_write("approve this result for the website"):
                        for a in group:
                            client.table("achievements").update({
                                "position": new_position.strip() or None,
                                "level": new_level,
                                "website_status": "approved",
                                "website_note": note.strip() or None,
                            }).eq("achievement_id", a["achievement_id"]).execute()
                        # Straight into public_achievements now, not left
                        # for export_achievements.py to pick up later —
                        # that script only ever appended and is retired
                        # (see the table's comment in supabase_schema.sql).
                        comp = comp_by_id.get(first["competition_id"]) or {}
                        year = (comp.get("competition_date") or "")[:4] or "unknown"
                        _insert_public_achievement(
                            competition=comp_name,
                            level=new_level,
                            year=year,
                            prize=new_position.strip(),
                            members=[user_name_by_id.get(a["user_id"], "Unknown") for a in group],
                            source_ids=[a["achievement_id"] for a in group],
                        )
                        invalidate_cache()
                    st.session_state.website_review_message = f"Approved {event_name}."
                    st.rerun()
                if bcol2.button(
                    "Decline", icon=":material/cancel:",
                    key=f"review_decline_ach_{first['achievement_id']}",
                ):
                    with safe_write("decline this result for the website"):
                        for a in group:
                            client.table("achievements").update({
                                "website_status": "declined",
                                "website_note": note.strip() or None,
                            }).eq("achievement_id", a["achievement_id"]).execute()
                        invalidate_cache()
                        # One email per person on the result, not one per
                        # row — a teammate result is several rows (one per
                        # achievement_id) but the same real decline.
                        # Best-effort like every other email in this app.
                        for uid in {a["user_id"] for a in group}:
                            recipient = user_email_by_id.get(uid)
                            if recipient:
                                send_email(
                                    recipient,
                                    "Your result wasn't approved for the website",
                                    f"A host didn't approve your {event_name} result for "
                                    "roboknights.in"
                                    + (f":\n\n{note.strip()}\n\n" if note.strip() else ".\n\n")
                                    + "You can delete it and log it again to ask for another "
                                    "review.",
                                )
                    st.session_state.website_review_message = f"Declined {event_name}."
                    st.rerun()
            else:
                badge = (
                    (":material/public: Approved — will appear on the site")
                    if status == "approved"
                    else ":material/public_off: Declined"
                )
                st.caption(badge + (f" — {first['website_note']}" if first.get("website_note") else ""))

st.divider()
st.subheader(":material/add_circle: Add a result directly")
st.caption(
    "For anything that didn't start as a member's self-report — an old "
    "result being backfilled, something a host heard about, a one-off. "
    "Goes straight onto the site's full list below; no competition or "
    "event record needed."
)
with st.form("add_public_achievement", clear_on_submit=True):
    add_comp = st.text_input("Competition")
    ac1, ac2, ac3 = st.columns(3)
    add_level = ac1.selectbox("Level", LEVEL_OPTIONS)
    add_year = ac2.text_input("Year", max_chars=4, placeholder="2026")
    add_prize = ac3.text_input("Result", placeholder="First / Second / Finalist / ...")
    add_members = st.text_input("Members (comma-separated)", placeholder="Naitik Jindal, Aryamman Ojha")
    if st.form_submit_button("Add", icon=":material/add:", type="primary"):
        if not add_comp.strip() or not add_year.strip():
            st.error("Competition and year are required.")
        else:
            with safe_write("add this result"):
                _insert_public_achievement(
                    competition=add_comp.strip(),
                    level=add_level,
                    year=add_year.strip(),
                    prize=add_prize.strip(),
                    members=[m.strip() for m in add_members.split(",") if m.strip()],
                )
                invalidate_cache()
            st.toast(f"Added {add_comp.strip()}.", icon=":material/emoji_events:")
            st.rerun()

st.divider()
st.subheader(":material/list_alt: All public achievements")
public_rows = _public_achievements()
st.caption(
    f"{len(public_rows)} result(s) — this is the full, editable list "
    "data/achievements.ts on the website gets regenerated from. Edit "
    "cells directly and Save, or delete a row below the table."
)

search = st.text_input(
    "Filter", placeholder="Competition, year, or a member's name",
    key="public_ach_search", label_visibility="collapsed",
)
term = search.strip().lower()
visible_public = [
    r for r in public_rows
    if not term
    or term in (r.get("competition") or "").lower()
    or term in (r.get("year") or "").lower()
    or any(term in m.lower() for m in (r.get("members") or []))
] if public_rows else []
# Newest first, so a just-added or just-approved result is easy to find
# without scrolling past two decades of history first.
visible_public.sort(key=lambda r: r.get("year") or "", reverse=True)

if not visible_public:
    st.caption("Nothing matches." if term else "Nothing here yet.")
else:
    public_table_rows = [
        {
            "Competition": r.get("competition") or "",
            "Level": r.get("level") or "",
            "Year": r.get("year") or "",
            "Result": r.get("prize") or "",
            "Members": ", ".join(r.get("members") or []),
        }
        for r in visible_public
    ]
    edited_public = st.data_editor(
        public_table_rows,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            "Competition": st.column_config.TextColumn("Competition", width="large"),
            "Level": st.column_config.SelectboxColumn("Level", options=LEVEL_OPTIONS, width="small"),
            "Year": st.column_config.TextColumn("Year", width="small"),
            "Result": st.column_config.TextColumn("Result", width="medium"),
            "Members": st.column_config.TextColumn(
                "Members", width="large", help="Comma-separated names.",
            ),
        },
        key="public_achievements_editor",
    )

    if st.button("Save changes", icon=":material/check:", type="primary", key="save_public_achievements"):
        changed = 0
        with safe_write("save these changes"):
            for original, edited in zip(visible_public, edited_public):
                updates = {}
                if edited["Competition"].strip() != (original.get("competition") or ""):
                    updates["competition"] = edited["Competition"].strip()
                if edited["Level"] != (original.get("level") or ""):
                    updates["level"] = edited["Level"]
                if edited["Year"].strip() != (original.get("year") or ""):
                    updates["year"] = edited["Year"].strip()
                if edited["Result"].strip() != (original.get("prize") or ""):
                    updates["prize"] = edited["Result"].strip()
                new_members = [m.strip() for m in edited["Members"].split(",") if m.strip()]
                if new_members != (original.get("members") or []):
                    updates["members"] = new_members
                if updates:
                    # A real Python-computed timestamp, not the literal
                    # string "now()" - PostgREST has no SQL function
                    # evaluation on an update payload, so that string
                    # would just get stored as unparseable text (the same
                    # gotcha export_achievements.py's exported_at stamp
                    # already had to work around).
                    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
                    client.table("public_achievements").update(updates).eq(
                        "public_achievement_id", original["public_achievement_id"]
                    ).execute()
                    changed += 1
            invalidate_cache()
        st.toast(f"Saved {changed} change(s)." if changed else "Nothing changed.", icon=":material/check:")
        if changed:
            st.rerun()

    with st.expander(":material/delete: Delete a result"):
        del_options = {
            f"{r['competition']} · {r['year']} · {r.get('prize') or '(no result)'}": r["public_achievement_id"]
            for r in visible_public
        }
        to_delete = st.selectbox(
            "Which one", list(del_options.keys()), index=None,
            placeholder="Choose a result to delete", key="public_ach_delete_pick",
        )
        if to_delete and st.button(
            "Delete permanently", icon=":material/delete_forever:", key="public_ach_delete_confirm",
        ):
            with safe_write("delete this result"):
                client.table("public_achievements").delete().eq(
                    "public_achievement_id", del_options[to_delete]
                ).execute()
                invalidate_cache()
            st.toast("Deleted.", icon=":material/delete:")
            st.rerun()

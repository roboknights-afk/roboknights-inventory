# Everything that publishes to roboknights.in, one page, one host-only
# area — replaces the old separate "Website: Results" / "Website:
# Members" sidebar entries (app_pages/website_achievements.py and
# website_members.py, both deleted 2026-09-22) plus adds Alumni, which
# had no host workflow at all before this.
#
# A top st.segmented_control picks the section, NOT st.tabs — this file
# was built right after re-reading the exact lesson already learned once
# on this page's own predecessor (discord_messages.py, 2026-09-15):
# st.tabs() has no state of its own and silently snaps back to the first
# tab on every st.rerun(), and every save/approve/delete button on this
# page calls st.rerun(). segmented_control's selection lives in
# session_state like any other widget and survives a rerun from anywhere,
# same reason that page switched to a selectbox.
#
# Host-only. Not linked from anywhere a non-host would see it — app.py
# only adds this page to the nav for hosts — but guarded here too, same
# reasoning as every other host-only page: st.navigation's page list
# isn't the only way in if someone hits the URL directly.
#
# Each section is a flat, host-editable Supabase table that a matching
# export_public_*.py script in the repo root fully regenerates the
# website's data/*.ts file from — see supabase_schema.sql's comments on
# public_achievements / public_alumni for why (both replaced an older,
# append-only or nonexistent workflow). Members works differently: the
# website's full roster (data/members.ts) is generated straight from the
# real users table by export_members.py — there's no "public_members"
# table, because a member is always a real account, not something a host
# can freely add or delete the way a result or an alumnus can. What IS
# website-specific for Members is the review queue below, and the
# quick-edit table under it.

from datetime import datetime, timezone

import streamlit as st

from shared import cached_table, get_client, get_storage_client, invalidate_cache, safe_write, send_email

is_host = st.session_state.is_host
if not is_host:
    st.error("Access only for hosts.")
    st.stop()

client = get_client()

st.title(":material/language: Website")
st.caption("Everything that publishes to roboknights.in, in one place.")

section = st.segmented_control(
    "Section", ["Achievements", "Members", "Alumni"],
    default="Achievements", label_visibility="collapsed", key="website_section",
)

if "website_page_message" not in st.session_state:
    st.session_state.website_page_message = None
if st.session_state.website_page_message:
    st.toast(st.session_state.website_page_message, icon=":material/check_circle:")
    st.session_state.website_page_message = None

st.divider()

# ============================================================ Achievements
if section == "Achievements":
    # "Regional (Delhi)" is a real, distinct level in the historical data
    # - not the same as plain "Regional" - see LEVEL_LABEL in
    # import_public_achievements.py, which this list matches exactly.
    LEVEL_OPTIONS = ["Interschool", "National", "International", "Regional", "Regional (Delhi)"]

    def _public_achievements():
        try:
            return cached_table("public_achievements")
        except Exception as e:
            # PostgREST's actual wording for "this table doesn't exist"
            # is "Could not find the table ... in the schema cache"
            # (code PGRST205) - NOT "does not exist", which is what a
            # raw Postgres error says instead. Checking only the wrong
            # string here meant this fallback never fired and the whole
            # page crashed instead of showing the setup message, found
            # live 2026-09-22 the first time someone actually hit this
            # path (public_alumni, before its migration had been run).
            if "does not exist" in str(e) or "PGRST205" in str(e):
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

    st.subheader(":material/emoji_events: Results")
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
            "the site's existing results, before using this section."
        )
        st.stop()

    all_achievements = cached_table("achievements")
    competitions = cached_table("competitions")
    comp_name_by_id = {c["competition_id"]: c["name"] for c in competitions}
    comp_by_id = {c["competition_id"]: c for c in competitions}
    event_name_by_id = {e["event_id"]: e["name"] for e in cached_table("competition_events")}
    user_name_by_id = st.session_state.user_name_by_id
    user_email_by_id = st.session_state.user_email_by_id

    # One card per actual result, not per person - the same (competition,
    # event, position) grouping export_public_achievements.py's source
    # data uses, so what a host reviews here is exactly what becomes one
    # website entry.
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
                            # Straight into public_achievements now, not
                            # left for a later export run to pick up.
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
                        st.session_state.website_page_message = f"Approved {event_name}."
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
                            # One email per person on the result, not one
                            # per row — a teammate result is several rows
                            # but the same real decline. Best-effort like
                            # every other email in this app.
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
                        st.session_state.website_page_message = f"Declined {event_name}."
                        st.rerun()
                else:
                    badge = (
                        (":material/public: Approved — will appear on the site")
                        if status == "approved"
                        else ":material/public_off: Declined"
                    )
                    st.caption(badge + (f" — {first['website_note']}" if first.get("website_note") else ""))

    st.divider()
    st.markdown("##### :material/add_circle: Add a result directly")
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
                st.session_state.website_page_message = f"Added {add_comp.strip()}."
                st.rerun()

    st.divider()
    st.markdown("##### :material/list_alt: All public achievements")
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
    # Newest first, so a just-added or just-approved result is easy to
    # find without scrolling past two decades of history first.
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
                        # A real Python-computed timestamp, not the
                        # literal string "now()" - PostgREST does not
                        # evaluate SQL functions in an update payload.
                        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
                        client.table("public_achievements").update(updates).eq(
                            "public_achievement_id", original["public_achievement_id"]
                        ).execute()
                        changed += 1
                invalidate_cache()
            st.session_state.website_page_message = f"Saved {changed} change(s)." if changed else "Nothing changed."
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
                st.session_state.website_page_message = "Deleted."
                st.rerun()

# ================================================================ Members
elif section == "Members":
    # NOT client.storage - this section needs to read OTHER people's
    # photos, not your own, and the storage bucket's policies only ever
    # let someone read their own file. Only the service-role client,
    # which bypasses those policies by design, can see everyone's. Full
    # explanation in get_storage_client()'s own comment in shared.py.
    storage = get_storage_client()
    BUCKET = "member-photos"
    STATUS_OPTIONS = ["pending", "approved", "declined"]
    # Same values as app_pages/members.py's own GRADE_OPTIONS/
    # STANDING_LABELS/STANDING_VALUES - kept in sync by hand since
    # app_pages/*.py files run top-to-bottom as scripts, not importable
    # modules (see CLAUDE.md), so these can't just be imported from there.
    GRADE_OPTIONS = [7, 8, 9, 10, 11, 12]
    STANDING_LABELS = {"core_member": "Core member", "member": "Member", "adhoc": "Ad hoc", None: ""}
    STANDING_VALUES = {v: k for k, v in STANDING_LABELS.items()}

    st.subheader(":material/badge: Members")
    st.caption(
        "Every member who has asked to be shown on roboknights.in — their "
        "photo and social handles, waiting for a yes or no. A member is "
        "always a real signed-up account (Supabase Auth login) — nobody "
        "can be added here who hasn't created one themselves; the full "
        "editable roster below is for correcting details on an existing "
        "account, not creating a new one."
    )

    all_users = cached_table("users")
    # Only people who actually asked to be shown - a member who never
    # ticked the box on Your profile never appears here at all.
    requested = [u for u in all_users if u.get("photo_public")]
    mem_pending = [u for u in requested if (u.get("website_status") or "pending") == "pending"]
    mem_decided = [u for u in requested if (u.get("website_status") or "pending") != "pending"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Waiting for review", len(mem_pending), border=True)
    m2.metric("Approved", sum(1 for u in mem_decided if u["website_status"] == "approved"), border=True)
    m3.metric("Declined", sum(1 for u in mem_decided if u["website_status"] == "declined"), border=True)

    show_all = st.toggle("Show already-reviewed profiles too", key="mem_show_reviewed")
    mem_list = requested if show_all else mem_pending

    if not mem_list:
        st.caption("Nothing waiting on a member profile right now.")
    else:
        if storage is None:
            st.warning(
                "Can't load photos here — SUPABASE_SERVICE_KEY is missing on "
                "this server. Everything else on this page still works."
            )
        for u in mem_list:
            status = u.get("website_status") or "pending"
            with st.container(border=True, key=f"rkcard_review_user_{u['user_id']}"):
                pcol, dcol = st.columns([1, 4])
                if u.get("photo_path") and storage is not None:
                    try:
                        pcol.image(storage.storage.from_(BUCKET).download(u["photo_path"]), width=90)
                    except Exception as error:
                        pcol.caption(f"Photo unavailable ({error})")
                with dcol:
                    st.markdown(f"**{u['name']}**")
                    ig = st.text_input(
                        "Instagram", value=u.get("instagram") or "",
                        key=f"review_ig_{u['user_id']}", disabled=status != "pending",
                    )
                    li = st.text_input(
                        "LinkedIn", value=u.get("linkedin") or "",
                        key=f"review_li_{u['user_id']}", disabled=status != "pending",
                    )
                    gh = st.text_input(
                        "GitHub", value=u.get("github") or "",
                        key=f"review_gh_{u['user_id']}", disabled=status != "pending",
                    )

                if status == "pending":
                    note = st.text_input(
                        "Note (only needed if declining — shown back to them)",
                        key=f"review_note_user_{u['user_id']}",
                    )
                    bcol1, bcol2 = st.columns(2)
                    if bcol1.button(
                        "Approve", icon=":material/check_circle:", type="primary",
                        key=f"review_approve_user_{u['user_id']}",
                    ):
                        with safe_write("approve this profile for the website"):
                            client.table("users").update({
                                "instagram": ig.strip(),
                                "linkedin": li.strip(),
                                "github": gh.strip(),
                                "website_status": "approved",
                                "website_note": note.strip() or None,
                            }).eq("user_id", u["user_id"]).execute()
                            invalidate_cache()
                        st.session_state.website_page_message = f"Approved {u['name']}."
                        st.rerun()
                    if bcol2.button(
                        "Decline", icon=":material/cancel:",
                        key=f"review_decline_user_{u['user_id']}",
                    ):
                        with safe_write("decline this profile for the website"):
                            client.table("users").update({
                                "website_status": "declined",
                                "website_note": note.strip() or None,
                            }).eq("user_id", u["user_id"]).execute()
                            invalidate_cache()
                            if u.get("email"):
                                send_email(
                                    u["email"],
                                    "Your website photo wasn't approved",
                                    "A host didn't approve your photo/links for roboknights.in"
                                    + (f":\n\n{note.strip()}\n\n" if note.strip() else ".\n\n")
                                    + "You can fix it and tick the box again on Your Profile to "
                                    "ask for another review.",
                                )
                        st.session_state.website_page_message = f"Declined {u['name']}."
                        st.rerun()
                else:
                    badge = (
                        ":material/public: Approved — will appear on the site"
                        if status == "approved"
                        else ":material/public_off: Declined"
                    )
                    st.caption(badge + (f" — {u['website_note']}" if u.get("website_note") else ""))

    st.divider()
    st.markdown("##### :material/list_alt: All members")
    st.caption(
        "Every confirmed member, not just the ones who've ticked 'show my "
        "photo' — edit name, grade, standing, or social handles for anyone "
        "here in one place. Setting handles for someone who hasn't "
        "requested to be shown yet doesn't publish anything by itself; "
        "'Requested'/'Status' show whether it actually will. No delete "
        "here, and no way to add someone new — a member is always a real "
        "signed-up account (see the note above the review queue); the "
        "closest thing to 'add a member' is a hand-in sign-up link, not "
        "a host creating one from this page."
    )

    search2 = st.text_input(
        "Filter", placeholder="Name", key="public_mem_search", label_visibility="collapsed",
    )
    term2 = search2.strip().lower()
    # Everyone with an account, not just `requested` (photo_public) - this
    # is the full roster export_members.py builds data/members.ts from,
    # same criteria (confirmed = has a real users row, staff included).
    visible_mem = [u for u in all_users if not term2 or term2 in (u.get("name") or "").lower()]
    visible_mem.sort(key=lambda u: u.get("name") or "")

    if not visible_mem:
        st.caption("Nothing matches." if term2 else "No members yet.")
    else:
        mem_table_rows = [
            {
                "Name": u["name"],
                "Grade": u.get("grade"),
                "Standing": STANDING_LABELS.get(u.get("role"), u.get("role") or ""),
                "Requested": bool(u.get("photo_public")),
                "Status": u.get("website_status") or "pending",
                "Instagram": u.get("instagram") or "",
                "LinkedIn": u.get("linkedin") or "",
                "GitHub": u.get("github") or "",
            }
            for u in visible_mem
        ]
        edited_mem = st.data_editor(
            mem_table_rows,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Name": st.column_config.TextColumn("Name", width="medium"),
                "Grade": st.column_config.SelectboxColumn("Grade", options=GRADE_OPTIONS, width="small"),
                "Standing": st.column_config.SelectboxColumn(
                    "Standing", width="small",
                    options=["", "Core member", "Member", "Ad hoc"],
                ),
                "Requested": st.column_config.CheckboxColumn(
                    "Requested", width="small", disabled=True,
                    help="Whether they've ticked 'show my photo' on Your profile. "
                         "Set by the member, not editable here.",
                ),
                "Status": st.column_config.SelectboxColumn(
                    "Status", options=STATUS_OPTIONS, width="small",
                    help="Only matters once Requested is checked.",
                ),
                "Instagram": st.column_config.TextColumn("Instagram", width="medium"),
                "LinkedIn": st.column_config.TextColumn("LinkedIn", width="medium"),
                "GitHub": st.column_config.TextColumn("GitHub", width="medium"),
            },
            key="public_members_editor",
        )

        if st.button("Save changes", icon=":material/check:", type="primary", key="save_public_members"):
            changed = 0
            with safe_write("save these changes"):
                for original, edited in zip(visible_mem, edited_mem):
                    updates = {}
                    if edited["Name"].strip() != (original.get("name") or ""):
                        updates["name"] = edited["Name"].strip()
                    if edited["Grade"] != original.get("grade"):
                        updates["grade"] = edited["Grade"]
                    new_role = STANDING_VALUES.get(edited["Standing"], edited["Standing"]) or None
                    if new_role != original.get("role"):
                        updates["role"] = new_role
                    if edited["Status"] != (original.get("website_status") or "pending"):
                        updates["website_status"] = edited["Status"]
                    if edited["Instagram"].strip() != (original.get("instagram") or ""):
                        updates["instagram"] = edited["Instagram"].strip()
                    if edited["LinkedIn"].strip() != (original.get("linkedin") or ""):
                        updates["linkedin"] = edited["LinkedIn"].strip()
                    if edited["GitHub"].strip() != (original.get("github") or ""):
                        updates["github"] = edited["GitHub"].strip()
                    if updates:
                        client.table("users").update(updates).eq("user_id", original["user_id"]).execute()
                        changed += 1
                invalidate_cache()
            st.session_state.website_page_message = f"Saved {changed} change(s)." if changed else "Nothing changed."
            if changed:
                st.rerun()

# ================================================================= Alumni
elif section == "Alumni":
    def _public_alumni():
        try:
            return cached_table("public_alumni")
        except Exception as e:
            # PostgREST's actual wording for "this table doesn't exist"
            # is "Could not find the table ... in the schema cache"
            # (code PGRST205) - NOT "does not exist", which is what a
            # raw Postgres error says instead. Checking only the wrong
            # string here meant this fallback never fired and the whole
            # page crashed instead of showing the setup message, found
            # live 2026-09-22 the first time someone actually hit this
            # path (public_alumni, before its migration had been run).
            if "does not exist" in str(e) or "PGRST205" in str(e):
                return None
            raise

    def _format_socials(socials):
        # "type:url; type:url" - free-form rather than fixed
        # Instagram/LinkedIn/GitHub columns, because the real data has
        # seven different types (youtube, facebook, medium, behance too)
        # and a fixed set of columns would silently drop whichever ones
        # didn't get a column.
        return "; ".join(f'{s["type"]}:{s["url"]}' for s in (socials or []))

    def _parse_socials(text):
        out = []
        for part in text.split(";"):
            part = part.strip()
            if not part or ":" not in part:
                continue
            kind, url = part.split(":", 1)
            kind, url = kind.strip().lower(), url.strip()
            if kind and url:
                out.append({"type": kind, "url": url})
        return out

    st.subheader(":material/school: Alumni")
    st.caption(
        "Every past member on the Alumni page, grouped by batch — add, "
        "edit or remove anyone here. Nothing else in the dashboard managed "
        "this before; the file was hand-typed TypeScript with no host "
        "workflow at all."
    )

    if _public_alumni() is None:
        st.error(
            ":material/database_off: The `public_alumni` table doesn't exist yet. "
            "Run the migration at the end of `supabase_schema.sql` in Supabase's SQL "
            "editor, then run `python import_public_alumni.py` once to bring in the "
            "site's existing alumni, before using this section."
        )
        st.stop()

    alumni_rows = _public_alumni()
    batches_seen = sorted({r["batch"] for r in alumni_rows}, reverse=True) if alumni_rows else []
    presidents = sum(1 for r in alumni_rows if r.get("role") and "president" in r["role"].lower())

    m1, m2, m3 = st.columns(3)
    m1.metric("Alumni", len(alumni_rows), border=True)
    m2.metric("Presidents", presidents, border=True)
    m3.metric("Batches", len(batches_seen), border=True)

    st.markdown("##### :material/add_circle: Add an alumnus directly")
    with st.form("add_public_alumnus", clear_on_submit=True):
        add_name = st.text_input("Name")
        ac1, ac2 = st.columns(2)
        add_batch = ac1.text_input("Batch", placeholder="2025-26")
        add_role = ac2.text_input("Role", placeholder="President / Core Member / Member / ...")
        add_src = st.text_input(
            "Photo path or URL (optional)",
            placeholder="/images/members/Name.jpg or a full https:// URL",
        )
        add_socials = st.text_input(
            "Socials (optional)",
            placeholder="instagram:https://instagram.com/x; linkedin:https://linkedin.com/in/y",
            help='Semicolon-separated "type:url" pairs. Any type works - '
                 "instagram, linkedin, github, youtube, facebook, medium, behance "
                 "are the ones already in use.",
        )
        if st.form_submit_button("Add", icon=":material/add:", type="primary"):
            if not add_name.strip() or not add_batch.strip():
                st.error("Name and batch are required.")
            else:
                with safe_write("add this alumnus"):
                    client.table("public_alumni").insert({
                        "batch": add_batch.strip(),
                        "name": add_name.strip(),
                        "role": add_role.strip() or None,
                        "src": add_src.strip() or None,
                        "socials": _parse_socials(add_socials),
                    }).execute()
                    invalidate_cache()
                st.session_state.website_page_message = f"Added {add_name.strip()}."
                st.rerun()

    st.divider()
    st.markdown("##### :material/list_alt: All alumni")
    st.caption(
        f"{len(alumni_rows)} alumnus/alumni — this is the full, editable list "
        "data/alumni.ts on the website gets regenerated from. Edit cells "
        "directly and Save, or delete a row below the table."
    )

    search3 = st.text_input(
        "Filter", placeholder="Name or batch", key="public_alum_search", label_visibility="collapsed",
    )
    term3 = search3.strip().lower()
    visible_alum = [
        r for r in alumni_rows
        if not term3
        or term3 in (r.get("name") or "").lower()
        or term3 in (r.get("batch") or "").lower()
    ] if alumni_rows else []
    visible_alum.sort(key=lambda r: (r.get("batch") or "", r.get("name") or ""), reverse=True)

    if not visible_alum:
        st.caption("Nothing matches." if term3 else "Nothing here yet.")
    else:
        alum_table_rows = [
            {
                "Batch": r.get("batch") or "",
                "Name": r.get("name") or "",
                "Role": r.get("role") or "",
                "Photo": r.get("src") or "",
                "Socials": _format_socials(r.get("socials")),
            }
            for r in visible_alum
        ]
        edited_alum = st.data_editor(
            alum_table_rows,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Batch": st.column_config.TextColumn("Batch", width="small"),
                "Name": st.column_config.TextColumn("Name", width="medium"),
                "Role": st.column_config.TextColumn("Role", width="medium"),
                "Photo": st.column_config.TextColumn(
                    "Photo", width="medium", help="A /images/... path already in the "
                    "website repo, or a full https:// URL.",
                ),
                "Socials": st.column_config.TextColumn(
                    "Socials", width="large", help='Semicolon-separated "type:url" pairs.',
                ),
            },
            key="public_alumni_editor",
        )

        if st.button("Save changes", icon=":material/check:", type="primary", key="save_public_alumni"):
            changed = 0
            with safe_write("save these changes"):
                for original, edited in zip(visible_alum, edited_alum):
                    updates = {}
                    if edited["Batch"].strip() != (original.get("batch") or ""):
                        updates["batch"] = edited["Batch"].strip()
                    if edited["Name"].strip() != (original.get("name") or ""):
                        updates["name"] = edited["Name"].strip()
                    if edited["Role"].strip() != (original.get("role") or ""):
                        updates["role"] = edited["Role"].strip() or None
                    if edited["Photo"].strip() != (original.get("src") or ""):
                        updates["src"] = edited["Photo"].strip() or None
                    new_socials = _parse_socials(edited["Socials"])
                    if new_socials != (original.get("socials") or []):
                        updates["socials"] = new_socials
                    if updates:
                        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
                        client.table("public_alumni").update(updates).eq(
                            "public_alumni_id", original["public_alumni_id"]
                        ).execute()
                        changed += 1
                invalidate_cache()
            st.session_state.website_page_message = f"Saved {changed} change(s)." if changed else "Nothing changed."
            if changed:
                st.rerun()

        with st.expander(":material/delete: Delete an alumnus"):
            del_options = {
                f"{r['name']} · {r['batch']}": r["public_alumni_id"] for r in visible_alum
            }
            to_delete = st.selectbox(
                "Which one", list(del_options.keys()), index=None,
                placeholder="Choose someone to delete", key="public_alum_delete_pick",
            )
            if to_delete and st.button(
                "Delete permanently", icon=":material/delete_forever:", key="public_alum_delete_confirm",
            ):
                with safe_write("delete this alumnus"):
                    client.table("public_alumni").delete().eq(
                        "public_alumni_id", del_options[to_delete]
                    ).execute()
                    invalidate_cache()
                st.session_state.website_page_message = "Deleted."
                st.rerun()

else:
    st.info("Pick a section above.")

# Chunk 1 of the Competitions feature: a host-only form to add a competition
# (with its links and events), and a read-only "browse everything" view for
# every logged-in member. Chunk 3 adds students volunteering for events
# they're eligible for by grade, and hosts can now edit an existing
# competition (details, links, and events) the same way they create one.
# Chunk 4 lets the host finalize which volunteers are actually selected for
# an event, capped at team_size * max_teams, and emails newly-selected
# people. Day-before reminders and bot status live in send_due_reminders.py
# instead, since those need to run on a schedule, not a page view.
# Announcements moved to their own page (app_pages/announcements.py).

from datetime import date, datetime

import streamlit as st

from e2c_import import scan_e2c_sheet
from shared import (
    EXUN_EMAILS, HOST_EMAILS, IST, cached_table, get_client, invalidate_cache, safe_write,
    send_discord_message, send_email,
)

# The page-local name everything below already uses — the implementation
# moved to shared.py so Inventory and the other pages get the same
# crash-proofing without a second copy to keep in sync.
_safe_write = safe_write

client = get_client()
is_host = st.session_state.is_host
is_exun = st.session_state.is_exun
current_user_id = st.session_state.current_user_id
current_user_grade = st.session_state.current_user_grade
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

GRADES = [6, 7, 8, 9, 10, 11, 12]  # 6 included since E2C-imported events can genuinely be 6th-grade eligible
BLANK_LINK = {"label": "", "url": ""}
BLANK_EVENT = {"event_id": None, "name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}


def _validate_competition_form(name, comp_date, events):
    # Shared by the create form, the edit form, and the E2C-import form —
    # all three were repeating the same four checks separately, which made
    # it easy to update one and forget another.
    errors = []
    if not name.strip():
        errors.append("Competition name is required.")
    if not comp_date:
        errors.append("Competition date is required.")
    if not events:
        errors.append("At least one event is required.")
    for e in events:
        if e["min_grade"] > e["max_grade"]:
            errors.append(f"Event '{e['name']}': min grade can't be higher than max grade.")
    return errors


st.title("Competitions")

# --- Host-only: add a competition ------------------------------------------
# Links and events are variable-length lists, so they're kept in
# session_state as plain lists of dicts — "Add another" appends a blank
# entry and reruns, same rerun-to-update-the-UI pattern used everywhere
# else in this app (e.g. the Approve two-step confirm on the Inventory page).

if "new_links" not in st.session_state:
    st.session_state.new_links = [dict(BLANK_LINK)]
if "new_events" not in st.session_state:
    st.session_state.new_events = [dict(BLANK_EVENT)]
if "competition_message" not in st.session_state:
    st.session_state.competition_message = None
if "editing_competition_id" not in st.session_state:
    st.session_state.editing_competition_id = None
if "deleting_competition_id" not in st.session_state:
    st.session_state.deleting_competition_id = None
if "confirming_date_change_id" not in st.session_state:
    st.session_state.confirming_date_change_id = None
if "pending_date_change" not in st.session_state:
    st.session_state.pending_date_change = None
if "confirming_e2c_single_sync_idx" not in st.session_state:
    st.session_state.confirming_e2c_single_sync_idx = None
if "pending_e2c_single_sync" not in st.session_state:
    st.session_state.pending_e2c_single_sync = None
if "confirming_e2c_bulk_sync" not in st.session_state:
    st.session_state.confirming_e2c_bulk_sync = False
if "pending_e2c_bulk_sync" not in st.session_state:
    st.session_state.pending_e2c_bulk_sync = None


def _notify_date_change(name, old_date, new_date):
    # Shared by every path that can change a competition's date: the Edit
    # form's Accept button, and now the E2C single/bulk sync Accept
    # buttons too — same audience, same wording, wherever the change
    # actually came from.
    old_str = old_date.strftime("%d %b %Y") if old_date else "no date set"
    new_str = new_date.strftime("%d %b %Y")
    all_emails = [
        email for email in user_email_by_id.values()
        if email and email not in EXUN_EMAILS
    ]
    for email in all_emails:
        send_email(
            email,
            f"Date change: {name.strip()}",
            f"The date for {name.strip()} has changed from {old_str} to {new_str}.\n\n"
            f"Log in to the app for full details.",
        )


def _notify_new_event(comp_name, event_name, min_grade, max_grade):
    # Registered members only — an E2C "ad-hoc" (an unregistered guest
    # named on the sheet) has no account or email in this system, so
    # there's nothing to notify them at. Exun is excluded too: they can
    # view every event but can never volunteer for one (see the
    # is_eligible check further down this file), so "you're eligible for
    # this" doesn't apply to them.
    eligible_users = [
        u for u in cached_table("users")
        if u.get("grade") is not None and min_grade <= u["grade"] <= max_grade
        and u["email"] not in EXUN_EMAILS
    ]
    for u in eligible_users:
        send_email(
            u["email"],
            f"New event you're eligible for: {event_name}",
            f"A new event, {event_name}, was just added to {comp_name} — open to your grade.\n\n"
            f"Log in to the app to volunteer.",
        )

    # Discord: same eligible-by-grade audience as the email above, tagged
    # individually via each member's own linked discord_user_id (self-
    # linked on Home, or host-fixed on Members) — anyone who hasn't
    # linked one yet just isn't tagged; the announcement still posts
    # either way. No-ops entirely if the webhook isn't configured.
    tags = " ".join(f"<@{u['discord_user_id']}>" for u in eligible_users if u.get("discord_user_id"))
    message = (
        f":loudspeaker: **New competition added: {comp_name}**\n"
        f"New event: **{event_name}** — open to Grade {min_grade}–{max_grade}."
    )
    if tags:
        message += f"\n{tags}"
    send_discord_message(message)


def render_add_competition():
    with st.container(border=True):
        st.subheader(":material/add_box: Add a competition")
        name = st.text_input("Competition name", key="new_comp_name")
        venue = st.text_input("Venue", key="new_comp_venue")
        comp_date = st.date_input("Competition date", key="new_comp_date", value=None)
        reg_deadline = st.date_input("Registration deadline", key="new_comp_reg_deadline", value=None)
        student_incharge = st.text_input("Student in-charge", key="new_comp_incharge")

        st.markdown("**Links** (brochure, registration form, etc.)")
        for i, link in enumerate(st.session_state.new_links):
            lcol1, lcol2, lcol3 = st.columns([2, 4, 1], vertical_alignment="bottom")
            link["label"] = lcol1.text_input(
                "Label", value=link["label"], key=f"link_label_{i}",
                label_visibility="collapsed", placeholder="Label (e.g. Brochure)",
            )
            link["url"] = lcol2.text_input(
                "URL", value=link["url"], key=f"link_url_{i}",
                label_visibility="collapsed", placeholder="https://...",
            )
            if len(st.session_state.new_links) > 1:
                if lcol3.button("Remove", key=f"remove_link_{i}", icon=":material/close:"):
                    st.session_state.new_links.pop(i)
                    st.rerun()
        if st.button("Add another link", key="add_link", icon=":material/add:"):
            st.session_state.new_links.append(dict(BLANK_LINK))
            st.rerun()

        st.markdown("**Events** (e.g. Robosoccer, Roborace)")
        for i, event in enumerate(st.session_state.new_events):
            with st.container(border=True):
                event["name"] = st.text_input("Event name", value=event["name"], key=f"event_name_{i}")
                event["details"] = st.text_area(
                    "Details / rules", value=event["details"], key=f"event_details_{i}"
                )
                ecol1, ecol2, ecol3, ecol4 = st.columns(4)
                event["team_size"] = ecol1.number_input(
                    "Team size", min_value=1, value=event["team_size"], key=f"event_team_size_{i}"
                )
                event["max_teams"] = ecol2.number_input(
                    "Max teams", min_value=1, value=event["max_teams"], key=f"event_max_teams_{i}"
                )
                event["min_grade"] = ecol3.selectbox(
                    "Min grade", GRADES, index=GRADES.index(event["min_grade"]), key=f"event_min_grade_{i}"
                )
                event["max_grade"] = ecol4.selectbox(
                    "Max grade", GRADES, index=GRADES.index(event["max_grade"]), key=f"event_max_grade_{i}"
                )
                if len(st.session_state.new_events) > 1:
                    if st.button("Remove event", key=f"remove_event_{i}", icon=":material/close:"):
                        st.session_state.new_events.pop(i)
                        st.rerun()
        if st.button("Add another event", key="add_event", icon=":material/add:"):
            st.session_state.new_events.append(dict(BLANK_EVENT))
            st.rerun()

        if st.button("Create competition", type="primary", icon=":material/check:"):
            valid_events = [e for e in st.session_state.new_events if e["name"].strip()]
            errors = _validate_competition_form(name, comp_date, valid_events)

            if errors:
                st.session_state.competition_message = ("error", " ".join(errors))
                st.rerun()
            else:
                with _safe_write("create the competition"):
                    comp_result = client.table("competitions").insert({
                        "name": name.strip(),
                        "venue": venue.strip(),
                        "competition_date": comp_date.isoformat(),
                        "registration_deadline": reg_deadline.isoformat() if reg_deadline else None,
                        "student_incharge": student_incharge.strip(),
                    }).execute()
                    competition_id = comp_result.data[0]["competition_id"]

                    valid_links = [l for l in st.session_state.new_links if l["url"].strip()]
                    if valid_links:
                        client.table("competition_links").insert([
                            {
                                "competition_id": competition_id,
                                "label": l["label"].strip() or "Link",
                                # Add https:// if they typed a bare domain, so the
                                # stored URL is always clickable as-is.
                                "url": l["url"].strip() if l["url"].strip().startswith(("http://", "https://"))
                                else "https://" + l["url"].strip(),
                            }
                            for l in valid_links
                        ]).execute()

                    client.table("competition_events").insert([
                        {
                            "competition_id": competition_id,
                            "name": e["name"].strip(),
                            "details": e["details"].strip(),
                            "team_size": e["team_size"],
                            "max_teams": e["max_teams"],
                            "min_grade": e["min_grade"],
                            "max_grade": e["max_grade"],
                        }
                        for e in valid_events
                    ]).execute()
                    invalidate_cache()

                    for e in valid_events:
                        _notify_new_event(name.strip(), e["name"].strip(), e["min_grade"], e["max_grade"])

                    st.session_state.competition_message = ("success", f"Added {name.strip()}.")
                    # Reset the form's lists back to one blank row each.
                    st.session_state.new_links = [dict(BLANK_LINK)]
                    st.session_state.new_events = [dict(BLANK_EVENT)]
                    st.rerun()

# --- Host-only: scan the E2C sheet for robotics competitions ---------------
# Re-scanning always re-reads the live sheet fresh, so a new/unimported
# competition or event just shows up again next time — there's no "you
# already saw this and skipped it" memory to fight with. Already-imported
# competitions instead get a Update button (re-sync their top-level fields
# from the sheet) and a per-event "add this one" option for any event the
# sheet has that we don't, so a later addition on the sheet doesn't require
# re-importing everything about that competition.


def _sync_competition_from_scan(client, comp):
    # Only overwrites a field when the sheet actually gave us something —
    # a field we couldn't parse (blank venue, unparseable date) leaves
    # whatever's already stored alone, rather than blanking it out.
    payload = {}
    if comp["venue"]:
        payload["venue"] = comp["venue"]
    if comp["date_parsed"]:
        payload["competition_date"] = comp["date_parsed"].isoformat()
    if comp["deadline_parsed"]:
        payload["registration_deadline"] = comp["deadline_parsed"].isoformat()
    if comp["student_incharge"]:
        payload["student_incharge"] = comp["student_incharge"]
    if comp.get("mode"):
        payload["mode"] = comp["mode"]
    if comp.get("priority_label"):
        payload["priority_label"] = comp["priority_label"]
    if payload:
        client.table("competitions").update(payload).eq("competition_id", comp["existing_id"]).execute()

    if comp["links"]:
        client.table("competition_links").delete().eq("competition_id", comp["existing_id"]).execute()
        client.table("competition_links").insert([
            {"competition_id": comp["existing_id"], "label": l["label"], "url": l["url"]}
            for l in comp["links"]
        ]).execute()

    if payload or comp["links"]:
        invalidate_cache()


def _stored_competition_date(existing_id):
    # The sheet-scan dict only knows the SHEET's date, not what's currently
    # in our own database — needed to tell whether syncing would actually
    # change the date, same comparison the Edit form's Save does.
    stored = next(
        (c for c in cached_table("competitions") if c["competition_id"] == existing_id), None
    )
    return date.fromisoformat(stored["competition_date"]) if stored and stored.get("competition_date") else None


def _apply_e2c_single_sync(fresh_comp, accept_date):
    # accept_date=False re-syncs everything (venue, deadline, in-charge,
    # links, rosters) EXCEPT the date — done by stripping date_parsed
    # before handing off to _sync_competition_from_scan, which already
    # skips any field it wasn't given.
    old_date = _stored_competition_date(fresh_comp.get("existing_id"))
    new_date = fresh_comp.get("date_parsed")
    date_changed = bool(new_date and new_date != old_date)
    sync_comp = fresh_comp if accept_date else {**fresh_comp, "date_parsed": None}

    with _safe_write(f"update {fresh_comp['name']}"):
        _sync_competition_from_scan(client, sync_comp)
        if date_changed and accept_date:
            _notify_date_change(fresh_comp["name"], old_date, new_date)
        for e in fresh_comp["events"]:
            if e.get("existing_event_id"):
                _insert_matched_participants(
                    client, e.get("existing_event_id"), e.get("teams", []), e["name"], fresh_comp["name"],
                )
        st.session_state.confirming_e2c_single_sync_idx = None
        st.session_state.pending_e2c_single_sync = None
        st.toast(f"Updated {fresh_comp['name']}.", icon=":material/check_circle:")
        st.rerun()


def _apply_e2c_bulk_sync(fresh_by_name, already_imported, accept_dates):
    # Same accept/reject-the-date shape as the single-competition sync,
    # applied across the whole batch: every competition's non-date fields
    # always sync, but its date only changes if accept_dates is True AND
    # the sheet actually disagrees with what's stored.
    with _safe_write("sync the already-imported competitions"):
        for comp in already_imported:
            fresh_comp = fresh_by_name.get(comp["name"].strip().lower(), comp)
            old_date = _stored_competition_date(comp["existing_id"])
            new_date = fresh_comp.get("date_parsed")
            date_changed = bool(new_date and new_date != old_date)
            sync_comp = fresh_comp if (accept_dates or not date_changed) else {**fresh_comp, "date_parsed": None}

            _sync_competition_from_scan(client, sync_comp)
            if date_changed and accept_dates:
                _notify_date_change(fresh_comp["name"], old_date, new_date)
            for e in fresh_comp["events"]:
                if e.get("existing_event_id"):
                    _insert_matched_participants(
                        client, e.get("existing_event_id"), e.get("teams", []), e["name"], fresh_comp["name"],
                    )
        st.session_state.confirming_e2c_bulk_sync = False
        st.session_state.pending_e2c_bulk_sync = None
        st.toast(f"Synced {len(already_imported)} competition(s).", icon=":material/check_circle:")
        st.rerun()


def _insert_matched_participants(client, event_id, teams, event_name, comp_name):
    # teams: list of team-rows, each {"team_no", "participants": [{"user_id",
    # "name", "selected"}]} matched against real members off the sheet —
    # "selected" reflects that specific cell's green/white color.
    #
    # Explicit rule from the student: sync only ever touches the SELECTED
    # flag, never whether someone is a volunteer at all.
    #   - A white (unconfirmed) cell never drives any change by itself —
    #     it doesn't add a new volunteer and doesn't touch an existing one.
    #   - A green cell adds a brand-new volunteer as selected, or promotes
    #     an existing (non-selected) volunteer to selected.
    #   - Anyone currently selected=True whose name ISN'T in a green cell
    #     this sync gets downgraded to selected=False — regardless of
    #     whether they were originally selected via a previous sheet sync
    #     OR finalized by a host manually. E2C's green cells are the one
    #     source of truth for who's currently selected.
    #   - The event_volunteers ROW itself is never deleted by sync, for
    #     anyone, sheet-sourced or self-volunteered — downgrading just
    #     leaves them as a plain (unselected) volunteer.
    # Anyone newly added or promoted to selected gets the same "You're
    # selected" email the manual finalize flow sends.
    #
    # This read is deliberately NOT cached_table: it decides insert-vs-
    # promote-vs-downgrade, so it needs the true current state, not up to
    # 8s old (this function is also called repeatedly in a loop across
    # many events in one sync, where stale data would risk a duplicate).
    existing = {
        v["user_id"]: v
        for v in client.table("event_volunteers")
        .select("user_id, selected, synced_from_sheet, team_no")
        .eq("event_id", event_id)
        .execute()
        .data
    }
    changed = False
    green_user_ids = set()

    for team in teams:
        team_no = team["team_no"]
        for p in team["participants"]:
            if not p["selected"]:
                continue  # a white/unconfirmed cell never drives a change
            green_user_ids.add(p["user_id"])
            if p["user_id"] in existing:
                row = existing[p["user_id"]]
                updates = {}
                if not row.get("synced_from_sheet"):
                    updates["synced_from_sheet"] = True
                if not row["selected"]:
                    updates["selected"] = True
                if row.get("team_no") != team_no:
                    # The sheet's own team groupings are the source of truth
                    # for who's currently on which team together — keep this
                    # in sync the same way "selected" is, so the Teams
                    # display doesn't go stale when the sheet reshuffles who's
                    # grouped with who.
                    updates["team_no"] = team_no
                if updates:
                    client.table("event_volunteers").update(updates).eq(
                        "event_id", event_id
                    ).eq("user_id", p["user_id"]).execute()
                    changed = True
                    if updates.get("selected"):
                        _send_selected_email(p["user_id"], event_name, comp_name)
                continue
            client.table("event_volunteers").insert({
                "event_id": event_id, "user_id": p["user_id"],
                "team_no": team_no, "selected": True,
                "synced_from_sheet": True,
            }).execute()
            existing[p["user_id"]] = {"selected": True, "synced_from_sheet": True, "team_no": team_no}
            changed = True
            _send_selected_email(p["user_id"], event_name, comp_name)

    downgrade_ids = [
        uid for uid, row in existing.items()
        if row.get("selected") and uid not in green_user_ids
    ]
    if downgrade_ids:
        # Clear team_no too, not just selected — a name no longer in a green
        # cell isn't on that team anymore per this sync. It still stays a
        # volunteer (never deleted), just falls back to the plain
        # Selected/Volunteers list instead of appearing grouped under a team
        # it's no longer actually part of.
        client.table("event_volunteers").update({"selected": False, "team_no": None}).eq(
            "event_id", event_id
        ).in_("user_id", downgrade_ids).execute()
        changed = True
        for uid in downgrade_ids:
            _send_unselected_email(uid, event_name, comp_name)

    if changed:
        invalidate_cache()


def _send_selected_email(user_id, event_name, comp_name):
    send_email(
        user_email_by_id.get(user_id),
        f"You're selected: {event_name} at {comp_name}",
        f"You've been selected to represent RoboKnights in "
        f"{event_name} at {comp_name}.\n\n"
        f"Log in to the app for full details.",
    )


def _send_unselected_email(user_id, event_name, comp_name):
    # Sync can now un-select someone who's not (or no longer) in a green
    # cell on the real sheet — without this, they'd only find out by
    # happening to check the app again themselves. They're still a
    # volunteer for this event, just not currently finalized — sync
    # never removes anyone from the volunteer list itself.
    send_email(
        user_email_by_id.get(user_id),
        f"Team change: {event_name} at {comp_name}",
        f"You're no longer marked as selected for {event_name} at {comp_name} — "
        f"the team roster on the registration sheet has changed. You're still "
        f"listed as a volunteer. If this looks wrong, check with your student "
        f"in-charge or a host.",
    )


def _teams_caption(teams):
    # Small visibility line so the host sees who's about to be added/synced
    # before actually clicking a write button.
    people = [p for team in teams for p in team["participants"]]
    if not people:
        return None
    parts = [f"{p['name']} ({'selected' if p['selected'] else 'pending'})" for p in people]
    return ":material/group: Registered member(s) found: " + ", ".join(parts)


def _render_e2c_scan_skeleton():
    # The one place in this app doing a live external fetch (Google
    # Sheets, not a cached table) right before filling a specific-shaped
    # area with an unknown number of competition cards — a real "content
    # is coming" wait, unlike everywhere else in this app where reads are
    # already near-instant off the 8s cache. Shaped/styled like the real
    # result cards below (bordered container, gold shimmer) rather than a
    # generic grey shimmer-library look.
    st.caption(":material/travel_explore: Scanning the E2C sheet…")
    for i in range(3):
        with st.container(border=True, key=f"rkskeleton_e2c_{i}"):
            st.html(
                '<div class="rk-skel-bar rk-skel-title"></div>'
                '<div class="rk-skel-bar rk-skel-wide"></div>'
                '<div class="rk-skel-bar rk-skel-narrow"></div>'
            )


def render_e2c_import():
    with st.container(border=True):
        st.subheader(":material/travel_explore: Import from E2C sheet")
        st.caption("Reads the club's E2C sheet directly — no link to paste.")
        if st.button("Scan for robotics competitions", icon=":material/search:"):
            skeleton = st.empty()
            with skeleton.container():
                _render_e2c_scan_skeleton()
            try:
                st.session_state.e2c_scan_results = scan_e2c_sheet(client)
                st.session_state.e2c_scan_error = None
            except Exception as e:
                st.session_state.e2c_scan_results = None
                st.session_state.e2c_scan_error = str(e)
            skeleton.empty()

        if st.session_state.get("e2c_scan_error"):
            st.error(f"Couldn't read the E2C sheet: {st.session_state.e2c_scan_error}")

        results = st.session_state.get("e2c_scan_results")
        if results is not None:
            if not results:
                st.caption("No robotics competitions found in the current year's tab.")

            # Events the host has explicitly said "no" to before — filtered
            # out of every suggestion list below so a rejected event stops
            # coming back on every future scan. Name-keyed (not id-keyed),
            # see the schema comment for why.
            rejected_pairs = {
                (r["competition_name"].strip().lower(), r["event_name"].strip().lower())
                for r in cached_table("e2c_rejected_events")
            }

            def _reject_event_button(comp_name, event_name, key):
                if st.button(
                    "Reject", key=key, icon=":material/block:",
                    help="Never suggest this event again for this competition",
                ):
                    with _safe_write("reject this event"):
                        client.table("e2c_rejected_events").upsert(
                            {"competition_name": comp_name.strip(), "event_name": event_name.strip()},
                            on_conflict="competition_name,event_name",
                        ).execute()
                        invalidate_cache()
                        st.toast(f"Won't suggest \"{event_name}\" for {comp_name} again.", icon=":material/check_circle:")
                        st.rerun()

            already_imported = [c for c in results if c["already_imported"]]
            if already_imported:
                if st.session_state.confirming_e2c_bulk_sync:
                    pending = st.session_state.pending_e2c_bulk_sync
                    change_lines = "\n".join(
                        f"- **{c['name']}**: "
                        f"{c['old_date'].strftime('%d %b %Y') if c['old_date'] else 'no date set'} → "
                        f"{c['new_date'].strftime('%d %b %Y')}"
                        for c in pending["changes"]
                    )
                    st.warning(
                        f"The sheet has a different date for {len(pending['changes'])} "
                        f"competition(s):\n\n{change_lines}\n\nAccepting updates these dates and "
                        f"emails every member. Rejecting still syncs venue/links/rosters for "
                        f"everything, but keeps the original dates."
                    )
                    accept_col, reject_col = st.columns([1, 1])
                    if accept_col.button(
                        "Accept all date changes", key="e2c_accept_bulk_dates",
                        icon=":material/check:", type="primary",
                    ):
                        _apply_e2c_bulk_sync(pending["fresh_by_name"], pending["already_imported"], accept_dates=True)
                    if reject_col.button(
                        "Reject date changes", key="e2c_reject_bulk_dates", icon=":material/close:"
                    ):
                        _apply_e2c_bulk_sync(pending["fresh_by_name"], pending["already_imported"], accept_dates=False)
                elif st.button(
                    f"Sync all {len(already_imported)} already-imported competitions",
                    key="e2c_update_all", icon=":material/sync:",
                    help="Re-reads the sheet fresh, so anyone who signed up since your last Scan is included",
                ):
                    try:
                        with st.spinner("Re-reading the E2C sheet…"):
                            # Re-scan fresh rather than reusing the cached results —
                            # cached team data only remembers names that matched a
                            # real member AT SCAN TIME, so replaying it can never
                            # pick up someone who signed up since.
                            fresh_by_name = {c["name"].strip().lower(): c for c in scan_e2c_sheet(client)}
                    except Exception as e:
                        st.error(f"Couldn't re-read the E2C sheet: {e}")
                        fresh_by_name = {}
                    if fresh_by_name:
                        changes = []
                        for comp in already_imported:
                            fresh_comp = fresh_by_name.get(comp["name"].strip().lower(), comp)
                            old_date = _stored_competition_date(comp["existing_id"])
                            new_date = fresh_comp.get("date_parsed")
                            if new_date and new_date != old_date:
                                changes.append({"name": fresh_comp["name"], "old_date": old_date, "new_date": new_date})
                        if changes:
                            st.session_state.pending_e2c_bulk_sync = {
                                "fresh_by_name": fresh_by_name, "already_imported": already_imported,
                                "changes": changes,
                            }
                            st.session_state.confirming_e2c_bulk_sync = True
                            st.rerun()
                        else:
                            _apply_e2c_bulk_sync(fresh_by_name, already_imported, accept_dates=True)

            pending_new = []  # collects each new competition's current widget values

            for idx, comp in enumerate(results):
                if comp["already_imported"]:
                    continue  # shown separately below, in the collapsed "Already imported" section
                with st.container(border=True):
                    # --- New competition: editable fields + per-event checkboxes ---
                    st.markdown(f"**{comp['name']}**")
                    venue = st.text_input("Venue", value=comp["venue"], key=f"e2c_venue_{idx}")
                    comp_date = st.date_input(
                        "Competition date", value=comp["date_parsed"], key=f"e2c_date_{idx}"
                    )
                    deadline = st.date_input(
                        "Registration deadline", value=comp["deadline_parsed"], key=f"e2c_deadline_{idx}"
                    )
                    incharge = st.text_input(
                        "Student in-charge", value=comp["student_incharge"], key=f"e2c_incharge_{idx}"
                    )
                    if not comp["date_parsed"]:
                        st.caption(f":material/warning: Couldn't read a clear date from `{comp['date_text']}` — pick one above.")
                    if comp["links"]:
                        st.caption(
                            "Links: " + "  •  ".join(f"[{l['label']}]({l['url']})" for l in comp["links"])
                        )

                    st.markdown("**Robotics events to import:**")

                    # Rejected ones are filtered out of the suggested list
                    # entirely, not just left unchecked — that's the whole
                    # point of Reject vs. just unticking Include.
                    visible_candidate_events = [
                        e for e in comp["events"]
                        if (comp["name"].strip().lower(), e["name"].strip().lower()) not in rejected_pairs
                    ]

                    # Lets the host pull in a specific event that wasn't
                    # auto-detected as robotics (e.g. a borderline AI/IoT
                    # one) by typing its exact name from the sheet.
                    extra_key = f"e2c_extra_events_{idx}"
                    if extra_key not in st.session_state:
                        st.session_state[extra_key] = []
                    add_col1, add_col2 = st.columns([4, 1])
                    add_name = add_col1.text_input(
                        "Add another event by name", key=f"e2c_addname_{idx}",
                        placeholder="Add another event by its exact name (e.g. Vision 2047)",
                        label_visibility="collapsed",
                    )
                    if add_col2.button("Add", key=f"e2c_addname_btn_{idx}", icon=":material/add:") and add_name.strip():
                        shown_names = {
                            e["name"].strip().lower() for e in visible_candidate_events + st.session_state[extra_key]
                        }
                        match = next(
                            (e for e in comp.get("all_events", []) if e["name"].strip().lower() == add_name.strip().lower()),
                            None,
                        )
                        if not match:
                            st.session_state[f"e2c_addname_msg_{idx}"] = (
                                "error", f"No event named \"{add_name}\" found in {comp['name']}."
                            )
                        elif match["name"].strip().lower() in shown_names:
                            st.session_state[f"e2c_addname_msg_{idx}"] = (
                                "info", f"\"{match['name']}\" is already in the list."
                            )
                        else:
                            st.session_state[extra_key].append(match)
                            st.session_state[f"e2c_addname_msg_{idx}"] = ("success", f"Added \"{match['name']}\".")
                        st.rerun()
                    addname_msg = st.session_state.pop(f"e2c_addname_msg_{idx}", None)
                    if addname_msg:
                        kind, text = addname_msg
                        st.caption(f"{':material/error:' if kind == 'error' else ':material/info:'} {text}")

                    event_widgets = []
                    for eidx, e in enumerate(visible_candidate_events + st.session_state[extra_key]):
                        with st.container(border=True):
                            ecol1, ecol2, ecol3 = st.columns([4, 1, 1])
                            ecol1.markdown(
                                f"**{e['name']}**"
                                + ("  :material/warning: check team size/max teams" if e["flagged"] else "")
                                + ("  :material/help: not auto-detected as robotics — RoboKnights members "
                                   "are already on the roster, confirm before including"
                                   if e.get("needs_review") else "")
                            )
                            include = ecol2.checkbox(
                                "Include", value=not e.get("needs_review"), key=f"e2c_incl_{idx}_{eidx}",
                            )
                            with ecol3:
                                _reject_event_button(comp["name"], e["name"], f"e2c_reject_{idx}_{eidx}")
                            c1, c2, c3, c4 = st.columns(4)
                            team_size = c1.number_input(
                                "Team size", min_value=1, value=e["team_size"], key=f"e2c_ts_{idx}_{eidx}",
                            )
                            max_teams = c2.number_input(
                                "Max teams", min_value=1, value=e["max_teams"], key=f"e2c_mt_{idx}_{eidx}",
                            )
                            min_grade = c3.selectbox(
                                "Min grade", GRADES, index=GRADES.index(e["min_grade"]),
                                key=f"e2c_ming_{idx}_{eidx}",
                            )
                            max_grade = c4.selectbox(
                                "Max grade", GRADES, index=GRADES.index(e["max_grade"]),
                                key=f"e2c_maxg_{idx}_{eidx}",
                            )
                            teams_caption = _teams_caption(e.get("teams", []))
                            if teams_caption:
                                st.caption(teams_caption)
                            event_widgets.append({
                                "include": include, "name": e["name"], "details": e["details"],
                                "team_size": team_size, "max_teams": max_teams,
                                "min_grade": min_grade, "max_grade": max_grade,
                                "teams": e.get("teams", []),
                            })

                    pending_new.append({
                        "name": comp["name"], "venue": venue, "competition_date": comp_date,
                        "registration_deadline": deadline, "student_incharge": incharge,
                        "mode": comp.get("mode", ""), "priority_label": comp.get("priority_label", ""),
                        "links": comp["links"], "events": event_widgets,
                    })

            if pending_new:
                if st.button(
                    "Import selected", type="primary", key="e2c_import_btn", icon=":material/download:",
                    help="Re-reads the sheet fresh so anyone newly signed up is included",
                ):
                    # One fresh re-scan for the whole import, not the cached
                    # data — someone could've signed up since the last Scan.
                    try:
                        with st.spinner("Re-reading the E2C sheet…"):
                            fresh_all_events_by_comp = {
                                c["name"].strip().lower(): c.get("all_events", []) for c in scan_e2c_sheet(client)
                            }
                    except Exception as ex:
                        st.error(f"Couldn't re-read the E2C sheet: {ex}")
                        fresh_all_events_by_comp = {}

                    errors = []
                    imported = 0
                    with _safe_write("import the selected competitions"):
                        for data in pending_new:
                            valid_events = [e for e in data["events"] if e["include"]]
                            if not valid_events:
                                continue  # nothing checked for this competition — skip quietly
                            if not data["competition_date"]:
                                errors.append(f"{data['name']}: competition date is required.")
                                continue
                            for e in valid_events:
                                if e["min_grade"] > e["max_grade"]:
                                    errors.append(f"{data['name']} / {e['name']}: min grade can't be higher than max grade.")
                            if errors:
                                continue

                            comp_result = client.table("competitions").insert({
                                "name": data["name"],
                                "venue": data["venue"].strip(),
                                "competition_date": data["competition_date"].isoformat(),
                                "registration_deadline": (
                                    data["registration_deadline"].isoformat() if data["registration_deadline"] else None
                                ),
                                "student_incharge": data["student_incharge"].strip(),
                                "mode": data.get("mode") or None,
                                "priority_label": data.get("priority_label") or None,
                            }).execute()
                            competition_id = comp_result.data[0]["competition_id"]

                            if data["links"]:
                                client.table("competition_links").insert([
                                    {"competition_id": competition_id, "label": l["label"], "url": l["url"]}
                                    for l in data["links"]
                                ]).execute()

                            fresh_events_here = fresh_all_events_by_comp.get(data["name"].strip().lower(), [])
                            for e in valid_events:
                                event_result = client.table("competition_events").insert({
                                    "competition_id": competition_id,
                                    "name": e["name"], "details": e["details"],
                                    "team_size": e["team_size"], "max_teams": e["max_teams"],
                                    "min_grade": e["min_grade"], "max_grade": e["max_grade"],
                                }).execute()
                                new_event_id = event_result.data[0]["event_id"]
                                _notify_new_event(data["name"], e["name"], e["min_grade"], e["max_grade"])
                                fresh_event = next(
                                    (fe for fe in fresh_events_here if fe["name"].strip().lower() == e["name"].strip().lower()),
                                    e,
                                )
                                _insert_matched_participants(
                                    client, new_event_id, fresh_event.get("teams", []), e["name"], data["name"]
                                )
                            # _insert_matched_participants already invalidates when IT
                            # changes something; this covers the competition/links/
                            # events inserts just above, which it doesn't know about.
                            invalidate_cache()
                            imported += 1

                        if errors:
                            st.session_state.competition_message = ("error", " ".join(errors))
                        else:
                            st.session_state.e2c_scan_results = None  # force a fresh scan next time
                            st.session_state.competition_message = (
                                "success", f"Imported {imported} competition(s) from E2C."
                            )
                        st.rerun()

            if already_imported:
                with st.expander(f":material/inventory_2: Already imported ({len(already_imported)})"):
                    for idx, comp in enumerate(results):
                        if not comp["already_imported"]:
                            continue
                        with st.container(border=True):
                            # --- Already in our database: sync + add-new-events only ---
                            st.markdown(f"**{comp['name']}**")
                            st.caption(
                                f":material/location_on: {comp['venue'] or '(not found)'}  •  "
                                f":material/event: {comp['date_text'] or '(not found)'}"
                            )
                            if st.session_state.confirming_e2c_single_sync_idx == idx:
                                pending = st.session_state.pending_e2c_single_sync
                                old_str = (
                                    pending["old_date"].strftime("%d %b %Y") if pending["old_date"] else "no date set"
                                )
                                new_str = pending["new_date"].strftime("%d %b %Y")
                                st.warning(
                                    f"The sheet's date for **{pending['fresh_comp']['name']}** is "
                                    f"**{new_str}**, but this competition is currently stored as "
                                    f"**{old_str}**. Accepting updates the date and emails every "
                                    f"member. Rejecting still syncs venue/links/roster, but keeps "
                                    f"the original date."
                                )
                                accept_col, reject_col = st.columns([1, 1])
                                if accept_col.button(
                                    "Accept date change", key=f"e2c_accept_date_{idx}",
                                    icon=":material/check:", type="primary",
                                ):
                                    _apply_e2c_single_sync(pending["fresh_comp"], accept_date=True)
                                if reject_col.button(
                                    "Reject date change", key=f"e2c_reject_date_{idx}", icon=":material/close:"
                                ):
                                    _apply_e2c_single_sync(pending["fresh_comp"], accept_date=False)
                            elif st.button(
                                "Update this competition", key=f"e2c_update_{idx}", icon=":material/sync:",
                                help="Re-reads the sheet fresh — anyone who signed up since your last "
                                     "Scan is included",
                            ):
                                try:
                                    with st.spinner("Re-reading the E2C sheet…"):
                                        fresh_comp = next(
                                            (c for c in scan_e2c_sheet(client)
                                             if c["name"].strip().lower() == comp["name"].strip().lower()),
                                            comp,
                                        )
                                except Exception as e:
                                    st.error(f"Couldn't re-read the E2C sheet: {e}")
                                    fresh_comp = None
                                if fresh_comp:
                                    old_date = _stored_competition_date(fresh_comp.get("existing_id"))
                                    new_date = fresh_comp.get("date_parsed")
                                    if new_date and new_date != old_date:
                                        st.session_state.pending_e2c_single_sync = {
                                            "fresh_comp": fresh_comp, "old_date": old_date, "new_date": new_date,
                                        }
                                        st.session_state.confirming_e2c_single_sync_idx = idx
                                        st.rerun()
                                    else:
                                        _apply_e2c_single_sync(fresh_comp, accept_date=True)

                            # Lets the host pull in a specific event that wasn't
                            # auto-detected as robotics (e.g. a borderline AI/IoT
                            # one) by typing its exact name from the sheet.
                            extra_key = f"e2c_extra_missing_{idx}"
                            if extra_key not in st.session_state:
                                st.session_state[extra_key] = []
                            add_col1, add_col2 = st.columns([4, 1])
                            add_name = add_col1.text_input(
                                "Add another event by name", key=f"e2c_addname_existing_{idx}",
                                placeholder="Add another event by its exact name (e.g. Vision 2047)",
                                label_visibility="collapsed",
                            )
                            if add_col2.button(
                                "Add", key=f"e2c_addname_existing_btn_{idx}", icon=":material/add:"
                            ) and add_name.strip():
                                already_listed = {
                                    e["name"].strip().lower()
                                    for e in comp["events"] + st.session_state[extra_key]
                                    if not e["already_imported"]
                                    and (comp["name"].strip().lower(), e["name"].strip().lower()) not in rejected_pairs
                                }
                                match = next(
                                    (e for e in comp.get("all_events", [])
                                     if e["name"].strip().lower() == add_name.strip().lower()),
                                    None,
                                )
                                if not match:
                                    st.session_state[f"e2c_addname_existing_msg_{idx}"] = (
                                        "error", f"No event named \"{add_name}\" found in {comp['name']}."
                                    )
                                elif match["already_imported"]:
                                    st.session_state[f"e2c_addname_existing_msg_{idx}"] = (
                                        "info", f"\"{match['name']}\" is already imported."
                                    )
                                elif match["name"].strip().lower() in already_listed:
                                    st.session_state[f"e2c_addname_existing_msg_{idx}"] = (
                                        "info", f"\"{match['name']}\" is already in the list."
                                    )
                                else:
                                    st.session_state[extra_key].append(match)
                                    st.session_state[f"e2c_addname_existing_msg_{idx}"] = (
                                        "success", f"Added \"{match['name']}\"."
                                    )
                                st.rerun()
                            addname_msg = st.session_state.pop(f"e2c_addname_existing_msg_{idx}", None)
                            if addname_msg:
                                kind, text = addname_msg
                                st.caption(f"{':material/error:' if kind == 'error' else ':material/info:'} {text}")

                            new_events = (
                                [
                                    e for e in comp["events"] if not e["already_imported"]
                                    and (comp["name"].strip().lower(), e["name"].strip().lower()) not in rejected_pairs
                                ]
                                + st.session_state[extra_key]
                            )
                            if new_events:
                                st.markdown("**New robotics events found on the sheet, not yet added:**")
                                to_add = []
                                for e in new_events:
                                    label = (
                                        f"{e['name']} — {e['team_size']} per team, up to "
                                        f"{e['max_teams']} team(s), grades {e['min_grade']}–{e['max_grade']}"
                                    )
                                    if e["flagged"]:
                                        label += " ⚠️ check team size/max teams before adding"
                                    if e.get("needs_review"):
                                        label += (
                                            " — not auto-detected as robotics, RoboKnights members are "
                                            "already on the roster, confirm before adding"
                                        )
                                    check_col, reject_col2 = st.columns([5, 1])
                                    if check_col.checkbox(
                                        label, value=not e["flagged"] and not e.get("needs_review"),
                                        key=f"e2c_addevent_{idx}_{e['name']}",
                                    ):
                                        to_add.append(e)
                                    with reject_col2:
                                        _reject_event_button(
                                            comp["name"], e["name"], f"e2c_reject_existing_{idx}_{e['name']}"
                                        )
                                    teams_caption = _teams_caption(e.get("teams", []))
                                    if teams_caption:
                                        st.caption(teams_caption)
                                if st.button(
                                    "Add selected events", key=f"e2c_addevents_btn_{idx}", icon=":material/add:",
                                    help="Re-reads the sheet fresh so anyone newly signed up is included",
                                ):
                                    # Re-scan fresh (not the cached teams data) so anyone who
                                    # signed up since the last Scan still gets matched here.
                                    try:
                                        with st.spinner("Re-reading the E2C sheet…"):
                                            fresh_comp = next(
                                                (c for c in scan_e2c_sheet(client)
                                                 if c["name"].strip().lower() == comp["name"].strip().lower()),
                                                None,
                                            )
                                    except Exception as ex:
                                        st.error(f"Couldn't re-read the E2C sheet: {ex}")
                                        fresh_comp = None
                                    fresh_all_events = fresh_comp.get("all_events", []) if fresh_comp else []
                                    with _safe_write(f"add events to {comp['name']}"):
                                        for e in to_add:
                                            event_result = client.table("competition_events").insert({
                                                "competition_id": comp["existing_id"],
                                                "name": e["name"], "details": e["details"],
                                                "team_size": e["team_size"], "max_teams": e["max_teams"],
                                                "min_grade": e["min_grade"], "max_grade": e["max_grade"],
                                            }).execute()
                                            new_event_id = event_result.data[0]["event_id"]
                                            _notify_new_event(comp["name"], e["name"], e["min_grade"], e["max_grade"])
                                            fresh_event = next(
                                                (fe for fe in fresh_all_events
                                                 if fe["name"].strip().lower() == e["name"].strip().lower()),
                                                e,
                                            )
                                            _insert_matched_participants(
                                                client, new_event_id, fresh_event.get("teams", []), e["name"], comp["name"]
                                            )
                                        if to_add:
                                            invalidate_cache()  # covers the competition_events inserts above
                                            st.toast(
                                                f"Added {len(to_add)} event(s) to {comp['name']}.",
                                                icon=":material/check_circle:",
                                            )
                                            st.rerun()

# Success pops as a toast; errors stay inline so they can't be missed.
if st.session_state.competition_message:
    kind, text = st.session_state.competition_message
    if kind == "success":
        st.toast(text, icon=":material/check_circle:")
    else:
        st.error(text)
    st.session_state.competition_message = None

# --- Everyone: browse all competitions --------------------------------------
# Everyone sees every competition and every event/link on it. Grade
# eligibility controls whether a "Volunteer" button appears on a given
# event — mirrors the existing "Request this" pattern on the Inventory page
# (only shown when you're allowed to act). Hosts additionally get an Edit
# button on each competition, plus a way to move one to/from "past" — by
# hand (e.g. it got cancelled), or automatically once its date has gone by.

if "volunteer_message" not in st.session_state:
    st.session_state.volunteer_message = None
if "withdrawing_event_id" not in st.session_state:
    st.session_state.withdrawing_event_id = None

if st.session_state.volunteer_message:
    st.toast(st.session_state.volunteer_message, icon=":material/check_circle:")
    st.session_state.volunteer_message = None

# Auto-flip: anything whose date has already passed and isn't marked past
# yet gets marked past right now. No scheduled job needed for this one —
# the page gets viewed often enough that a same-day flip is good enough,
# unlike the reminder emails which genuinely need a fixed daily time.
#
# Uses IST "today", not the server's own date — Streamlit Cloud runs in
# UTC, and between midnight and 5:30 AM IST the UTC calendar date is still
# "yesterday". Using date.today() directly (as this used to) meant a
# competition happening "today" IST could stay un-flipped, and the
# bot-status panel below could stay locked, for up to 5.5 hours longer
# than it should on the day it matters most.
#
# Checks against the already-cached list first (a plain read, ~free) so
# an ordinary visit only issues a write when something ACTUALLY needs
# flipping, rather than firing an update query on literally every page
# load by every user. Wrapped in _safe_write since this runs unconditionally
# for anyone who opens the page — a transient failure here shouldn't take
# the whole page down with it.
today_ist = datetime.now(IST).date()
with _safe_write("check for newly-past competitions"):
    stale_ids = [
        c["competition_id"] for c in cached_table("competitions")
        if not c.get("is_past") and c.get("competition_date")
        and date.fromisoformat(c["competition_date"]) < today_ist
    ]
    if stale_ids:
        client.table("competitions").update({"is_past": True}).in_(
            "competition_id", stale_ids
        ).execute()
        invalidate_cache()

competitions = sorted(
    cached_table("competitions"),
    key=lambda c: (c.get("competition_date") or "9999-99-99", c["competition_id"]),
)
# Whether anything exists at all, as opposed to "nothing matched the search" —
# they need different empty messages.
competitions_exist = bool(competitions)

all_links = cached_table("competition_links")
all_events = cached_table("competition_events")
all_volunteers = cached_table("event_volunteers")

# --- At-a-glance numbers -----------------------------------------------------
# Counted before the search filter, so the totals don't shift while you type.
my_volunteer_rows = [v for v in all_volunteers if v["user_id"] == current_user_id]
my_event_ids = {v["event_id"] for v in my_volunteer_rows}
my_selected_count = sum(1 for v in my_volunteer_rows if v.get("selected"))
upcoming_all = [c for c in competitions if not c.get("is_past")]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Upcoming", len(upcoming_all), border=True)
m2.metric("Events I'm in", len(my_event_ids), border=True, help="Events you've volunteered for")
m3.metric("Selected for", my_selected_count, border=True, help="Events you've actually been picked for")
m4.metric("Past", sum(1 for c in competitions if c.get("is_past")), border=True)

comp_search = st.text_input(
    "Search competitions",
    key="comp_search",
    placeholder="Search by name, venue, or event",
    icon=":material/search:",
    label_visibility="collapsed",
)
if comp_search:
    q = comp_search.lower()
    # Also matches on an event's own name (e.g. "RoboRace"), not just the
    # competition's — searching for a specific event used to only work if
    # it happened to also match the competition's own name or venue.
    matching_comp_ids = {e["competition_id"] for e in all_events if q in e["name"].lower()}
    competitions = [
        c for c in competitions
        if q in (c["name"] + " " + (c.get("venue") or "")).lower()
        or c["competition_id"] in matching_comp_ids
    ]

def _apply_competition_save(
    cid, name, venue, final_date, reg_deadline, incharge,
    valid_events, valid_links, original_events, date_changed, old_date,
):
    # Shared by all three ways an edit can finish: no date change (saves
    # immediately), Accept (saves with the new date), Reject (saves
    # everything else but keeps the original date) — same write logic
    # either way, only the date value and whether to email about it differ.
    with _safe_write("save changes to this competition"):
        client.table("competitions").update({
            "name": name.strip(),
            "venue": venue.strip(),
            "competition_date": final_date.isoformat(),
            "registration_deadline": reg_deadline.isoformat() if reg_deadline else None,
            "student_incharge": incharge.strip(),
        }).eq("competition_id", cid).execute()

        # Links: nothing else references them, so simplest to just replace
        # the whole set.
        client.table("competition_links").delete().eq("competition_id", cid).execute()
        if valid_links:
            client.table("competition_links").insert([
                {
                    "competition_id": cid,
                    "label": l["label"].strip() or "Link",
                    "url": l["url"].strip()
                    if l["url"].strip().startswith(("http://", "https://"))
                    else "https://" + l["url"].strip(),
                }
                for l in valid_links
            ]).execute()

        # Events: update existing ones in place (so their volunteer signups
        # survive), insert brand-new ones, delete any that were removed.
        kept_ids = {e["event_id"] for e in valid_events if e["event_id"] is not None}
        original_ids = {e["event_id"] for e in original_events}
        for old_id in original_ids - kept_ids:
            client.table("competition_events").delete().eq("event_id", old_id).execute()
        for e in valid_events:
            payload = {
                "name": e["name"].strip(),
                "details": e["details"].strip(),
                "team_size": e["team_size"],
                "max_teams": e["max_teams"],
                "min_grade": e["min_grade"],
                "max_grade": e["max_grade"],
            }
            if e["event_id"] is None:
                client.table("competition_events").insert({**payload, "competition_id": cid}).execute()
                _notify_new_event(name, e["name"], e["min_grade"], e["max_grade"])
            else:
                client.table("competition_events").update(payload).eq("event_id", e["event_id"]).execute()

        invalidate_cache()

        if date_changed:
            _notify_date_change(name, old_date, final_date)

        st.session_state.competition_message = ("success", f"Updated {name.strip()}.")
        st.session_state.editing_competition_id = None
        st.session_state.confirming_date_change_id = None
        st.session_state.pending_date_change = None
        st.rerun()


def render_competition_card(comp):
    cid = comp["competition_id"]
    links = [l for l in all_links if l["competition_id"] == cid]
    events = [e for e in all_events if e["competition_id"] == cid]
    editing_this = is_host and st.session_state.editing_competition_id == cid

    # key= gives the card a stable "st-key-rkcard_..." CSS class, which
    # the hover animation in app.py targets.
    with st.container(border=True, key=f"rkcard_comp_{cid}"):
        if editing_this and st.session_state.confirming_date_change_id == cid:
            # --- Host: confirm a date change before it's applied --------
            # The date is the one field this app won't silently change on
            # a plain Save — a wrong/accidental date is disruptive enough
            # (it goes out to every member) that it gets its own explicit
            # Accept/Reject step, same two-step-confirm shape as deleting a
            # competition or withdrawing from an event elsewhere on this page.
            pending = st.session_state.pending_date_change
            old_str = pending["old_date"].strftime("%d %b %Y") if pending["old_date"] else "no date set"
            new_str = pending["new_date"].strftime("%d %b %Y")
            st.warning(
                f"**{pending['name'].strip()}**'s date is changing from **{old_str}** to "
                f"**{new_str}**. Accepting saves your changes and emails every member about "
                f"the new date. Rejecting saves your other changes but keeps the original date."
            )
            accept_col, reject_col = st.columns([1, 1])
            if accept_col.button(
                "Accept date change", key=f"accept_date_{cid}", icon=":material/check:", type="primary"
            ):
                _apply_competition_save(
                    pending["cid"], pending["name"], pending["venue"], pending["new_date"],
                    pending["reg_deadline"], pending["incharge"], pending["valid_events"],
                    pending["valid_links"], pending["original_events"],
                    date_changed=True, old_date=pending["old_date"],
                )
            if reject_col.button("Reject date change", key=f"reject_date_{cid}", icon=":material/close:"):
                _apply_competition_save(
                    pending["cid"], pending["name"], pending["venue"], pending["old_date"],
                    pending["reg_deadline"], pending["incharge"], pending["valid_events"],
                    pending["valid_links"], pending["original_events"],
                    date_changed=False, old_date=pending["old_date"],
                )

        elif editing_this:
            # --- Host: edit this competition ---------------------------
            # Same list-in-session-state + Add/Remove pattern as the
            # Create form above, just pre-filled with the existing data.
            st.markdown(f"**Editing: {comp['name']}**")
            edit_name = st.text_input("Competition name", value=comp["name"], key=f"edit_name_{cid}")
            edit_venue = st.text_input("Venue", value=comp.get("venue") or "", key=f"edit_venue_{cid}")
            edit_date = st.date_input(
                "Competition date",
                value=date.fromisoformat(comp["competition_date"]) if comp.get("competition_date") else None,
                key=f"edit_date_{cid}",
            )
            edit_reg_deadline = st.date_input(
                "Registration deadline",
                value=date.fromisoformat(comp["registration_deadline"])
                if comp.get("registration_deadline") else None,
                key=f"edit_reg_deadline_{cid}",
            )
            edit_incharge = st.text_input(
                "Student in-charge", value=comp.get("student_incharge") or "", key=f"edit_incharge_{cid}"
            )

            st.markdown("**Links**")
            for i, link in enumerate(st.session_state.edit_comp_links):
                lcol1, lcol2, lcol3 = st.columns([2, 4, 1], vertical_alignment="bottom")
                link["label"] = lcol1.text_input(
                    "Label", value=link["label"], key=f"edit_link_label_{cid}_{i}",
                    label_visibility="collapsed", placeholder="Label (e.g. Brochure)",
                )
                link["url"] = lcol2.text_input(
                    "URL", value=link["url"], key=f"edit_link_url_{cid}_{i}",
                    label_visibility="collapsed", placeholder="https://...",
                )
                if len(st.session_state.edit_comp_links) > 1:
                    if lcol3.button("Remove", key=f"edit_remove_link_{cid}_{i}", icon=":material/close:"):
                        st.session_state.edit_comp_links.pop(i)
                        st.rerun()
            if st.button("Add another link", key=f"edit_add_link_{cid}", icon=":material/add:"):
                st.session_state.edit_comp_links.append(dict(BLANK_LINK))
                st.rerun()

            st.markdown("**Events**")
            for i, event in enumerate(st.session_state.edit_comp_events):
                with st.container(border=True):
                    event["name"] = st.text_input(
                        "Event name", value=event["name"], key=f"edit_event_name_{cid}_{i}"
                    )
                    event["details"] = st.text_area(
                        "Details / rules", value=event["details"], key=f"edit_event_details_{cid}_{i}"
                    )
                    ecol1, ecol2, ecol3, ecol4 = st.columns(4)
                    event["team_size"] = ecol1.number_input(
                        "Team size", min_value=1, value=event["team_size"],
                        key=f"edit_event_team_size_{cid}_{i}",
                    )
                    event["max_teams"] = ecol2.number_input(
                        "Max teams", min_value=1, value=event["max_teams"],
                        key=f"edit_event_max_teams_{cid}_{i}",
                    )
                    event["min_grade"] = ecol3.selectbox(
                        "Min grade", GRADES, index=GRADES.index(event["min_grade"]),
                        key=f"edit_event_min_grade_{cid}_{i}",
                    )
                    event["max_grade"] = ecol4.selectbox(
                        "Max grade", GRADES, index=GRADES.index(event["max_grade"]),
                        key=f"edit_event_max_grade_{cid}_{i}",
                    )
                    if len(st.session_state.edit_comp_events) > 1:
                        if st.button(
                            "Remove event", key=f"edit_remove_event_{cid}_{i}", icon=":material/close:"
                        ):
                            # Removing an existing event here deletes it
                            # (and anyone's volunteer signups for it) on
                            # Save — same as deleting a part deletes its
                            # request history. Untouched events keep
                            # their signups (updated, not recreated).
                            st.session_state.edit_comp_events.pop(i)
                            st.rerun()
            if st.button("Add another event", key=f"edit_add_event_{cid}", icon=":material/add:"):
                st.session_state.edit_comp_events.append(dict(BLANK_EVENT))
                st.rerun()

            save_col, cancel_col = st.columns([1, 1])
            if save_col.button(
                "Save changes", key=f"save_comp_{cid}", icon=":material/check:", type="primary"
            ):
                valid_events = [e for e in st.session_state.edit_comp_events if e["name"].strip()]
                errors = _validate_competition_form(edit_name, edit_date, valid_events)

                if errors:
                    st.session_state.competition_message = ("error", " ".join(errors))
                    st.rerun()
                else:
                    valid_links = [l for l in st.session_state.edit_comp_links if l["url"].strip()]
                    old_date = (
                        date.fromisoformat(comp["competition_date"]) if comp.get("competition_date") else None
                    )
                    if edit_date != old_date:
                        # Don't save yet — hold everything in session_state
                        # and show the Accept/Reject prompt on the next
                        # rerun instead. Other field edits ride along with
                        # whichever choice the host makes.
                        st.session_state.pending_date_change = {
                            "cid": cid, "name": edit_name, "venue": edit_venue, "new_date": edit_date,
                            "reg_deadline": edit_reg_deadline, "incharge": edit_incharge,
                            "valid_events": valid_events, "valid_links": valid_links,
                            "original_events": events, "old_date": old_date,
                        }
                        st.session_state.confirming_date_change_id = cid
                        st.rerun()
                    else:
                        _apply_competition_save(
                            cid, edit_name, edit_venue, edit_date, edit_reg_deadline, edit_incharge,
                            valid_events, valid_links, events, date_changed=False, old_date=old_date,
                        )
            if cancel_col.button("Cancel", key=f"cancel_comp_{cid}", icon=":material/close:"):
                st.session_state.editing_competition_id = None
                st.rerun()

        else:
            # --- Read-only view (everyone) ------------------------------
            if is_host and st.session_state.deleting_competition_id == cid:
                achievement_count = sum(
                    1 for a in cached_table("achievements") if a["competition_id"] == cid
                )
                st.warning(
                    f"Delete **{comp['name']}**? This also deletes its {len(events)} event(s), "
                    f"all volunteer signups, and {achievement_count} logged achievement(s) for it. "
                    f"This can't be undone."
                )
                confirm_col, cancel_col = st.columns([1, 1])
                if confirm_col.button(
                    "Confirm delete", key=f"confirm_delete_comp_{cid}",
                    icon=":material/delete_forever:", type="primary",
                ):
                    with _safe_write("delete this competition"):
                        client.table("competitions").delete().eq("competition_id", cid).execute()
                        invalidate_cache()
                        st.session_state.deleting_competition_id = None
                        st.session_state.competition_message = ("success", f"Deleted {comp['name']}.")
                        st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_delete_comp_{cid}", icon=":material/close:"):
                    st.session_state.deleting_competition_id = None
                    st.rerun()
                return  # skip the rest of this card while confirming

            title_col, going_col, past_col, edit_col, delete_col = st.columns([3, 1, 1, 1, 1])
            title_col.markdown(f"### {comp['name']}")
            if is_host:
                if comp.get("not_attending"):
                    if going_col.button(
                        "Going after all", key=f"going_comp_{cid}", icon=":material/undo:",
                        help="Un-mark \"not attending\" for this competition",
                    ):
                        with _safe_write("update this competition"):
                            client.table("competitions").update({"not_attending": False}).eq(
                                "competition_id", cid
                            ).execute()
                            invalidate_cache()
                            st.rerun()
                else:
                    if going_col.button(
                        "Not going", key=f"notgoing_comp_{cid}", icon=":material/event_busy:",
                        help="Mark that RoboKnights isn't attending this competition",
                    ):
                        with _safe_write("update this competition"):
                            client.table("competitions").update({"not_attending": True}).eq(
                                "competition_id", cid
                            ).execute()
                            invalidate_cache()
                            # Anyone already selected for one of its events gets told
                            # directly — they'd otherwise still be expecting to go,
                            # and the day-before/day-of reminder emails now skip this
                            # competition entirely, so this is the only notice they get.
                            event_ids_here = {e["event_id"] for e in events}
                            selected_uids = {
                                v["user_id"] for v in all_volunteers
                                if v["event_id"] in event_ids_here and v.get("selected")
                            }
                            for uid in selected_uids:
                                send_email(
                                    user_email_by_id.get(uid),
                                    f"RoboKnights is not attending {comp['name']}",
                                    f"You were selected to represent RoboKnights at {comp['name']}, "
                                    f"but due to unforeseen circumstances the club won't be attending "
                                    f"after all. Sorry for the change of plans — you don't need to do "
                                    f"anything further for this one.",
                                )
                            st.rerun()
                if comp.get("is_past"):
                    if past_col.button(
                        "Restore", key=f"unpast_comp_{cid}", icon=":material/undo:",
                        help="Move this competition back to the upcoming list",
                    ):
                        with _safe_write("update this competition"):
                            client.table("competitions").update({"is_past": False}).eq(
                                "competition_id", cid
                            ).execute()
                            invalidate_cache()
                            st.rerun()
                else:
                    if past_col.button(
                        "Mark past", key=f"mark_past_comp_{cid}", icon=":material/history:",
                        help="Move this competition to the Past section",
                    ):
                        with _safe_write("update this competition"):
                            client.table("competitions").update({"is_past": True}).eq(
                                "competition_id", cid
                            ).execute()
                            invalidate_cache()
                            st.rerun()
                if delete_col.button(
                    "Delete", key=f"delete_comp_{cid}", icon=":material/delete:",
                    help="Delete this competition (with confirmation)",
                ):
                    st.session_state.deleting_competition_id = cid
                    st.rerun()
                if edit_col.button("Edit", key=f"edit_comp_{cid}", icon=":material/edit:"):
                    st.session_state.editing_competition_id = cid
                    st.session_state.edit_comp_links = (
                        [{"label": l["label"], "url": l["url"]} for l in links] or [dict(BLANK_LINK)]
                    )
                    st.session_state.edit_comp_events = (
                        [
                            {
                                "event_id": e["event_id"],
                                "name": e["name"],
                                "details": e.get("details") or "",
                                "team_size": e["team_size"],
                                "max_teams": e["max_teams"],
                                "min_grade": e["min_grade"],
                                "max_grade": e["max_grade"],
                            }
                            for e in events
                        ]
                        or [dict(BLANK_EVENT)]
                    )
                    st.rerun()

            if comp.get("not_attending"):
                st.error(":material/event_busy: **ROBOKNIGHTS IS NOT ATTENDING THIS COMPETITION**")

            info_bits = []
            if comp.get("venue"):
                info_bits.append(f":material/location_on: {comp['venue']}")
            if comp.get("competition_date"):
                info_bits.append(
                    f":material/event: {date.fromisoformat(comp['competition_date']).strftime('%d %b %Y')}"
                )
            if comp.get("registration_deadline"):
                info_bits.append(
                    f":material/schedule: Register by "
                    f"{date.fromisoformat(comp['registration_deadline']).strftime('%d %b %Y')}"
                )
            if comp.get("student_incharge"):
                info_bits.append(f":material/person: {comp['student_incharge']}")
            if info_bits:
                st.caption("  •  ".join(info_bits))

            if links:
                # A link typed without http(s):// (e.g. "discord.com")
                # would otherwise be treated as relative to the app's
                # own URL, sending clicks to localhost:8501/discord.com
                # instead of the real site.
                st.markdown(
                    "**Links:** "
                    + "  •  ".join(
                        f"[{l['label']}]"
                        f"({l['url'] if l['url'].startswith(('http://', 'https://')) else 'https://' + l['url']})"
                        for l in links
                    )
                )

            if events:
                st.markdown("**Events:**")
                for e in events:
                    grade_range = (
                        f"Grade {e['min_grade']}"
                        if e["min_grade"] == e["max_grade"]
                        else f"Grades {e['min_grade']}–{e['max_grade']}"
                    )
                    event_volunteers = [v for v in all_volunteers if v["event_id"] == e["event_id"]]
                    # An event capped at exactly 1 team has no real
                    # "other" team to be separate from — anyone finalized
                    # or self-volunteered without a sheet team_no (e.g.
                    # someone the host added by hand) is still trying for
                    # that same single slot as whoever the sheet DID
                    # assign a team_no. Only kicks in once the sheet has
                    # actually given this event a real team_no to begin
                    # with — a plain in-app-only event (no sheet team data
                    # at all) keeps the old flat Volunteers list, since
                    # calling that a "Team" would be misleading.
                    single_team_merge = e["max_teams"] == 1 and any(
                        v.get("team_no") for v in event_volunteers
                    )

                    def _effective_team_no(v):
                        return 1 if single_team_merge else v.get("team_no")

                    # team_no itself is the sheet ROW position, with gaps
                    # left by rows that had no matched members (see
                    # _parse_teams in e2c_import.py — it has to stay that
                    # way underneath, so a newly-matched row can't merge
                    # into an unrelated team_no). Only the display needs to
                    # be gap-free, so remap to a plain 1, 2, 3... just for
                    # what's shown on screen.
                    team_no_display = {
                        real: i
                        for i, real in enumerate(
                            sorted({_effective_team_no(v) for v in event_volunteers if _effective_team_no(v)}),
                            start=1,
                        )
                    }
                    selected_names = [
                        user_name_by_id.get(v["user_id"], "Unknown")
                        for v in event_volunteers
                        if v.get("selected")
                    ]
                    already_volunteered = any(v["user_id"] == current_user_id for v in event_volunteers)
                    is_eligible = (
                        not is_exun  # Exun can view every event, but never volunteer for one
                        and not comp.get("not_attending")
                        and current_user_grade is not None
                        and e["min_grade"] <= current_user_grade <= e["max_grade"]
                    )
                    cap = e["team_size"] * e["max_teams"]

                    with st.container(border=True, key=f"rkcard_event_{e['event_id']}"):
                        st.markdown(
                            f"**{e['name']}** — {grade_range}, {e['team_size']} per team, "
                            f"up to {e['max_teams']} team(s)"
                        )
                        if e.get("details"):
                            st.caption(e["details"])

                        # Teams (e.g. from the E2C import, where the sheet gave us
                        # real team rows) get shown grouped by team_no instead of one
                        # flat name list — makes it obvious who's actually on the
                        # same team together. Anyone without a team_no yet (regular
                        # in-app volunteering) falls back to the old flat display.
                        teamed = [v for v in event_volunteers if _effective_team_no(v)]
                        unteamed = [v for v in event_volunteers if not _effective_team_no(v)]

                        if teamed:
                            st.markdown("**Teams:**")
                            st.caption(
                                ":material/travel_explore: From the E2C sheet — kept in sync "
                                "automatically, so this can change if the sheet does."
                            )
                            teams_by_no = {}
                            for v in teamed:
                                teams_by_no.setdefault(_effective_team_no(v), []).append(v)
                            for team_no in sorted(teams_by_no):
                                members = ", ".join(
                                    user_name_by_id.get(v["user_id"], "Unknown")
                                    + ("" if v.get("selected") else " (pending)")
                                    for v in teams_by_no[team_no]
                                )
                                st.caption(f":material/group: Team {team_no_display[team_no]}: {members}")

                        if unteamed:
                            unteamed_selected = [
                                user_name_by_id.get(v["user_id"], "Unknown") for v in unteamed if v.get("selected")
                            ]
                            unteamed_volunteers = [
                                user_name_by_id.get(v["user_id"], "Unknown") for v in unteamed
                            ]
                            if unteamed_selected:
                                st.caption(
                                    f":material/verified: Selected ({len(unteamed_selected)}/{cap}): "
                                    + ", ".join(unteamed_selected)
                                )
                            st.caption(
                                f":material/group: Volunteers ({len(unteamed_volunteers)}): "
                                + ", ".join(unteamed_volunteers)
                            )
                        elif not teamed:
                            st.caption(":material/group: No volunteers yet")

                        # Only shown when eligible — same pattern as the
                        # "Request this" button on Inventory only
                        # showing up when a part is actually
                        # requestable by you.
                        if is_eligible:
                            if already_volunteered:
                                my_row = next(
                                    (v for v in event_volunteers if v["user_id"] == current_user_id), None
                                )
                                confirming = st.session_state.withdrawing_event_id == e["event_id"]
                                if confirming:
                                    warning = f"Withdraw from {e['name']}?"
                                    if my_row and my_row.get("selected"):
                                        warning += (
                                            " You're currently **selected** for this event — "
                                            "the host will be notified so they can find a replacement."
                                        )
                                    st.warning(warning)
                                    wcol1, wcol2 = st.columns([1, 1])
                                    if wcol1.button(
                                        "Confirm withdraw", key=f"confirm_withdraw_{e['event_id']}",
                                        icon=":material/close:", type="primary",
                                    ):
                                        with _safe_write("withdraw from this event"):
                                            was_selected = bool(my_row and my_row.get("selected"))
                                            client.table("event_volunteers").delete().eq(
                                                "event_id", e["event_id"]
                                            ).eq("user_id", current_user_id).execute()
                                            invalidate_cache()
                                            if was_selected:
                                                volunteer_name = user_name_by_id.get(current_user_id, "A member")
                                                host_emails = [
                                                    email for uid, email in user_email_by_id.items()
                                                    if email in HOST_EMAILS
                                                ]
                                                for host_email in host_emails:
                                                    send_email(
                                                        host_email,
                                                        f"{volunteer_name} withdrew: {e['name']} at {comp['name']}",
                                                        f"{volunteer_name} was selected for {e['name']} at "
                                                        f"{comp['name']}, but just withdrew. You may want to "
                                                        f"pick a replacement.",
                                                    )
                                            st.session_state.withdrawing_event_id = None
                                            st.session_state.volunteer_message = f"Withdrew from {e['name']}."
                                            st.rerun()
                                    if wcol2.button(
                                        "Cancel", key=f"cancel_withdraw_{e['event_id']}", icon=":material/close:"
                                    ):
                                        st.session_state.withdrawing_event_id = None
                                        st.rerun()
                                else:
                                    if st.button(
                                        "Withdraw", key=f"withdraw_{e['event_id']}", icon=":material/close:"
                                    ):
                                        st.session_state.withdrawing_event_id = e["event_id"]
                                        st.rerun()
                            else:
                                if st.button(
                                    "Volunteer",
                                    key=f"volunteer_{e['event_id']}",
                                    icon=":material/front_hand:",
                                    type="primary",
                                ):
                                    with _safe_write("volunteer for this event"):
                                        client.table("event_volunteers").insert({
                                            "event_id": e["event_id"],
                                            "user_id": current_user_id,
                                        }).execute()
                                        invalidate_cache()
                                        st.session_state.volunteer_message = f"You volunteered for {e['name']}!"
                                        st.rerun()

                        # Host-only: finalize who's actually selected,
                        # capped at team_size * max_teams. Only newly
                        # selected people (not already-selected ones
                        # re-saved unchanged) get an email.
                        if is_host and event_volunteers:
                            already_selected_ids = [
                                v["user_id"] for v in event_volunteers if v.get("selected")
                            ]
                            if len(already_selected_ids) > cap:
                                st.caption(
                                    f":material/warning: {len(already_selected_ids)} people are "
                                    f"marked selected, over the {cap}-person cap for this event "
                                    f"(likely from the E2C sheet sync, which doesn't check the cap) "
                                    f"— remove some below and save to fix it."
                                )
                            volunteer_team_no_by_uid = {
                                v["user_id"]: _effective_team_no(v) for v in event_volunteers
                            }
                            finalize_ids = st.multiselect(
                                "Finalize volunteers",
                                options=[v["user_id"] for v in event_volunteers],
                                default=already_selected_ids,
                                # Shows which sheet-team someone's on (e.g. "Naitik
                                # Jindal (Team 1)") — a flat name list made it hard to
                                # tell teammates apart while picking who's finalized.
                                format_func=lambda uid: (
                                    f"{user_name_by_id.get(uid, 'Unknown')} "
                                    f"(Team {team_no_display[volunteer_team_no_by_uid[uid]]})"
                                    if volunteer_team_no_by_uid.get(uid)
                                    else user_name_by_id.get(uid, "Unknown")
                                ),
                                # Never below however many are ALREADY selected — Streamlit
                                # refuses to render a multiselect whose own default exceeds
                                # max_selections, which is exactly what crashed this page
                                # (E2C sync can push someone selected past the cap with no
                                # check, since that path doesn't go through this widget at all).
                                max_selections=max(cap, len(already_selected_ids)),
                                key=f"finalize_{e['event_id']}",
                            )
                            if st.button(
                                "Save selection", key=f"save_finalize_{e['event_id']}",
                                icon=":material/check:",
                            ):
                                with _safe_write("save this selection"):
                                    previously_selected = {
                                        v["user_id"] for v in event_volunteers if v.get("selected")
                                    }
                                    newly_selected = set(finalize_ids) - previously_selected
                                    newly_deselected = previously_selected - set(finalize_ids)

                                    for uid in newly_selected:
                                        client.table("event_volunteers").update({"selected": True}).eq(
                                            "event_id", e["event_id"]
                                        ).eq("user_id", uid).execute()
                                        _send_selected_email(uid, e["name"], comp["name"])
                                    for uid in newly_deselected:
                                        client.table("event_volunteers").update({"selected": False}).eq(
                                            "event_id", e["event_id"]
                                        ).eq("user_id", uid).execute()

                                    if newly_selected or newly_deselected:
                                        invalidate_cache()
                                    st.session_state.volunteer_message = f"Saved selection for {e['name']}."
                                    st.rerun()

                        # Bot status: unlocked starting the day before
                        # the competition (matches when the day-before
                        # reminder email goes out), for anyone selected.
                        # Everyone can see the statuses once unlocked;
                        # only the selected person themselves can edit
                        # their own.
                        if selected_names and comp.get("competition_date"):
                            days_until = (
                                date.fromisoformat(comp["competition_date"]) - today_ist
                            ).days
                            if days_until <= 1:
                                st.markdown("**Bot status**")
                                for v in event_volunteers:
                                    if not v.get("selected"):
                                        continue
                                    vol_name = user_name_by_id.get(v["user_id"], "Unknown")
                                    status_text = v.get("bot_status") or "Not updated yet"
                                    st.caption(f"{vol_name}: {status_text}")

                                my_row = next(
                                    (v for v in event_volunteers if v["user_id"] == current_user_id),
                                    None,
                                )
                                if my_row and my_row.get("selected"):
                                    new_status = st.text_input(
                                        "Update your bot's status",
                                        value=my_row.get("bot_status") or "",
                                        key=f"bot_status_{e['event_id']}",
                                    )
                                    if st.button(
                                        "Save status", key=f"save_bot_status_{e['event_id']}",
                                        icon=":material/check:",
                                    ):
                                        with _safe_write("save the bot status"):
                                            # Shared across the whole team (same team_no) when
                                            # one's known — one bot, one status, no need for every
                                            # teammate to separately type the same update.
                                            my_team_no = my_row.get("team_no")
                                            update_query = (
                                                client.table("event_volunteers")
                                                .update({"bot_status": new_status.strip()})
                                                .eq("event_id", e["event_id"])
                                            )
                                            if my_team_no:
                                                update_query = update_query.eq("team_no", my_team_no)
                                            else:
                                                update_query = update_query.eq("user_id", current_user_id)
                                            update_query.execute()
                                            invalidate_cache()
                                            st.session_state.volunteer_message = "Bot status updated."
                                            st.rerun()


# --- Layout ------------------------------------------------------------------
# Upcoming and past are separate tabs so the main view stays short, with the
# host's own tools (add a competition, pull from the E2C sheet) on a third
# tab rather than stacked above everyone's browsing.

upcoming = [c for c in competitions if not c.get("is_past")]
past = [c for c in competitions if c.get("is_past")]

tab_labels = [f"Upcoming ({len(upcoming)})", f"Past ({len(past)})"]
if is_host:
    tab_labels.append("Add & import")
open_tabs = st.tabs(tab_labels)
tab_upcoming, tab_past = open_tabs[0], open_tabs[1]

with tab_upcoming:
    if not competitions_exist:
        st.caption("No competitions yet.")
    elif not upcoming:
        st.caption(
            "Nothing coming up."
            if not comp_search else "No upcoming competitions match your search."
        )
    else:
        for comp in upcoming:
            render_competition_card(comp)

with tab_past:
    if not past:
        st.caption(
            "Nothing here yet — competitions move across once their date passes."
            if not comp_search else "No past competitions match your search."
        )
    else:
        for comp in reversed(past):  # most recently past first
            render_competition_card(comp)

if is_host:
    with open_tabs[2]:
        render_add_competition()
        render_e2c_import()

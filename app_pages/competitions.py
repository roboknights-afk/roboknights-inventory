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

from contextlib import contextmanager
from datetime import date, datetime

import streamlit as st

from e2c_import import scan_e2c_sheet
from shared import HOST_EMAILS, IST, cached_table, get_client, invalidate_cache, send_email

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_grade = st.session_state.current_user_grade
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

GRADES = [6, 7, 8, 9, 10, 11, 12]  # 6 included since E2C-imported events can genuinely be 6th-grade eligible
BLANK_LINK = {"label": "", "url": ""}
BLANK_EVENT = {"event_id": None, "name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}


@contextmanager
def _safe_write(action_description):
    # Wraps a block of Supabase writes so a transient failure (network
    # blip, a Supabase hiccup) shows a clean inline error instead of
    # crashing the whole page for whoever's using it right then — the same
    # crash class as the DMMITS multiselect bug, just triggered by an API
    # failure instead of a bad widget default.
    try:
        yield
    except Exception as e:
        st.error(f"Couldn't {action_description}: {e}")


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


def _insert_matched_participants(client, event_id, teams, event_name, comp_name):
    # teams: list of team-rows, each {"team_no", "participants": [{"user_id",
    # "name", "selected"}]} matched against real members off the sheet's
    # green cells. A row's presence here can ADD someone, PROMOTE them from
    # pending to selected, and now also REMOVE someone who's been swapped
    # out of the green cell on the real sheet — but that removal only ever
    # touches a row THIS sync itself put there (synced_from_sheet=True).
    # A row from someone clicking Volunteer directly in the app is never
    # touched, so this can't silently undo an in-app action, only ever a
    # sheet-sourced one going stale. Anyone newly added or promoted as
    # selected gets the same "You're selected" email the manual finalize
    # flow sends — being confirmed on the real sheet is no different from
    # being finalized by the host.
    #
    # This read is deliberately NOT cached_table: it decides insert-vs-
    # promote-vs-remove, so it needs the true current state, not up to 8s
    # old (this function is also called repeatedly in a loop across many
    # events in one sync, where stale data would risk a duplicate insert).
    existing = {
        v["user_id"]: v
        for v in client.table("event_volunteers")
        .select("user_id, selected, synced_from_sheet")
        .eq("event_id", event_id)
        .execute()
        .data
    }
    changed = False
    matched_user_ids = set()

    for team in teams:
        team_no = team["team_no"]
        for p in team["participants"]:
            matched_user_ids.add(p["user_id"])
            if p["user_id"] in existing:
                row = existing[p["user_id"]]
                updates = {}
                if not row.get("synced_from_sheet"):
                    # Confirmed present on the sheet right now — safe for a
                    # FUTURE sync to remove this row if they're ever swapped
                    # out later, same as any other sheet-sourced entry.
                    updates["synced_from_sheet"] = True
                if p["selected"] and not row["selected"]:
                    updates["selected"] = True
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
                "team_no": team_no, "selected": p["selected"],
                "synced_from_sheet": True,
            }).execute()
            existing[p["user_id"]] = {"selected": p["selected"], "synced_from_sheet": True}
            changed = True
            if p["selected"]:
                _send_selected_email(p["user_id"], event_name, comp_name)

    stale_ids = [
        uid for uid, row in existing.items()
        if row.get("synced_from_sheet") and uid not in matched_user_ids
    ]
    if stale_ids:
        client.table("event_volunteers").delete().eq("event_id", event_id).in_("user_id", stale_ids).execute()
        changed = True
        # Only tell people who'd actually been confirmed selected — a
        # pending volunteer going stale was never told they were "on the
        # team" in the first place, so there's nothing to un-notify them of.
        for uid in stale_ids:
            if existing[uid].get("selected"):
                _send_removed_email(uid, event_name, comp_name)

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


def _send_removed_email(user_id, event_name, comp_name):
    # Sync can now remove someone who's been swapped out on the real
    # sheet (see _insert_matched_participants) — without this, they'd only
    # find out by happening to check the app again themselves.
    send_email(
        user_email_by_id.get(user_id),
        f"Team change: {event_name} at {comp_name}",
        f"You're no longer listed for {event_name} at {comp_name} — the "
        f"team roster on the registration sheet has changed. If this "
        f"looks wrong, check with your student in-charge or a host.",
    )


def _teams_caption(teams):
    # Small visibility line so the host sees who's about to be added/synced
    # before actually clicking a write button.
    people = [p for team in teams for p in team["participants"]]
    if not people:
        return None
    parts = [f"{p['name']} ({'selected' if p['selected'] else 'pending'})" for p in people]
    return ":material/group: Registered member(s) found: " + ", ".join(parts)


def render_e2c_import():
    with st.container(border=True):
        st.subheader(":material/travel_explore: Import from E2C sheet")
        st.caption("Reads the club's E2C sheet directly — no link to paste.")
        if st.button("Scan for robotics competitions", icon=":material/search:"):
            try:
                st.session_state.e2c_scan_results = scan_e2c_sheet(client)
                st.session_state.e2c_scan_error = None
            except Exception as e:
                st.session_state.e2c_scan_results = None
                st.session_state.e2c_scan_error = str(e)

        if st.session_state.get("e2c_scan_error"):
            st.error(f"Couldn't read the E2C sheet: {st.session_state.e2c_scan_error}")

        results = st.session_state.get("e2c_scan_results")
        if results is not None:
            if not results:
                st.caption("No robotics competitions found in the current year's tab.")

            already_imported = [c for c in results if c["already_imported"]]
            if already_imported:
                if st.button(
                    f"Sync all {len(already_imported)} already-imported competitions",
                    key="e2c_update_all", icon=":material/sync:",
                    help="Re-reads the sheet fresh, so anyone who signed up since your last Scan is included",
                ):
                    try:
                        # Re-scan fresh rather than reusing the cached results —
                        # cached team data only remembers names that matched a
                        # real member AT SCAN TIME, so replaying it can never
                        # pick up someone who signed up since.
                        fresh_by_name = {c["name"].strip().lower(): c for c in scan_e2c_sheet(client)}
                    except Exception as e:
                        st.error(f"Couldn't re-read the E2C sheet: {e}")
                        fresh_by_name = {}
                    if fresh_by_name:
                        with _safe_write("sync the already-imported competitions"):
                            for comp in already_imported:
                                fresh_comp = fresh_by_name.get(comp["name"].strip().lower(), comp)
                                _sync_competition_from_scan(client, fresh_comp)
                                for e in fresh_comp["events"]:
                                    if e.get("existing_event_id"):
                                        _insert_matched_participants(
                                            client, e.get("existing_event_id"), e.get("teams", []),
                                            e["name"], fresh_comp["name"],
                                        )
                            st.toast(f"Synced {len(already_imported)} competition(s).", icon=":material/check_circle:")
                            st.rerun()

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
                            e["name"].strip().lower() for e in comp["events"] + st.session_state[extra_key]
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
                    for eidx, e in enumerate(comp["events"] + st.session_state[extra_key]):
                        with st.container(border=True):
                            ecol1, ecol2 = st.columns([4, 1])
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
                            if st.button(
                                "Update this competition", key=f"e2c_update_{idx}", icon=":material/sync:",
                                help="Re-reads the sheet fresh — anyone who signed up since your last "
                                     "Scan is included",
                            ):
                                try:
                                    fresh_comp = next(
                                        (c for c in scan_e2c_sheet(client)
                                         if c["name"].strip().lower() == comp["name"].strip().lower()),
                                        comp,
                                    )
                                except Exception as e:
                                    st.error(f"Couldn't re-read the E2C sheet: {e}")
                                    fresh_comp = None
                                if fresh_comp:
                                    with _safe_write(f"update {comp['name']}"):
                                        _sync_competition_from_scan(client, fresh_comp)
                                        for e in fresh_comp["events"]:
                                            if e.get("existing_event_id"):
                                                _insert_matched_participants(
                                                    client, e.get("existing_event_id"), e.get("teams", []),
                                                    e["name"], fresh_comp["name"],
                                                )
                                        st.toast(f"Updated {comp['name']}.", icon=":material/check_circle:")
                                        st.rerun()

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
                                [e for e in comp["events"] if not e["already_imported"]]
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
                                    if st.checkbox(
                                        label, value=not e["flagged"] and not e.get("needs_review"),
                                        key=f"e2c_addevent_{idx}_{e['name']}",
                                    ):
                                        to_add.append(e)
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

def render_competition_card(comp):
    cid = comp["competition_id"]
    links = [l for l in all_links if l["competition_id"] == cid]
    events = [e for e in all_events if e["competition_id"] == cid]
    editing_this = is_host and st.session_state.editing_competition_id == cid

    # key= gives the card a stable "st-key-rkcard_..." CSS class, which
    # the hover animation in app.py targets.
    with st.container(border=True, key=f"rkcard_comp_{cid}"):
        if editing_this:
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
                    with _safe_write("save changes to this competition"):
                        client.table("competitions").update({
                            "name": edit_name.strip(),
                            "venue": edit_venue.strip(),
                            "competition_date": edit_date.isoformat(),
                            "registration_deadline": edit_reg_deadline.isoformat() if edit_reg_deadline else None,
                            "student_incharge": edit_incharge.strip(),
                        }).eq("competition_id", cid).execute()

                        # Links: nothing else references them, so simplest
                        # to just replace the whole set.
                        client.table("competition_links").delete().eq("competition_id", cid).execute()
                        valid_links = [l for l in st.session_state.edit_comp_links if l["url"].strip()]
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

                        # Events: update existing ones in place (so their
                        # volunteer signups survive), insert brand-new ones,
                        # delete any that were removed from the list.
                        kept_ids = {e["event_id"] for e in valid_events if e["event_id"] is not None}
                        original_ids = {e["event_id"] for e in events}
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
                                client.table("competition_events").insert(
                                    {**payload, "competition_id": cid}
                                ).execute()
                            else:
                                client.table("competition_events").update(payload).eq(
                                    "event_id", e["event_id"]
                                ).execute()

                        invalidate_cache()
                        st.session_state.competition_message = ("success", f"Updated {edit_name.strip()}.")
                        st.session_state.editing_competition_id = None
                        st.rerun()
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
                    selected_names = [
                        user_name_by_id.get(v["user_id"], "Unknown")
                        for v in event_volunteers
                        if v.get("selected")
                    ]
                    already_volunteered = any(v["user_id"] == current_user_id for v in event_volunteers)
                    is_eligible = (
                        not comp.get("not_attending")
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
                        teamed = [v for v in event_volunteers if v.get("team_no")]
                        unteamed = [v for v in event_volunteers if not v.get("team_no")]

                        if teamed:
                            st.markdown("**Teams:**")
                            st.caption(
                                ":material/travel_explore: From the E2C sheet — kept in sync "
                                "automatically, so this can change if the sheet does."
                            )
                            teams_by_no = {}
                            for v in teamed:
                                teams_by_no.setdefault(v["team_no"], []).append(v)
                            for team_no in sorted(teams_by_no):
                                members = ", ".join(
                                    user_name_by_id.get(v["user_id"], "Unknown")
                                    + ("" if v.get("selected") else " (pending)")
                                    for v in teams_by_no[team_no]
                                )
                                st.caption(f":material/group: Team {team_no}: {members}")

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
                                v["user_id"]: v.get("team_no") for v in event_volunteers
                            }
                            finalize_ids = st.multiselect(
                                "Finalize volunteers",
                                options=[v["user_id"] for v in event_volunteers],
                                default=already_selected_ids,
                                # Shows which sheet-team someone's on (e.g. "Naitik
                                # Jindal (Team 1)") — a flat name list made it hard to
                                # tell teammates apart while picking who's finalized.
                                format_func=lambda uid: (
                                    f"{user_name_by_id.get(uid, 'Unknown')} (Team {volunteer_team_no_by_uid[uid]})"
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

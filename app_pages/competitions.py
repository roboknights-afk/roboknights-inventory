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

from datetime import date

import streamlit as st

from e2c_import import scan_e2c_sheet
from shared import get_client, send_email

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_grade = st.session_state.current_user_grade
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

GRADES = [6, 7, 8, 9, 10, 11, 12]  # 6 included since E2C-imported events can genuinely be 6th-grade eligible
BLANK_LINK = {"label": "", "url": ""}
BLANK_EVENT = {"event_id": None, "name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}

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

if is_host:
    with st.expander(":material/add_box: Add a competition"):
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
            errors = []
            if not name.strip():
                errors.append("Competition name is required.")
            if not comp_date:
                errors.append("Competition date is required.")
            valid_events = [e for e in st.session_state.new_events if e["name"].strip()]
            if not valid_events:
                errors.append("At least one event is required.")
            for e in valid_events:
                if e["min_grade"] > e["max_grade"]:
                    errors.append(f"Event '{e['name']}': min grade can't be higher than max grade.")

            if errors:
                st.session_state.competition_message = ("error", " ".join(errors))
            else:
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


def _insert_matched_participants(client, event_id, teams):
    # teams: list of team-rows, each a list of {"user_id", "name", "selected"}
    # matched against real members. Re-scanning can only ADD someone or
    # PROMOTE them from pending to selected — never remove or un-select
    # anyone, so this can't silently undo a host's own finalize decision.
    existing = {
        v["user_id"]: v["selected"]
        for v in client.table("event_volunteers").select("user_id, selected").eq("event_id", event_id).execute().data
    }
    for team_no, team in enumerate(teams, start=1):
        for p in team:
            if p["user_id"] in existing:
                if p["selected"] and not existing[p["user_id"]]:
                    client.table("event_volunteers").update({"selected": True}).eq(
                        "event_id", event_id
                    ).eq("user_id", p["user_id"]).execute()
                continue
            client.table("event_volunteers").insert({
                "event_id": event_id, "user_id": p["user_id"],
                "team_no": team_no, "selected": p["selected"],
            }).execute()
            existing[p["user_id"]] = p["selected"]


def _teams_caption(teams):
    # Small visibility line so the host sees who's about to be added/synced
    # before actually clicking a write button.
    people = [p for team in teams for p in team]
    if not people:
        return None
    parts = [f"{p['name']} ({'selected' if p['selected'] else 'pending'})" for p in people]
    return ":material/group: Registered member(s) found: " + ", ".join(parts)


if is_host:
    with st.expander(":material/travel_explore: Import from E2C sheet"):
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
                    f"Update all {len(already_imported)} already-imported competitions",
                    key="e2c_update_all", icon=":material/sync:",
                ):
                    for comp in already_imported:
                        _sync_competition_from_scan(client, comp)
                        for e in comp["events"]:
                            if e.get("existing_event_id"):
                                _insert_matched_participants(client, e.get("existing_event_id"), e.get("teams", []))
                    st.toast(f"Updated {len(already_imported)} competition(s).", icon=":material/check_circle:")
                    st.rerun()

            pending_new = []  # collects each new competition's current widget values

            for idx, comp in enumerate(results):
                with st.container(border=True):
                    if comp["already_imported"]:
                        # --- Already in our database: sync + add-new-events only ---
                        st.markdown(f"**{comp['name']}** _(already imported)_")
                        st.caption(
                            f":material/location_on: {comp['venue'] or '(not found)'}  •  "
                            f":material/event: {comp['date_text'] or '(not found)'}"
                        )
                        if st.button(
                            "Update this competition", key=f"e2c_update_{idx}", icon=":material/sync:",
                            help="Also syncs registered participants for this competition's events",
                        ):
                            _sync_competition_from_scan(client, comp)
                            for e in comp["events"]:
                                if e.get("existing_event_id"):
                                    _insert_matched_participants(client, e.get("existing_event_id"), e.get("teams", []))
                            st.toast(f"Updated {comp['name']}.", icon=":material/check_circle:")
                            st.rerun()

                        new_events = [e for e in comp["events"] if not e["already_imported"]]
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
                                if st.checkbox(label, value=not e["flagged"], key=f"e2c_addevent_{idx}_{e['name']}"):
                                    to_add.append(e)
                                teams_caption = _teams_caption(e.get("teams", []))
                                if teams_caption:
                                    st.caption(teams_caption)
                            if st.button("Add selected events", key=f"e2c_addevents_btn_{idx}", icon=":material/add:"):
                                for e in to_add:
                                    event_result = client.table("competition_events").insert({
                                        "competition_id": comp["existing_id"],
                                        "name": e["name"], "details": e["details"],
                                        "team_size": e["team_size"], "max_teams": e["max_teams"],
                                        "min_grade": e["min_grade"], "max_grade": e["max_grade"],
                                    }).execute()
                                    new_event_id = event_result.data[0]["event_id"]
                                    _insert_matched_participants(client, new_event_id, e.get("teams", []))
                                if to_add:
                                    st.toast(
                                        f"Added {len(to_add)} event(s) to {comp['name']}.",
                                        icon=":material/check_circle:",
                                    )
                                    st.rerun()

                    else:
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
                        event_widgets = []
                        for eidx, e in enumerate(comp["events"]):
                            with st.container(border=True):
                                ecol1, ecol2 = st.columns([4, 1])
                                ecol1.markdown(
                                    f"**{e['name']}**"
                                    + ("  :material/warning: check team size/max teams" if e["flagged"] else "")
                                )
                                include = ecol2.checkbox("Include", value=True, key=f"e2c_incl_{idx}_{eidx}")
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
                if st.button("Import selected", type="primary", key="e2c_import_btn", icon=":material/download:"):
                    errors = []
                    imported = 0
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

                        for e in valid_events:
                            event_result = client.table("competition_events").insert({
                                "competition_id": competition_id,
                                "name": e["name"], "details": e["details"],
                                "team_size": e["team_size"], "max_teams": e["max_teams"],
                                "min_grade": e["min_grade"], "max_grade": e["max_grade"],
                            }).execute()
                            new_event_id = event_result.data[0]["event_id"]
                            _insert_matched_participants(client, new_event_id, e.get("teams", []))
                        imported += 1

                    if errors:
                        st.session_state.competition_message = ("error", " ".join(errors))
                    else:
                        st.session_state.e2c_scan_results = None  # force a fresh scan next time
                        st.session_state.competition_message = (
                            "success", f"Imported {imported} competition(s) from E2C."
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

if st.session_state.volunteer_message:
    st.toast(st.session_state.volunteer_message, icon=":material/check_circle:")
    st.session_state.volunteer_message = None

# Auto-flip: anything whose date has already passed and isn't marked past
# yet gets marked past right now. No scheduled job needed for this one —
# the page gets viewed often enough that a same-day flip is good enough,
# unlike the reminder emails which genuinely need a fixed daily time.
client.table("competitions").update({"is_past": True}).lt(
    "competition_date", date.today().isoformat()
).eq("is_past", False).execute()

st.subheader(":material/list_alt: All competitions")

competitions = client.table("competitions").select("*").order("competition_date").execute().data

comp_search = st.text_input(
    "Search competitions",
    key="comp_search",
    placeholder="Search by name or venue",
    icon=":material/search:",
    label_visibility="collapsed",
)
if comp_search:
    competitions = [
        c for c in competitions
        if comp_search.lower() in (c["name"] + " " + (c.get("venue") or "")).lower()
    ]

if not competitions:
    st.caption("No competitions yet." if not comp_search else "No competitions match your search.")
else:
    all_links = client.table("competition_links").select("*").execute().data
    all_events = client.table("competition_events").select("*").execute().data
    all_volunteers = client.table("event_volunteers").select("*").execute().data

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
                    errors = []
                    if not edit_name.strip():
                        errors.append("Competition name is required.")
                    if not edit_date:
                        errors.append("Competition date is required.")
                    valid_events = [e for e in st.session_state.edit_comp_events if e["name"].strip()]
                    if not valid_events:
                        errors.append("At least one event is required.")
                    for e in valid_events:
                        if e["min_grade"] > e["max_grade"]:
                            errors.append(f"Event '{e['name']}': min grade can't be higher than max grade.")

                    if errors:
                        st.session_state.competition_message = ("error", " ".join(errors))
                    else:
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

                        st.session_state.competition_message = ("success", f"Updated {edit_name.strip()}.")
                        st.session_state.editing_competition_id = None
                        st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_comp_{cid}", icon=":material/close:"):
                    st.session_state.editing_competition_id = None
                    st.rerun()

            else:
                # --- Read-only view (everyone) ------------------------------
                if is_host and st.session_state.deleting_competition_id == cid:
                    achievement_count = len(
                        client.table("achievements").select("achievement_id")
                        .eq("competition_id", cid).execute().data
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
                        client.table("competitions").delete().eq("competition_id", cid).execute()
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
                            client.table("competitions").update({"not_attending": False}).eq(
                                "competition_id", cid
                            ).execute()
                            st.rerun()
                    else:
                        if going_col.button(
                            "Not going", key=f"notgoing_comp_{cid}", icon=":material/event_busy:",
                            help="Mark that RoboKnights isn't attending this competition",
                        ):
                            client.table("competitions").update({"not_attending": True}).eq(
                                "competition_id", cid
                            ).execute()
                            st.rerun()
                    if comp.get("is_past"):
                        if past_col.button(
                            "Restore", key=f"unpast_comp_{cid}", icon=":material/undo:",
                            help="Move this competition back to the upcoming list",
                        ):
                            client.table("competitions").update({"is_past": False}).eq(
                                "competition_id", cid
                            ).execute()
                            st.rerun()
                    else:
                        if past_col.button(
                            "Mark past", key=f"mark_past_comp_{cid}", icon=":material/history:",
                            help="Move this competition to the Past section",
                        ):
                            client.table("competitions").update({"is_past": True}).eq(
                                "competition_id", cid
                            ).execute()
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
                    st.caption(":material/event_busy: RoboKnights is not attending this competition.")

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
                                    if st.button(
                                        "Withdraw", key=f"withdraw_{e['event_id']}", icon=":material/close:"
                                    ):
                                        client.table("event_volunteers").delete().eq(
                                            "event_id", e["event_id"]
                                        ).eq("user_id", current_user_id).execute()
                                        st.session_state.volunteer_message = f"Withdrew from {e['name']}."
                                        st.rerun()
                                else:
                                    if st.button(
                                        "Volunteer",
                                        key=f"volunteer_{e['event_id']}",
                                        icon=":material/front_hand:",
                                        type="primary",
                                    ):
                                        client.table("event_volunteers").insert({
                                            "event_id": e["event_id"],
                                            "user_id": current_user_id,
                                        }).execute()
                                        st.session_state.volunteer_message = f"You volunteered for {e['name']}!"
                                        st.rerun()

                            # Host-only: finalize who's actually selected,
                            # capped at team_size * max_teams. Only newly
                            # selected people (not already-selected ones
                            # re-saved unchanged) get an email.
                            if is_host and event_volunteers:
                                finalize_ids = st.multiselect(
                                    "Finalize volunteers",
                                    options=[v["user_id"] for v in event_volunteers],
                                    default=[v["user_id"] for v in event_volunteers if v.get("selected")],
                                    format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                                    max_selections=cap,
                                    key=f"finalize_{e['event_id']}",
                                )
                                if st.button(
                                    "Save selection", key=f"save_finalize_{e['event_id']}",
                                    icon=":material/check:",
                                ):
                                    previously_selected = {
                                        v["user_id"] for v in event_volunteers if v.get("selected")
                                    }
                                    newly_selected = set(finalize_ids) - previously_selected
                                    newly_deselected = previously_selected - set(finalize_ids)

                                    for uid in newly_selected:
                                        client.table("event_volunteers").update({"selected": True}).eq(
                                            "event_id", e["event_id"]
                                        ).eq("user_id", uid).execute()
                                        send_email(
                                            user_email_by_id.get(uid),
                                            f"You're selected: {e['name']} at {comp['name']}",
                                            f"You've been selected to represent RoboKnights in "
                                            f"{e['name']} at {comp['name']}.\n\n"
                                            f"Log in to the app for full details.",
                                        )
                                    for uid in newly_deselected:
                                        client.table("event_volunteers").update({"selected": False}).eq(
                                            "event_id", e["event_id"]
                                        ).eq("user_id", uid).execute()

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
                                    date.fromisoformat(comp["competition_date"]) - date.today()
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
                                            client.table("event_volunteers").update(
                                                {"bot_status": new_status.strip()}
                                            ).eq("event_id", e["event_id"]).eq(
                                                "user_id", current_user_id
                                            ).execute()
                                            st.session_state.volunteer_message = "Bot status updated."
                                            st.rerun()

    upcoming = [c for c in competitions if not c.get("is_past")]
    past = [c for c in competitions if c.get("is_past")]

    for comp in upcoming:
        render_competition_card(comp)

    if past:
        with st.expander(f":material/history: Past competitions ({len(past)})"):
            for comp in reversed(past):  # most recently past first
                render_competition_card(comp)

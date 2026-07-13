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

from shared import get_client, send_email

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_grade = st.session_state.current_user_grade
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

GRADES = [7, 8, 9, 10, 11, 12]
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

if st.session_state.competition_message:
    kind, text = st.session_state.competition_message
    (st.success if kind == "success" else st.error)(text)
    st.session_state.competition_message = None

# --- Everyone: browse all competitions --------------------------------------
# Everyone sees every competition and every event/link on it. Grade
# eligibility controls whether a "Volunteer" button appears on a given
# event — mirrors the existing "Request this" pattern on the Inventory page
# (only shown when you're allowed to act). Hosts additionally get an Edit
# button on each competition.

if "volunteer_message" not in st.session_state:
    st.session_state.volunteer_message = None

if st.session_state.volunteer_message:
    st.success(st.session_state.volunteer_message)
    st.session_state.volunteer_message = None

st.subheader(":material/list_alt: All competitions")

competitions = client.table("competitions").select("*").order("competition_date").execute().data

if not competitions:
    st.caption("No competitions yet.")
else:
    all_links = client.table("competition_links").select("*").execute().data
    all_events = client.table("competition_events").select("*").execute().data
    all_volunteers = client.table("event_volunteers").select("*").execute().data

    for comp in competitions:
        cid = comp["competition_id"]
        links = [l for l in all_links if l["competition_id"] == cid]
        events = [e for e in all_events if e["competition_id"] == cid]
        editing_this = is_host and st.session_state.editing_competition_id == cid

        with st.container(border=True):
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
                title_col, edit_col = st.columns([5, 1])
                title_col.markdown(f"### {comp['name']}")
                if is_host:
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
                        volunteer_names = [
                            user_name_by_id.get(v["user_id"], "Unknown") for v in event_volunteers
                        ]
                        selected_names = [
                            user_name_by_id.get(v["user_id"], "Unknown")
                            for v in event_volunteers
                            if v.get("selected")
                        ]
                        already_volunteered = any(v["user_id"] == current_user_id for v in event_volunteers)
                        is_eligible = (
                            current_user_grade is not None
                            and e["min_grade"] <= current_user_grade <= e["max_grade"]
                        )
                        cap = e["team_size"] * e["max_teams"]

                        with st.container(border=True):
                            st.markdown(
                                f"**{e['name']}** — {grade_range}, {e['team_size']} per team, "
                                f"up to {e['max_teams']} team(s)"
                            )
                            if e.get("details"):
                                st.caption(e["details"])

                            if selected_names:
                                st.caption(
                                    f":material/verified: Selected ({len(selected_names)}/{cap}): "
                                    + ", ".join(selected_names)
                                )
                            if volunteer_names:
                                st.caption(
                                    f":material/group: Volunteers ({len(volunteer_names)}): "
                                    + ", ".join(volunteer_names)
                                )
                            else:
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

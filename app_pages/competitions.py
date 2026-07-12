# Chunk 1 of the Competitions feature: a host-only form to add a competition
# (with its links and events), and a read-only "browse everything" view for
# every logged-in member. Chunk 3 adds students volunteering for events
# they're eligible for by grade. Notifications and finalizing volunteers are
# still later chunks, built one at a time per the usual house rule.

from datetime import date

import streamlit as st

from shared import get_client

client = get_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_grade = st.session_state.current_user_grade
user_name_by_id = st.session_state.user_name_by_id

GRADES = [7, 8, 9, 10, 11, 12]

st.title("Competitions")

# --- Host-only: add a competition ------------------------------------------
# Links and events are variable-length lists, so they're kept in
# session_state as plain lists of dicts — "Add another" appends a blank
# entry and reruns, same rerun-to-update-the-UI pattern used everywhere
# else in this app (e.g. the Approve two-step confirm on the Inventory page).

if "new_links" not in st.session_state:
    st.session_state.new_links = [{"label": "", "url": ""}]
if "new_events" not in st.session_state:
    st.session_state.new_events = [
        {"name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}
    ]
if "competition_added_message" not in st.session_state:
    st.session_state.competition_added_message = None

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
            st.session_state.new_links.append({"label": "", "url": ""})
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
            st.session_state.new_events.append(
                {"name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}
            )
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
                st.session_state.competition_added_message = ("error", " ".join(errors))
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

                st.session_state.competition_added_message = ("success", f"Added {name.strip()}.")
                # Reset the form's lists back to one blank row each.
                st.session_state.new_links = [{"label": "", "url": ""}]
                st.session_state.new_events = [
                    {"name": "", "details": "", "team_size": 1, "max_teams": 1, "min_grade": 7, "max_grade": 12}
                ]
                st.rerun()

if st.session_state.competition_added_message:
    kind, text = st.session_state.competition_added_message
    (st.success if kind == "success" else st.error)(text)
    st.session_state.competition_added_message = None

# --- Everyone: browse all competitions --------------------------------------
# Everyone sees every competition and every event/link on it. Grade
# eligibility controls whether a "Volunteer" button appears on a given
# event — mirrors the existing "Request this" pattern on the Inventory page
# (only shown when you're allowed to act).

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
        links = [l for l in all_links if l["competition_id"] == comp["competition_id"]]
        events = [e for e in all_events if e["competition_id"] == comp["competition_id"]]

        with st.container(border=True):
            st.markdown(f"### {comp['name']}")

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
                # A link typed without http(s):// (e.g. "discord.com") would
                # otherwise be treated as relative to the app's own URL,
                # sending clicks to localhost:8501/discord.com instead of
                # the real site.
                st.markdown(
                    "**Links:** "
                    + "  •  ".join(
                        f"[{l['label']}]({l['url'] if l['url'].startswith(('http://', 'https://')) else 'https://' + l['url']})"
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
                    volunteer_names = [user_name_by_id.get(v["user_id"], "Unknown") for v in event_volunteers]
                    already_volunteered = any(v["user_id"] == current_user_id for v in event_volunteers)
                    is_eligible = (
                        current_user_grade is not None
                        and e["min_grade"] <= current_user_grade <= e["max_grade"]
                    )

                    with st.container(border=True):
                        st.markdown(
                            f"**{e['name']}** — {grade_range}, {e['team_size']} per team, "
                            f"up to {e['max_teams']} team(s)"
                        )
                        if e.get("details"):
                            st.caption(e["details"])

                        if volunteer_names:
                            st.caption(
                                f":material/group: Volunteers ({len(volunteer_names)}): "
                                + ", ".join(volunteer_names)
                            )
                        else:
                            st.caption(":material/group: No volunteers yet")

                        # Only shown when eligible — same pattern as the
                        # "Request this" button on Inventory only showing up
                        # when a part is actually requestable by you.
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

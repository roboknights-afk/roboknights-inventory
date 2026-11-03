# Host-only member directory — Exun (RoboKnights' sister club) also gets
# read-only access to the same full data, per an explicit call, but can't
# edit anything. Not linked from anywhere a non-host/non-Exun would see
# it — app.py only adds this page to the nav at all for those two groups —
# but guarded here too in case someone hits the URL directly, since that's
# the one thing st.navigation's page list alone doesn't stop.

from datetime import datetime, timezone

import streamlit as st

from shared import (
    HOST_EMAILS, IST, cached_table, get_client, get_storage_client, invalidate_cache, removal_email,
    removed_emails, safe_write, send_email,
)

is_host = st.session_state.is_host
is_exun = st.session_state.is_exun
is_viewer = st.session_state.is_viewer
is_read_only = st.session_state.is_read_only

if not is_host and not is_exun and not is_viewer:
    st.error("Access only for hosts, Exun, and viewer accounts.")
    st.stop()

# Section, admission no. and phone no. are private to each member
# everywhere else in the app; this directory is the one place they're
# shown together. The read-only VIEWER account is a look-around account,
# not a host, so it doesn't get them - it sees the same roster with those
# three columns dropped entirely (not blanked, so it's obvious they're
# not being withheld per-row).
PRIVATE_COLUMNS = ("Section", "Admission no.", "Phone no.")

client = get_client()

st.title(":material/badge: Members")

if "member_edit_message" not in st.session_state:
    st.session_state.member_edit_message = None
if "confirming_delete_member_id" not in st.session_state:
    st.session_state.confirming_delete_member_id = None

if st.session_state.member_edit_message:
    st.toast(st.session_state.member_edit_message, icon=":material/check_circle:")
    st.session_state.member_edit_message = None

# Sorted by name with user_id as a tiebreak — two members can share a name
# (there are duplicates in the real data), and without a deterministic
# secondary key which one shows first would be an unreliable storage-order
# artifact rather than something predictable.
users = sorted(cached_table("users"), key=lambda u: (u["name"], u["user_id"]))


def _removed_column_ready():
    try:
        client.table("access_roles").select("is_removed").limit(1).execute()
        return True
    except Exception:
        return False


def _upcoming_signups(user_id):
    # [(volunteer row, label)] for every competition event this member is
    # signed up or selected for that hasn't happened yet - not marked past,
    # and dated today or later (or not dated yet).
    today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
    events = {e["event_id"]: e for e in cached_table("competition_events")}
    comps = {c["competition_id"]: c for c in cached_table("competitions")}
    found = []
    for v in cached_table("event_volunteers"):
        if v["user_id"] != user_id or v["event_id"] not in events:
            continue
        event = events[v["event_id"]]
        comp = comps.get(event["competition_id"])
        if not comp or comp.get("is_past") or (comp.get("competition_date") or "9999") < today:
            continue
        when = (
            datetime.fromisoformat(comp["competition_date"]).strftime("%d %b")
            if comp.get("competition_date") else "date not set"
        )
        label = f"{'selected' if v.get('selected') else 'signed up'} for {event['name']} at {comp['name']} ({when})"
        found.append((v, label))
    return found


def _remove_members(targets):
    # Lines for the host, one per member. Each member on their own, so one
    # failure doesn't stop the rest.
    admin = get_storage_client()  # service key: the only client that can delete a login
    report = []
    for u in targets:
        name = u["name"]
        email = (u.get("email") or "").strip().lower()
        # 1. Blocked first, so nothing below can leave them able to get back in.
        try:
            client.table("access_roles").upsert({"email": email, "is_removed": True}).execute()
            removed_emails.clear()
        except Exception as e:
            report.append(f"- {name}: NOT removed - couldn't block their email ({e}).")
            continue
        # 2. The email, to their school address and personal one if on file.
        subject, body = removal_email(name)
        to = [a for a in {email, (u.get("personal_email") or "").strip().lower()} if a]
        emailed = []
        for address in to:
            try:
                send_email(address, subject, body, raise_errors=True)
                emailed.append(address)
            except Exception:
                pass
        mail_note = "emailed" if emailed else "NOT emailed (the email failed)"
        # 3. Off every upcoming event, whether the profile below is deleted
        # or only disabled (a disabled one would otherwise stay selected).
        upcoming = _upcoming_signups(u["user_id"])
        taken_off = []
        for v, label in upcoming:
            try:
                client.table("event_volunteers").delete().eq("volunteer_id", v["volunteer_id"]).execute()
                taken_off.append(label)
            except Exception:
                pass
        events_note = (
            f" Taken off {len(taken_off)} upcoming event{'s' if len(taken_off) != 1 else ''}: "
            + "; ".join(taken_off) + "." if taken_off else ""
        )
        if len(taken_off) < len(upcoming):
            events_note += " Couldn't take them off every upcoming event - check Competitions."
        # 4. The profile. Log tables only point at them, so those links are
        # cleared rather than blocking the delete.
        try:
            for table, column in (("ai_chat_messages", "user_id"), ("discord_channel_log", "linked_user_id")):
                try:
                    client.table(table).update({column: None}).eq(column, u["user_id"]).execute()
                except Exception:
                    pass
            client.table("users").delete().eq("user_id", u["user_id"]).execute()
            profile_note = "account deleted"
        except Exception:
            try:
                client.table("users").update({
                    "is_disabled": True,
                    "disabled_at": datetime.now(timezone.utc).isoformat(),
                    "disabled_by": st.session_state.auth_user["email"],
                }).eq("user_id", u["user_id"]).execute()
                profile_note = "account disabled, not deleted (other records still point to it)"
            except Exception:
                profile_note = "profile NOT deleted or disabled (blocked from signing in all the same)"
        # 5. The login itself.
        try:
            if admin is None:
                raise RuntimeError("no service key")
            admin.auth.admin.delete_user(u["user_id"])
        except Exception:
            profile_note += "; their login wasn't deleted, but it's blocked"
        report.append(f"- {name}: {mail_note}, {profile_note}.{events_note}")
    invalidate_cache()
    return ["Removed from the club:"] + report

# --- At-a-glance numbers -----------------------------------------------------
GRADE_OPTIONS = [7, 8, 9, 10, 11, 12]
# Maps the raw DB value (see supabase_schema.sql's users_role_check) to
# what's actually shown/picked in the UI, and back. "" is its own option
# (not just "no selection") since a member with no standing set yet is a
# real, common state — most members had none until a host went through
# and set them by hand — not something to force a choice on immediately.
STANDING_LABELS = {"core_member": "Core member", "member": "Member", "adhoc": "Ad hoc", None: ""}
STANDING_VALUES = {v: k for k, v in STANDING_LABELS.items()}
def _is_missing_details(u):
    # Staff accounts aren't expected to have grade/section/admission no.
    return not u.get("is_staff") and not (
        u.get("grade") and u.get("section") and u.get("admission_no") and u.get("phone_no")
    )


m1, m2, m3, m4 = st.columns(4)
m1.metric("Members", len(users), border=True)
m2.metric(
    "Grades represented", len({u.get("grade") for u in users if u.get("grade")}), border=True
)
m3.metric(
    "Missing details",
    sum(1 for u in users if _is_missing_details(u)),
    border=True,
    help="Students with at least one blank field (staff accounts aren't expected to have grade/section/admission no.)",
)
m4.metric(
    "Verified",
    sum(1 for u in users if u.get("details_verified")),
    border=True,
    help="Students who've confirmed their details in the mandatory \"Verify your details\" popup.",
)

with st.expander(":material/info: About this page"):
    st.markdown(
        "Grade, section, admission no., and phone no. are private to each member "
        "everywhere else in the app — this directory (visible to hosts and Exun) "
        "is the one place they're shown together."
    )
    if is_viewer:
        st.info(
            "You're on a read-only viewer account: section, admission no. and "
            "phone no. are hidden here, and nothing on this page is editable."
        )
    if not is_read_only:
        st.warning(
            "Editing **Institutional email** here only updates this profile record. "
            "It does **not** change their actual login email in Supabase Auth, which "
            "is a separate system this app doesn't have admin access to."
        )

if not users:
    st.caption("No members yet.")
else:
    disabled_users = [u for u in users if u.get("is_disabled")]
    if disabled_users:
        st.warning(
            "**Account(s) disabled:** " + ", ".join(u["name"] for u in disabled_users)
            + " — blocked from logging in until re-enabled below."
        )

    # Search + grade + standing filters above the table.
    search_col, grade_col, standing_col = st.columns([2, 1, 1], vertical_alignment="center")
    member_search = search_col.text_input(
        "Search members",
        key="member_search",
        placeholder="Search by name, email, section or admission no.",
        icon=":material/search:",
        label_visibility="collapsed",
    )
    grade_filter = grade_col.multiselect(
        "Filter by grade", GRADE_OPTIONS, key="member_grade_filter",
        placeholder="All grades", label_visibility="collapsed",
    )
    standing_filter = standing_col.multiselect(
        "Filter by standing",
        ["Core member", "Member", "Ad hoc", "Not set"],
        key="member_standing_filter",
        placeholder="All standings", label_visibility="collapsed",
    )

    # The metrics up top say HOW MANY are missing details / unverified /
    # disabled; this is how to get to those people.
    attention_filter = st.segmented_control(
        "Needs attention", ["All", "Missing details", "Not verified", "Disabled"],
        default="All", key="member_attention_filter", label_visibility="collapsed",
    ) or "All"  # deselecting every pill returns None - treat that as "All"

    visible_users = []
    for u in users:
        haystack = " ".join([
            u["name"], u["email"], str(u.get("section") or ""), str(u.get("admission_no") or ""),
        ]).lower()
        if member_search and member_search.lower() not in haystack:
            continue
        if grade_filter and u.get("grade") not in grade_filter:
            continue
        if attention_filter == "Missing details" and not _is_missing_details(u):
            continue
        if attention_filter == "Not verified" and (u.get("details_verified") or u.get("is_staff")):
            continue
        if attention_filter == "Disabled" and not u.get("is_disabled"):
            continue
        if standing_filter:
            label = STANDING_LABELS.get(u.get("role")) or "Not set"
            if label not in standing_filter:
                continue
        visible_users.append(u)

    if not visible_users:
        st.caption("No members match your search or filter.")
        st.stop()

    st.caption(f"Showing {len(visible_users)} of {len(users)} member(s).")

    table_rows = [
        {
            "Name": u["name"],
            "Role": "Staff" if u.get("is_staff") else "Student",
            "Standing": STANDING_LABELS.get(u.get("role"), u.get("role") or ""),
            "Institutional email": u["email"],
            "Grade": u.get("grade"),
            "Section": u.get("section") or "",
            "Admission no.": u.get("admission_no") or "",
            "Phone no.": u.get("phone_no") or "",
            "Discord ID": u.get("discord_user_id") or "",
            "Verified": bool(u.get("details_verified")),
            # Set on the Your profile page, by the member themselves - a
            # host can see these but not tick them here, same as Role above.
            # Two separate checkboxes because they're two separate facts:
            # someone can have uploaded a photo and still be keeping it off
            # the public site (photo_public defaults false and stays that
            # way until they choose otherwise).
            "Photo": bool(u.get("photo_path")),
            "On site": bool(u.get("photo_public")),
            "Disabled": bool(u.get("is_disabled")),
        }
        for u in visible_users
    ]

    if is_viewer:
        table_rows = [
            {k: v for k, v in row.items() if k not in PRIVATE_COLUMNS}
            for row in table_rows
        ]

    if is_read_only:
        # View-only tiers: the same roster a host sees (minus the private
        # columns for viewers, per above), with no editing at all.
        st.dataframe(
            table_rows, hide_index=True, width="stretch",
            column_config={"Name": st.column_config.TextColumn("Name", pinned=True)},
        )
    else:
        # Editable straight in the table. num_rows="fixed" so hosts can't
        # add/delete rows here — a "member" only ever comes from someone
        # actually signing up.
        edited_rows = st.data_editor(
            table_rows,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Name": st.column_config.TextColumn("Name", width="medium", pinned=True),
                "Role": st.column_config.TextColumn("Role", width="small", disabled=True),
                "Standing": st.column_config.SelectboxColumn(
                    "Standing", width="small",
                    options=["", "Core member", "Member", "Ad hoc"],
                    help="Host-set club standing — separate from Role above, which is "
                         "just staff vs. student.",
                ),
                "Institutional email": st.column_config.TextColumn("Institutional email", width="medium"),
                "Grade": st.column_config.SelectboxColumn("Grade", options=GRADE_OPTIONS, width="small"),
                "Section": st.column_config.TextColumn("Section", width="small"),
                "Admission no.": st.column_config.TextColumn("Admission no.", width="small"),
                "Phone no.": st.column_config.TextColumn("Phone no.", width="small"),
                "Discord ID": st.column_config.TextColumn(
                    "Discord ID", width="small",
                    help="Their numeric Discord User ID — normally self-linked on the Home page.",
                ),
                "Verified": st.column_config.CheckboxColumn(
                    "Verified", width="small",
                    help="Whether they've confirmed their details in the mandatory popup. "
                         "Uncheck to make the popup reappear for them next login (e.g. so "
                         "they can add something they missed).",
                ),
                "Photo": st.column_config.CheckboxColumn(
                    "Photo", width="small", disabled=True,
                    help="Whether they've uploaded a photo on the Your profile page. "
                         "Set by the member, not editable here.",
                ),
                "On site": st.column_config.CheckboxColumn(
                    "On site", width="small", disabled=True,
                    help="Whether their photo is published on roboknights.in. Off by "
                         "default even after uploading — the member ticks this themselves "
                         "on Your profile. Not the same as Photo: someone can upload one "
                         "and keep it dashboard-only.",
                ),
                "Disabled": st.column_config.CheckboxColumn(
                    "Disabled", width="small",
                    help="Blocks them from logging in entirely (they see \"account disabled, "
                         "contact the admin\"). Doesn't touch their data — check again to "
                         "re-enable.",
                ),
            },
            key="members_editor",
        )

        if st.button("Save changes", icon=":material/check:", type="primary"):
            # Row order is preserved by data_editor, so we can zip the rows
            # that were actually shown back up with the edited ones and write
            # only what changed. Zipping against the FILTERED list matters —
            # pairing edits with the unfiltered list would write them to the
            # wrong people whenever a search or grade filter is active.
            changed = 0
            with safe_write("save member changes"):
                for original, edited in zip(visible_users, edited_rows):
                    updates = {}
                    if edited["Name"].strip() != original["name"]:
                        updates["name"] = edited["Name"].strip()
                    if edited["Institutional email"].strip() != original["email"]:
                        updates["email"] = edited["Institutional email"].strip()
                    if edited["Grade"] != original.get("grade"):
                        updates["grade"] = edited["Grade"]
                    if STANDING_VALUES.get(edited["Standing"], edited["Standing"]) != original.get("role"):
                        updates["role"] = STANDING_VALUES.get(edited["Standing"]) or None
                    if edited["Section"].strip() != (original.get("section") or ""):
                        updates["section"] = edited["Section"].strip()
                    if edited["Admission no."].strip() != (original.get("admission_no") or ""):
                        updates["admission_no"] = edited["Admission no."].strip()
                    if edited["Phone no."].strip() != (original.get("phone_no") or ""):
                        updates["phone_no"] = edited["Phone no."].strip()
                    if edited["Discord ID"].strip() != (original.get("discord_user_id") or ""):
                        updates["discord_user_id"] = edited["Discord ID"].strip() or None
                    if edited["Verified"] != bool(original.get("details_verified")):
                        updates["details_verified"] = edited["Verified"]
                    if edited["Disabled"] != bool(original.get("is_disabled")):
                        updates["is_disabled"] = edited["Disabled"]
                        # Who and when, on the row itself as well as in the
                        # audit log. Only written if the migration that adds
                        # these columns has been run - an unknown column
                        # would fail the whole save.
                        if "disabled_at" in original:
                            updates["disabled_at"] = (
                                datetime.now(timezone.utc).isoformat() if edited["Disabled"] else None
                            )
                            updates["disabled_by"] = (
                                st.session_state.auth_user["email"] if edited["Disabled"] else None
                            )

                    if updates:
                        client.table("users").update(updates).eq("user_id", original["user_id"]).execute()
                        changed += 1

                if changed:
                    invalidate_cache()
            st.session_state.member_edit_message = (
                f"Updated {changed} member(s)." if changed else "No changes to save."
            )
            st.rerun()

        with st.expander(":material/person_remove: Delete a member"):
            st.caption(
                "Removes their profile from this app only — it does **not** delete their "
                "actual Supabase Auth login (a separate system this app has no admin access "
                "to, same limitation as editing Institutional email above). They could still "
                "log in afterward, just with no profile data."
            )
            delete_target_id = st.selectbox(
                "Member to delete",
                options=[u["user_id"] for u in users],
                format_func=lambda uid: next(
                    (u["name"] for u in users if u["user_id"] == uid), "Unknown"
                ),
                key="delete_member_target",
                index=None,
                placeholder="Choose a member...",
            )
            if delete_target_id:
                target = next(u for u in users if u["user_id"] == delete_target_id)

                # Some tables don't cascade-delete on a user (see
                # supabase_schema.sql) — parts/requests deliberately, so
                # ownership/loan history can't silently vanish, and Exun
                # channel/query messages since those belong to a shared
                # thread, not just the sender. Deleting would otherwise
                # crash on a raw foreign-key error, so check first and
                # tell the host exactly what to resolve.
                owned_parts = [p for p in cached_table("parts") if p["owner_id"] == delete_target_id]
                their_requests = [
                    r for r in cached_table("requests")
                    if r["requester_id"] == delete_target_id or r["owner_id"] == delete_target_id
                ]
                their_exun_msgs = [
                    m for m in cached_table("exun_channel_messages")
                    if m["sender_id"] == delete_target_id
                ]
                their_query_msgs = [
                    m for m in cached_table("query_messages") if m["sender_id"] == delete_target_id
                ]

                blockers = []
                if owned_parts:
                    blockers.append(f"still owns {len(owned_parts)} part(s) — reassign or delete them on Inventory first")
                if their_requests:
                    blockers.append(f"has {len(their_requests)} borrow request(s) on record, as borrower or lender")
                if their_exun_msgs:
                    blockers.append(f"sent {len(their_exun_msgs)} message(s) in the Exun channel")
                if their_query_msgs:
                    blockers.append(f"sent {len(their_query_msgs)} message(s) in Queries")

                if blockers:
                    st.error(f"Can't delete **{target['name']}** — they " + "; ".join(blockers) + ".")
                elif st.session_state.confirming_delete_member_id == delete_target_id:
                    # Everything else DOES cascade-delete with the user row
                    # (event_volunteers, meeting_rsvps/attendance,
                    # achievements, their own query threads) — summarized
                    # here so the host knows what's actually going away.
                    vol_count = sum(
                        1 for v in cached_table("event_volunteers") if v["user_id"] == delete_target_id
                    )
                    rsvp_count = sum(
                        1 for r in cached_table("meeting_rsvps") if r["user_id"] == delete_target_id
                    )
                    achievement_count = sum(
                        1 for a in cached_table("achievements") if a["user_id"] == delete_target_id
                    )
                    query_thread_count = sum(
                        1 for q in cached_table("queries") if q["student_id"] == delete_target_id
                    )
                    st.warning(
                        f"Delete **{target['name']}**'s profile? This also removes their "
                        f"{vol_count} competition signup(s), {rsvp_count} meeting RSVP(s), "
                        f"{achievement_count} achievement(s), and {query_thread_count} query "
                        f"thread(s). This can't be undone."
                    )
                    confirm_col, cancel_col = st.columns([1, 1])
                    if confirm_col.button(
                        "Confirm delete", icon=":material/delete_forever:", type="primary",
                        key="confirm_delete_member",
                    ):
                        with safe_write(f"delete {target['name']}"):
                            client.table("users").delete().eq("user_id", delete_target_id).execute()
                            invalidate_cache()
                        st.session_state.confirming_delete_member_id = None
                        st.session_state.member_edit_message = f"Deleted {target['name']}."
                        st.rerun()
                    if cancel_col.button("Cancel", icon=":material/close:", key="cancel_delete_member"):
                        st.session_state.confirming_delete_member_id = None
                        st.rerun()
                else:
                    if st.button(
                        "Delete this member", icon=":material/delete:", key="delete_member_btn"
                    ):
                        st.session_state.confirming_delete_member_id = delete_target_id
                        st.rerun()

        # --- Remove kicked members (2026-11-03) ---------------------------
        # For members kicked from the club. For each one picked: their
        # school email is blocked (access_roles.is_removed - no sign-up or
        # Google sign-in again, see app.py), they're emailed, their profile
        # is deleted and so is their login itself (needs the service key).
        # A profile other records still point to (parts, borrows, messages
        # ...) can't be deleted without breaking those, so it's disabled
        # instead - still blocked, still emailed.
        with st.expander(":material/group_remove: Remove kicked members"):
            if st.session_state.get("kick_report"):
                st.info("\n".join(st.session_state.kick_report))
            if not _removed_column_ready():
                st.warning(
                    "Needs a one-time database change first: run the last statement in "
                    "supabase_schema.sql (access_roles.is_removed) in Supabase's SQL editor."
                )
            else:
                st.caption(
                    "Each member picked gets an email saying their dashboard access has been revoked "
                    "as they've been removed from the club. Their account is then deleted, and their "
                    "school email can't be used to sign up or sign in again. They're taken off "
                    "upcoming competition events, and their competition sign-ups, RSVPs and logged "
                    "results go with the account. This can't be undone."
                )
                kick_ids = st.multiselect(
                    "Members to remove",
                    options=[u["user_id"] for u in users if u["email"] not in HOST_EMAILS],
                    format_func=lambda uid: next((u["name"] for u in users if u["user_id"] == uid), "Unknown"),
                    key="kick_member_ids",
                    placeholder="Choose members...",
                )
                if kick_ids:
                    names = ", ".join(next(u["name"] for u in users if u["user_id"] == uid) for uid in kick_ids)
                    # Teams they'd leave a gap in - shown before confirming,
                    # so a replacement (or a word with the organisers) can
                    # be sorted first. The dashboard doesn't do either.
                    clashes = [
                        f"- {next(u['name'] for u in users if u['user_id'] == uid)}: {label}"
                        for uid in kick_ids for _, label in _upcoming_signups(uid)
                    ]
                    if clashes:
                        st.warning(
                            "These members are on upcoming competition events. Removing them takes "
                            "them off, and nobody else is told - find a replacement and update the "
                            "organisers' registration yourself:\n" + "\n".join(clashes)
                        )
                    confirm = st.checkbox(
                        f"Yes, remove {len(kick_ids)} member{'s' if len(kick_ids) != 1 else ''}: {names}",
                        key="kick_confirm",
                    )
                    if st.button(
                        "Remove and email them", icon=":material/group_remove:", type="primary",
                        disabled=not confirm, key="kick_members_btn",
                    ):
                        st.session_state.kick_report = _remove_members(
                            [u for u in users if u["user_id"] in kick_ids]
                        )
                        st.session_state.pop("kick_member_ids", None)
                        st.session_state.pop("kick_confirm", None)
                        st.rerun()

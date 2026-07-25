# The original single-screen app, now living as its own page. Logic is
# unchanged from before the Competitions page existed — only the "getting
# client/current user" part changed, since app.py now computes those once
# and hands them over via st.session_state (see the multi-page guide: page
# files can't just read local variables from app.py, only session_state and
# cached resources).
#
# Laid out as three tabs (All parts / My loans & requests / Add & manage)
# so the page stops being one very long scroll. The add-a-part form used to
# live in the sidebar; it's now in the "Add & manage" tab, keeping every
# inventory action on the page itself.

import uuid
from datetime import date, timedelta

import streamlit as st

from shared import APP_URL, get_client, send_email

client = get_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id
is_host = st.session_state.is_host

# --- One-time messages ---------------------------------------------------
# Streamlit re-runs the whole script on every click, so a normal st.success()
# right before st.rerun() would get wiped out before you ever saw it. Instead
# we stash the message in session_state (it survives reruns), show it once,
# then clear it so it doesn't linger forever.
if "request_message" not in st.session_state:
    st.session_state.request_message = None
if "returned_message" not in st.session_state:
    st.session_state.returned_message = None
if "decision_message" not in st.session_state:
    st.session_state.decision_message = None
if "approving_request_id" not in st.session_state:
    st.session_state.approving_request_id = None
if "editing_part_id" not in st.session_state:
    st.session_state.editing_part_id = None
if "part_edited_message" not in st.session_state:
    st.session_state.part_edited_message = None
if "part_added_message" not in st.session_state:
    st.session_state.part_added_message = None
if "deleted_part_message" not in st.session_state:
    st.session_state.deleted_part_message = None

st.title("RoboKnights Parts Inventory")

# --- Data --------------------------------------------------------------------

if st.session_state.deleted_part_message:
    st.toast(st.session_state.deleted_part_message, icon=":material/delete:")
    st.session_state.deleted_part_message = None

if st.session_state.part_edited_message:
    st.toast(st.session_state.part_edited_message, icon=":material/edit:")
    st.session_state.part_edited_message = None

parts = client.table("parts").select("*").order("part_number").execute().data
part_by_id = {p["part_id"]: p for p in parts}

# Owners can see who their own on-loan part is with, right in the main
# list (not just buried in "Parts I've lent out"). Hosts can see this for
# every part, not just ones they own. Only ever one active "approved"
# request per part, since the Request button already disappears once a
# part is on loan.
if is_host:
    active_loan_by_part_id = {
        r["part_id"]: r
        for r in client.table("requests").select("*").eq("status", "approved").execute().data
    }
else:
    active_loan_by_part_id = {
        r["part_id"]: r
        for r in client.table("requests")
        .select("*")
        .eq("owner_id", current_user_id)
        .eq("status", "approved")
        .execute()
        .data
    }

# Fetched here (not inside the tab where it's displayed) so the metric row
# can show the pending count. Only the pending ones — approved and rejected
# requests don't need action anymore.
my_requests = (
    client.table("requests")
    .select("*")
    .eq("owner_id", current_user_id)
    .eq("status", "pending")
    .order("request_id")
    .execute()
    .data
)

# --- At-a-glance numbers -----------------------------------------------------

available_count = sum(1 for p in parts if p["status"] == "available")
on_loan_count = sum(1 for p in parts if p["status"] == "on loan")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total parts", len(parts), border=True)
m2.metric("Available", available_count, border=True)
m3.metric("On loan", on_loan_count, border=True)
# The one number that means "you need to do something" pulses gold while
# it's non-zero. The keyed container just gives CSS a stable hook
# (st-key-rkpulse_requests) — the rule in app.py does the pulsing.
if my_requests:
    with m4:
        with st.container(key="rkpulse_requests"):
            st.metric("Requests for me", len(my_requests), border=True)
else:
    m4.metric("Requests for me", len(my_requests), border=True)

# If they got here by clicking the "New request" email link, that link ends
# in ?tab=requests. Unlike the password-reset links, this is a normal query
# parameter (not a "#" fragment), so Streamlit reads it natively — no
# JavaScript needed. Rather than an in-page anchor jump (which can't reach
# content inside an unselected tab), that parameter now just opens the
# right tab directly.
LOANS_TAB = "My loans & requests"
came_from_request_email = st.query_params.get("tab") == "requests"
if came_from_request_email:
    st.info(f":material/mail: Opened **{LOANS_TAB}** below — the request is waiting there.")

tab_parts, tab_loans, tab_manage = st.tabs(
    ["All parts", LOANS_TAB, "Add & manage"],
    default=LOANS_TAB if came_from_request_email else None,
)


# --- Tab 1: every part in the inventory --------------------------------------

with tab_parts:
    st.subheader(":material/list_alt: All parts")

    # Search box + quick status filter, side by side above the table.
    search_col, filter_col = st.columns([2, 2], vertical_alignment="center")
    search_text = search_col.text_input(
        "Search parts",
        key="parts_search",
        placeholder="Search by serial no or name",
        icon=":material/search:",
        label_visibility="collapsed",
    )
    status_filter = filter_col.segmented_control(
        "Filter parts",
        ["All", "Available", "On loan", "Mine"],
        default="All",
        key="parts_filter",
        label_visibility="collapsed",
    ) or "All"  # deselecting every pill returns None — treat that as "All"

    visible_parts = []
    for p in parts:
        if search_text and search_text.lower() not in (p["part_number"] + " " + p["name"]).lower():
            continue
        if status_filter == "Available" and p["status"] != "available":
            continue
        if status_filter == "On loan" and p["status"] != "on loan":
            continue
        if status_filter == "Mine" and p["owner_id"] != current_user_id:
            continue
        visible_parts.append(p)

    # Toasts for actions taken on the last run (grouping means these can't
    # hang off a single part row any more).
    if st.session_state.request_message:
        st.toast(st.session_state.request_message, icon=":material/send:")
        st.session_state.request_message = None
    if st.session_state.returned_message:
        st.toast(st.session_state.returned_message, icon=":material/check_circle:")
        st.session_state.returned_message = None

    # Identical parts (same name, same owner) are shown as ONE card with a
    # count — "Johnson 600rpm, 4 available" — rather than four near-identical
    # rows. Under the hood each physical unit is still its own row with its
    # own serial, so two people can borrow two different motors and each is
    # returned independently; the grouping is purely how it's presented.
    part_groups = {}
    for p in visible_parts:
        part_groups.setdefault((p["name"], p["owner_id"]), []).append(p)

    if not parts:
        st.caption("No parts yet — add one from the **Add & manage** tab.")
    elif not visible_parts:
        st.caption("No parts match your search or filter.")
    else:
        # Column headers, lined up with the same widths as the cards below.
        # Qty/Days only appear when something in view is actually requestable —
        # both inputs are hidden on your own parts and on anything already on
        # loan, so otherwise the headers sit above permanently empty columns
        # and read like a bug.
        any_requestable = any(
            p["status"] == "available" and p["owner_id"] != current_user_id for p in visible_parts
        )
        head1, head2, head3, head4, head5, head6 = st.columns([2, 2, 2, 1, 1, 2])
        head1.markdown("**Name**")
        head2.markdown("**Owned by**")
        head3.markdown("**Availability**")
        if any_requestable:
            head4.markdown("**Qty**")
            head5.markdown("**Days**")

    for (group_name, group_owner_id), units in part_groups.items():
        units = sorted(units, key=lambda u: u["part_number"])
        available_units = [u for u in units if u["status"] == "available"]
        on_loan_units = [u for u in units if u["status"] == "on loan"]
        is_mine = group_owner_id == current_user_id

        owner_name = user_name_by_id.get(group_owner_id, "Unknown")
        if is_mine:
            owner_name += " (yours)"

        # Stable per-group key for the hover animation in app.py. Uses the
        # lowest part_id in the group so it doesn't change as units come and go.
        group_key = min(u["part_id"] for u in units)

        with st.container(border=True, key=f"rkcard_group_{group_key}"):
            col1, col2, col3, col4, col5, col6 = st.columns([2, 2, 2, 1, 1, 2], vertical_alignment="center")
            col1.markdown(f"**{group_name}**")
            col2.write(owner_name)
            with col3:
                if available_units:
                    st.badge(
                        f"{len(available_units)} available",
                        icon=":material/check_circle:", color="green",
                    )
                if on_loan_units:
                    st.badge(
                        f"{len(on_loan_units)} on loan",
                        icon=":material/schedule:", color="orange",
                    )

            if available_units and not is_mine:
                # How many units, and for how long. The owner can still change
                # the number of days when they approve.
                qty_wanted = col4.number_input(
                    "Qty", min_value=1, max_value=len(available_units), value=1,
                    key=f"qty_{group_key}", label_visibility="collapsed",
                )
                days_wanted = col5.number_input(
                    "Days", min_value=1, value=7,
                    key=f"days_{group_key}", label_visibility="collapsed",
                )
                if col6.button("Request", key=f"request_{group_key}", icon=":material/send:"):
                    # One request row per physical unit (each is lent and
                    # returned separately), all sharing a group id so the owner
                    # sees a single card and decides once for the whole batch.
                    chosen = available_units[:qty_wanted]
                    group_id = str(uuid.uuid4())
                    client.table("requests").insert([
                        {
                            "part_id": u["part_id"],
                            "requester_id": current_user_id,
                            "owner_id": group_owner_id,
                            "status": "pending",
                            "requested_days": days_wanted,
                            "request_group_id": group_id,
                        }
                        for u in chosen
                    ]).execute()
                    serial_list = ", ".join(u["part_number"] for u in chosen)
                    send_email(
                        user_email_by_id.get(group_owner_id),
                        f"New request for {len(chosen)} × {group_name}",
                        f"{current_user_name} wants to borrow {len(chosen)} × {group_name} "
                        f"({serial_list}) for {days_wanted} day(s).\n\n"
                        f"Approve or reject it here: {APP_URL}/?tab=requests",
                    )
                    st.session_state.request_message = (
                        f"Requested {len(chosen)} × {group_name} — waiting for {owner_name} to approve."
                    )
                    # Reload with fresh data so the lists elsewhere don't show
                    # stale info (e.g. these units still listed as available).
                    st.rerun()

            st.caption(f":material/tag: {', '.join(u['part_number'] for u in units)}")

            # Who's actually got the on-loan ones. Owners see this for their
            # own parts, hosts see it for every part.
            if (is_mine or is_host) and on_loan_units:
                for u in on_loan_units:
                    loan = active_loan_by_part_id.get(u["part_id"])
                    if loan:
                        borrower = user_name_by_id.get(loan["requester_id"], "Unknown")
                        st.caption(f":material/person: {u['part_number']} lent to {borrower}")

            # Per-unit actions (return, delete, host edit) live behind an
            # expander so the card stays readable — they're per physical unit,
            # which is the whole reason each one is still its own row.
            if is_mine or is_host:
                with st.expander(f"Manage units ({len(units)})"):
                    for part in units:
                        is_available = part["status"] == "available"
                        is_on_loan = part["status"] == "on loan"

                        ucol1, ucol2, ucol3 = st.columns([2, 2, 2], vertical_alignment="center")
                        ucol1.write(part["part_number"])
                        if is_available:
                            ucol2.badge("Available", icon=":material/check_circle:", color="green")
                        else:
                            ucol2.badge("On loan", icon=":material/schedule:", color="orange")

                        # The owner can mark their own on-loan part as returned —
                        # finishes the lifecycle: on loan -> returned -> available.
                        # Hosts can do this for ANY part too, as an admin override
                        # (e.g. the owner's away and someone needs to hand a part back).
                        if is_on_loan:
                            if ucol3.button(
                                "Mark as returned", key=f"return_{part['part_id']}",
                                icon=":material/assignment_return:",
                            ):
                                client.table("parts").update({"status": "available"}).eq(
                                    "part_id", part["part_id"]
                                ).execute()
                                # The approved request that put it on loan is done now.
                                # There's only ever one active "approved" request per
                                # part, because a part on loan can't be requested again.
                                client.table("requests").update({"status": "returned"}).eq(
                                    "part_id", part["part_id"]
                                ).eq("status", "approved").execute()
                                st.session_state.returned_message = (
                                    f"Marked {part['part_number']} as returned — it's available again."
                                )
                                st.rerun()

                        # Lets you delete your own part while it's available — not
                        # while it's on loan, so we never silently lose track of who
                        # currently has it. Hosts get the same override, for any part.
                        if is_available:
                            if ucol3.button(
                                "Delete", key=f"delete_{part['part_id']}", icon=":material/delete:"
                            ):
                                # A part can't be deleted while old request rows still
                                # point at it (foreign key), so its request history goes
                                # with it. That's fine — deleting a part means "this
                                # isn't in our inventory anymore," so nor is its history.
                                client.table("requests").delete().eq("part_id", part["part_id"]).execute()
                                client.table("parts").delete().eq("part_id", part["part_id"]).execute()
                                st.session_state.deleted_part_message = (
                                    f"Deleted {part['part_number']} — {part['name']}."
                                )
                                st.rerun()

                        # Host-only admin edit: rename, reassign owner, or flip
                        # status directly, bypassing the normal request/approve/
                        # return flow. Same click-to-open-inline-form pattern as
                        # the Approve flow.
                        if is_host:
                            if st.session_state.editing_part_id == part["part_id"]:
                                st.markdown(f"**Edit {part['part_number']}**")
                                edit_name = st.text_input(
                                    "Name", value=part["name"], key=f"edit_name_{part['part_id']}"
                                )
                                owner_ids = list(user_name_by_id.keys())
                                edit_owner_id = st.selectbox(
                                    "Owner",
                                    owner_ids,
                                    index=owner_ids.index(part["owner_id"]) if part["owner_id"] in owner_ids else 0,
                                    format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                                    key=f"edit_owner_{part['part_id']}",
                                )
                                statuses = ["available", "on loan"]
                                edit_status = st.selectbox(
                                    "Status",
                                    statuses,
                                    index=statuses.index(part["status"]),
                                    key=f"edit_status_{part['part_id']}",
                                )
                                save_col, cancel_col = st.columns([1, 1])
                                if save_col.button(
                                    "Save", key=f"save_edit_{part['part_id']}",
                                    icon=":material/check:", type="primary",
                                ):
                                    client.table("parts").update({
                                        "name": edit_name.strip(),
                                        "owner_id": edit_owner_id,
                                        "status": edit_status,
                                    }).eq("part_id", part["part_id"]).execute()
                                    # Manually flipping an on-loan part back to
                                    # available closes out its active loan too, so it
                                    # doesn't linger in lent-out/borrowed.
                                    if part["status"] == "on loan" and edit_status == "available":
                                        client.table("requests").update({"status": "returned"}).eq(
                                            "part_id", part["part_id"]
                                        ).eq("status", "approved").execute()
                                    st.session_state.part_edited_message = f"Updated {part['part_number']}."
                                    st.session_state.editing_part_id = None
                                    st.rerun()
                                if cancel_col.button(
                                    "Cancel", key=f"cancel_edit_{part['part_id']}", icon=":material/close:"
                                ):
                                    st.session_state.editing_part_id = None
                                    st.rerun()
                            else:
                                if st.button(
                                    "Edit", key=f"edit_{part['part_id']}", icon=":material/edit:"
                                ):
                                    st.session_state.editing_part_id = part["part_id"]
                                    st.rerun()


# --- Tab 2: everything about loans involving me -------------------------------
# "Parts I've lent out" and "What I've borrowed" both read from the same
# place: an "approved" request means that loan is still active. The moment
# it's marked returned (status -> 'returned'), it naturally disappears from
# both lists — nothing extra to track.


def group_by_request(reqs):
    # Units asked for together share a request_group_id, so they read as one
    # loan ("3 × Johnson 600rpm") instead of three identical lines. Anything
    # from before grouping existed has no id and stands alone.
    grouped = {}
    for r in reqs:
        gid = r.get("request_group_id") or f"single-{r['request_id']}"
        grouped.setdefault(gid, []).append(r)
    return grouped


def format_due(req):
    if not req.get("due_date"):
        return "no due date set"
    return date.fromisoformat(req["due_date"]).strftime("%d %b %Y")


def due_badge(target, req):
    # Same "how urgent is this" read as the Home page, so a loan that's
    # nearly up looks the same wherever you run into it.
    if not req.get("due_date"):
        target.badge("No due date", color="grey", icon=":material/help:")
        return
    days_left = (date.fromisoformat(req["due_date"]) - date.today()).days
    if days_left < 0:
        target.badge(f"Overdue by {-days_left}d", color="red", icon=":material/warning:")
    elif days_left == 0:
        target.badge("Due today", color="orange", icon=":material/schedule:")
    elif days_left <= 2:
        target.badge(f"Due in {days_left}d", color="orange", icon=":material/schedule:")
    else:
        target.badge(f"Due in {days_left}d", color="green", icon=":material/check_circle:")


with tab_loans:
    st.subheader(":material/inbox: Requests for my parts")

    # Show the Approve/Reject outcome once, as a toast.
    if st.session_state.decision_message:
        st.toast(st.session_state.decision_message, icon=":material/check_circle:")
        st.session_state.decision_message = None

    # my_requests was already fetched up top (the metric row needed the count).
    if not my_requests:
        st.caption("No pending requests.")

    # Several units asked for in one go share a request_group_id, so they're
    # shown as a single card and approved or rejected together — one decision,
    # one email. Requests made before grouping existed have no group id; each
    # of those is simply a group of one.
    request_groups = {}
    for req in my_requests:
        gid = req.get("request_group_id") or f"single-{req['request_id']}"
        request_groups.setdefault(gid, []).append(req)

    for gid, group_reqs in request_groups.items():
        first = group_reqs[0]
        group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
        if not group_parts:
            continue  # the part was deleted out from under the request
        part_name = group_parts[0]["name"]
        serial_list = ", ".join(p["part_number"] for p in group_parts)
        requester_name = user_name_by_id.get(first["requester_id"], "Unknown")
        # Older requests made before loan durations existed won't have this set.
        requested_days = first.get("requested_days") or 7

        with st.container(border=True, key=f"rkcard_req_{gid}"):
            col1, col2, col3, col4 = st.columns([2, 2, 1, 1], vertical_alignment="center")
            col1.markdown(f"**{len(group_parts)} × {part_name}**")
            col2.write(f"Requested by {requester_name} for {requested_days} day(s)")

            # Approving is two steps: click Approve, then confirm (optionally
            # changing) how many days it's actually approved for. We only know
            # the final due date once that second click happens.
            if st.session_state.approving_request_id == gid:
                approve_days = st.number_input(
                    "Approve for how many days?",
                    min_value=1,
                    value=requested_days,
                    key=f"approve_days_{gid}",
                )
                confirm_col, cancel_col = st.columns([1, 1])
                if confirm_col.button("Confirm approval", key=f"confirm_{gid}", icon=":material/check:"):
                    due_date = date.today() + timedelta(days=approve_days)
                    for r in group_reqs:
                        client.table("requests").update({
                            "status": "approved",
                            "due_date": due_date.isoformat(),
                        }).eq("request_id", r["request_id"]).execute()
                        client.table("parts").update({"status": "on loan"}).eq(
                            "part_id", r["part_id"]
                        ).execute()
                    send_email(
                        user_email_by_id.get(first["requester_id"]),
                        f"Request approved: {len(group_parts)} × {part_name}",
                        f"{current_user_name} approved your request for {len(group_parts)} × {part_name} "
                        f"({serial_list}) for {approve_days} day(s) "
                        f"(until {due_date.strftime('%d %b %Y')}).\n\n"
                        f"Get in touch with them to arrange collection.",
                    )
                    st.session_state.decision_message = (
                        f"Approved. {len(group_parts)} × {part_name} on loan until "
                        f"{due_date.strftime('%d %b %Y')}."
                    )
                    st.session_state.approving_request_id = None
                    st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_{gid}", icon=":material/close:"):
                    st.session_state.approving_request_id = None
                    st.rerun()
            else:
                if col3.button("Approve", key=f"approve_{gid}", icon=":material/check:"):
                    st.session_state.approving_request_id = gid
                    st.rerun()

                if col4.button("Reject", key=f"reject_{gid}", icon=":material/close:"):
                    for r in group_reqs:
                        client.table("requests").update({"status": "rejected"}).eq(
                            "request_id", r["request_id"]
                        ).execute()
                    send_email(
                        user_email_by_id.get(first["requester_id"]),
                        f"Request rejected: {len(group_parts)} × {part_name}",
                        f"{current_user_name} rejected your request for {len(group_parts)} × {part_name} "
                        f"({serial_list}).",
                    )
                    st.session_state.decision_message = (
                        f"Rejected the request for {len(group_parts)} × {part_name}."
                    )
                    st.rerun()

            st.caption(f":material/tag: {serial_list}")

    st.subheader(":material/logout: Parts I've lent out")

    lent_out = (
        client.table("requests")
        .select("*")
        .eq("owner_id", current_user_id)
        .eq("status", "approved")
        .order("due_date")
        .execute()
        .data
    )

    if not lent_out:
        st.caption("You haven't lent out any parts.")
    else:
        for gid, group_reqs in group_by_request(lent_out).items():
            first = group_reqs[0]
            group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
            if not group_parts:
                continue
            borrower_name = user_name_by_id.get(first["requester_id"], "Unknown")
            with st.container(border=True, key=f"rkcard_lent_{gid}"):
                col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
                col1.markdown(f"**{len(group_parts)} × {group_parts[0]['name']}**")
                col2.write(f"Lent to {borrower_name}")
                due_badge(col3, first)
                col3.caption(f"Due {format_due(first)}")
                st.caption(f":material/tag: {', '.join(p['part_number'] for p in group_parts)}")

    st.subheader(":material/login: What I've borrowed")

    borrowed = (
        client.table("requests")
        .select("*")
        .eq("requester_id", current_user_id)
        .eq("status", "approved")
        .order("due_date")
        .execute()
        .data
    )

    if not borrowed:
        st.caption("You haven't borrowed any parts.")
    else:
        for gid, group_reqs in group_by_request(borrowed).items():
            first = group_reqs[0]
            group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
            if not group_parts:
                continue
            owner_name = user_name_by_id.get(first["owner_id"], "Unknown")
            with st.container(border=True, key=f"rkcard_borrowed_{gid}"):
                col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
                col1.markdown(f"**{len(group_parts)} × {group_parts[0]['name']}**")
                col2.write(f"Borrowed from {owner_name}")
                due_badge(col3, first)
                col3.caption(f"Due {format_due(first)}")
                st.caption(f":material/tag: {', '.join(p['part_number'] for p in group_parts)}")


# --- Tab 3: add a part, plus notes about the admin tools ----------------------

with tab_manage:
    st.subheader(":material/add_box: Add a part I own")

    with st.container(border=True):
        new_part_name = st.text_input("Part name (e.g. N20 gear motor)", key="new_part_name")
        new_part_qty = st.number_input("Quantity", min_value=1, value=1, key="new_part_qty")
        st.caption(
            "Serial numbers are assigned automatically (RK-0001, RK-0002, …) — "
            "nothing to type in by hand."
        )
        if st.button("Add part", icon=":material/add:", type="primary"):
            if not new_part_name.strip():
                st.session_state.part_added_message = ("error", "Part name is required.")
            else:
                # Quantity 3 means three separate rows, each with its own serial —
                # so every physical unit can be requested, lent, and returned
                # independently (a single row with a qty number would put the
                # whole batch on loan the moment one person borrows).
                #
                # No typing part numbers by hand — that's how we once got two
                # different parts both called "2". Serials are the LOWEST RK-####
                # numbers not currently in use, so numbers freed up by deleted
                # parts get recycled. Existing parts never get renumbered (their
                # serial may be written on the physical part, or quoted in old
                # emails — it has to stay stable).
                used_numbers = {
                    int(p["part_number"][3:])
                    for p in client.table("parts").select("part_number").execute().data
                    if p["part_number"].startswith("RK-") and p["part_number"][3:].isdigit()
                }
                serials = []
                candidate = 1
                while len(serials) < new_part_qty:
                    if candidate not in used_numbers:
                        serials.append(f"RK-{candidate:04d}")
                    candidate += 1

                client.table("parts").insert([
                    {
                        "part_number": serial,
                        "name": new_part_name.strip(),
                        "owner_id": current_user_id,
                        "status": "available",
                    }
                    for serial in serials
                ]).execute()

                if len(serials) == 1:
                    st.session_state.part_added_message = ("success", f"Added {serials[0]} — {new_part_name.strip()}.")
                else:
                    st.session_state.part_added_message = (
                        "success",
                        f"Added {len(serials)} units of {new_part_name.strip()}: {', '.join(serials)}.",
                    )
            st.rerun()

        # Success pops up as a toast (an animated notification, bottom-right);
        # errors stay put under the form so they can't be missed. Stashed in
        # session_state so it survives the rerun the click causes, same as ever.
        if st.session_state.part_added_message:
            kind, text = st.session_state.part_added_message
            if kind == "success":
                st.toast(text, icon=":material/check_circle:")
            else:
                st.error(text)
            st.session_state.part_added_message = None

    if is_host:
        st.subheader(":material/shield_person: Host tools")
        with st.container(border=True):
            st.markdown(
                "As a host you can **Edit**, **Mark as returned**, or **Delete** "
                "any part — not just your own. Those buttons sit on each part's "
                "row in the **All parts** tab."
            )
            st.caption(
                "Editing a part's status from 'on loan' straight to 'available' "
                "also closes out its active loan, so it stops showing up in the "
                "lent-out and borrowed lists."
            )

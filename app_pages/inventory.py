# The original single-screen app, now living as its own page. Logic is
# unchanged from before the Competitions page existed — only the "getting
# client/current user" part changed, since app.py now computes those once
# and hands them over via st.session_state (see the multi-page guide: page
# files can't just read local variables from app.py, only session_state and
# cached resources).

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
if "requested_part_id" not in st.session_state:
    st.session_state.requested_part_id = None
if "returned_part_id" not in st.session_state:
    st.session_state.returned_part_id = None
if "decision_message" not in st.session_state:
    st.session_state.decision_message = None
if "approving_request_id" not in st.session_state:
    st.session_state.approving_request_id = None
if "editing_part_id" not in st.session_state:
    st.session_state.editing_part_id = None
if "part_edited_message" not in st.session_state:
    st.session_state.part_edited_message = None

# --- Sidebar: account + add a part --------------------------------------------
# Account controls and the add-part form live in the sidebar so the main
# page is purely "the inventory" — less clutter, clearer focus.

if "part_added_message" not in st.session_state:
    st.session_state.part_added_message = None

with st.sidebar:
    st.subheader(":material/add_box: Add a part I own")
    new_part_name = st.text_input("Part name (e.g. N20 gear motor)", key="new_part_name")
    new_part_qty = st.number_input("Quantity", min_value=1, value=1, key="new_part_qty")
    if st.button("Add part", icon=":material/add:"):
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

# --- Main page -----------------------------------------------------------

st.title("RoboKnights Parts Inventory")

# If they got here by clicking the "New request" email link, that link ends
# in ?tab=requests. Unlike the password-reset links, this is a normal query
# parameter (not a "#" fragment), so Streamlit reads it natively — no
# JavaScript needed. "Requests for my parts" further down already has an
# auto-generated #requests-for-my-parts anchor (every st.subheader gets
# one), so a plain link can jump straight to it.
if st.query_params.get("tab") == "requests":
    st.info("You clicked a link about a new request. [Jump to it ↓](#requests-for-my-parts)")

# --- Parts list ------------------------------------------------------------

if "deleted_part_message" not in st.session_state:
    st.session_state.deleted_part_message = None

if st.session_state.deleted_part_message:
    st.toast(st.session_state.deleted_part_message, icon=":material/delete:")
    st.session_state.deleted_part_message = None

if st.session_state.part_edited_message:
    st.toast(st.session_state.part_edited_message, icon=":material/edit:")
    st.session_state.part_edited_message = None

parts = client.table("parts").select("*").order("part_number").execute().data
part_by_id = {p["part_id"]: p for p in parts}

# Owners can see who their own on-loan part is with, right in the main
# list (not just buried in "Parts I've lent out" further down). Hosts can
# see this for every part, not just ones they own. Only ever one active
# "approved" request per part, since the Request button already disappears
# once a part is on loan.
active_loan_by_part_id = {}
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

# Fetched here (not further down where it's displayed) so the metric row
# below can show the pending count. Only the pending ones — approved and
# rejected requests don't need action anymore.
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

m1, m2, m3, m4 = st.columns(4, border=True)
m1.metric("Total parts", len(parts))
m2.metric("Available", available_count)
m3.metric("On loan", on_loan_count)
# The one number that means "you need to do something" pulses gold while
# it's non-zero. The keyed container just gives CSS a stable hook
# (st-key-rkpulse_requests) — the :has() rule in app.py does the pulsing.
if my_requests:
    with m4:
        with st.container(key="rkpulse_requests"):
            st.metric("Requests for me", len(my_requests))
else:
    m4.metric("Requests for me", len(my_requests))

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

if not parts:
    st.caption("No parts yet — add one from the sidebar.")
elif not visible_parts:
    st.caption("No parts match your search or filter.")
else:
    # Column headers, lined up with the same widths as the data rows below.
    head1, head2, head3, head4, head5, head6 = st.columns([1, 2, 2, 2, 1, 2])
    head1.markdown("**Serial no**")
    head2.markdown("**Name**")
    head3.markdown("**Owned by**")
    head4.markdown("**Status**")
    head5.markdown("**Days**")

# One row of columns per part, so each row can have its own button.
for part in visible_parts:
    owner_name = user_name_by_id.get(part["owner_id"], "Unknown")
    if part["owner_id"] == current_user_id:
        owner_name += " (yours)"

    # Only show a Request button if the part is free and it isn't already yours.
    is_available = part["status"] == "available"
    is_mine = part["owner_id"] == current_user_id
    is_on_loan = part["status"] == "on loan"

    # key= gives the card a stable "st-key-rkcard_..." CSS class, which the
    # hover animation in app.py targets.
    with st.container(border=True, key=f"rkcard_part_{part['part_id']}"):
        col1, col2, col3, col4, col5, col6 = st.columns([1, 2, 2, 2, 1, 2], vertical_alignment="center")
        col1.write(part["part_number"])
        col2.write(part["name"])
        col3.write(owner_name)
        if is_available:
            col4.badge("Available", icon=":material/check_circle:", color="green")
        else:
            col4.badge("On loan", icon=":material/schedule:", color="orange")

        # Owner sees this for their own parts, host sees it for every part.
        if (is_mine or is_host) and is_on_loan:
            loan = active_loan_by_part_id.get(part["part_id"])
            if loan:
                st.caption(f":material/person: Lent to {user_name_by_id.get(loan['requester_id'], 'Unknown')}")

        if is_available and not is_mine:
            # The requester says how many days they want it for; the owner
            # gets to keep that number or change it when they approve (below).
            days_wanted = col5.number_input(
                "Days", min_value=1, value=7, key=f"days_{part['part_id']}", label_visibility="collapsed"
            )
            # key= makes each button unique so Streamlit doesn't mix them up.
            if col6.button("Request this", key=f"request_{part['part_id']}", icon=":material/send:"):
                client.table("requests").insert({
                    "part_id": part["part_id"],
                    "requester_id": current_user_id,
                    "owner_id": part["owner_id"],
                    "status": "pending",
                    "requested_days": days_wanted,
                }).execute()
                send_email(
                    user_email_by_id.get(part["owner_id"]),
                    f"New request for {part['part_number']}",
                    f"{current_user_name} wants to borrow your {part['part_number']} ({part['name']}) "
                    f"for {days_wanted} day(s).\n\n"
                    f"Approve or reject it here: {APP_URL}/?tab=requests",
                )
                st.session_state.requested_part_id = part["part_id"]
                # Reload the page with fresh data so the tables below don't
                # show stale info (e.g. this same part still listed as available).
                st.rerun()

        # The owner can mark their own on-loan part as returned — finishes the
        # last step of the lifecycle: on loan -> returned -> available again.
        # Hosts can do this for ANY part too, as an admin override (e.g. the
        # owner's away and someone needs to hand a part back).
        if is_on_loan and (is_mine or is_host):
            if col6.button("Mark as returned", key=f"return_{part['part_id']}", icon=":material/assignment_return:"):
                client.table("parts").update({"status": "available"}).eq("part_id", part["part_id"]).execute()
                # The approved request that put it on loan is done now. There's
                # only ever one active "approved" request per part, because the
                # Request button already disappears once a part is on loan.
                client.table("requests").update({"status": "returned"}).eq(
                    "part_id", part["part_id"]
                ).eq("status", "approved").execute()
                st.session_state.returned_part_id = part["part_id"]
                st.rerun()

        # Lets you delete your own part while it's available — not while it's
        # on loan, so we never silently lose track of who currently has it.
        # Hosts get the same admin override as above, for any part.
        if is_available and (is_mine or is_host):
            if col6.button("Delete", key=f"delete_{part['part_id']}", icon=":material/delete:"):
                # A part can't be deleted while old request rows still point at
                # it (foreign key), so its request history goes with it. That's
                # fine here — deleting a part means "this doesn't exist in our
                # inventory anymore," so its history isn't needed either.
                client.table("requests").delete().eq("part_id", part["part_id"]).execute()
                client.table("parts").delete().eq("part_id", part["part_id"]).execute()
                st.session_state.deleted_part_message = f"Deleted {part['part_number']} — {part['name']}."
                st.rerun()

        # Host-only admin edit: rename, reassign owner, or flip status
        # directly, bypassing the normal request/approve/return flow. Same
        # click-to-open-inline-form pattern as the Approve flow above.
        if is_host:
            if st.session_state.editing_part_id == part["part_id"]:
                st.markdown("**Edit part**")
                edit_name = st.text_input("Name", value=part["name"], key=f"edit_name_{part['part_id']}")
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
                if save_col.button("Save", key=f"save_edit_{part['part_id']}", icon=":material/check:", type="primary"):
                    client.table("parts").update({
                        "name": edit_name.strip(),
                        "owner_id": edit_owner_id,
                        "status": edit_status,
                    }).eq("part_id", part["part_id"]).execute()
                    # Manually flipping an on-loan part back to available
                    # closes out its active loan too, so it doesn't linger in
                    # "Parts I've lent out"/"What I've borrowed".
                    if part["status"] == "on loan" and edit_status == "available":
                        client.table("requests").update({"status": "returned"}).eq(
                            "part_id", part["part_id"]
                        ).eq("status", "approved").execute()
                    st.session_state.part_edited_message = f"Updated {part['part_number']}."
                    st.session_state.editing_part_id = None
                    st.rerun()
                if cancel_col.button("Cancel", key=f"cancel_edit_{part['part_id']}", icon=":material/close:"):
                    st.session_state.editing_part_id = None
                    st.rerun()
            else:
                if st.button("Edit", key=f"edit_{part['part_id']}", icon=":material/edit:"):
                    st.session_state.editing_part_id = part["part_id"]
                    st.rerun()

        # Show "Returned" just once, as a toast.
        if part["part_id"] == st.session_state.returned_part_id:
            st.toast(f"Marked {part['part_number']} as returned — it's available again.", icon=":material/check_circle:")
            st.session_state.returned_part_id = None

        # Show "Requested" just once, as a toast.
        if part["part_id"] == st.session_state.requested_part_id:
            st.toast(f"Requested — waiting for {owner_name} to approve.", icon=":material/send:")
            st.session_state.requested_part_id = None

# --- Requests for my parts ---------------------------------------------------

# Plain text on purpose, no icon prefix — the "Jump to it" link from the
# new-request email hardcodes #requests-for-my-parts as the anchor, and
# that anchor is auto-generated from this exact heading text.
st.subheader("Requests for my parts")

# Show the Approve/Reject outcome once, as a toast.
if st.session_state.decision_message:
    st.toast(st.session_state.decision_message, icon=":material/check_circle:")
    st.session_state.decision_message = None

# my_requests was already fetched up top (the metric row needed the count).
if not my_requests:
    st.caption("No pending requests.")

for req in my_requests:
    part = part_by_id.get(req["part_id"])
    requester_name = user_name_by_id.get(req["requester_id"], "Unknown")
    # Older requests made before loan durations existed won't have this set.
    requested_days = req.get("requested_days") or 7

    with st.container(border=True, key=f"rkcard_req_{req['request_id']}"):
        col1, col2, col3, col4 = st.columns([2, 2, 1, 1], vertical_alignment="center")
        col1.write(f"{part['part_number']} — {part['name']}")
        col2.write(f"Requested by {requester_name} for {requested_days} day(s)")

        # Approving is two steps: click Approve, then confirm (optionally
        # changing) how many days it's actually approved for. We only know
        # the final due date once that second click happens.
        if st.session_state.approving_request_id == req["request_id"]:
            approve_days = st.number_input(
                "Approve for how many days?",
                min_value=1,
                value=requested_days,
                key=f"approve_days_{req['request_id']}",
            )
            confirm_col, cancel_col = st.columns([1, 1])
            if confirm_col.button("Confirm approval", key=f"confirm_{req['request_id']}", icon=":material/check:"):
                due_date = date.today() + timedelta(days=approve_days)
                client.table("requests").update({
                    "status": "approved",
                    "due_date": due_date.isoformat(),
                }).eq("request_id", req["request_id"]).execute()
                client.table("parts").update({"status": "on loan"}).eq("part_id", req["part_id"]).execute()
                send_email(
                    user_email_by_id.get(req["requester_id"]),
                    f"Request approved: {part['part_number']}",
                    f"{current_user_name} approved your request for {part['part_number']} ({part['name']}) "
                    f"for {approve_days} day(s) (until {due_date.strftime('%d %b %Y')}).\n\n"
                    f"Get in touch with them to arrange collection.",
                )
                st.session_state.decision_message = (
                    f"Approved. {part['part_number']} is now on loan until {due_date.strftime('%d %b %Y')}."
                )
                st.session_state.approving_request_id = None
                st.rerun()
            if cancel_col.button("Cancel", key=f"cancel_{req['request_id']}", icon=":material/close:"):
                st.session_state.approving_request_id = None
                st.rerun()
        else:
            if col3.button("Approve", key=f"approve_{req['request_id']}", icon=":material/check:"):
                st.session_state.approving_request_id = req["request_id"]
                st.rerun()

            if col4.button("Reject", key=f"reject_{req['request_id']}", icon=":material/close:"):
                client.table("requests").update({"status": "rejected"}).eq("request_id", req["request_id"]).execute()
                send_email(
                    user_email_by_id.get(req["requester_id"]),
                    f"Request rejected: {part['part_number']}",
                    f"{current_user_name} rejected your request for {part['part_number']} ({part['name']}).",
                )
                st.session_state.decision_message = f"Rejected the request for {part['part_number']}."
                st.rerun()

# --- Parts I've lent out / what I've borrowed ---------------------------------
# Both read from the same place: an "approved" request means that loan is
# still active. The moment it's marked returned (status -> 'returned'),
# it naturally disappears from both lists below — nothing extra to track.


def format_due(req):
    if not req.get("due_date"):
        return "no due date set"
    return date.fromisoformat(req["due_date"]).strftime("%d %b %Y")


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
    for req in lent_out:
        part = part_by_id.get(req["part_id"])
        borrower_name = user_name_by_id.get(req["requester_id"], "Unknown")
        with st.container(border=True, key=f"rkcard_lent_{req['request_id']}"):
            col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
            col1.write(f"{part['part_number']} — {part['name']}")
            col2.write(f"Lent to {borrower_name}")
            col3.write(f"Due {format_due(req)}")

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
    for req in borrowed:
        part = part_by_id.get(req["part_id"])
        owner_name = user_name_by_id.get(req["owner_id"], "Unknown")
        with st.container(border=True, key=f"rkcard_borrowed_{req['request_id']}"):
            col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
            col1.write(f"{part['part_number']} — {part['name']}")
            col2.write(f"Borrowed from {owner_name}")
            col3.write(f"Due {format_due(req)}")

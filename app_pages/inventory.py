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

from shared import (
    APP_URL, cached_table, create_request_thread, get_client, invalidate_cache,
    render_chat_thread, request_thread_id, safe_write, send_email, today_ist,
)

client = get_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id
is_host = st.session_state.is_host
is_read_only = st.session_state.is_read_only

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
if "cancel_message" not in st.session_state:
    st.session_state.cancel_message = None
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
if "confirming_bulk_delete" not in st.session_state:
    st.session_state.confirming_bulk_delete = False
# Which dialog (if any) is open. Same reasoning as the Competitions page: a
# dialog only stays on screen while something re-calls its function each run,
# and every st.rerun() in these forms is a full-app rerun — so the dialogs are
# driven by a flag rather than being called straight from their button, and
# closing one is just clearing its flag.
if "requesting_key" not in st.session_state:
    st.session_state.requesting_key = None
if "show_add_part" not in st.session_state:
    st.session_state.show_add_part = False

st.title("RoboKnights Parts Inventory")

if is_read_only:
    st.info(
        ":material/visibility: Read-only account — you can browse everything "
        "here, but requesting, adding and editing parts are turned off."
    )

# --- Data --------------------------------------------------------------------

if st.session_state.deleted_part_message:
    st.toast(st.session_state.deleted_part_message, icon=":material/delete:")
    st.session_state.deleted_part_message = None

if st.session_state.part_edited_message:
    st.toast(st.session_state.part_edited_message, icon=":material/edit:")
    st.session_state.part_edited_message = None

# Every read on this page goes through the shared 8-second cache, and
# "requests" specifically was being fetched 4 separate times with different
# filters right here alone — one fetch + Python filtering replaces all of
# them. select("*") (not naming the quantity column) so this still works on
# a database where the bulk columns haven't been added yet — a missing
# column then just reads as "1 unit" instead of failing the whole page load.
parts = sorted(cached_table("parts"), key=lambda p: p["part_number"])
part_by_id = {p["part_id"]: p for p in parts}
all_requests = cached_table("requests")
approved_requests = [r for r in all_requests if r["status"] == "approved"]

# Caps how many requests you can have sitting pending at once — nothing
# stopped someone from spamming a dozen joke requests in one go before
# this (same spirit as the Govind fake-MacBooks incident, just the
# request side instead of the add-a-part side). Once one of yours gets
# approved or rejected, it drops out of this count and frees up a slot.
MAX_PENDING_REQUESTS = 5
# Requests I'VE made that nobody has acted on yet. Nothing on the page used
# to show these at all, so a request sent by mistake couldn't be taken back —
# it just sat there using up one of the slots above until the owner happened
# to approve or reject it. They're now their own view in the loans tab, with
# a Cancel button (see VIEW_WAITING).
my_pending = sorted(
    (r for r in all_requests
     if r["requester_id"] == current_user_id and r["status"] == "pending"),
    key=lambda r: r["request_id"],
)
my_pending_count = len(my_pending)

# For bulk items, how many are currently out on loan — derived from the
# outstanding approved requests rather than stored on the part, so the count
# can't drift out of sync with reality. Everyone needs this (it's what makes
# "12 of 50 available" true), so it isn't filtered to my own parts.
on_loan_qty_by_part_id = {}
for r in approved_requests:
    on_loan_qty_by_part_id[r["part_id"]] = (
        on_loan_qty_by_part_id.get(r["part_id"], 0) + (r.get("quantity") or 1)
    )


def bulk_available(part):
    # Total owned minus whatever's currently lent out.
    return (part.get("quantity") or 1) - on_loan_qty_by_part_id.get(part["part_id"], 0)

# Owners can see who their own on-loan part is with, right in the main
# list (not just buried in "Parts I've lent out"). Hosts can see this for
# every part, not just ones they own. Only ever one active "approved"
# request per part, since the Request button already disappears once a
# part is on loan.
if is_host:
    active_loan_by_part_id = {r["part_id"]: r for r in approved_requests}
else:
    active_loan_by_part_id = {
        r["part_id"]: r for r in approved_requests if r["owner_id"] == current_user_id
    }

# Fetched here (not inside the tab where it's displayed) so the metric row
# can show the pending count. Only the pending ones — approved and rejected
# requests don't need action anymore.
my_requests = sorted(
    (r for r in all_requests if r["owner_id"] == current_user_id and r["status"] == "pending"),
    key=lambda r: r["request_id"],
)

# --- Dialogs -----------------------------------------------------------------
# Every "do something" form on this page is a modal now. They used to render
# inline: the Qty/Days inputs sat in every single requestable row (two extra
# widgets per part), and the Approve and host-Edit forms opened in place,
# which made the card you clicked visibly grow and shove the rest of the list
# down the page.
#
# Opening one happens in an on_click callback rather than in the button's `if`
# body, so the flag is set before the tabs render — all three tabs draw in one
# pass, and Streamlit refuses to open two dialogs in the same run.


def _close_request():
    st.session_state.requesting_key = None


def _open_request(key):
    st.session_state.requesting_key = key
    st.session_state.show_add_part = False
    st.session_state.editing_part_id = None
    st.session_state.approving_request_id = None
    # Reset the two inputs, so opening this for a different part doesn't
    # inherit the last one's numbers (and can't carry a quantity that's above
    # the new part's maximum, which Streamlit would reject).
    st.session_state.pop("request_qty", None)
    st.session_state.pop("request_days", None)


def _close_add_part():
    st.session_state.show_add_part = False


def _open_add_part():
    st.session_state.show_add_part = True
    st.session_state.requesting_key = None
    st.session_state.editing_part_id = None
    st.session_state.approving_request_id = None


def _close_edit_part():
    st.session_state.editing_part_id = None


def _open_edit_part(part_id):
    st.session_state.editing_part_id = part_id
    st.session_state.requesting_key = None
    st.session_state.show_add_part = False
    st.session_state.approving_request_id = None


def _close_approve():
    st.session_state.approving_request_id = None


def _open_approve(gid):
    st.session_state.approving_request_id = gid
    st.session_state.requesting_key = None
    st.session_state.show_add_part = False
    st.session_state.editing_part_id = None


@st.dialog("Request a part", on_dismiss=_close_request)
def render_request_dialog(name, owner_id, owner_name, max_qty, units=None, bulk_part=None):
    # Handles both kinds of part. `units` is the list of individually
    # serialised units to pick from; `bulk_part` is the single loose-item row.
    # Exactly one of the two is passed in — the widgets are identical, only
    # the rows that get written differ.
    st.markdown(f"**{name}** — owned by {owner_name}")
    qty_wanted = st.number_input(
        "How many?", min_value=1, max_value=max_qty, value=1, key="request_qty"
    )
    days_wanted = st.number_input(
        "For how many days?", min_value=1, value=7, key="request_days",
        help="The owner can still change this when they approve.",
    )

    send_col, cancel_col = st.columns([1, 1])
    if send_col.button(
        "Send request", icon=":material/send:", type="primary", key="confirm_request",
    ):
        with safe_write("send this request"):
            if bulk_part is not None:
                # A single request row carrying the quantity — unlike
                # serialised parts there are no individual units to point at.
                # It still gets a group id, purely so the request chat below
                # has something stable to hang off — a bulk request had no
                # group of its own before this.
                group_id = str(uuid.uuid4())
                client.table("requests").insert({
                    "part_id": bulk_part["part_id"],
                    "requester_id": current_user_id,
                    "owner_id": owner_id,
                    "status": "pending",
                    "requested_days": days_wanted,
                    "quantity": qty_wanted,
                    "request_group_id": group_id,
                }).execute()
                serial_list = bulk_part["part_number"]
            else:
                # One request row per physical unit (each is lent and returned
                # separately), all sharing a group id so the owner sees a
                # single card and decides once for the whole batch.
                chosen = units[:qty_wanted]
                group_id = str(uuid.uuid4())
                client.table("requests").insert([
                    {
                        "part_id": u["part_id"],
                        "requester_id": current_user_id,
                        "owner_id": owner_id,
                        "status": "pending",
                        "requested_days": days_wanted,
                        "request_group_id": group_id,
                    }
                    for u in chosen
                ]).execute()
                serial_list = ", ".join(u["part_number"] for u in chosen)

            # A chat between the two people involved, created with the
            # request rather than on demand, so there's always somewhere
            # obvious to sort out details ("which one?", "when can I pick
            # it up?"). Best-effort — see create_request_thread.
            create_request_thread(
                group_id, current_user_id, owner_id, f"{qty_wanted} × {name}",
            )

            invalidate_cache()
            send_email(
                user_email_by_id.get(owner_id),
                f"New request for {qty_wanted} × {name}",
                f"{current_user_name} wants to borrow {qty_wanted} × {name} "
                f"({serial_list}) for {days_wanted} day(s).\n\n"
                f"Approve or reject it here: {APP_URL}/?tab=requests",
            )
            st.session_state.request_message = (
                f"Requested {qty_wanted} × {name} — waiting for {owner_name} to approve."
            )
            _close_request()  # clearing the flag is what closes the dialog
            # Reload with fresh data so the lists elsewhere don't show stale
            # info (e.g. these units still listed as available).
            st.rerun()
    if cancel_col.button("Cancel", icon=":material/close:", key="cancel_request"):
        _close_request()
        st.rerun()


@st.dialog("Add a part I own", on_dismiss=_close_add_part)
def render_add_part_dialog():
    new_part_name = st.text_input("Part name (e.g. N20 gear motor)", key="new_part_name")
    new_part_qty = st.number_input("Quantity", min_value=1, value=1, key="new_part_qty")
    new_part_is_bulk = st.checkbox(
        "Loose item — count them, don't number them individually",
        key="new_part_is_bulk",
        help="For things like XT60 connectors, screws or wire, where nobody "
             "tracks an individual one. Leave unticked for motors, batteries "
             "and anything else you'd label with a serial.",
    )
    if new_part_is_bulk:
        st.caption(
            "Kept as one entry with a running count — people ask for however "
            "many they need."
        )
    else:
        st.caption(
            "Each unit gets its own serial (RK-0001, RK-0002, …) so they're "
            "lent and returned separately. Nothing to type in by hand."
        )

    if st.button("Add part", icon=":material/add:", type="primary", key="confirm_add_part"):
        if not new_part_name.strip():
            # Inline, not stashed for the page behind the dialog — closing it
            # to report this would throw away what was typed.
            st.error("Part name is required.")
        else:
            with safe_write("add this part"):
                # For serialised parts, quantity 3 means three separate rows,
                # each with its own serial — so every physical unit can be
                # requested, lent, and returned independently. A loose item is
                # the opposite: ONE row carrying the count, because nobody
                # tracks an individual XT60.
                #
                # Either way no typing part numbers by hand — that's how we once
                # got two different parts both called "2". Serials are the LOWEST
                # RK-#### numbers not currently in use, so numbers freed up by
                # deleted parts get recycled. Existing parts never get renumbered
                # (their serial may be written on the physical part, or quoted in
                # old emails — it has to stay stable).
                #
                # Deliberately NOT cached_table here, unlike everywhere else on
                # this page: two people adding parts within the same cache
                # window could otherwise both read the same "next free number"
                # and collide on one serial. A fresh, uncached read every time
                # is worth the one extra query for something used to generate
                # a supposedly-unique id.
                used_numbers = {
                    int(p["part_number"][3:])
                    for p in client.table("parts").select("part_number").execute().data
                    if p["part_number"].startswith("RK-") and p["part_number"][3:].isdigit()
                }
                rows_needed = 1 if new_part_is_bulk else new_part_qty
                serials = []
                candidate = 1
                while len(serials) < rows_needed:
                    if candidate not in used_numbers:
                        serials.append(f"RK-{candidate:04d}")
                    candidate += 1

                client.table("parts").insert([
                    {
                        "part_number": serial,
                        "name": new_part_name.strip(),
                        "owner_id": current_user_id,
                        "status": "available",
                        "is_bulk": new_part_is_bulk,
                        "quantity": new_part_qty if new_part_is_bulk else 1,
                    }
                    for serial in serials
                ]).execute()
                invalidate_cache()

                if new_part_is_bulk:
                    st.session_state.part_added_message = (
                        "success",
                        f"Added {new_part_qty} × {new_part_name.strip()} as a loose item ({serials[0]}).",
                    )
                elif len(serials) == 1:
                    st.session_state.part_added_message = (
                        "success", f"Added {serials[0]} — {new_part_name.strip()}."
                    )
                else:
                    st.session_state.part_added_message = (
                        "success",
                        f"Added {len(serials)} units of {new_part_name.strip()}: {', '.join(serials)}.",
                    )
                # Clear the form's own widget values, so reopening the dialog
                # doesn't hand back the part just added.
                for k in ("new_part_name", "new_part_qty", "new_part_is_bulk"):
                    st.session_state.pop(k, None)
                _close_add_part()
                # Inside the safe_write block on purpose: if the insert
                # failed, we want its inline error to stay on screen, not
                # get wiped by a rerun that fires regardless.
                st.rerun()


@st.dialog("Edit part", on_dismiss=_close_edit_part)
def render_edit_part_dialog(part):
    # Host-only admin edit: rename, reassign owner, or flip status directly,
    # bypassing the normal request/approve/return flow.
    st.markdown(f"**{part['part_number']}**")
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
        "Status", statuses, index=statuses.index(part["status"]),
        key=f"edit_status_{part['part_id']}",
    )
    save_col, cancel_col = st.columns([1, 1])
    if save_col.button(
        "Save", key=f"save_edit_{part['part_id']}", icon=":material/check:", type="primary",
    ):
        with safe_write("save these part edits"):
            client.table("parts").update({
                "name": edit_name.strip(),
                "owner_id": edit_owner_id,
                "status": edit_status,
            }).eq("part_id", part["part_id"]).execute()
            # Manually flipping an on-loan part back to available closes out
            # its active loan too, so it doesn't linger in lent-out/borrowed.
            if part["status"] == "on loan" and edit_status == "available":
                client.table("requests").update({"status": "returned"}).eq(
                    "part_id", part["part_id"]
                ).eq("status", "approved").execute()
            invalidate_cache()
            st.session_state.part_edited_message = f"Updated {part['part_number']}."
            _close_edit_part()
            st.rerun()
    if cancel_col.button(
        "Cancel", key=f"cancel_edit_{part['part_id']}", icon=":material/close:"
    ):
        _close_edit_part()
        st.rerun()


@st.dialog("Approve request", on_dismiss=_close_approve)
def render_approve_dialog(gid, group_reqs, part_name, serial_list, unit_count, requested_days, requester_id):
    # Approving is two steps: click Approve, then confirm (optionally
    # changing) how many days it's actually approved for. We only know the
    # final due date once that second click happens.
    requester_name = user_name_by_id.get(requester_id, "Unknown")
    st.markdown(f"**{unit_count} × {part_name}** — requested by {requester_name}")
    st.caption(f":material/tag: {serial_list}")
    approve_days = st.number_input(
        "Approve for how many days?", min_value=1, value=requested_days, key=f"approve_days_{gid}",
    )
    due_preview = today_ist() + timedelta(days=approve_days)
    st.caption(f":material/event: Due back {due_preview.strftime('%d %b %Y')}")

    confirm_col, cancel_col = st.columns([1, 1])
    if confirm_col.button(
        "Confirm approval", key=f"confirm_{gid}", icon=":material/check:", type="primary",
    ):
        with safe_write("approve this request"):
            # today_ist(), not date.today(): approving in the early IST
            # morning (before 5:30 AM) would otherwise store a due date one
            # day EARLIER than the approver intended, since the UTC server's
            # date is still "yesterday".
            due_date = today_ist() + timedelta(days=approve_days)
            for r in group_reqs:
                client.table("requests").update({
                    "status": "approved",
                    "due_date": due_date.isoformat(),
                }).eq("request_id", r["request_id"]).execute()
                # A serialised unit is wholly lent out, so its status flips. A
                # bulk row isn't — only part of the stack goes out — so its
                # availability stays derived from the loans.
                if not part_by_id[r["part_id"]].get("is_bulk"):
                    client.table("parts").update({"status": "on loan"}).eq(
                        "part_id", r["part_id"]
                    ).execute()
            invalidate_cache()
            send_email(
                user_email_by_id.get(requester_id),
                f"Request approved: {unit_count} × {part_name}",
                f"{current_user_name} approved your request for {unit_count} × {part_name} "
                f"({serial_list}) for {approve_days} day(s) "
                f"(until {due_date.strftime('%d %b %Y')}).\n\n"
                f"Get in touch with them to arrange collection.",
            )
            st.session_state.decision_message = (
                f"Approved. {unit_count} × {part_name} on loan until "
                f"{due_date.strftime('%d %b %Y')}."
            )
            _close_approve()
            st.rerun()
    if cancel_col.button("Cancel", key=f"cancel_{gid}", icon=":material/close:"):
        _close_approve()
        st.rerun()


# --- At-a-glance numbers -----------------------------------------------------

# Counted in units, not rows — a bulk row of 50 XT60s is 50 parts, not 1.
total_count = sum((p.get("quantity") or 1) if p.get("is_bulk") else 1 for p in parts)
available_count = sum(
    bulk_available(p) if p.get("is_bulk") else (1 if p["status"] == "available" else 0)
    for p in parts
)
on_loan_count = total_count - available_count

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total parts", total_count, border=True)
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


def _render_serialised_card(group_name, group_owner_id, units):
    # Identical serialised parts (same name, same owner) share one card with
    # a count; each physical unit underneath keeps its own serial and its own
    # lend/return lifecycle.
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
        col1, col2, col3, col4 = st.columns([3, 2, 2, 2], vertical_alignment="center")
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

        if available_units and not is_mine and not is_read_only and my_pending_count < MAX_PENDING_REQUESTS:
            # How many, and for how long, are asked in the dialog — they
            # used to be two number inputs sitting in this row on every
            # requestable part.
            col4.button(
                "Request", key=f"request_{group_key}", icon=":material/send:",
                width="stretch", on_click=_open_request, args=(("group", group_key),),
            )
            if st.session_state.requesting_key == ("group", group_key):
                render_request_dialog(
                    group_name, group_owner_id, owner_name,
                    max_qty=len(available_units), units=available_units,
                )

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
                            with safe_write("mark this part returned"):
                                client.table("parts").update({"status": "available"}).eq(
                                    "part_id", part["part_id"]
                                ).execute()
                                # The approved request that put it on loan is done now.
                                # There's only ever one active "approved" request per
                                # part, because a part on loan can't be requested again.
                                client.table("requests").update({"status": "returned"}).eq(
                                    "part_id", part["part_id"]
                                ).eq("status", "approved").execute()
                                invalidate_cache()
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
                            with safe_write("delete this part"):
                                # A part can't be deleted while old request rows still
                                # point at it (foreign key), so its request history goes
                                # with it. That's fine — deleting a part means "this
                                # isn't in our inventory anymore," so nor is its history.
                                client.table("requests").delete().eq("part_id", part["part_id"]).execute()
                                client.table("parts").delete().eq("part_id", part["part_id"]).execute()
                                invalidate_cache()
                                st.session_state.deleted_part_message = (
                                    f"Deleted {part['part_number']} — {part['name']}."
                                )
                                st.rerun()

                    # Host-only admin edit: rename, reassign owner, or flip
                    # status directly, bypassing the normal request/approve/
                    # return flow. Opens as a dialog — it used to expand
                    # inline here, inside an expander inside a card, which
                    # made the whole list jump every time it opened.
                    if is_host:
                        st.button(
                            "Edit", key=f"edit_{part['part_id']}", icon=":material/edit:",
                            on_click=_open_edit_part, args=(part["part_id"],),
                        )
                        if st.session_state.editing_part_id == part["part_id"]:
                            render_edit_part_dialog(part)


def _render_bulk_card(part):
# --- Bulk / loose items ---------------------------------------------------
# One row, one card, a count instead of serial numbers. There's no
# per-unit lifecycle here: 12 of 50 being out is just 12 units spoken
# for, which is why availability is derived from the loans rather than
# from the single status field serialised parts use.
    total_qty = part.get("quantity") or 1
    free_qty = bulk_available(part)
    out_qty = total_qty - free_qty
    is_mine = part["owner_id"] == current_user_id

    owner_name = user_name_by_id.get(part["owner_id"], "Unknown")
    if is_mine:
        owner_name += " (yours)"

    with st.container(border=True, key=f"rkcard_bulk_{part['part_id']}"):
        col1, col2, col3, col4 = st.columns([3, 2, 2, 2], vertical_alignment="center")
        col1.markdown(f"**{part['name']}**")
        col2.write(owner_name)
        with col3:
            st.badge(
                f"{free_qty} of {total_qty} free",
                icon=":material/check_circle:" if free_qty else ":material/block:",
                color="green" if free_qty else "red",
            )
            if out_qty:
                st.badge(f"{out_qty} on loan", icon=":material/schedule:", color="orange")

        if free_qty > 0 and not is_mine and not is_read_only and my_pending_count < MAX_PENDING_REQUESTS:
            col4.button(
                "Request", key=f"bulkrequest_{part['part_id']}", icon=":material/send:",
                width="stretch", on_click=_open_request,
                args=(("bulk", part["part_id"]),),
            )
            if st.session_state.requesting_key == ("bulk", part["part_id"]):
                render_request_dialog(
                    part["name"], part["owner_id"], owner_name,
                    max_qty=free_qty, bulk_part=part,
                )

        st.caption(f":material/inventory_2: Loose item · {part['part_number']}")

        # Who's holding what, for the owner and hosts.
        if is_mine or is_host:
            out_loans = [
                r for r in approved_requests if r["part_id"] == part["part_id"]
            ]
            for loan in out_loans:
                borrower = user_name_by_id.get(loan["requester_id"], "Unknown")
                st.caption(f":material/person: {loan.get('quantity') or 1} with {borrower}")

            with st.expander("Manage stock"):
                new_total = st.number_input(
                    "Total quantity owned", min_value=out_qty, value=total_qty,
                    key=f"bulktotal_{part['part_id']}",
                    help="Can't go below the number currently on loan.",
                )
                save_col, del_col = st.columns([1, 1])
                if save_col.button(
                    "Save quantity", key=f"bulksave_{part['part_id']}",
                    icon=":material/check:", type="primary",
                ):
                    with safe_write("save the new quantity"):
                        client.table("parts").update({"quantity": new_total}).eq(
                            "part_id", part["part_id"]
                        ).execute()
                        invalidate_cache()
                        st.session_state.part_edited_message = (
                            f"{part['name']} now shows {new_total} in stock."
                        )
                        st.rerun()
                # Same rule as serialised parts: nothing gets deleted while
                # any of it is still out with someone.
                if out_qty == 0:
                    if del_col.button(
                        "Delete", key=f"bulkdelete_{part['part_id']}", icon=":material/delete:"
                    ):
                        with safe_write("delete this item"):
                            client.table("requests").delete().eq("part_id", part["part_id"]).execute()
                            client.table("parts").delete().eq("part_id", part["part_id"]).execute()
                            invalidate_cache()
                            st.session_state.deleted_part_message = f"Deleted {part['name']}."
                            st.rerun()
                else:
                    del_col.caption("Can't delete while some are on loan.")


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
        # A bulk row can be partly out, so its availability comes from the
        # derived count rather than the single status field serialised parts use.
        if p.get("is_bulk"):
            free = bulk_available(p)
            out = (p.get("quantity") or 1) - free
            if status_filter == "Available" and free <= 0:
                continue
            if status_filter == "On loan" and out <= 0:
                continue
        else:
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

    if my_pending_count >= MAX_PENDING_REQUESTS:
        st.info(
            f":material/block: You have {MAX_PENDING_REQUESTS} requests pending already — "
            f"wait for an owner to approve or reject one before requesting more."
        )

    # Identical parts (same name, same owner) are shown as ONE card with a
    # count — "Johnson 600rpm, 4 available" — rather than four near-identical
    # rows. Under the hood each physical unit is still its own row with its
    # own serial, so two people can borrow two different motors and each is
    # returned independently; the grouping is purely how it's presented.
    # Bulk rows are already a stack of their own, so they're shown one card
    # per row; only serialised units get collapsed by (name, owner).
    part_groups = {}
    bulk_parts = []
    for p in visible_parts:
        if p.get("is_bulk"):
            bulk_parts.append(p)
        else:
            part_groups.setdefault((p["name"], p["owner_id"]), []).append(p)

    # One ordered list, so a loose item sorts in among the serialised parts by
    # name instead of every loose item being dumped below all of them (they
    # used to render in two separate loops, one after the other).
    cards = [
        ("group", name, owner_id, units)
        for (name, owner_id), units in part_groups.items()
    ] + [
        ("bulk", p["name"], p["owner_id"], p) for p in bulk_parts
    ]
    cards.sort(key=lambda c: (c[1].lower(), user_name_by_id.get(c[2], "").lower()))

    if not parts:
        st.caption("No parts yet — add one from the **Add & manage** tab.")
    elif not cards:
        st.caption("No parts match your search or filter.")
    else:
        # Headers and cards are deliberately in the same branch, keyed off the
        # same `cards` list. They used to sit in separate blocks — the headers
        # under this if/else, the card loops outside it — which happened to
        # line up only because both were derived from visible_parts.
        head1, head2, head3, head4 = st.columns([3, 2, 2, 2])
        head1.markdown("**Name**")
        head2.markdown("**Owned by**")
        head3.markdown("**Availability**")

        for kind, _name, _owner_id, payload in cards:
            if kind == "group":
                _render_serialised_card(_name, _owner_id, payload)
            else:
                _render_bulk_card(payload)


# --- Tab 2: everything about loans involving me -------------------------------
# "Parts I've lent out" and "What I've borrowed" both read from the same
# place: an "approved" request means that loan is still active. The moment
# it's marked returned (status -> 'returned'), it naturally disappears from
# both lists — nothing extra to track.


def render_request_chat(gid, first, key_prefix):
    # The per-request chat, shown on both sides of a loan (the owner's
    # request card and the borrower's own card) and at both stages
    # (pending, and still-on-loan after approval) — the details worth
    # sorting out don't stop mattering the moment it's approved.
    #
    # Requests made before this feature existed have no thread, and a
    # read-only account has no business in someone's private chat.
    if is_read_only:
        return
    thread_id = request_thread_id(gid)
    if not thread_id:
        return
    with st.expander(":material/chat: Messages about this"):
        render_chat_thread(
            thread_id, [first["requester_id"], first["owner_id"]], key_prefix=key_prefix,
        )


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
    # nearly up looks the same wherever you run into it. today_ist(), not
    # date.today(): the server runs in UTC, where early-IST-morning is
    # still "yesterday" — a loan due today would wrongly show as not yet
    # due (and an overdue one as merely due) until 5:30 AM IST.
    if not req.get("due_date"):
        target.badge("No due date", color="grey", icon=":material/help:")
        return
    days_left = (date.fromisoformat(req["due_date"]) - today_ist()).days
    if days_left < 0:
        target.badge(f"Overdue by {-days_left}d", color="red", icon=":material/warning:")
    elif days_left == 0:
        target.badge("Due today", color="orange", icon=":material/schedule:")
    elif days_left <= 2:
        target.badge(f"Due in {days_left}d", color="orange", icon=":material/schedule:")
    else:
        target.badge(f"Due in {days_left}d", color="green", icon=":material/check_circle:")


with tab_loans:
    # Three stacked sections (incoming requests, lent out, borrowed) used to
    # run one after another down a single long scroll. One filter row now picks
    # which to show, with the counts on the labels — same segmented_control
    # idiom the All parts tab uses for its status filter.
    lent_out = sorted(
        (r for r in approved_requests if r["owner_id"] == current_user_id),
        key=lambda r: (r.get("due_date") or "9999-99-99", r["request_id"]),
    )
    borrowed = sorted(
        (r for r in approved_requests if r["requester_id"] == current_user_id),
        key=lambda r: (r.get("due_date") or "9999-99-99", r["request_id"]),
    )

    VIEW_REQUESTS = f"Requests for me ({len(my_requests)})"
    VIEW_WAITING = f"My requests ({len(group_by_request(my_pending))})"
    VIEW_LENT = f"Lent out ({len(group_by_request(lent_out))})"
    VIEW_BORROWED = f"Borrowed ({len(group_by_request(borrowed))})"
    loans_view = st.segmented_control(
        "Which loans to show",
        [VIEW_REQUESTS, VIEW_WAITING, VIEW_LENT, VIEW_BORROWED],
        default=VIEW_REQUESTS,
        key="loans_view",
        label_visibility="collapsed",
    ) or VIEW_REQUESTS  # deselecting returns None — fall back to the first view

    if loans_view == VIEW_REQUESTS:
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
            # Units asked for. A serialised batch is N rows of 1; a bulk request is
            # one row carrying N — summing the quantities covers both.
            unit_count = sum(r.get("quantity") or 1 for r in group_reqs)

            with st.container(border=True, key=f"rkcard_req_{gid}"):
                col1, col2, col3, col4 = st.columns([2, 2, 1, 1], vertical_alignment="center")
                col1.markdown(f"**{unit_count} × {part_name}**")
                col2.write(f"Requested by {requester_name} for {requested_days} day(s)")

                # Approving is two steps: click Approve, then confirm (optionally
                # changing) how many days it's actually approved for — that second
                # step is a dialog now, so the card stops growing under your cursor
                # and shoving the rest of the list down.
                col3.button(
                    "Approve", key=f"approve_{gid}", icon=":material/check:",
                    width="stretch", on_click=_open_approve, args=(gid,),
                )
                if st.session_state.approving_request_id == gid:
                    render_approve_dialog(
                        gid, group_reqs, part_name, serial_list, unit_count,
                        requested_days, first["requester_id"],
                    )

                if col4.button(
                    "Reject", key=f"reject_{gid}", icon=":material/close:", width="stretch",
                ):
                    with safe_write("reject this request"):
                        for r in group_reqs:
                            client.table("requests").update({"status": "rejected"}).eq(
                                "request_id", r["request_id"]
                            ).execute()
                        invalidate_cache()
                        send_email(
                            user_email_by_id.get(first["requester_id"]),
                            f"Request rejected: {unit_count} × {part_name}",
                            f"{current_user_name} rejected your request for {unit_count} × {part_name} "
                            f"({serial_list}).",
                        )
                        st.session_state.decision_message = (
                            f"Rejected the request for {unit_count} × {part_name}."
                        )
                        st.rerun()

                st.caption(f":material/tag: {serial_list}")
                render_request_chat(gid, first, key_prefix=f"reqchat_{gid}")

    elif loans_view == VIEW_WAITING:
        st.subheader(":material/hourglass_top: My requests")
        st.caption(
            "Requests you've sent that the owner hasn't approved or rejected yet. "
            "Cancelling one frees up a slot straight away."
        )

        if st.session_state.cancel_message:
            st.toast(st.session_state.cancel_message, icon=":material/undo:")
            st.session_state.cancel_message = None

        if not my_pending:
            st.caption("You have no requests waiting on an owner.")
        else:
            for gid, group_reqs in group_by_request(my_pending).items():
                first = group_reqs[0]
                group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
                if not group_parts:
                    continue  # the part was deleted out from under the request
                part_name = group_parts[0]["name"]
                serial_list = ", ".join(p["part_number"] for p in group_parts)
                owner_name = user_name_by_id.get(first["owner_id"], "Unknown")
                unit_count = sum(r.get("quantity") or 1 for r in group_reqs)
                requested_days = first.get("requested_days") or 7

                with st.container(border=True, key=f"rkcard_waiting_{gid}"):
                    col1, col2, col3, col4 = st.columns([2, 2, 2, 2], vertical_alignment="center")
                    col1.markdown(f"**{unit_count} × {part_name}**")
                    col2.write(f"Asked {owner_name} for {requested_days} day(s)")
                    col3.badge("Waiting on owner", icon=":material/schedule:", color="orange")

                    if col4.button(
                        "Cancel request", key=f"cancel_req_{gid}",
                        icon=":material/close:", width="stretch",
                    ):
                        with safe_write("cancel this request"):
                            # 'cancelled', not a deleted row: the request
                            # history is a record like every other status
                            # here, and the owner may already have been
                            # emailed about it. It drops out of their
                            # "Requests for my parts" list either way,
                            # since that only reads 'pending'.
                            for r in group_reqs:
                                client.table("requests").update({"status": "cancelled"}).eq(
                                    "request_id", r["request_id"]
                                ).execute()
                            invalidate_cache()
                            # The owner was emailed when this came in, so
                            # tell them it's withdrawn — otherwise they go
                            # to approve something that no longer exists.
                            send_email(
                                user_email_by_id.get(first["owner_id"]),
                                f"Request cancelled: {unit_count} × {part_name}",
                                f"{current_user_name} cancelled their request for "
                                f"{unit_count} × {part_name} ({serial_list}). "
                                f"No action needed from you.",
                            )
                            st.session_state.cancel_message = (
                                f"Cancelled your request for {unit_count} × {part_name}."
                            )
                            st.rerun()

                    st.caption(f":material/tag: {serial_list}")
                    render_request_chat(gid, first, key_prefix=f"waitchat_{gid}")

    elif loans_view == VIEW_LENT:
        st.subheader(":material/logout: Parts I've lent out")

        if not lent_out:
            st.caption("You haven't lent out any parts.")
        else:
            for gid, group_reqs in group_by_request(lent_out).items():
                first = group_reqs[0]
                group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
                if not group_parts:
                    continue
                borrower_name = user_name_by_id.get(first["requester_id"], "Unknown")
                unit_count = sum(r.get("quantity") or 1 for r in group_reqs)
                is_bulk_loan = group_parts[0].get("is_bulk")
                with st.container(border=True, key=f"rkcard_lent_{gid}"):
                    col1, col2, col3, col4 = st.columns([2, 2, 2, 2], vertical_alignment="center")
                    col1.markdown(f"**{unit_count} × {group_parts[0]['name']}**")
                    col2.write(f"Lent to {borrower_name}")
                    due_badge(col3, first)
                    col3.caption(f"Due {format_due(first)}")
                    # Serialised units are returned one at a time from "Manage units"
                    # on the part card. A bulk loan has no per-unit rows to go to, so
                    # it's closed out from here instead.
                    if is_bulk_loan:
                        if col4.button(
                            "Mark returned", key=f"bulkreturn_{gid}",
                            icon=":material/assignment_return:",
                        ):
                            with safe_write("mark this loan returned"):
                                for r in group_reqs:
                                    client.table("requests").update({"status": "returned"}).eq(
                                        "request_id", r["request_id"]
                                    ).execute()
                                invalidate_cache()
                                st.session_state.returned_message = (
                                    f"{unit_count} × {group_parts[0]['name']} returned — back in stock."
                                )
                                st.rerun()
                    st.caption(f":material/tag: {', '.join(p['part_number'] for p in group_parts)}")
                    render_request_chat(gid, first, key_prefix=f"lentchat_{gid}")

    else:
        st.subheader(":material/login: What I've borrowed")

        if not borrowed:
            st.caption("You haven't borrowed any parts.")
        else:
            for gid, group_reqs in group_by_request(borrowed).items():
                first = group_reqs[0]
                group_parts = [part_by_id[r["part_id"]] for r in group_reqs if r["part_id"] in part_by_id]
                if not group_parts:
                    continue
                owner_name = user_name_by_id.get(first["owner_id"], "Unknown")
                unit_count = sum(r.get("quantity") or 1 for r in group_reqs)
                with st.container(border=True, key=f"rkcard_borrowed_{gid}"):
                    col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
                    col1.markdown(f"**{unit_count} × {group_parts[0]['name']}**")
                    col2.write(f"Borrowed from {owner_name}")
                    due_badge(col3, first)
                    col3.caption(f"Due {format_due(first)}")
                    st.caption(f":material/tag: {', '.join(p['part_number'] for p in group_parts)}")
                    render_request_chat(gid, first, key_prefix=f"borrowchat_{gid}")


# --- Tab 3: add a part, plus notes about the admin tools ----------------------

with tab_manage:
    st.subheader(":material/add_box: Add a part I own")
    st.caption("Everything you own goes in here so other members can borrow it.")
    st.button(
        "Add a part", icon=":material/add:", type="primary", key="open_add_part",
        on_click=_open_add_part, disabled=is_read_only,
    )
    if st.session_state.show_add_part:
        render_add_part_dialog()

    # Success pops up as a toast (an animated notification, bottom-right).
    # Validation errors are shown inside the dialog itself instead, so they
    # can't close the form and throw away what was typed.
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

            st.divider()
            st.markdown("**Bulk delete parts**")
            st.caption(
                "Pick several parts (e.g. a batch of joke or duplicate entries) and "
                "remove them all in one go. Only available parts are listed — "
                "anything on loan has to be returned first, same rule as deleting "
                "one at a time."
            )
            deletable_parts = sorted(
                (p for p in parts if p["status"] == "available"), key=lambda p: p["part_number"]
            )
            part_label_by_id = {
                p["part_id"]: f"{p['part_number']} — {p['name']} "
                               f"(owned by {user_name_by_id.get(p['owner_id'], 'Unknown')})"
                for p in deletable_parts
            }
            bulk_delete_ids = st.multiselect(
                "Parts to delete",
                options=list(part_label_by_id.keys()),
                format_func=lambda pid: part_label_by_id.get(pid, "Unknown"),
                key="bulk_delete_part_ids",
                placeholder="Search and select parts...",
            )
            if bulk_delete_ids:
                if st.session_state.confirming_bulk_delete:
                    names = "; ".join(part_label_by_id[pid] for pid in bulk_delete_ids)
                    st.warning(
                        f"Delete these {len(bulk_delete_ids)} part(s)? {names}. This also "
                        f"deletes their request history. This can't be undone."
                    )
                    confirm_col, cancel_col = st.columns([1, 1])
                    if confirm_col.button(
                        "Confirm delete", icon=":material/delete_forever:", type="primary",
                        key="confirm_bulk_delete_btn",
                    ):
                        with safe_write(f"delete {len(bulk_delete_ids)} part(s)"):
                            for pid in bulk_delete_ids:
                                # Same order as the single-part delete: request
                                # history has to go first, it's what's pointing
                                # at the part (foreign key), not the other way
                                # round.
                                client.table("requests").delete().eq("part_id", pid).execute()
                                client.table("parts").delete().eq("part_id", pid).execute()
                            invalidate_cache()
                        st.session_state.confirming_bulk_delete = False
                        st.session_state.deleted_part_message = f"Deleted {len(bulk_delete_ids)} part(s)."
                        st.rerun()
                    if cancel_col.button(
                        "Cancel", icon=":material/close:", key="cancel_bulk_delete_btn"
                    ):
                        st.session_state.confirming_bulk_delete = False
                        st.rerun()
                else:
                    if st.button(
                        f"Delete {len(bulk_delete_ids)} selected part(s)",
                        icon=":material/delete:", key="bulk_delete_btn",
                    ):
                        st.session_state.confirming_bulk_delete = True
                        st.rerun()

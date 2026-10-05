# Merch: a host-run drive, in two phases. Phase 1 is just registering
# interest — name, username, the number to show on the merch — open until
# the drive's deadline. Phase 2 (payment) is released by a host whenever
# they're ready, and only reaches people who already registered: they see
# the price/QR/UPI details for the first time then, and submit a payment
# screenshot; a host manually approves or rejects it before the order
# counts as paid. Real money from minors, so a human stays in the loop
# rather than just showing a QR and trusting people — see CLAUDE.md's
# earlier note on this exact tradeoff.

import re
from datetime import date, datetime, timezone

import streamlit as st

from shared import (
    HOST_EMAILS, MERCH_HOST_EMAILS, cached_table, get_client, get_storage_client,
    invalidate_cache, render_file_open_and_download, safe_write, send_email,
    sync_merch_orders_to_sheet, today_ist,
)

client = get_client()
storage = get_storage_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_email_by_id = st.session_state.user_email_by_id
current_user_email = user_email_by_id.get(current_user_id)
all_users = cached_table("users")
current_user_row = next((u for u in all_users if u["user_id"] == current_user_id), None)
has_access = bool(current_user_row and current_user_row.get("has_merch_access"))

# A merch co-host (2026-10-01, first use: Yashraj) sees every host control
# on this page — can_manage_merch covers UI visibility everywhere "is_host"
# used to gate it — but isn't a real host anywhere else in the app, and
# none of their actions here write immediately. Every one is staged into
# merch_pending_changes instead and only takes effect once a REAL host
# approves it (render_pending_approvals, below) — same "a human stays in
# the loop" reasoning this file already applies to real money.
is_merch_cohost = bool(current_user_email) and current_user_email in MERCH_HOST_EMAILS and not is_host
can_manage_merch = is_host or is_merch_cohost

# Belt and braces alongside the nav gating in app.py — everyone in the club
# reaches this page (adhocs included), but only people a host has actually
# granted merch access see the drives themselves. A host (or merch
# co-host) always gets through, even without merch access personally, to
# manage the Access list and review registrations.
if not can_manage_merch and not has_access:
    st.title(":material/storefront: Merch")
    st.warning(
        "Sorry — you're not eligible for RoboKnights merch right now. "
        "Contact a senior member, or try again next year."
    )
    st.stop()

BUCKET = "merch-assets"
ALLOWED_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
MAX_MB = 5
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "3XL"]
CASH_CONTACT_NOTE = "Naitik Jindal via WhatsApp at +91 93112 59439"

STATUS_LABELS = {
    "registered": "Registered",
    "pending_review": "Awaiting payment review",
    "cash_awaited": "Cash payment awaited",
    "paid": "Paid",
    "rejected": "Rejected — resubmit",
}
STATUS_BADGE_COLOR = {
    "registered": "blue", "pending_review": "orange", "cash_awaited": "violet",
    "paid": "green", "rejected": "red",
}

st.title(":material/storefront: Merch")
st.caption("Merch drives the club is running right now, and where you stand on each.")

if "merch_message" not in st.session_state:
    st.session_state.merch_message = None
if "show_new_drive" not in st.session_state:
    st.session_state.show_new_drive = False
if "editing_drive_id" not in st.session_state:
    st.session_state.editing_drive_id = None
if "editing_order_id" not in st.session_state:
    st.session_state.editing_order_id = None
if "manual_entry_drive_id" not in st.session_state:
    st.session_state.manual_entry_drive_id = None

drives = sorted(cached_table("merch_drives"), key=lambda d: d["created_at"], reverse=True)
all_orders = cached_table("merch_orders")
orders_by_drive = {}
for o in all_orders:
    orders_by_drive.setdefault(o["drive_id"], []).append(o)

today_iso = today_ist().isoformat()


def _save_image(upload, path_prefix):
    # path_prefix has no extension yet — the real uploaded extension
    # (normalized jpeg->jpg, same as profile.py's photo upload) decides it,
    # rather than every image silently being saved as ".png" regardless of
    # what was actually uploaded.
    raw = upload.getvalue()
    ext = upload.name.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMAGES:
        st.error("That is not a PNG or JPG file.")
        st.stop()
    if len(raw) > MAX_MB * 1024 * 1024:
        st.error(f"That file is {len(raw) / 1024 / 1024:.1f} MB — the limit is {MAX_MB} MB.")
        st.stop()
    path = f"{path_prefix}.{'png' if ext == 'png' else 'jpg'}"
    storage.storage.from_(BUCKET).upload(path, raw, {"content-type": ALLOWED_IMAGES[ext], "upsert": "true"})
    return path


def _price_value(d):
    # PostgREST returns `numeric` columns as JSON strings, not floats
    # (avoids float precision loss) — cast before doing arithmetic or
    # formatting, or this breaks the moment a real price comes back.
    return float(d.get("price") or 0)


def _price_text(d):
    value = _price_value(d)
    return f"₹{value:.0f}" if value == int(value) else f"₹{value:.2f}"


NUMBER_RANGE = range(100)
_RESERVATION_LINE = re.compile(r"^\s*(\d{1,2})\s*[-–:]\s*(.+?)\s*$")


def _reserved_numbers(d):
    # {number: name it's held for} — numbers a host has promised someone
    # outside the app (e.g. claimed on Discord before they registered).
    out = {}
    for k, v in (d.get("reserved_numbers") or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            pass
    return out


def _taken_numbers(d, for_name, orders, exclude_order_id=None):
    # {number: who has it}. A reservation doesn't block the person it's
    # reserved FOR — matched against their account name, not the
    # free-text "Name" box, so nobody can claim one just by typing it.
    taken = {
        n: f"reserved for {who}" for n, who in _reserved_numbers(d).items()
        if (who or "").strip().lower() != (for_name or "").strip().lower()
    }
    for o in orders:
        if o["order_id"] != exclude_order_id:
            taken[o["custom_number"]] = o["name"]
    return taken


def _fresh_drive_orders(drive_id):
    # Straight from the database, not the 8-second cache — used right
    # before saving a number, so two people submitting at the same moment
    # can't both get it.
    return client.table("merch_orders").select("order_id,name,custom_number").eq(
        "drive_id", drive_id
    ).execute().data


def _render_taken_numbers(taken):
    if not taken:
        return
    with st.expander(f":material/block: Numbers already taken ({len(taken)})"):
        st.caption("  ·  ".join(f"**{n:02d}** {who}" for n, who in sorted(taken.items())))


def _pending_changes():
    # merch_pending_changes is a brand-new table (see supabase_schema.sql)
    # that needs a migration run by hand before it exists — degrade to
    # "nothing pending" instead of crashing the whole Merch page for every
    # host on every visit until that migration lands, same defensive
    # pattern as meeting_invitee_rows()/has_unread_queries() elsewhere in
    # this app for exactly this "table added after the pages that read it"
    # situation.
    try:
        return cached_table("merch_pending_changes")
    except Exception:
        return []


def _stage_pending_change(action, summary, payload):
    # What a merch co-host's button click does instead of the real write —
    # record the intent, change nothing yet. requested_by is whoever is
    # logged in right now, always the co-host here (a real host never
    # calls this; see every write site below).
    client.table("merch_pending_changes").insert({
        "requested_by": current_user_id,
        "action": action,
        "summary": summary,
        "payload": payload,
        "status": "pending",
    }).execute()


def _apply_pending_change(action, payload, requested_by):
    # The actual effect of any merch write — shared by a host's own direct
    # action AND a host approving something a co-host submitted, so the
    # two paths can't quietly drift apart. Caller is responsible for the
    # safe_write/invalidate_cache/toast around this.
    if action == "edit_access":
        for uid in payload.get("add", []):
            client.table("users").update({"has_merch_access": True}).eq("user_id", uid).execute()
        for uid in payload.get("remove", []):
            client.table("users").update({"has_merch_access": False}).eq("user_id", uid).execute()
    elif action == "start_drive":
        client.table("merch_drives").insert({**payload, "created_by": requested_by}).execute()
    elif action == "edit_drive":
        client.table("merch_drives").update(payload["update"]).eq("drive_id", payload["drive_id"]).execute()
    elif action == "delete_drive":
        drive = next((dd for dd in drives if dd["drive_id"] == payload["drive_id"]), None)
        if drive and storage is not None:
            stale_paths = [drive["qr_image_path"]] if drive.get("qr_image_path") else []
            if drive.get("size_chart_image_path"):
                stale_paths.append(drive["size_chart_image_path"])
            stale_paths += drive.get("design_image_paths") or []
            if stale_paths:
                try:
                    storage.storage.from_(BUCKET).remove(stale_paths)
                except Exception:
                    pass
        client.table("merch_drives").delete().eq("drive_id", payload["drive_id"]).execute()
    elif action == "manual_order":
        clash = next(
            (x["name"] for x in _fresh_drive_orders(payload["drive_id"])
             if x["custom_number"] == payload["custom_number"]),
            None,
        )
        if clash:
            raise ValueError(f"number {payload['custom_number']:02d} is already {clash}'s")
        client.table("merch_orders").insert(payload).execute()
    elif action == "edit_order":
        new_number = payload["update"].get("custom_number")
        order = next((oo for oo in all_orders if oo["order_id"] == payload["order_id"]), None)
        if order and new_number is not None:
            clash = next(
                (x["name"] for x in _fresh_drive_orders(order["drive_id"])
                 if x["custom_number"] == new_number and x["order_id"] != order["order_id"]),
                None,
            )
            if clash:
                # safe_write turns this into a clean inline error and
                # nothing below runs, so the pending change stays pending.
                raise ValueError(f"number {new_number:02d} now belongs to {clash}")
        client.table("merch_orders").update(payload["update"]).eq("order_id", payload["order_id"]).execute()
    elif action == "approve_order":
        order = next((oo for oo in all_orders if oo["order_id"] == payload["order_id"]), None)
        if order is None:
            return
        # Auto-assign a merch role from the buyer's real club role — but
        # never for a host's own order (their host status isn't a club
        # "role" at all, and the student explicitly didn't want this
        # guessed for them).
        update_row = {"status": "paid"}
        buyer_email = user_email_by_id.get(order["user_id"])
        if buyer_email not in HOST_EMAILS:
            buyer = next((u for u in all_users if u["user_id"] == order["user_id"]), None)
            update_row["merch_role"] = "core" if buyer and buyer.get("role") == "core_member" else "member"
        client.table("merch_orders").update(update_row).eq("order_id", order["order_id"]).execute()
        drive = next((dd for dd in drives if dd["drive_id"] == order["drive_id"]), None)
        recipient = user_email_by_id.get(order["user_id"])
        if recipient and drive:
            send_email(
                recipient, f"Merch order confirmed: {drive['title']}",
                f"Your payment for \"{drive['title']}\" has been confirmed.\n"
                f"Order: #{order['custom_number']}, {order['username']}.",
            )
    elif action == "reject_order":
        order = next((oo for oo in all_orders if oo["order_id"] == payload["order_id"]), None)
        if order is None:
            return
        client.table("merch_orders").update({"status": "rejected"}).eq("order_id", order["order_id"]).execute()
        drive = next((dd for dd in drives if dd["drive_id"] == order["drive_id"]), None)
        recipient = user_email_by_id.get(order["user_id"])
        if recipient and drive:
            if order.get("payment_method") == "cash":
                send_email(
                    recipient, f"Merch payment issue: {drive['title']}",
                    f"Your cash payment for \"{drive['title']}\" couldn't be confirmed. "
                    "Please check the Merch page and get in touch to sort it out.",
                )
            else:
                send_email(
                    recipient, f"Merch order needs a new screenshot: {drive['title']}",
                    f"Your payment screenshot for \"{drive['title']}\" couldn't be confirmed. "
                    "Please check the Merch page and resubmit.",
                )


# --- Host: who has merch access -----------------------------------------
# Same multiselect-diff pattern exun_tasks.py already uses for its
# volunteer list: a picker of real names, pre-filled with who currently has
# access, and Save only writes whatever actually changed.
def render_access_manager():
    eligible = [u for u in all_users if u.get("has_merch_access")]
    with st.expander(f":material/badge: Merch access ({len(eligible)})", expanded=False):
        st.caption(
            "Who can see and register for drives on this page — independent of "
            "club role, so add or remove anyone here directly."
        )
        selected = st.multiselect(
            "Has merch access",
            options=[u["user_id"] for u in all_users],
            default=[u["user_id"] for u in eligible],
            format_func=lambda uid: st.session_state.user_name_by_id.get(uid, "Unknown"),
            key="merch_access_picker",
        )
        if st.button("Save access list", icon=":material/save:", key="save_merch_access"):
            had_access = {u["user_id"] for u in eligible}
            now_access = set(selected)
            added, removed = sorted(now_access - had_access), sorted(had_access - now_access)
            if not added and not removed:
                st.session_state.merch_message = ("error", "No changes to save.")
            elif is_host:
                with safe_write("update merch access"):
                    _apply_pending_change("edit_access", {"add": added, "remove": removed}, current_user_id)
                    invalidate_cache()
                st.session_state.merch_message = ("success", "Merch access updated.")
            else:
                name = lambda uid: st.session_state.user_name_by_id.get(uid, "Unknown")
                summary = (
                    f"Merch access — add: {', '.join(name(u) for u in added) or 'none'}; "
                    f"remove: {', '.join(name(u) for u in removed) or 'none'}"
                )
                with safe_write("request a merch access change"):
                    _stage_pending_change("edit_access", summary, {"add": added, "remove": removed})
                    invalidate_cache()
                st.session_state.merch_message = ("success", "Submitted — a host needs to approve this.")
            st.rerun()


# --- Host: start a drive -----------------------------------------------
# Price/QR/UPI are all set up FRONT here, even though members won't see
# them until a host opens payment collection (below) — no reason to make
# a host re-enter payment details later just because members shouldn't see
# them yet.
@st.dialog("Start a merch drive", on_dismiss=lambda: st.session_state.update(show_new_drive=False))
def render_new_drive():
    title = st.text_input(
        "What's being sold", key="new_drive_title", placeholder="e.g. RoboKnights Hoodie 2026"
    )
    description = st.text_area(
        "Details for members", key="new_drive_description",
        placeholder="Sizes, what's included...",
    )
    price = st.number_input(
        "Price (₹)", min_value=0.0, step=1.0, key="new_drive_price",
    )
    design_uploads = st.file_uploader(
        "Design pictures (optional)", type=list(ALLOWED_IMAGES), key="new_drive_design",
        help=f"What the merch looks like — shown directly on the page. PNG or JPG, up to {MAX_MB} MB each.",
        disabled=storage is None, accept_multiple_files=True,
    )
    size_chart_upload = st.file_uploader(
        "Size chart (optional)", type=list(ALLOWED_IMAGES), key="new_drive_size_chart",
        help=f"Shown directly on the page so people can pick a size. PNG or JPG, up to {MAX_MB} MB.",
        disabled=storage is None,
    )
    upi_id = st.text_input("UPI ID", key="new_drive_upi_id")
    qr_upload = st.file_uploader(
        "UPI payment QR code", type=list(ALLOWED_IMAGES), key="new_drive_qr",
        help=f"PNG or JPG, up to {MAX_MB} MB. Not shown to members until you open payment collection.",
        disabled=storage is None,
    )
    deadline = st.date_input(
        "Last day to register", key="new_drive_deadline", value=date(2026, 10, 1),
    )
    if st.button("Start this drive", icon=":material/storefront:", type="primary", key="confirm_new_drive"):
        if not title.strip():
            st.error("Title is required.")
        elif price <= 0:
            st.error("Enter a price greater than 0.")
        elif not upi_id.strip():
            st.error("UPI ID is required.")
        elif qr_upload is None:
            st.error("Upload the payment QR code.")
        elif storage is None:
            st.error("File uploads aren't set up on this server yet — tell a host to add SUPABASE_SERVICE_KEY.")
        else:
            stamp = int(datetime.now(timezone.utc).timestamp())
            with safe_write("start this drive"):
                qr_path = _save_image(qr_upload, f"qr_{stamp}")
                design_paths = [
                    _save_image(upload, f"design_{stamp}_{i}")
                    for i, upload in enumerate(design_uploads)
                ]
                size_chart_path = (
                    _save_image(size_chart_upload, f"sizechart_{stamp}") if size_chart_upload else None
                )
                drive_payload = {
                    "title": title.strip(),
                    "description": description.strip(),
                    "price": price,
                    "qr_image_path": qr_path,
                    "upi_id": upi_id.strip(),
                    "design_image_paths": design_paths,
                    "size_chart_image_path": size_chart_path,
                    "deadline": deadline.isoformat(),
                }
                if is_host:
                    _apply_pending_change("start_drive", drive_payload, current_user_id)
                else:
                    _stage_pending_change(
                        "start_drive", f"Start a new drive: \"{title.strip()}\"", drive_payload
                    )
                invalidate_cache()
            st.session_state.merch_message = (
                ("success", f"Started \"{title.strip()}\".") if is_host
                else ("success", "Submitted — a host needs to approve starting this drive.")
            )
            for k in ("new_drive_title", "new_drive_description", "new_drive_upi_id"):
                st.session_state.pop(k, None)
            st.session_state.show_new_drive = False
            st.rerun()


# --- Payment details (price/QR/UPI) --------------------------------------
# Shared by the host's always-visible view and a registrant's view once
# payment collection is open — one place for this block so the two never
# drift apart.
def render_payment_details(d):
    st.markdown(f"**{_price_text(d)}**")
    if d.get("qr_image_path") and storage is not None:
        try:
            st.image(
                storage.storage.from_(BUCKET).download(d["qr_image_path"]),
                width=400, caption="Pay via UPI",
            )
        except Exception:
            st.caption(":material/error: Couldn't load the QR code right now.")
    if d.get("upi_id"):
        st.caption(f":material/badge: UPI ID: **{d['upi_id']}**")


# --- Phase 1: register interest -------------------------------------------
def render_register_form(d):
    render_payment_details(d)
    st.markdown("**Register for this drive**")
    name = st.text_input("Name", value=current_user_name, key=f"register_name_{d['drive_id']}")
    username = st.text_input("Username", key=f"register_username_{d['drive_id']}")
    taken = _taken_numbers(d, current_user_name, orders_by_drive.get(d["drive_id"], []))
    available = [n for n in NUMBER_RANGE if n not in taken]
    mine = next(
        (n for n, who in _reserved_numbers(d).items()
         if (who or "").strip().lower() == (current_user_name or "").strip().lower() and n in available),
        None,
    )
    number = st.selectbox(
        f"Your number — {len(available)} still available",
        available, index=available.index(mine) if mine is not None else None,
        format_func=lambda n: f"{n:02d}" + ("  (reserved for you)" if n == mine else ""),
        placeholder="Pick an available number", key=f"register_number_{d['drive_id']}",
    )
    _render_taken_numbers(taken)
    size = st.selectbox(
        "Size", SIZES, index=None, placeholder="Select a size", key=f"register_size_{d['drive_id']}",
    )
    quote = st.text_area("Quote (optional)", key=f"register_quote_{d['drive_id']}")
    payment_method = st.radio(
        "How will you pay?", ["UPI", "Cash"], horizontal=True, key=f"register_method_{d['drive_id']}",
    )
    screenshot = None
    if payment_method == "UPI":
        screenshot = st.file_uploader(
            "Payment screenshot",
            type=list(ALLOWED_IMAGES), key=f"register_screenshot_{d['drive_id']}",
            help=f"Pay via the QR above first. PNG or JPG, up to {MAX_MB} MB.",
            disabled=storage is None,
        )
    else:
        st.info(f":material/call: Paying in cash — contact {CASH_CONTACT_NOTE} to arrange payment.")
    if st.button(
        "Register", key=f"register_btn_{d['drive_id']}", icon=":material/how_to_reg:", type="primary",
    ):
        fresh_taken = _taken_numbers(d, current_user_name, _fresh_drive_orders(d["drive_id"]))
        if not name.strip() or not username.strip():
            st.error("Name and username are required.")
        elif number is None:
            st.error("Pick a number.")
        elif number in fresh_taken:
            st.error(f"Sorry — {number:02d} was just taken ({fresh_taken[number]}). Pick another.")
        elif size is None:
            st.error("Pick a size.")
        elif payment_method == "UPI" and screenshot is None:
            st.error("Attach your payment screenshot.")
        else:
            row = {
                "drive_id": d["drive_id"],
                "user_id": current_user_id,
                "name": name.strip(),
                "username": username.strip(),
                "custom_number": int(number),
                "size": size,
                "quote": quote.strip(),
                "payment_method": "cash" if payment_method == "Cash" else "upi",
                "status": "cash_awaited" if payment_method == "Cash" else "pending_review",
            }
            with safe_write("register for this drive"):
                if payment_method == "UPI":
                    row["payment_screenshot_path"] = _save_image(
                        screenshot, f"proof_{d['drive_id']}_{current_user_id}"
                    )
                    row["payment_screenshot_name"] = screenshot.name
                client.table("merch_orders").insert(row).execute()
                invalidate_cache()
                sync_merch_orders_to_sheet()
            for email in HOST_EMAILS:
                send_email(
                    email, f"Merch registration: {d['title']}",
                    f"{name.strip()} registered for \"{d['title']}\" and will pay by "
                    f"{row['payment_method']} — needs review.",
                )
            st.session_state.merch_message = (
                "success",
                "Registered — contact Naitik to pay by cash, and a host will confirm it."
                if payment_method == "Cash"
                else "Registered and payment submitted for review.",
            )
            st.rerun()


# --- Phase 2: pay (only reachable once a host opens it) --------------------
def render_payment_form(d, order):
    st.markdown("**Complete your payment**")
    quote = st.text_area(
        "Quote (optional)", value=order.get("quote") or "", key=f"pay_quote_{order['order_id']}"
    )
    default_method_index = 1 if order.get("payment_method") == "cash" else 0
    payment_method = st.radio(
        "How will you pay?", ["UPI", "Cash"], index=default_method_index, horizontal=True,
        key=f"pay_method_{order['order_id']}",
    )
    screenshot = None
    if payment_method == "UPI":
        screenshot = st.file_uploader(
            "Payment screenshot", type=list(ALLOWED_IMAGES), key=f"pay_screenshot_{order['order_id']}",
            help=f"Pay via the QR above first, then attach proof here. PNG or JPG, up to {MAX_MB} MB.",
            disabled=storage is None,
        )
    else:
        st.info(f":material/call: Paying in cash — contact {CASH_CONTACT_NOTE} to arrange payment.")
    if st.button(
        "Submit payment proof" if payment_method == "UPI" else "I've arranged cash payment",
        key=f"pay_submit_{order['order_id']}", icon=":material/send:", type="primary",
    ):
        if payment_method == "UPI" and screenshot is None:
            st.error("Attach your payment screenshot before submitting.")
        elif payment_method == "UPI" and storage is None:
            st.error("File uploads aren't set up on this server yet — tell a host.")
        else:
            row = {
                "quote": quote.strip(),
                "payment_method": "cash" if payment_method == "Cash" else "upi",
                "status": "cash_awaited" if payment_method == "Cash" else "pending_review",
            }
            with safe_write("submit your payment"):
                if payment_method == "UPI":
                    row["payment_screenshot_path"] = _save_image(
                        screenshot, f"proof_{d['drive_id']}_{current_user_id}"
                    )
                    row["payment_screenshot_name"] = screenshot.name
                client.table("merch_orders").update(row).eq("order_id", order["order_id"]).execute()
                invalidate_cache()
                sync_merch_orders_to_sheet()
            for email in HOST_EMAILS:
                send_email(
                    email, f"Merch payment: {d['title']}",
                    f"{order['name']} submitted a {row['payment_method']} payment for \"{d['title']}\" and needs review.",
                )
            st.session_state.merch_message = ("success", "Submitted — a host will review it.")
            st.rerun()


def render_my_registration(d, order):
    st.markdown("**Your registration**")
    st.caption(f"{order['name']} · {order['username']} · #{order['custom_number']} · Size {order['size']}")
    st.badge(
        STATUS_LABELS[order["status"]], color=STATUS_BADGE_COLOR[order["status"]],
        icon=":material/how_to_reg:",
    )
    if order["status"] in ("registered", "rejected"):
        if order["status"] == "rejected":
            st.warning("Your payment screenshot was rejected. Fix it and resubmit below.")
        render_payment_details(d)
        render_payment_form(d, order)
    elif order["status"] == "pending_review":
        st.caption("Your payment is being reviewed by a host.")
    elif order["status"] == "cash_awaited":
        st.caption(f"Contact {CASH_CONTACT_NOTE} to pay — a host will confirm it once received.")
    elif order["status"] == "paid":
        st.caption("All set — your order is confirmed.")


# --- Host: add a registration by hand --------------------------------------
# For someone who paid or registered outside the app (cash handed over in
# person, a number claimed on Discord, a person with no account at all).
# Optionally linked to a real member; with no member picked the order has
# no user_id, which needs the "user_id drop not null" migration at the end
# of supabase_schema.sql. A co-host's entry is staged for approval like
# everything else they do here.
@st.dialog("Add a manual registration", on_dismiss=lambda: st.session_state.update(manual_entry_drive_id=None))
def render_manual_entry(d):
    drive_orders = orders_by_drive.get(d["drive_id"], [])
    has_order = {o["user_id"] for o in drive_orders}
    member_options = [None] + [
        u["user_id"] for u in sorted(all_users, key=lambda u: (u.get("name") or "").lower())
        if u["user_id"] not in has_order
    ]
    member_id = st.selectbox(
        "Link to a member (optional)", member_options,
        format_func=lambda uid: "— no account / outside the app —" if uid is None
        else st.session_state.user_name_by_id.get(uid, "Unknown"),
        key=f"manual_member_{d['drive_id']}",
        help="Members who already have a registration for this drive aren't listed.",
    )
    member = next((u for u in all_users if u["user_id"] == member_id), None)
    k = f"{d['drive_id']}_{member_id}"  # fresh defaults whenever the member changes
    name = st.text_input("Name", value=(member or {}).get("name") or "", key=f"manual_name_{k}")
    username = st.text_input("Username", key=f"manual_username_{k}")
    held = {o["custom_number"]: o["name"] for o in drive_orders}
    reserved = _reserved_numbers(d)
    numbers = [n for n in NUMBER_RANGE if n not in held]
    number = st.selectbox(
        f"Number — {len(numbers)} still free", numbers, index=None, placeholder="Pick a number",
        format_func=lambda n: f"{n:02d}" + (f"  (reserved for {reserved[n]})" if n in reserved else ""),
        key=f"manual_number_{k}",
    )
    size = st.selectbox("Size", SIZES, index=None, placeholder="Select a size", key=f"manual_size_{k}")
    quote = st.text_area("Quote (optional)", key=f"manual_quote_{k}")
    method = st.radio("Payment method", ["UPI", "Cash"], horizontal=True, key=f"manual_method_{k}")
    status = st.selectbox(
        "Status", list(STATUS_LABELS), format_func=lambda s: STATUS_LABELS[s],
        index=list(STATUS_LABELS).index("paid"), key=f"manual_status_{k}",
    )
    default_role = "" if member is None else ("core" if member.get("role") == "core_member" else "member")
    merch_role = st.text_input("Merch role", value=default_role, key=f"manual_role_{k}")
    if st.button("Add registration" if is_host else "Submit for approval", icon=":material/person_add:",
                 type="primary", key=f"manual_submit_{k}"):
        clash = next((x["name"] for x in _fresh_drive_orders(d["drive_id"]) if x["custom_number"] == number), None)
        if not name.strip() or not username.strip():
            st.error("Name and username are required.")
        elif number is None:
            st.error("Pick a number.")
        elif clash:
            st.error(f"{number:02d} was just taken by {clash}. Pick another.")
        elif size is None:
            st.error("Pick a size.")
        else:
            row = {
                "drive_id": d["drive_id"], "user_id": member_id, "name": name.strip(),
                "username": username.strip(), "custom_number": int(number), "size": size,
                "quote": quote.strip(), "payment_method": "cash" if method == "Cash" else "upi",
                "status": status, "merch_role": merch_role.strip() or None,
            }
            with safe_write("add this registration"):
                if is_host:
                    _apply_pending_change("manual_order", row, current_user_id)
                    invalidate_cache()
                    sync_merch_orders_to_sheet()
                else:
                    _stage_pending_change(
                        "manual_order", f"Add a manual registration: {row['name']} (#{number:02d})", row,
                    )
                    invalidate_cache()
            st.session_state.merch_message = (
                ("success", f"Added {row['name']}.") if is_host
                else ("success", "Submitted — a host needs to approve this.")
            )
            st.session_state.manual_entry_drive_id = None
            st.rerun()


# --- Host: edit any field of someone's registration -----------------------
def render_edit_order_form(d, o):
    st.markdown(f"**Editing {o['name']}'s registration**")
    edit_name = st.text_input("Name", value=o["name"], key=f"edit_order_name_{o['order_id']}")
    edit_username = st.text_input("Username", value=o["username"], key=f"edit_order_username_{o['order_id']}")
    # Hosts can hand out a reserved number (they're the ones who reserved
    # it), but never one another registration already holds.
    held = {
        x["custom_number"]: x["name"] for x in orders_by_drive.get(d["drive_id"], [])
        if x["order_id"] != o["order_id"]
    }
    reserved = _reserved_numbers(d)
    number_options = [n for n in NUMBER_RANGE if n not in held or n == o["custom_number"]]
    edit_number = st.selectbox(
        "Number", number_options, index=number_options.index(o["custom_number"]),
        format_func=lambda n: f"{n:02d}" + (
            f"  (also held by {held[n]} — change one)" if n in held
            else f"  (reserved for {reserved[n]})" if n in reserved else ""
        ),
        key=f"edit_order_number_{o['order_id']}",
    )
    edit_size = st.selectbox(
        "Size", SIZES, index=SIZES.index(o["size"]) if o.get("size") in SIZES else None,
        key=f"edit_order_size_{o['order_id']}",
    )
    edit_quote = st.text_area("Quote", value=o.get("quote") or "", key=f"edit_order_quote_{o['order_id']}")
    edit_method = st.radio(
        "Payment method", ["UPI", "Cash"],
        index=1 if o.get("payment_method") == "cash" else 0,
        horizontal=True, key=f"edit_order_method_{o['order_id']}",
    )
    edit_status = st.selectbox(
        "Status", list(STATUS_LABELS), format_func=lambda s: STATUS_LABELS[s],
        index=list(STATUS_LABELS).index(o["status"]), key=f"edit_order_status_{o['order_id']}",
    )
    edit_merch_role = st.text_input(
        "Merch role", value=o.get("merch_role") or "",
        placeholder="e.g. core, member, alumni, staff...",
        help="Shown on this registration and in the review list. Leave blank for none — type anything.",
        key=f"edit_order_merch_role_{o['order_id']}",
    )
    new_screenshot = None
    if edit_method == "UPI":
        new_screenshot = st.file_uploader(
            "Replace the payment screenshot (optional)", type=list(ALLOWED_IMAGES),
            key=f"edit_order_screenshot_{o['order_id']}", disabled=storage is None,
        )
    save_col, cancel_col = st.columns(2)
    if save_col.button(
        "Save changes", key=f"save_order_{o['order_id']}", icon=":material/check:", type="primary"
    ):
        if not edit_name.strip() or not edit_username.strip():
            st.session_state.merch_message = ("error", "Name and username are required.")
            st.rerun()
        if edit_size is None:
            st.session_state.merch_message = ("error", "Pick a size.")
            st.rerun()
        clash = next(
            (x["name"] for x in _fresh_drive_orders(d["drive_id"])
             if x["custom_number"] == edit_number and x["order_id"] != o["order_id"]),
            None,
        )
        if clash:
            st.session_state.merch_message = ("error", f"{edit_number:02d} is already {clash}'s number — pick another.")
            st.rerun()
        update_row = {
            "name": edit_name.strip(),
            "username": edit_username.strip(),
            "custom_number": int(edit_number),
            "size": edit_size,
            "quote": edit_quote.strip(),
            "payment_method": "cash" if edit_method == "Cash" else "upi",
            "status": edit_status,
            "merch_role": edit_merch_role.strip() or None,
        }
        with safe_write(f"update {edit_name.strip()}'s registration"):
            if edit_method == "UPI" and new_screenshot is not None:
                update_row["payment_screenshot_path"] = _save_image(
                    new_screenshot, f"proof_{d['drive_id']}_{o['user_id']}"
                )
                update_row["payment_screenshot_name"] = new_screenshot.name
            if is_host:
                _apply_pending_change(
                    "edit_order", {"order_id": o["order_id"], "update": update_row}, current_user_id
                )
                invalidate_cache()
                sync_merch_orders_to_sheet()
            else:
                _stage_pending_change(
                    "edit_order", f"Edit {o['name']}'s registration",
                    {"order_id": o["order_id"], "update": update_row},
                )
                invalidate_cache()
        st.session_state.merch_message = (
            ("success", f"Updated {edit_name.strip()}'s registration.") if is_host
            else ("success", "Submitted — a host needs to approve this edit.")
        )
        st.session_state.editing_order_id = None
        st.rerun()
    if cancel_col.button("Cancel", key=f"cancel_order_{o['order_id']}", icon=":material/close:"):
        st.session_state.editing_order_id = None
        st.rerun()


# --- Host: review registrations / payments --------------------------------
def render_registration_review(d, orders):
    if not orders:
        return
    pending_count = sum(1 for o in orders if o["status"] in ("pending_review", "cash_awaited"))
    with st.expander(f":material/how_to_reg: Registrations ({len(orders)})", expanded=pending_count > 0):
        for o in sorted(
            orders, key=lambda o: (o["status"] not in ("pending_review", "cash_awaited"), o["created_at"])
        ):
            with st.container(border=True, key=f"rkcard_merchorder_{o['order_id']}"):
                if st.session_state.editing_order_id == o["order_id"]:
                    render_edit_order_form(d, o)
                    continue

                name_col, badge_col, edit_col = st.columns([3, 1, 1], vertical_alignment="center")
                name_col.markdown(f"**{o['name']}** ({o['username']}) — #{o['custom_number']}, Size {o['size']}")
                badge_col.badge(
                    STATUS_LABELS[o["status"]], color=STATUS_BADGE_COLOR[o["status"]],
                    icon=":material/how_to_reg:",
                )
                if edit_col.button("Edit", key=f"edit_order_{o['order_id']}", icon=":material/edit:"):
                    st.session_state.editing_order_id = o["order_id"]
                    st.rerun()
                if o.get("merch_role"):
                    st.caption(f":material/military_tech: Merch role: **{o['merch_role'].title()}**")
                if o.get("quote"):
                    st.caption(f"“{o['quote']}”")
                if o.get("payment_method") == "cash":
                    st.caption(":material/payments: Paying by cash — confirm with Naitik before approving.")
                elif o.get("payment_screenshot_path"):
                    render_file_open_and_download(
                        storage, BUCKET, o["payment_screenshot_path"], o.get("payment_screenshot_name"),
                        key_suffix=f"merchproof_{o['order_id']}",
                    )
                if o["status"] in ("pending_review", "cash_awaited"):
                    approve_col, reject_col = st.columns(2)
                    if approve_col.button(
                        "Approve — paid" if is_host else "Request approval — paid",
                        key=f"approve_order_{o['order_id']}",
                        icon=":material/check_circle:", type="primary",
                    ):
                        if is_host:
                            with safe_write("approve this order"):
                                _apply_pending_change("approve_order", {"order_id": o["order_id"]}, current_user_id)
                                invalidate_cache()
                                sync_merch_orders_to_sheet()
                            st.session_state.merch_message = ("success", "Marked paid.")
                        else:
                            with safe_write("request approving this order"):
                                _stage_pending_change(
                                    "approve_order",
                                    f"Mark {o['name']}'s order paid (#{o['custom_number']})",
                                    {"order_id": o["order_id"]},
                                )
                                invalidate_cache()
                            st.session_state.merch_message = ("success", "Submitted — a host needs to approve this.")
                        st.rerun()
                    if reject_col.button(
                        "Reject" if is_host else "Request rejection",
                        key=f"reject_order_{o['order_id']}", icon=":material/cancel:",
                    ):
                        if is_host:
                            with safe_write("reject this order"):
                                _apply_pending_change("reject_order", {"order_id": o["order_id"]}, current_user_id)
                                invalidate_cache()
                                sync_merch_orders_to_sheet()
                            st.session_state.merch_message = ("success", "Marked rejected.")
                        else:
                            with safe_write("request rejecting this order"):
                                _stage_pending_change(
                                    "reject_order",
                                    f"Reject {o['name']}'s order (#{o['custom_number']})",
                                    {"order_id": o["order_id"]},
                                )
                                invalidate_cache()
                            st.session_state.merch_message = ("success", "Submitted — a host needs to approve this.")
                        st.rerun()


# --- Host: approve/reject what a merch co-host has submitted --------------
def render_pending_approvals():
    pending = sorted(
        (p for p in _pending_changes() if p["status"] == "pending"),
        key=lambda p: p["created_at"],
    )
    if not pending:
        return
    with st.expander(f":material/pending_actions: Pending merch approvals ({len(pending)})", expanded=True):
        st.caption("Submitted by a merch co-host — nothing below has happened yet.")
        for p in pending:
            with st.container(border=True, key=f"rkcard_merchpending_{p['pending_id']}"):
                requester = st.session_state.user_name_by_id.get(p["requested_by"], "Unknown")
                st.markdown(f"**{p['summary']}**")
                st.caption(f"Requested by {requester}")
                approve_col, reject_col = st.columns(2)
                if approve_col.button(
                    "Approve", key=f"approve_pending_{p['pending_id']}",
                    icon=":material/check_circle:", type="primary",
                ):
                    with safe_write("approve this change"):
                        _apply_pending_change(p["action"], p.get("payload") or {}, p["requested_by"])
                        client.table("merch_pending_changes").update({
                            "status": "approved", "reviewed_by": current_user_id,
                            "reviewed_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("pending_id", p["pending_id"]).execute()
                        invalidate_cache()
                        if p["action"] in ("delete_drive", "edit_order", "approve_order", "reject_order", "manual_order"):
                            sync_merch_orders_to_sheet()
                    st.session_state.merch_message = ("success", "Approved and applied.")
                    st.rerun()
                if reject_col.button(
                    "Reject", key=f"reject_pending_{p['pending_id']}", icon=":material/cancel:",
                ):
                    with safe_write("reject this change"):
                        client.table("merch_pending_changes").update({
                            "status": "rejected", "reviewed_by": current_user_id,
                            "reviewed_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("pending_id", p["pending_id"]).execute()
                        invalidate_cache()
                    st.session_state.merch_message = ("success", "Rejected.")
                    st.rerun()


# --- Co-host: status of what they've submitted -----------------------------
def render_my_pending_changes():
    mine = [p for p in _pending_changes() if p["requested_by"] == current_user_id]
    if not mine:
        return
    badge_color = {"pending": "orange", "approved": "green", "rejected": "red"}
    with st.expander(f":material/pending_actions: Your submitted changes ({len(mine)})", expanded=False):
        for p in sorted(mine, key=lambda p: p["created_at"], reverse=True):
            row_col, badge_col = st.columns([4, 1], vertical_alignment="center")
            row_col.caption(p["summary"])
            badge_col.badge(p["status"].title(), color=badge_color[p["status"]])


# --- Layout ----------------------------------------------------------------

if is_host:
    render_pending_approvals()

if is_merch_cohost:
    st.info(
        "You have merch co-host access — every change you make here is submitted "
        "for a host to approve before it actually takes effect.",
        icon=":material/pending_actions:",
    )
    render_my_pending_changes()

if can_manage_merch:
    render_access_manager()
    if st.button("Start a merch drive", icon=":material/add_box:", type="primary", key="open_new_drive"):
        st.session_state.show_new_drive = True
    if st.session_state.show_new_drive:
        render_new_drive()

if st.session_state.merch_message:
    kind, text = st.session_state.merch_message
    if kind == "success":
        st.toast(text, icon=":material/check_circle:")
    else:
        st.error(text)
    st.session_state.merch_message = None

if not drives:
    st.caption("No merch drives yet.")

for d in drives:
    is_open = d["deadline"] >= today_iso
    my_order = next(
        (o for o in orders_by_drive.get(d["drive_id"], []) if o["user_id"] == current_user_id), None
    )
    with st.container(border=True, key=f"rkcard_merchdrive_{d['drive_id']}"):
        editing_this = can_manage_merch and st.session_state.editing_drive_id == d["drive_id"]

        if editing_this:
            edit_title = st.text_input(
                "What's being sold", value=d["title"], key=f"edit_drive_title_{d['drive_id']}"
            )
            edit_description = st.text_area(
                "Details for members", value=d.get("description") or "",
                key=f"edit_drive_desc_{d['drive_id']}",
            )
            edit_price = st.number_input(
                "Price (₹)", min_value=0.0, step=1.0, value=float(d.get("price") or 0),
                key=f"edit_drive_price_{d['drive_id']}",
            )
            new_design_uploads = st.file_uploader(
                "Replace the design pictures (optional)", type=list(ALLOWED_IMAGES),
                key=f"edit_drive_design_{d['drive_id']}", disabled=storage is None,
                accept_multiple_files=True,
                help="Uploading new ones here replaces the current set entirely.",
            )
            new_size_chart = st.file_uploader(
                "Replace the size chart (optional)", type=list(ALLOWED_IMAGES),
                key=f"edit_drive_size_chart_{d['drive_id']}", disabled=storage is None,
            )
            edit_upi_id = st.text_input(
                "UPI ID", value=d.get("upi_id") or "",
                key=f"edit_drive_upi_{d['drive_id']}",
            )
            edit_deadline = st.date_input(
                "Last day to register", value=date.fromisoformat(d["deadline"]),
                key=f"edit_drive_deadline_{d['drive_id']}",
            )
            new_qr = st.file_uploader(
                "Replace the QR code (optional)", type=list(ALLOWED_IMAGES),
                key=f"edit_drive_qr_{d['drive_id']}", disabled=storage is None,
            )
            # Only offered once the reserved_numbers migration has run —
            # sending the key to a table without the column would fail
            # the whole save.
            has_reservations_column = "reserved_numbers" in d
            if has_reservations_column:
                edit_reserved = st.text_area(
                    "Reserved numbers",
                    value="\n".join(f"{n:02d} - {who}" for n, who in sorted(_reserved_numbers(d).items())),
                    placeholder="33 - Adhiraj Jain\n19 - Aryamman ojha",
                    help=(
                        "One per line: number - name. Blocked for everyone except that person, "
                        "matched against their account name. Remove a line to free the number."
                    ),
                    key=f"edit_drive_reserved_{d['drive_id']}",
                )
            save_col, cancel_col = st.columns(2)
            if save_col.button(
                "Save changes", key=f"save_drive_{d['drive_id']}", icon=":material/check:", type="primary"
            ):
                if not edit_title.strip():
                    st.session_state.merch_message = ("error", "Title is required.")
                    st.rerun()
                update_row = {
                    "title": edit_title.strip(),
                    "description": edit_description.strip(),
                    "price": edit_price,
                    "upi_id": edit_upi_id.strip(),
                    "deadline": edit_deadline.isoformat(),
                }
                if has_reservations_column:
                    reserved, bad_lines = {}, []
                    for line in edit_reserved.splitlines():
                        if not line.strip():
                            continue
                        m = _RESERVATION_LINE.match(line)
                        if m and int(m.group(1)) in NUMBER_RANGE:
                            reserved[str(int(m.group(1)))] = m.group(2)
                        else:
                            bad_lines.append(line.strip())
                    if bad_lines:
                        st.session_state.merch_message = (
                            "error", f"Couldn't read: {', '.join(bad_lines)} — use \"33 - Name\".",
                        )
                        st.rerun()
                    update_row["reserved_numbers"] = reserved
                stamp = int(datetime.now(timezone.utc).timestamp())
                with safe_write(f"update {edit_title.strip()}"):
                    if new_qr is not None:
                        update_row["qr_image_path"] = _save_image(new_qr, f"qr_{d['drive_id']}_{stamp}")
                    if new_design_uploads:
                        update_row["design_image_paths"] = [
                            _save_image(upload, f"design_{d['drive_id']}_{stamp}_{i}")
                            for i, upload in enumerate(new_design_uploads)
                        ]
                    if new_size_chart is not None:
                        update_row["size_chart_image_path"] = _save_image(
                            new_size_chart, f"sizechart_{d['drive_id']}_{stamp}"
                        )
                    if is_host:
                        _apply_pending_change(
                            "edit_drive", {"drive_id": d["drive_id"], "update": update_row}, current_user_id
                        )
                    else:
                        _stage_pending_change(
                            "edit_drive", f"Edit drive \"{d['title']}\"",
                            {"drive_id": d["drive_id"], "update": update_row},
                        )
                    invalidate_cache()
                st.session_state.merch_message = (
                    ("success", f"Updated {edit_title.strip()}.") if is_host
                    else ("success", "Submitted — a host needs to approve this edit.")
                )
                st.session_state.editing_drive_id = None
                st.rerun()
            if cancel_col.button("Cancel", key=f"cancel_drive_{d['drive_id']}", icon=":material/close:"):
                st.session_state.editing_drive_id = None
                st.rerun()
            continue

        title_col, badge_col, edit_col, delete_col = st.columns([3, 1, 1, 1], vertical_alignment="center")
        title_col.markdown(f"### {d['title']}")
        if is_open:
            badge_col.badge("Open", color="green", icon=":material/how_to_reg:")
        else:
            badge_col.badge("Closed", color="grey", icon=":material/lock:")
        if can_manage_merch:
            if edit_col.button("Edit", key=f"edit_btn_drive_{d['drive_id']}", icon=":material/edit:"):
                st.session_state.editing_drive_id = d["drive_id"]
                st.rerun()
            if delete_col.button(
                "Delete" if is_host else "Request delete",
                key=f"delete_btn_drive_{d['drive_id']}", icon=":material/delete:",
            ):
                if is_host:
                    with safe_write(f"delete {d['title']}"):
                        _apply_pending_change("delete_drive", {"drive_id": d["drive_id"]}, current_user_id)
                        invalidate_cache()
                        sync_merch_orders_to_sheet()
                    st.session_state.merch_message = ("success", f"Deleted {d['title']}.")
                else:
                    with safe_write(f"request deleting {d['title']}"):
                        _stage_pending_change(
                            "delete_drive", f"Delete drive \"{d['title']}\"", {"drive_id": d["drive_id"]}
                        )
                        invalidate_cache()
                    st.session_state.merch_message = ("success", "Submitted — a host needs to approve this delete.")
                st.rerun()

        st.caption(
            f":material/event: Registration open through {date.fromisoformat(d['deadline']).strftime('%d %b %Y')}"
        )
        if d.get("description"):
            st.write(d["description"])

        if d.get("design_image_paths") and storage is not None:
            design_images = []
            for path in d["design_image_paths"]:
                try:
                    design_images.append(storage.storage.from_(BUCKET).download(path))
                except Exception:
                    pass
            if design_images:
                st.image(design_images, width=220)

        if d.get("size_chart_image_path") and storage is not None:
            try:
                size_chart_bytes = storage.storage.from_(BUCKET).download(d["size_chart_image_path"])
                st.image(size_chart_bytes, use_container_width=True, caption="Size chart")
                size_chart_ext = d["size_chart_image_path"].rsplit(".", 1)[-1]
                st.download_button(
                    "Download size chart", data=size_chart_bytes,
                    file_name=f"size_chart.{size_chart_ext}", icon=":material/download:",
                    key=f"download_sizechart_{d['drive_id']}",
                )
            except Exception:
                st.caption(":material/error: Couldn't load the size chart right now.")

        if can_manage_merch:
            render_payment_details(d)
            if st.button("Add manual registration", icon=":material/person_add:", key=f"open_manual_{d['drive_id']}"):
                st.session_state.manual_entry_drive_id = d["drive_id"]
            if st.session_state.manual_entry_drive_id == d["drive_id"]:
                render_manual_entry(d)

        if has_access:
            if my_order is None:
                if is_open:
                    render_register_form(d)
                else:
                    st.caption("Registration is closed for this drive.")
            else:
                render_my_registration(d, my_order)

        if can_manage_merch:
            render_registration_review(d, orders_by_drive.get(d["drive_id"], []))

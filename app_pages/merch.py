# Merch: a host-run order drive. A member fills in their order details and
# attaches a payment screenshot (paid via a UPI QR code the host uploads);
# a host manually reviews the screenshot and approves or rejects it before
# the order counts as paid. Real money from minors, so a human stays in the
# loop rather than just showing a QR and trusting people — see CLAUDE.md's
# earlier note on this exact tradeoff.

from datetime import date, datetime, timezone

import streamlit as st

from shared import (
    HOST_EMAILS, cached_table, get_client, get_storage_client, invalidate_cache,
    render_file_open_and_download, safe_write, send_email, today_ist,
)

client = get_client()
storage = get_storage_client()
is_host = st.session_state.is_host
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_email_by_id = st.session_state.user_email_by_id
all_users = cached_table("users")
current_user_row = next((u for u in all_users if u["user_id"] == current_user_id), None)
has_access = bool(current_user_row and current_user_row.get("has_merch_access"))

# Belt and braces alongside the nav gating in app.py — everyone in the club
# reaches this page (adhocs included), but only people a host has actually
# granted merch access see the drives themselves. A host always gets
# through, even without merch access personally, to manage the Access list
# and review orders.
if not is_host and not has_access:
    st.title(":material/storefront: Merch")
    st.warning(
        "Sorry — you're not eligible for RoboKnights merch right now. "
        "Contact a senior member, or try again next year."
    )
    st.stop()

BUCKET = "merch-assets"
ALLOWED_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
MAX_MB = 5

STATUS_LABELS = {"pending_review": "Awaiting review", "paid": "Paid", "rejected": "Rejected — resubmit"}
STATUS_BADGE_COLOR = {"pending_review": "orange", "paid": "green", "rejected": "red"}

st.title(":material/storefront: Merch")
st.caption("Order drives the club is running right now, and what you've ordered.")

if "merch_message" not in st.session_state:
    st.session_state.merch_message = None
if "show_new_drive" not in st.session_state:
    st.session_state.show_new_drive = False
if "editing_drive_id" not in st.session_state:
    st.session_state.editing_drive_id = None

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


# --- Host: who has merch access -----------------------------------------
# Same multiselect-diff pattern exun_tasks.py already uses for its
# volunteer list: a picker of real names, pre-filled with who currently has
# access, and Save only writes whatever actually changed.
def render_access_manager():
    eligible = [u for u in all_users if u.get("has_merch_access")]
    with st.expander(f":material/badge: Merch access ({len(eligible)})", expanded=False):
        st.caption(
            "Who can see and place orders on this page — independent of club "
            "role, so add or remove anyone here directly."
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
            with safe_write("update merch access"):
                for uid in now_access - had_access:
                    client.table("users").update({"has_merch_access": True}).eq("user_id", uid).execute()
                for uid in had_access - now_access:
                    client.table("users").update({"has_merch_access": False}).eq("user_id", uid).execute()
                invalidate_cache()
            st.session_state.merch_message = ("success", "Merch access updated.")
            st.rerun()


# --- Host: start a drive -----------------------------------------------
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
    upi_id = st.text_input("UPI ID", key="new_drive_upi_id")
    qr_upload = st.file_uploader(
        "UPI payment QR code", type=list(ALLOWED_IMAGES), key="new_drive_qr",
        help=f"PNG or JPG, up to {MAX_MB} MB.", disabled=storage is None,
    )
    deadline = st.date_input(
        "Last day orders are open", key="new_drive_deadline", value=date(2026, 10, 1),
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
                client.table("merch_drives").insert({
                    "title": title.strip(),
                    "description": description.strip(),
                    "price": price,
                    "qr_image_path": qr_path,
                    "upi_id": upi_id.strip(),
                    "design_image_paths": design_paths,
                    "deadline": deadline.isoformat(),
                    "created_by": current_user_id,
                }).execute()
                invalidate_cache()
            st.session_state.merch_message = ("success", f"Started \"{title.strip()}\".")
            for k in ("new_drive_title", "new_drive_description", "new_drive_upi_id"):
                st.session_state.pop(k, None)
            st.session_state.show_new_drive = False
            st.rerun()


# --- Member: place or resubmit an order ---------------------------------
def render_order_form(d, existing=None):
    st.markdown("**Resubmit your order**" if existing else "**Place your order**")
    name = st.text_input(
        "Name", value=(existing or {}).get("name") or current_user_name, key=f"order_name_{d['drive_id']}"
    )
    username = st.text_input(
        "Username", value=(existing or {}).get("username") or "", key=f"order_username_{d['drive_id']}"
    )
    number = st.number_input(
        "Your number (0-99)", min_value=0, max_value=99, step=1,
        value=(existing or {}).get("custom_number") or 0, key=f"order_number_{d['drive_id']}",
    )
    quote = st.text_area("Quote", value=(existing or {}).get("quote") or "", key=f"order_quote_{d['drive_id']}")
    screenshot = st.file_uploader(
        "Payment screenshot", type=list(ALLOWED_IMAGES), key=f"order_screenshot_{d['drive_id']}",
        help=f"Pay via the QR above first, then attach proof here. PNG or JPG, up to {MAX_MB} MB.",
        disabled=storage is None,
    )
    if st.button(
        "Resubmit order" if existing else "Submit order",
        key=f"submit_order_{d['drive_id']}", icon=":material/send:", type="primary",
    ):
        if not name.strip() or not username.strip():
            st.error("Name and username are required.")
        elif screenshot is None:
            st.error("Attach your payment screenshot before submitting.")
        elif storage is None:
            st.error("File uploads aren't set up on this server yet — tell a host.")
        else:
            row = {
                "name": name.strip(),
                "username": username.strip(),
                "custom_number": int(number),
                "quote": quote.strip(),
                "payment_screenshot_name": screenshot.name,
                "status": "pending_review",
            }
            with safe_write("submit this order"):
                row["payment_screenshot_path"] = _save_image(
                    screenshot, f"proof_{d['drive_id']}_{current_user_id}"
                )
                if existing is None:
                    client.table("merch_orders").insert({
                        **row, "drive_id": d["drive_id"], "user_id": current_user_id,
                    }).execute()
                else:
                    client.table("merch_orders").update(row).eq("order_id", existing["order_id"]).execute()
                invalidate_cache()
            for email in HOST_EMAILS:
                send_email(
                    email, f"Merch order: {d['title']}",
                    f"{name.strip()} submitted an order for \"{d['title']}\" and needs payment review.\n\n"
                    "Open the Merch page on the dashboard to review it.",
                )
            st.session_state.merch_message = ("success", "Order submitted — a host will review your payment.")
            st.rerun()


def render_my_order(d, order):
    st.markdown("**Your order**")
    caption = f"{order['name']} · {order['username']} · #{order['custom_number']}"
    if order.get("quote"):
        caption += f" — “{order['quote']}”"
    st.caption(caption)
    st.badge(
        STATUS_LABELS[order["status"]], color=STATUS_BADGE_COLOR[order["status"]],
        icon=":material/receipt_long:",
    )
    if order["status"] == "rejected" and d["deadline"] >= today_iso:
        st.warning("Your payment screenshot was rejected. Fix it and resubmit below.")
        render_order_form(d, existing=order)


# --- Host: review submitted orders ---------------------------------------
def render_order_review(d, orders):
    if not orders:
        return
    pending_count = sum(1 for o in orders if o["status"] == "pending_review")
    with st.expander(f":material/receipt_long: Orders ({len(orders)})", expanded=pending_count > 0):
        for o in sorted(orders, key=lambda o: (o["status"] != "pending_review", o["created_at"])):
            with st.container(border=True, key=f"rkcard_merchorder_{o['order_id']}"):
                name_col, badge_col = st.columns([3, 1], vertical_alignment="center")
                name_col.markdown(f"**{o['name']}** ({o['username']}) — #{o['custom_number']}")
                badge_col.badge(
                    STATUS_LABELS[o["status"]], color=STATUS_BADGE_COLOR[o["status"]],
                    icon=":material/receipt_long:",
                )
                if o.get("quote"):
                    st.caption(f"“{o['quote']}”")
                if o.get("payment_screenshot_path"):
                    render_file_open_and_download(
                        storage, BUCKET, o["payment_screenshot_path"], o.get("payment_screenshot_name"),
                        key_suffix=f"merchproof_{o['order_id']}",
                    )
                if o["status"] == "pending_review":
                    approve_col, reject_col = st.columns(2)
                    if approve_col.button(
                        "Approve — paid", key=f"approve_order_{o['order_id']}",
                        icon=":material/check_circle:", type="primary",
                    ):
                        with safe_write("approve this order"):
                            client.table("merch_orders").update({"status": "paid"}).eq(
                                "order_id", o["order_id"]
                            ).execute()
                            invalidate_cache()
                        recipient = user_email_by_id.get(o["user_id"])
                        if recipient:
                            send_email(
                                recipient, f"Merch order confirmed: {d['title']}",
                                f"Your payment for \"{d['title']}\" has been confirmed.\n"
                                f"Order: #{o['custom_number']}, {o['username']}.",
                            )
                        st.session_state.merch_message = ("success", "Marked paid.")
                        st.rerun()
                    if reject_col.button(
                        "Reject", key=f"reject_order_{o['order_id']}", icon=":material/cancel:",
                    ):
                        with safe_write("reject this order"):
                            client.table("merch_orders").update({"status": "rejected"}).eq(
                                "order_id", o["order_id"]
                            ).execute()
                            invalidate_cache()
                        recipient = user_email_by_id.get(o["user_id"])
                        if recipient:
                            send_email(
                                recipient, f"Merch order needs a new screenshot: {d['title']}",
                                f"Your payment screenshot for \"{d['title']}\" couldn't be confirmed. "
                                "Please check the Merch page and resubmit.",
                            )
                        st.session_state.merch_message = ("success", "Marked rejected.")
                        st.rerun()


# --- Layout ----------------------------------------------------------------

if is_host:
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
        editing_this = is_host and st.session_state.editing_drive_id == d["drive_id"]

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
            edit_upi_id = st.text_input(
                "UPI ID", value=d.get("upi_id") or "",
                key=f"edit_drive_upi_{d['drive_id']}",
            )
            edit_deadline = st.date_input(
                "Last day orders are open", value=date.fromisoformat(d["deadline"]),
                key=f"edit_drive_deadline_{d['drive_id']}",
            )
            new_qr = st.file_uploader(
                "Replace the QR code (optional)", type=list(ALLOWED_IMAGES),
                key=f"edit_drive_qr_{d['drive_id']}", disabled=storage is None,
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
                stamp = int(datetime.now(timezone.utc).timestamp())
                with safe_write(f"update {edit_title.strip()}"):
                    if new_qr is not None:
                        update_row["qr_image_path"] = _save_image(new_qr, f"qr_{d['drive_id']}_{stamp}")
                    if new_design_uploads:
                        update_row["design_image_paths"] = [
                            _save_image(upload, f"design_{d['drive_id']}_{stamp}_{i}")
                            for i, upload in enumerate(new_design_uploads)
                        ]
                    client.table("merch_drives").update(update_row).eq("drive_id", d["drive_id"]).execute()
                    invalidate_cache()
                st.session_state.merch_message = ("success", f"Updated {edit_title.strip()}.")
                st.session_state.editing_drive_id = None
                st.rerun()
            if cancel_col.button("Cancel", key=f"cancel_drive_{d['drive_id']}", icon=":material/close:"):
                st.session_state.editing_drive_id = None
                st.rerun()
            continue

        title_col, badge_col, edit_col, delete_col = st.columns([3, 1, 1, 1], vertical_alignment="center")
        title_col.markdown(f"### {d['title']}")
        if is_open:
            badge_col.badge("Open", color="green", icon=":material/storefront:")
        else:
            badge_col.badge("Closed", color="grey", icon=":material/lock:")
        if is_host:
            if edit_col.button("Edit", key=f"edit_btn_drive_{d['drive_id']}", icon=":material/edit:"):
                st.session_state.editing_drive_id = d["drive_id"]
                st.rerun()
            if delete_col.button("Delete", key=f"delete_btn_drive_{d['drive_id']}", icon=":material/delete:"):
                with safe_write(f"delete {d['title']}"):
                    if storage is not None:
                        stale_paths = [d["qr_image_path"]] if d.get("qr_image_path") else []
                        stale_paths += d.get("design_image_paths") or []
                        if stale_paths:
                            try:
                                storage.storage.from_(BUCKET).remove(stale_paths)
                            except Exception:
                                pass
                    client.table("merch_drives").delete().eq("drive_id", d["drive_id"]).execute()
                    invalidate_cache()
                st.session_state.merch_message = ("success", f"Deleted {d['title']}.")
                st.rerun()

        # PostgREST returns `numeric` columns as JSON strings, not floats
        # (avoids float precision loss) — cast before formatting or this
        # breaks the moment a real price comes back from the database.
        price_value = float(d.get("price") or 0)
        price_text = f"₹{price_value:.0f}" if price_value == int(price_value) else f"₹{price_value:.2f}"
        st.markdown(f"**{price_text}**")
        st.caption(
            f":material/event: Orders open through {date.fromisoformat(d['deadline']).strftime('%d %b %Y')}"
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

        if d.get("qr_image_path") and storage is not None:
            try:
                st.image(
                    storage.storage.from_(BUCKET).download(d["qr_image_path"]),
                    width=220, caption="Pay via UPI",
                )
            except Exception:
                st.caption(":material/error: Couldn't load the QR code right now.")
        if d.get("upi_id"):
            st.caption(f":material/badge: UPI ID: **{d['upi_id']}**")

        if has_access:
            if my_order is None:
                if is_open:
                    render_order_form(d)
                else:
                    st.caption("Orders are closed for this drive.")
            else:
                render_my_order(d, my_order)

        if is_host:
            render_order_review(d, orders_by_drive.get(d["drive_id"], []))

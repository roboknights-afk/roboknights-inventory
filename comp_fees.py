# Competition registration fees (2026-11-13, first used for NYRC 2027).
#
# A host sets a fee per event on a competition, plus where to pay it (UPI
# ID and QR). Everyone selected for an event splits that event's fee
# evenly (fee_math.py has the arithmetic). On the competition's card a
# selected member sees what they owe and why, pays by UPI, and uploads
# the payment screenshot; a host then confirms or rejects it, the same
# review-by-hand shape as Merch payments.
#
# Storage: the QR and the screenshots live in the private
# "competition-payments" bucket, through get_storage_client() (service
# key), with paths built only from the competition id and the signed-in
# user's own id. The UPI ID and QR are in the database, not in this repo,
# because the repo is public and they belong to a student.
#
# Needs the "Competition registration fees" migration at the end of
# supabase_schema.sql. Until it runs, the section says so instead of
# breaking the card.

from datetime import datetime, timezone

import streamlit as st

from fee_math import compute_shares, rupees, total
from shared import (
    cached_table, get_client, get_storage_client, invalidate_cache, safe_write, send_email,
)

BUCKET = "competition-payments"
ALLOWED_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
MAX_MB = 5
STATUS = {
    "pending_review": ("orange", ":material/hourglass_top:", "Screenshot sent, waiting for a host to confirm"),
    "paid": ("green", ":material/verified:", "Paid and confirmed"),
    "rejected": ("red", ":material/error:", "Screenshot not accepted, please upload a new one"),
}


def _payments_ready():
    try:
        get_client().table("competition_payments").select("payment_id").limit(1).execute()
        return True
    except Exception:
        return False


def _payments_for(cid):
    try:
        return [p for p in cached_table("competition_payments") if p["competition_id"] == cid]
    except Exception:
        return []


def _owed_for(comp, events):
    disabled = {u["user_id"] for u in cached_table("users") if u.get("is_disabled")}
    try:
        guests = cached_table("event_guest_selections")
    except Exception:
        guests = []
    return compute_shares(
        comp.get("fee_per_event"), events, cached_table("event_volunteers"), guests, disabled,
    )


def _save_image(storage, upload, path_prefix):
    raw = upload.getvalue()
    ext = upload.name.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMAGES:
        st.error("That is not a PNG or JPG file.")
        st.stop()
    if len(raw) > MAX_MB * 1024 * 1024:
        st.error(f"That file is {len(raw) / 1024 / 1024:.1f} MB. The limit is {MAX_MB} MB.")
        st.stop()
    path = f"{path_prefix}.{'png' if ext == 'png' else 'jpg'}"
    storage.storage.from_(BUCKET).upload(path, raw, {"content-type": ALLOWED_IMAGES[ext], "upsert": "true"})
    return path


def _breakdown_table(rows):
    st.dataframe(
        [
            {"Event": e["name"], "People": people, "Your share": rupees(share)}
            for e, people, share in rows
        ],
        hide_index=True,
    )


def _show_qr(storage, comp):
    if comp.get("fee_qr_path") and storage is not None:
        try:
            st.image(storage.storage.from_(BUCKET).download(comp["fee_qr_path"]), width=240)
        except Exception:
            st.caption(":material/error: Couldn't load the QR code right now.")
    if comp.get("fee_upi_id"):
        payee = f" ({comp['fee_payee_name']})" if comp.get("fee_payee_name") else ""
        st.markdown(f":material/account_balance_wallet: UPI ID: **{comp['fee_upi_id']}**{payee}")


# --- What a selected member sees --------------------------------------------

def _render_member(comp, owed, payments, storage):
    uid = st.session_state.current_user_id
    rows = owed.get(f"user:{uid}")
    if not rows:
        return
    due = total(rows)
    mine = next((p for p in payments if p["user_id"] == uid), None)
    status = mine["status"] if mine else None
    label = f"Registration fee · {rupees(due)}"
    if status:
        label += f" · {STATUS[status][2].split(',')[0].lower()}"

    with st.expander(
        label, icon=":material/payments:", key=f"fee_member_{comp['competition_id']}",
        expanded=status in (None, "rejected"),
    ):
        if status:
            colour, icon, text = STATUS[status]
            st.markdown(f":{colour}-badge[{icon} {text}]")
            if float(mine["amount"]) != due and status != "rejected":
                st.caption(
                    f"You paid {rupees(mine['amount'])}, but your share is now {rupees(due)} "
                    "because a team changed. A host will sort out the difference with you."
                )
        st.markdown(
            f"Each event's {rupees(comp['fee_per_event'])} registration fee is split evenly "
            f"between everyone selected for it. Your total is **{rupees(due)}**."
        )
        _breakdown_table(rows)
        if status == "paid" or status == "pending_review":
            return

        _show_qr(storage, comp)
        if st.session_state.is_read_only:
            return
        with st.form(f"fee_pay_{comp['competition_id']}", border=False):
            shot = st.file_uploader(
                "Payment screenshot", type=list(ALLOWED_IMAGES),
                help=f"Pay {rupees(due)} using the QR or UPI ID above first. PNG or JPG, up to {MAX_MB} MB.",
                disabled=storage is None,
            )
            if st.form_submit_button(f"I've paid {rupees(due)}", icon=":material/check:", type="primary"):
                if shot is None:
                    st.error("Attach your payment screenshot.")
                    return
                with safe_write("send your payment screenshot"):
                    path = _save_image(storage, shot, f"proof/{comp['competition_id']}/{uid}")
                    row = {
                        "competition_id": comp["competition_id"], "user_id": uid, "amount": due,
                        "screenshot_path": path, "screenshot_name": shot.name,
                        "status": "pending_review",
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "reviewed_by": None, "reviewed_at": None,
                    }
                    get_client().table("competition_payments").upsert(
                        row, on_conflict="competition_id,user_id"
                    ).execute()
                    invalidate_cache()
                    # Inside the with: on a failed save safe_write shows the
                    # error and these never run, so it isn't hidden by a rerun.
                    st.toast("Screenshot sent. A host will confirm it.", icon=":material/check_circle:")
                    st.rerun()


# --- What a host sees -------------------------------------------------------

def _render_host_setup(comp, storage):
    cid = comp["competition_id"]
    with st.form(f"fee_setup_{cid}", border=True):
        st.markdown("**Fee settings**")
        c1, c2 = st.columns(2)
        fee = c1.number_input(
            "Fee per event (₹)", min_value=0, step=100,
            value=int(float(comp.get("fee_per_event") or 0)),
            help="Split evenly between everyone selected for that event. 0 turns fees off.",
        )
        upi = c2.text_input("UPI ID", value=comp.get("fee_upi_id") or "")
        payee = c1.text_input("Paid to (name)", value=comp.get("fee_payee_name") or "")
        qr = c2.file_uploader(
            "UPI QR code" + (" (leave empty to keep the current one)" if comp.get("fee_qr_path") else ""),
            type=list(ALLOWED_IMAGES), disabled=storage is None,
        )
        if st.form_submit_button("Save fee settings", icon=":material/save:"):
            if fee and not upi.strip():
                st.error("Add the UPI ID people should pay to.")
                return
            with safe_write("save the fee settings"):
                updates = {
                    "fee_per_event": fee or None,
                    "fee_upi_id": upi.strip() or None,
                    "fee_payee_name": payee.strip() or None,
                }
                if qr is not None:
                    updates["fee_qr_path"] = _save_image(storage, qr, f"qr/{cid}")
                get_client().table("competitions").update(updates).eq("competition_id", cid).execute()
                invalidate_cache()
                st.toast("Fee settings saved.", icon=":material/check_circle:")
                st.rerun()


def _review(payment, new_status, comp, name_by_id, email_by_id):
    saved = False
    with safe_write("update this payment"):
        get_client().table("competition_payments").update({
            "status": new_status,
            "reviewed_by": st.session_state.auth_user["email"],
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("payment_id", payment["payment_id"]).execute()
        invalidate_cache()
        saved = True
    if not saved:
        return
    if new_status == "rejected" and email_by_id.get(payment["user_id"]):
        first = (name_by_id.get(payment["user_id"]) or "there").split()[0]
        send_email(
            email_by_id[payment["user_id"]],
            f"{comp['name']}: please upload your payment screenshot again",
            f"Hi {first},\n\nYour registration fee screenshot for {comp['name']} couldn't be "
            "confirmed. Please check the payment went through and upload the screenshot "
            "again on the Competitions page of the dashboard.\n\n- RoboKnights",
        )
    st.rerun()


def _render_host(comp, owed, payments, storage):
    cid = comp["competition_id"]
    users = {u["user_id"]: u for u in cached_table("users")}
    name_by_id = {k: u.get("name") or "" for k, u in users.items()}
    email_by_id = {k: u.get("email") or "" for k, u in users.items()}
    try:
        guest_name = {g["adm"]: f"{g['name']} ({g['club']})" for g in cached_table("event_guest_selections")}
    except Exception:
        guest_name = {}
    pay_by_uid = {p["user_id"]: p for p in payments}

    expected = sum(total(rows) for rows in owed.values())
    confirmed = sum(float(p["amount"]) for p in payments if p["status"] == "paid")
    waiting = [p for p in payments if p["status"] == "pending_review"]
    label = "Registration fees"
    if comp.get("fee_per_event"):
        label += f" · {rupees(confirmed)} of {rupees(expected)} confirmed"
        if waiting:
            label += f" · {len(waiting)} to check"

    with st.expander(label, icon=":material/payments:", key=f"fee_host_{cid}"):
        _render_host_setup(comp, storage)
        if not comp.get("fee_per_event"):
            st.caption("Set a fee per event above to start collecting.")
            return

        m1, m2, m3 = st.columns(3)
        m1.metric("Expected", rupees(expected), border=True)
        m2.metric("Confirmed", rupees(confirmed), border=True)
        m3.metric("To check", len(waiting), border=True)

        for p in waiting:
            with st.container(border=True, key=f"rkcard_fee_{p['payment_id']}"):
                due_now = total(owed.get(f"user:{p['user_id']}", []))
                note = "" if float(p["amount"]) == due_now else f" (share is now {rupees(due_now)})"
                st.markdown(f"**{name_by_id.get(p['user_id'], 'Unknown')}** paid **{rupees(p['amount'])}**{note}")
                if storage is not None and p.get("screenshot_path"):
                    try:
                        st.image(storage.storage.from_(BUCKET).download(p["screenshot_path"]), width=260)
                    except Exception:
                        st.caption(":material/error: Couldn't load the screenshot right now.")
                ok_col, no_col = st.columns(2)
                if ok_col.button("Confirm", key=f"fee_ok_{p['payment_id']}", icon=":material/check:",
                                 type="primary", width="stretch"):
                    _review(p, "paid", comp, name_by_id, email_by_id)
                if no_col.button("Reject", key=f"fee_no_{p['payment_id']}", icon=":material/close:",
                                 width="stretch"):
                    _review(p, "rejected", comp, name_by_id, email_by_id)

        table = []
        for key, rows in owed.items():
            kind, ident = key.split(":", 1)
            p = pay_by_uid.get(ident) if kind == "user" else None
            table.append({
                "Name": name_by_id.get(ident, "Unknown") if kind == "user" else guest_name.get(ident, ident),
                "Events": ", ".join(e["name"] for e, _, _ in rows),
                "Owes": rupees(total(rows)),
                "Status": (
                    STATUS[p["status"]][2].split(",")[0] if p
                    else "Pays by replying to the email" if kind == "guest" else "Not paid yet"
                ),
            })
        table.sort(key=lambda r: (r["Status"] != "Not paid yet", r["Name"]))
        st.dataframe(table, hide_index=True)
        st.caption(
            "Sister-club students have no dashboard account, so they send their screenshot by "
            "replying to the fee email. Mark those off by hand."
        )


def render_fees(comp, events):
    # Called on every competition card; draws nothing unless it applies.
    if comp.get("is_past") or comp.get("not_attending"):
        return
    is_host = st.session_state.is_host
    if not is_host and not comp.get("fee_per_event"):
        return
    if "fee_per_event" not in comp:
        if is_host:
            with st.expander("Registration fees", icon=":material/payments:"):
                st.caption(
                    "Needs a one-time database change: run the \"Competition registration fees\" "
                    "statements at the end of supabase_schema.sql in Supabase's SQL editor."
                )
        return
    if not _payments_ready():
        return
    storage = get_storage_client()
    owed = _owed_for(comp, events)
    payments = _payments_for(comp["competition_id"])
    if is_host:
        _render_host(comp, owed, payments, storage)
    # A host who is also selected for an event pays like everyone else.
    _render_member(comp, owed, payments, storage)

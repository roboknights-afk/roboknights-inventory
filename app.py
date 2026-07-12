import os
import smtplib
from datetime import date, timedelta
from email.mime.text import MIMEText

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

# Secrets (the Supabase URL and key) live in a local .env file, not in this
# file, so they never get accidentally shared or committed.
load_dotenv()

# Where this app is running. The new-request email links back here. Reads
# from an APP_URL secret if one is set (e.g. once deployed), otherwise
# falls back to your own laptop — so deploying doesn't need a code change,
# just one new secret.
APP_URL = os.environ.get("APP_URL", "http://localhost:8501")

# Must be the first Streamlit call in the script. "wide" gives the
# multi-column parts table room to breathe instead of squeezing everything
# into a narrow centered strip.
st.set_page_config(
    page_title="RoboKnights Parts Inventory",
    page_icon="static/roboknights_logo.svg",
    layout="wide",
)
st.logo("static/roboknights_logo.svg", size="large")


# --- Email notifications ------------------------------------------------------

def send_email(to_email, subject, body):
    # Plain-text email over the same Brevo SMTP relay Supabase's own login
    # emails already use. If sending fails for any reason (bad network, a
    # typo'd email, Brevo hiccup), we don't want that to break the actual
    # request/approve/reject action — the database change already happened;
    # the email is a nice-to-have on top, not something to fail loudly over.
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_SENDER"]
    msg["To"] = to_email

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
            server.starttls()
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    except Exception:
        pass


# --- Database setup ----------------------------------------------------------

def get_client():
    # Talks to Supabase over the internet instead of opening a local file.
    # The three tables (users, parts, requests) already exist in Supabase —
    # see supabase_schema.sql — this script no longer creates them.
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


# --- Login / signup ----------------------------------------------------------

def show_login_signup(client):
    # This whole screen only appears when nobody is logged in yet.
    # Everything sits in one centered, card-width column — on a wide layout,
    # a login form stretched across the full screen looks broken.
    pad_left, middle, pad_right = st.columns([1, 1.1, 1])
    with middle:
        # The wordmark logo is black-on-transparent, which would nearly
        # disappear on our dark theme — so it gets its own small white card.
        # This one CSS rule is scoped to just this card (via key=) rather
        # than touching the app's overall theming, which stays in config.toml.
        with st.container(key="rk_wordmark_box"):
            st.image("static/RKs Logo (2).png")
        st.html("""
            <style>
            .st-key-rk_wordmark_box {
                background-color: white;
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 8px;
            }
            </style>
        """)

        st.title("Parts Inventory", text_alignment="center")
        st.caption("Log in or create an account to continue.", text_alignment="center")

        with st.container(border=True):
            login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

            with login_tab:
                email = st.text_input("Email", key="login_email")
                password = st.text_input("Password", type="password", key="login_password")
                if st.button("Log in", icon=":material/login:", type="primary", width="stretch"):
                    try:
                        result = client.auth.sign_in_with_password({"email": email, "password": password})
                        st.session_state.auth_user = {"id": result.user.id, "email": result.user.email}
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't log in: {e}")

                # Sends the user to the separate reset screen (below). A plain
                # button + full screen swap avoids Streamlit's habit of
                # collapsing expanders and resetting tabs mid-flow.
                if st.button("Forgot password?", type="tertiary"):
                    st.session_state.show_reset = True
                    st.rerun()

            with signup_tab:
                name = st.text_input("Your name", key="signup_name")
                email = st.text_input("Email", key="signup_email")
                password = st.text_input("Password", type="password", key="signup_password")
                if st.button("Sign up", icon=":material/person_add:", type="primary", width="stretch"):
                    try:
                        result = client.auth.sign_up({"email": email, "password": password})

                        # Supabase quirk: if this email ALREADY has an account,
                        # it doesn't error — it "succeeds" but sends no email
                        # (so strangers can't probe which emails are registered).
                        # The giveaway is an empty identities list.
                        already_registered = result.user is not None and not result.user.identities
                        if already_registered:
                            st.error(
                                "This email already has an account — no email will be "
                                "sent. Log in instead, or use Forgot password on the "
                                "Log in tab."
                            )
                        else:
                            # Save the name alongside the real login id, in our
                            # own table — Supabase Auth only knows email/password.
                            client.table("users").upsert({
                                "user_id": result.user.id,
                                "name": name,
                                "email": email,
                            }).execute()

                            try:
                                # Only succeeds right away if "Confirm email" is
                                # off in Supabase. Otherwise they have to click
                                # the email link first.
                                login_result = client.auth.sign_in_with_password({"email": email, "password": password})
                                st.session_state.auth_user = {"id": login_result.user.id, "email": login_result.user.email}
                                st.rerun()
                            except Exception:
                                st.success("Account created! Check your email (including spam) to confirm it, then log in above.")
                    except Exception as e:
                        st.error(f"Couldn't sign up: {e}")

                # If the confirmation email never arrived (spam filter, typo
                # fixed, etc.), asks Supabase to send it again.
                if st.button("Resend confirmation email", icon=":material/mail:", type="tertiary"):
                    try:
                        client.auth.resend({"type": "signup", "email": email})
                        st.success("Confirmation email resent — check your inbox and spam folder.")
                    except Exception as e:
                        st.error(f"Couldn't resend: {e}")


def show_reset_screen(client):
    # A code-based password reset. No magic links: the email carries a plain
    # 6-digit code (see supabase_email_template.txt) that the user reads and
    # types in here. This sidesteps all the browser-security trouble that
    # clicking a reset *link* ran into inside Streamlit.
    # Same centered card-width column as the login screen.
    pad_left, middle, pad_right = st.columns([1, 1.1, 1])
    with middle:
        st.title("Reset your password", text_alignment="center")

        # Which of the two steps we're on. Kept in session_state so it
        # survives the reruns that button clicks cause.
        if "reset_sent_to" not in st.session_state:
            st.session_state.reset_sent_to = None

        with st.container(border=True):
            if st.session_state.reset_sent_to is None:
                # Step 1: ask for the email, send a code to it.
                email = st.text_input("Your email", key="reset_email")
                if st.button("Send reset code", icon=":material/send:", type="primary", width="stretch"):
                    try:
                        client.auth.reset_password_for_email(email)
                        st.session_state.reset_sent_to = email
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't send code: {e}")
            else:
                # Step 2: use whatever the email contained. Depending on how
                # the Supabase email template is set up, that's either a
                # 6-digit code ({{ .Token }}) or a clickable link. Both work
                # here: for a link, paste the whole thing and we pull the
                # token_hash out of it.
                st.write(f"We emailed **{st.session_state.reset_sent_to}**.")
                code = st.text_input(
                    "6-digit code from the email — or paste the full reset link",
                    key="reset_code",
                )
                new_password = st.text_input("New password", type="password", key="reset_new_password")
                if st.button("Update password", icon=":material/lock_reset:", type="primary", width="stretch"):
                    try:
                        entered = code.strip()
                        if "token_hash=" in entered:
                            # They pasted a link. Grab the token_hash=... value.
                            token_hash = entered.split("token_hash=")[1].split("&")[0]
                            client.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
                        elif entered.startswith("http") and "token=" in entered:
                            # Older link style: ...verify?token=pkce_xxx&type=recovery
                            token_hash = entered.split("token=")[1].split("&")[0]
                            client.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
                        else:
                            # A plain code they typed in.
                            client.auth.verify_otp({
                                "email": st.session_state.reset_sent_to,
                                "token": entered,
                                "type": "recovery",
                            })
                        client.auth.update_user({"password": new_password})
                        st.success("Password updated! Go back and log in with your new password.")
                    except Exception as e:
                        st.error(f"Couldn't update password: {e}")

            if st.button("← Back to login", type="tertiary"):
                st.session_state.reset_sent_to = None
                st.session_state.show_reset = False
                st.rerun()


# --- App ---------------------------------------------------------------------

client = get_client()

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
if "show_reset" not in st.session_state:
    st.session_state.show_reset = False

# Nobody logged in yet — show either the reset screen or the login/signup
# screen, then stop here so the parts list below stays hidden.
if st.session_state.auth_user is None:
    if st.session_state.show_reset:
        show_reset_screen(client)
    else:
        show_login_signup(client)
    st.stop()

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

# --- Who am I? -----------------------------------------------------------
# Real login now — no more dropdown. current_user_id comes from the actual
# Supabase Auth session, not a guess.
users = client.table("users").select("user_id, name, email").order("name").execute().data
user_name_by_id = {u["user_id"]: u["name"] for u in users}
user_email_by_id = {u["user_id"]: u["email"] for u in users}

current_user_id = st.session_state.auth_user["id"]
current_user_name = user_name_by_id.get(current_user_id, st.session_state.auth_user["email"])

# --- Sidebar: account + add a part --------------------------------------------
# Account controls and the add-part form live in the sidebar so the main
# page is purely "the inventory" — less clutter, clearer focus.

if "part_added_message" not in st.session_state:
    st.session_state.part_added_message = None

with st.sidebar:
    with st.container(border=True):
        st.markdown(f":material/person: Logged in as\n\n**{current_user_name}**")
        if st.button("Log out", icon=":material/logout:"):
            client.auth.sign_out()
            st.session_state.auth_user = None
            st.rerun()

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

    # Shown right under the form. Stashed in session_state so it survives
    # the rerun the button click causes (same pattern as everywhere else).
    if st.session_state.part_added_message:
        kind, text = st.session_state.part_added_message
        (st.success if kind == "success" else st.error)(text)
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

# Shown here (top of the section) rather than "under" the deleted row, since
# that row won't exist anymore once the part is gone.
if st.session_state.deleted_part_message:
    st.info(st.session_state.deleted_part_message)
    st.session_state.deleted_part_message = None

parts = client.table("parts").select("*").order("part_number").execute().data
part_by_id = {p["part_id"]: p for p in parts}

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

    with st.container(border=True):
        col1, col2, col3, col4, col5, col6 = st.columns([1, 2, 2, 2, 1, 2], vertical_alignment="center")
        col1.write(part["part_number"])
        col2.write(part["name"])
        col3.write(owner_name)
        if is_available:
            col4.badge("Available", icon=":material/check_circle:", color="green")
        else:
            col4.badge("On loan", icon=":material/schedule:", color="orange")

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

        # Only the owner can mark their own on-loan part as returned — finishes
        # the last step of the lifecycle: on loan -> returned -> available again.
        if is_on_loan and is_mine:
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

        # Only lets you delete your own part while it's available — not while
        # it's on loan, so we never silently lose track of who currently has it.
        if is_available and is_mine:
            if col6.button("Delete", key=f"delete_{part['part_id']}", icon=":material/delete:"):
                # A part can't be deleted while old request rows still point at
                # it (foreign key), so its request history goes with it. That's
                # fine here — deleting a part means "this doesn't exist in our
                # inventory anymore," so its history isn't needed either.
                client.table("requests").delete().eq("part_id", part["part_id"]).execute()
                client.table("parts").delete().eq("part_id", part["part_id"]).execute()
                st.session_state.deleted_part_message = f"Deleted {part['part_number']} — {part['name']}."
                st.rerun()

        # Show "Returned" just once, right under the row you clicked on.
        if part["part_id"] == st.session_state.returned_part_id:
            st.success(f"Marked {part['part_number']} as returned — it's available again.")
            st.session_state.returned_part_id = None

        # Show "Requested" just once, right under the row you clicked on.
        if part["part_id"] == st.session_state.requested_part_id:
            st.success(f"Requested — waiting for {owner_name} to approve.")
            st.session_state.requested_part_id = None

# --- Requests for my parts ---------------------------------------------------

# Plain text on purpose, no icon prefix — the "Jump to it" link from the
# new-request email hardcodes #requests-for-my-parts as the anchor, and
# that anchor is auto-generated from this exact heading text.
st.subheader("Requests for my parts")

# Show the Approve/Reject outcome once. The request row itself disappears
# from the list below (it's no longer pending), so this appears here instead
# of "under" a row that's gone.
if st.session_state.decision_message:
    st.info(st.session_state.decision_message)
    st.session_state.decision_message = None

# my_requests was already fetched up top (the metric row needed the count).
if not my_requests:
    st.caption("No pending requests.")

for req in my_requests:
    part = part_by_id.get(req["part_id"])
    requester_name = user_name_by_id.get(req["requester_id"], "Unknown")
    # Older requests made before loan durations existed won't have this set.
    requested_days = req.get("requested_days") or 7

    with st.container(border=True):
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
        with st.container(border=True):
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
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 2], vertical_alignment="center")
            col1.write(f"{part['part_number']} — {part['name']}")
            col2.write(f"Borrowed from {owner_name}")
            col3.write(f"Due {format_due(req)}")

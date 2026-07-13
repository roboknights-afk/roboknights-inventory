import streamlit as st
from dotenv import load_dotenv

from shared import HOST_EMAILS, get_client

# Secrets (the Supabase URL and key) live in a local .env file, not in this
# file, so they never get accidentally shared or committed.
load_dotenv()

# Must be the first Streamlit call in the script. "wide" gives the
# multi-column parts table room to breathe instead of squeezing everything
# into a narrow centered strip.
st.set_page_config(
    page_title="RoboKnights Parts Inventory",
    page_icon="static/roboknights_logo.svg",
    layout="wide",
)
st.logo("static/roboknights_logo.svg", size="large")


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

        st.title("RoboKnights Dashboard", text_alignment="center")
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
                # This IS the login email too — just labeled to make clear it
                # should be the school one, not a personal address.
                email = st.text_input("Institutional email", key="signup_email")
                # Individual grade, not a band — competitions later filter who
                # can volunteer for a given event by exactly this number.
                grade = st.selectbox("Your grade", [7, 8, 9, 10, 11, 12], key="signup_grade")
                section = st.text_input("Section", key="signup_section")
                admission_no = st.text_input("Admission no.", key="signup_admission_no")
                phone_no = st.text_input("Phone no.", key="signup_phone_no")
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
                            # Save profile details alongside the real login id,
                            # in our own table — Supabase Auth only knows
                            # email/password.
                            client.table("users").upsert({
                                "user_id": result.user.id,
                                "name": name,
                                "email": email,
                                "grade": grade,
                                "section": section.strip(),
                                "admission_no": admission_no.strip(),
                                "phone_no": phone_no.strip(),
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
# screen, then stop here so the rest of the app stays hidden.
if st.session_state.auth_user is None:
    if st.session_state.show_reset:
        show_reset_screen(client)
    else:
        show_login_signup(client)
    st.stop()

# --- Who am I? -----------------------------------------------------------
# Real login now — no more dropdown. current_user_id comes from the actual
# Supabase Auth session, not a guess. Computed once here (not per-page) and
# stashed in session_state, since each page in app_pages/ runs as its own
# script and can't see plain local variables from this file.
users = client.table("users").select("user_id, name, email, grade").order("name").execute().data
st.session_state.user_name_by_id = {u["user_id"]: u["name"] for u in users}
st.session_state.user_email_by_id = {u["user_id"]: u["email"] for u in users}
st.session_state.user_grade_by_id = {u["user_id"]: u.get("grade") for u in users}

st.session_state.current_user_id = st.session_state.auth_user["id"]
st.session_state.current_user_name = st.session_state.user_name_by_id.get(
    st.session_state.current_user_id, st.session_state.auth_user["email"]
)
st.session_state.current_user_grade = st.session_state.user_grade_by_id.get(st.session_state.current_user_id)
st.session_state.is_host = st.session_state.auth_user["email"] in HOST_EMAILS

# --- Sidebar: account card -------------------------------------------------
# Lives here (not in a page file) so it shows up no matter which page is
# open — a page-specific sidebar section only renders while that page is
# the active one.
with st.sidebar:
    with st.container(border=True):
        st.markdown(f":material/person: Logged in as\n\n**{st.session_state.current_user_name}**")
        if st.button("Log out", icon=":material/logout:"):
            client.auth.sign_out()
            st.session_state.auth_user = None
            st.rerun()

# --- Navigation ------------------------------------------------------------

pages = [
    st.Page("app_pages/inventory.py", title="Inventory", icon=":material/inventory_2:"),
    st.Page("app_pages/competitions.py", title="Competitions", icon=":material/emoji_events:"),
    st.Page("app_pages/announcements.py", title="Announcements", icon=":material/campaign:"),
]
# Host-only page — only added to the nav at all when logged in as a host,
# so non-hosts never even see it listed in the sidebar.
if st.session_state.is_host:
    pages.append(st.Page("app_pages/members.py", title="Members", icon=":material/badge:"))

page = st.navigation(pages)
page.run()

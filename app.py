import base64
from pathlib import Path

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

# --- Animations (app-wide) ----------------------------------------------------
# Streamlit has no animation system of its own, so this is the one other
# deliberate exception (besides the login wordmark card) to the "no custom
# CSS" rule — added at the student's direct request. Display-only: nothing
# here changes how any feature works. Card hover effects target the stable
# `st-key-rkcard_*` classes Streamlit itself generates from
# st.container(key="rkcard_...") — NOT the auto-generated emotion classes,
# which change between Streamlit versions and would silently break.
st.html("""
    <style>
    /* Page content fades up on every page load / page switch */
    @keyframes rk-fade-up {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    [data-testid="stMainBlockContainer"] { animation: rk-fade-up 0.35s ease-out; }

    /* Sidebar slides in */
    @keyframes rk-slide-right {
        from { opacity: 0; transform: translateX(-14px); }
        to   { opacity: 1; transform: translateX(0); }
    }
    [data-testid="stSidebar"] > div:first-child { animation: rk-slide-right 0.3s ease-out; }

    /* Buttons lift on hover, press down on click (not tertiary/link-style ones) */
    button[data-testid="stBaseButton-primary"],
    button[data-testid="stBaseButton-secondary"] {
        transition: transform 0.12s ease, box-shadow 0.12s ease, background-color 0.12s ease;
    }
    button[data-testid="stBaseButton-primary"]:hover,
    button[data-testid="stBaseButton-secondary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.35);
    }
    button[data-testid="stBaseButton-primary"]:active,
    button[data-testid="stBaseButton-secondary"]:active {
        transform: translateY(0) scale(0.98);
        box-shadow: none;
    }
    button[data-testid="stBaseButton-tertiary"] { transition: color 0.12s ease; }

    /* Gold primary buttons need dark text — Streamlit defaults to white,
       which is unreadable on this shade and has no theme option to fix. */
    button[data-testid="stBaseButton-primary"],
    button[data-testid="stBaseButton-primary"] * { color: #1E1E1E !important; }

    /* List cards (parts, competitions, announcements) lift slightly on
       hover, with a soft gold glow tying into the accent color */
    div[class*="st-key-rkcard_"] {
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    div[class*="st-key-rkcard_"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 14px rgba(0, 0, 0, 0.3), 0 0 10px 1px rgba(232, 179, 61, 0.25);
        border-color: rgba(232, 179, 61, 0.6);
    }

    /* Cards stagger in one after another. "backwards" fill (NOT forwards)
       on purpose: it hides a card during its delay, but releases control
       once the animation ends — with forwards, the final keyframe would
       permanently override the hover transform above. */
    @keyframes rk-card-in {
        from { opacity: 0; transform: translateY(12px); }
    }
    div[class*="st-key-rkcard_"] { animation: rk-card-in 0.4s ease-out backwards; }
    div[class*="st-key-rkcard_"]:nth-child(2)  { animation-delay: 0.04s; }
    div[class*="st-key-rkcard_"]:nth-child(3)  { animation-delay: 0.08s; }
    div[class*="st-key-rkcard_"]:nth-child(4)  { animation-delay: 0.12s; }
    div[class*="st-key-rkcard_"]:nth-child(5)  { animation-delay: 0.16s; }
    div[class*="st-key-rkcard_"]:nth-child(6)  { animation-delay: 0.20s; }
    div[class*="st-key-rkcard_"]:nth-child(7)  { animation-delay: 0.24s; }
    div[class*="st-key-rkcard_"]:nth-child(8)  { animation-delay: 0.28s; }
    div[class*="st-key-rkcard_"]:nth-child(9)  { animation-delay: 0.32s; }
    div[class*="st-key-rkcard_"]:nth-child(10) { animation-delay: 0.36s; }
    div[class*="st-key-rkcard_"]:nth-child(11) { animation-delay: 0.40s; }
    div[class*="st-key-rkcard_"]:nth-child(12) { animation-delay: 0.44s; }
    div[class*="st-key-rkcard_"]:nth-child(13) { animation-delay: 0.48s; }
    div[class*="st-key-rkcard_"]:nth-child(14) { animation-delay: 0.52s; }
    div[class*="st-key-rkcard_"]:nth-child(n+15) { animation-delay: 0.56s; }

    /* "Requests for me" pulses gold while there's something to act on.
       The rkpulse container is only rendered when the count is non-zero
       (see inventory.py), and :has() lights up its surrounding column. */
    @keyframes rk-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(232, 179, 61, 0); }
        50%      { box-shadow: 0 0 14px 1px rgba(232, 179, 61, 0.35); border-color: rgba(232, 179, 61, 0.8); }
    }
    div[data-testid="stColumn"]:has(div[class*="st-key-rkpulse"]) {
        animation: rk-pulse 2.2s ease-in-out infinite;
    }

    /* The gear logo does a little turn when hovered */
    [data-testid="stHeaderLogo"] { transition: transform 0.4s ease; }
    [data-testid="stHeaderLogo"]:hover { transform: rotate(60deg); }

    /* Inputs ease their focus-border in instead of snapping */
    [data-testid="stTextInputRootElement"] { transition: border-color 0.15s ease; }
    </style>
""")

# --- Gear watermark -------------------------------------------------------
# A huge, extremely faint, slowly rotating gear in the bottom-right corner,
# behind everything interactive (pointer-events: none). Deliberately blurred
# and at 5% opacity so it reads as texture, not content. Inlined as a
# base64 data-URI because Streamlit doesn't serve the static/ folder over
# HTTP by default.
_gear_b64 = base64.b64encode(Path("static/roboknights_logo.svg").read_bytes()).decode()
st.html(f"""
    <style>
    @keyframes rk-watermark-spin {{
        from {{ transform: rotate(0deg); }}
        to   {{ transform: rotate(360deg); }}
    }}
    [data-testid="stApp"]::after {{
        content: "";
        position: fixed;
        width: 75vmin;
        height: 75vmin;
        right: -15vmin;
        bottom: -15vmin;
        background: url("data:image/svg+xml;base64,{_gear_b64}") no-repeat center / contain;
        opacity: 0.05;
        filter: blur(2px);
        pointer-events: none;
        z-index: 0;
        animation: rk-watermark-spin 120s linear infinite;
    }}
    </style>
""")


def render_gear_splash(direction="in"):
    # The gear overlay: "in" plays after login (spins up from tiny, then the
    # overlay fades to reveal the app), "out" plays on logout (spins away).
    # Two hard-won gotchas baked in:
    #  - st.html silently STRIPS inline <svg>, so this must be st.markdown
    #    with unsafe_allow_html=True instead.
    #  - the HTML must be flush-left: Markdown turns indented lines into a
    #    literal code block (the CSS would show up on screen as text).
    gear_svg = Path("static/roboknights_logo.svg").read_text(encoding="utf-8")
    if direction == "in":
        overlay_secs, gear_secs = "1.8s", "1.6s"
        gear_frames = """
0%   { transform: scale(0.2) rotate(0deg); opacity: 0; }
20%  { opacity: 1; }
75%  { transform: scale(1.0) rotate(540deg); opacity: 1; }
100% { transform: scale(1.3) rotate(720deg); opacity: 0; }"""
    else:
        overlay_secs, gear_secs = "1.3s", "1.1s"
        gear_frames = """
0%   { transform: scale(1.1) rotate(0deg); opacity: 1; }
100% { transform: scale(0.15) rotate(-540deg); opacity: 0; }"""
    st.markdown(f"""<style>
#rk-splash {{
    position: fixed; inset: 0; z-index: 999999; background: #242424;
    display: flex; align-items: center; justify-content: center;
    pointer-events: none;
    animation: rk-splash-fade {overlay_secs} ease forwards;
}}
#rk-splash svg {{
    width: 150px; height: 150px;
    animation: rk-splash-gear {gear_secs} cubic-bezier(0.4, 0, 0.2, 1) forwards;
}}
@keyframes rk-splash-gear {{{gear_frames}
}}
@keyframes rk-splash-fade {{
    0%, 80% {{ opacity: 1; }}
    100%    {{ opacity: 0; visibility: hidden; }}
}}
</style>
<div id="rk-splash">{gear_svg}</div>""", unsafe_allow_html=True)


# --- Login / signup ----------------------------------------------------------

# Only school addresses can sign up, log in, or reset a password now.
# Rather than typing the full address and risk a typo'd or personal domain,
# every email box here only takes the username — this appends
# @dpsrkp.net automatically so nothing else is possible to submit.
ALLOWED_EMAIL_DOMAIN = "@dpsrkp.net"


def build_school_email(username):
    # Defensive: if someone pastes their full address anyway, don't double
    # up the suffix.
    username = username.strip()
    if username.lower().endswith(ALLOWED_EMAIL_DOMAIN):
        username = username[: -len(ALLOWED_EMAIL_DOMAIN)]
    return f"{username}{ALLOWED_EMAIL_DOMAIN}"


def school_email_input(label, key):
    # A text_input with a fixed "@dpsrkp.net" suffix shown alongside it —
    # Streamlit has no native "input with suffix" widget, so this is just
    # two columns: the box, then the suffix as a label next to it.
    col1, col2 = st.columns([2, 1.2], vertical_alignment="bottom")
    username = col1.text_input(label, key=key, placeholder="yourusername")
    col2.markdown(f"`{ALLOWED_EMAIL_DOMAIN}`")
    return build_school_email(username)


ADMISSION_NO_PREFIXES = ["R", "E", "V"]


def admission_no_input(key_prefix):
    # Every real admission number seen so far starts with R, E, or V
    # (e.g. R22639) — a dropdown for the letter plus a plain number field
    # for the digits, instead of one free-text box prone to typos.
    col1, col2 = st.columns([1, 2], vertical_alignment="bottom")
    prefix = col1.selectbox("Admission no.", ADMISSION_NO_PREFIXES, key=f"{key_prefix}_prefix")
    digits = col2.text_input(
        "Admission no. digits", key=f"{key_prefix}_digits",
        label_visibility="collapsed", placeholder="12345",
    )
    return f"{prefix}{digits.strip()}"


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
                email = school_email_input("Email", key="login_username")
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
                # This IS the login email too — just the username half, the
                # @dpsrkp.net suffix is fixed and appended automatically.
                email = school_email_input("Institutional email", key="signup_username")
                # Individual grade, not a band — competitions later filter who
                # can volunteer for a given event by exactly this number.
                grade = st.selectbox("Your grade", [7, 8, 9, 10, 11, 12], key="signup_grade")
                section = st.text_input("Section", key="signup_section")
                admission_no = admission_no_input("signup_admission_no")
                phone_no = st.text_input("Phone no.", key="signup_phone_no")
                password = st.text_input("Password", type="password", key="signup_password")
                if st.button("Sign up", icon=":material/person_add:", type="primary", width="stretch"):
                    if email == ALLOWED_EMAIL_DOMAIN:
                        st.error("Enter your username.")
                    else:
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
                                    # Balloons fly after the rerun lands them in
                                    # the app (fired there — anything drawn here
                                    # would be wiped by the rerun itself).
                                    st.session_state.just_signed_up = True
                                    st.rerun()
                                except Exception:
                                    st.balloons()
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
                email = school_email_input("Your email", key="reset_username")
                if st.button("Send reset code", icon=":material/send:", type="primary", width="stretch"):
                    if email == ALLOWED_EMAIL_DOMAIN:
                        st.error("Enter your username.")
                    else:
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
    # Re-arm the gear splash so it plays again on the next login.
    st.session_state.splash_shown = False
    # Coming here straight from a Log out click: gear spins away once.
    if st.session_state.pop("splash_out", False):
        render_gear_splash("out")
    if st.session_state.show_reset:
        show_reset_screen(client)
    else:
        show_login_signup(client)
    st.stop()

# --- Gear splash ---------------------------------------------------------
# Plays exactly once per login: the gear spins up in the center, then the
# overlay fades away to reveal the app. pointer-events: none, so even while
# visible it can't block a click — and the session_state flag stops it
# replaying on every button-click rerun.
if not st.session_state.get("splash_shown"):
    st.session_state.splash_shown = True
    render_gear_splash("in")

# New-account celebration: balloons fly once, right after the very first
# login that immediately follows signing up.
if st.session_state.pop("just_signed_up", False):
    st.balloons()

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
            # Tells the login screen to play the gear spin-DOWN once.
            st.session_state.splash_out = True
            st.rerun()

# --- Navigation ------------------------------------------------------------

pages = [
    st.Page("app_pages/home.py", title="Home", icon=":material/home:"),
    st.Page("app_pages/inventory.py", title="Inventory", icon=":material/inventory_2:"),
    st.Page("app_pages/competitions.py", title="Competitions", icon=":material/emoji_events:"),
    st.Page("app_pages/announcements.py", title="Announcements", icon=":material/campaign:"),
    st.Page("app_pages/queries.py", title="Queries", icon=":material/quiz:"),
    st.Page("app_pages/meetings.py", title="Meetings", icon=":material/groups:"),
    st.Page("app_pages/achievements.py", title="Achievements", icon=":material/military_tech:"),
]
# Host-only page — only added to the nav at all when logged in as a host,
# so non-hosts never even see it listed in the sidebar.
if st.session_state.is_host:
    pages.append(st.Page("app_pages/members.py", title="Members", icon=":material/badge:"))

page = st.navigation(pages)
page.run()

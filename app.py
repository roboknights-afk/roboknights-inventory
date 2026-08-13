import base64
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from supabase_auth.helpers import generate_pkce_challenge, generate_pkce_verifier

from shared import (
    APP_URL, EXUN_CHANNEL_MEMBERS, EXUN_EMAILS, HOST_EMAILS, HOST_ROLES, cached_table,
    get_client, has_unread_exun_channel, has_unread_queries, invalidate_cache, safe_write, send_email,
)

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
    /* Stops scroll from chaining into a pull-to-refresh/bounce on the body
       once an inner scroll container (sidebar, chat, dataframe) hits its
       edge — most noticeable on mobile/trackpad. */
    html, body, [data-testid="stApp"] { overscroll-behavior: none; }

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
       hover, with a soft gold glow tying into the accent color. Targets the
       stable st-key-rkcard_* class Streamlit generates from
       st.container(key="rkcard_..."). Deliberately NOT scoped under
       stVerticalBlock: that assumes a fixed nesting depth Streamlit doesn't
       guarantee, and the border-wrapper testid doesn't exist in 1.60. */
    div[class*="st-key-rkcard_"] {
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    div[class*="st-key-rkcard_"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(232, 179, 61, 0.15);
        border-color: rgba(232, 179, 61, 0.45);
    }

    /* Metric tiles (st.metric with border=True) get the same treatment, so
       a summary strip reads as part of the same card system as the lists. */
    div[data-testid="stMetric"] {
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(232, 179, 61, 0.15);
        border-color: rgba(232, 179, 61, 0.45);
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
       (see inventory.py); the pulse lands on the bordered metric tile
       inside it, which is the element actually drawing a border. */
    @keyframes rk-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(232, 179, 61, 0); }
        50%      { box-shadow: 0 0 14px 1px rgba(232, 179, 61, 0.35); border-color: rgba(232, 179, 61, 0.8); }
    }
    div[class*="st-key-rkpulse"] div[data-testid="stMetric"] {
        animation: rk-pulse 2.2s ease-in-out infinite;
    }

    /* The gear logo does a little turn when hovered */
    [data-testid="stHeaderLogo"] { transition: transform 0.4s ease; }
    [data-testid="stHeaderLogo"]:hover { transform: rotate(60deg); }

    /* Inputs ease their focus-border in instead of snapping */
    [data-testid="stTextInputRootElement"] { transition: border-color 0.15s ease; }

    /* Chat messages (Queries, Exun channel) get a soft bubble background so
       a conversation reads as bubbles, not bare rows. The translucent grey
       works on both the dark and light theme without needing two rules. */
    [data-testid="stChatMessage"] {
        background: rgba(128, 128, 128, 0.08);
        border-radius: 12px;
        padding: 12px 16px;
    }

    /* Sidebar nav links nudge right on hover — same "this is alive"
       language as the buttons and cards. Anchor tags inside the nav are
       stable across Streamlit versions, unlike the emotion-cache classes. */
    [data-testid="stSidebarNav"] a { transition: transform 0.12s ease; }
    [data-testid="stSidebarNav"] a:hover { transform: translateX(3px); }

    /* Skeleton placeholder bars — used ONLY where something genuinely
       slow (a live external fetch, not a cached table read) is about to
       fill a specific shaped area, e.g. the E2C sheet scan results. Gold
       shimmer tying into the accent color, not a generic grey shimmer-
       library look. */
    @keyframes rk-skeleton-shimmer {
        0%   { background-position: -300px 0; }
        100% { background-position: 300px 0; }
    }
    .rk-skel-bar {
        height: 14px;
        border-radius: 6px;
        margin: 8px 0;
        background: linear-gradient(
            90deg,
            rgba(232, 179, 61, 0.08) 25%,
            rgba(232, 179, 61, 0.20) 50%,
            rgba(232, 179, 61, 0.08) 75%
        );
        background-size: 600px 100%;
        animation: rk-skeleton-shimmer 1.3s ease-in-out infinite;
    }
    .rk-skel-title { width: 55%; height: 18px; }
    .rk-skel-wide { width: 92%; }
    .rk-skel-narrow { width: 38%; }
    </style>
""")

# --- Gear watermark -------------------------------------------------------
# Two huge, extremely faint gears meshing in the CENTRE of the screen,
# behind everything interactive (pointer-events: none). Deliberately
# blurred and at 5% opacity so it reads as texture, not content. Inlined
# as base64 data-URIs because Streamlit doesn't serve static/ over HTTP.
#
# The logo is genuinely drawn as two interlocking gears, but as one baked
# SVG they could only ever spin together like a sticker — which is exactly
# what made it look fake. gear_big.svg / gear_small.svg are the same
# artwork split into its two gears (same 128x99 canvas each, so they stay
# in their drawn, meshed positions when stacked). Each layer then rotates
# about ITS OWN gear's centre (the transform-origin percentages below are
# those centres measured from the artwork), in OPPOSITE directions — and
# the small gear turns 1.836x faster, because that's the big:small radius
# ratio measured from the same artwork. That's real meshed-gear physics:
# the big one drives, the small one is driven, teeth speeds match.
#
# White artwork would vanish on the light theme; Streamlit doesn't expose
# the viewer's live theme pick to plain CSS (same limitation as the splash
# below), so prefers-color-scheme + invert(1) is the best available signal.
_gear_big_b64 = base64.b64encode(Path("static/gear_big.svg").read_bytes()).decode()
_gear_small_b64 = base64.b64encode(Path("static/gear_small.svg").read_bytes()).decode()
st.html(f"""
    <style>
    @keyframes rk-gear-drive {{
        from {{ transform: rotate(0deg); }}
        to   {{ transform: rotate(360deg); }}
    }}
    @keyframes rk-gear-driven {{
        from {{ transform: rotate(0deg); }}
        to   {{ transform: rotate(-360deg); }}
    }}
    /* One pseudo-element per gear, BOTH on stApp itself — a div injected by
       st.html would sit inside stMainBlockContainer, whose transform (from
       the fade-up animation) hijacks position:fixed and drags the "fixed"
       gears around with the page content (verified live in the DOM).
       Centred with calc() rather than translate(-50%,-50%) so the keyframe
       transform stays pure rotation. 80vmin wide; height is 80 x 99/128 =
       61.875vmin (the artwork's aspect), so half-height is 30.9375vmin. */
    [data-testid="stApp"]::before,
    [data-testid="stApp"]::after {{
        content: "";
        position: fixed;
        left: calc(50% - 40vmin);
        top: calc(50% - 30.9375vmin);
        width: 80vmin;
        height: 61.875vmin;
        background-repeat: no-repeat;
        background-size: 100% 100%;
        opacity: 0.05;
        filter: blur(2px);
        pointer-events: none;
        z-index: 0;
    }}
    [data-testid="stApp"]::before {{
        background-image: url("data:image/svg+xml;base64,{_gear_big_b64}");
        transform-origin: 65.3% 45.3%;
        animation: rk-gear-drive 110s linear infinite;
    }}
    [data-testid="stApp"]::after {{
        background-image: url("data:image/svg+xml;base64,{_gear_small_b64}");
        transform-origin: 18.9% 75.4%;
        animation: rk-gear-driven 59.9s linear infinite;
    }}
    @media (prefers-color-scheme: light) {{
        [data-testid="stApp"]::before,
        [data-testid="stApp"]::after {{ filter: blur(2px) invert(1); }}
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
/* Same limitation as the watermark above: Streamlit doesn't expose the
   viewer's live theme selection to plain CSS in this version, so this
   uses the OS-level signal instead — correct for "System", a reasonable
   default otherwise. Without it, a light-mode viewer would see a jarring
   near-black flash (and an invisible white-on-white gear) on every
   login/logout. */
@media (prefers-color-scheme: light) {{
    #rk-splash {{ background: #FFFFFF; }}
    #rk-splash svg {{ filter: invert(1); }}
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
    # autocomplete="username" is the standard hint browsers use to
    # recognize a login-identifier field — same value regardless of
    # whether this particular box is on the login, signup, or reset
    # screen, since it means the same thing on all three.
    col1, col2 = st.columns([2, 1.2], vertical_alignment="bottom")
    username = col1.text_input(
        label, key=key, placeholder="yourusername", autocomplete="username"
    )
    col2.markdown(f"`{ALLOWED_EMAIL_DOMAIN}`")
    return build_school_email(username)


# --- "Save my login details" -----------------------------------------------
# A real stay-logged-in session, using Supabase's OWN refresh token stored
# in a cookie — never the actual password — so you don't have to type your
# password in again next time you open the app. (The other half of "save my
# login details" — asking the BROWSER to offer saving the password itself —
# was tried via the Credential Management API and pulled back out: it
# requires a very recent, direct user gesture ("transient activation"), and
# Streamlit's login button click round-trips through the server before any
# script of ours runs, which used up that window every time. Not a bug to
# chase further — a real mismatch between what that API demands and how
# Streamlit is built. autocomplete="username"/"current-password" on the
# fields below is the actual fix: the standards-based hint every browser's
# OWN save-password heuristic already looks for, no custom JS involved.)
#
# Writing the cookie needs actual JS, and st.html() turned out not to run
# <script> tags at all (confirmed live: a script inside st.html silently
# does nothing, same family of gotcha as it stripping inline <svg> — see
# the gear splash notes below). st.components.v1.html DOES run scripts
# reliably, because it renders into a real sandboxed iframe rather than
# being poured into the page via innerHTML — confirmed live too: a cookie
# set from inside it shows up in st.context.cookies on the next load.
REMEMBER_ME_COOKIE = "rk_remember_token"
REMEMBER_ME_DAYS = 30


def _set_remember_cookie(refresh_token):
    max_age = REMEMBER_ME_DAYS * 24 * 60 * 60
    components.html(
        f"""<script>
        window.parent.document.cookie =
            "{REMEMBER_ME_COOKIE}={refresh_token}; max-age={max_age}; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _clear_remember_cookie():
    components.html(
        f"""<script>
        window.parent.document.cookie = "{REMEMBER_ME_COOKIE}=; max-age=0; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


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


# --- Login with Google -------------------------------------------------------
# Requires Google enabled as a provider in Supabase (Authentication ->
# Providers -> Google) with a Google Cloud OAuth client — see DEPLOY.md for
# the exact steps and the redirect URI to register.
#
# Built by hand rather than via client.auth.sign_in_with_oauth(): that
# method generates the PKCE code_verifier and stores it in the CALLING
# client's own storage — and get_client() is @st.cache_resource, one
# client object shared by the whole app process, not one per visitor. Two
# people starting a Google login around the same time would silently
# overwrite each other's verifier under that default.
#
# The verifier is kept in a short-lived COOKIE, not st.session_state —
# confirmed live (the first real test of this feature) that session_state
# does NOT survive the round trip: clicking the button is a genuine
# top-level browser navigation away to Google, and when Supabase redirects
# back, that's a brand-new page load with a brand-new, empty Streamlit
# session. The verifier was already gone by the time the callback ran,
# which silently no-opped instead of logging anyone in. A cookie survives
# that navigation (SameSite=Lax explicitly allows it on a top-level GET
# redirect like this), same technique this file already uses for the
# "remember me" login token below.
GOOGLE_VERIFIER_COOKIE = "rk_google_pkce_verifier"


def _set_google_verifier_cookie(verifier):
    # 10 minutes is generous for "leave the page, sign into Google, come
    # back" — long enough for a slow sign-in, short enough that a verifier
    # left over from an abandoned attempt doesn't linger.
    components.html(
        f"""<script>
        window.parent.document.cookie =
            "{GOOGLE_VERIFIER_COOKIE}={verifier}; max-age=600; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _clear_google_verifier_cookie():
    components.html(
        f"""<script>
        window.parent.document.cookie = "{GOOGLE_VERIFIER_COOKIE}=; max-age=0; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _google_authorize_url():
    verifier = generate_pkce_verifier()
    _set_google_verifier_cookie(verifier)
    challenge = generate_pkce_challenge(verifier)
    params = {
        "provider": "google",
        "redirect_to": APP_URL,
        "code_challenge": challenge,
        "code_challenge_method": "s256" if challenge != verifier else "plain",
        # Narrows Google's OWN account picker to the school domain — purely
        # a convenience so members don't accidentally start with a personal
        # Gmail. NOT the security boundary: Google still lets someone pick
        # "use another account" here. handle_google_oauth_callback's
        # server-side @dpsrkp.net check below is the real gate.
        "hd": ALLOWED_EMAIL_DOMAIN.lstrip("@"),
    }
    # .rstrip("/") because this project's SUPABASE_URL has a trailing
    # slash (confirmed in .env) — get_client()'s create_client() call
    # tolerates that fine internally, but building the URL by hand here
    # doesn't, and would otherwise double up as ".co//auth/v1/...".
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    return f"{supabase_url}/auth/v1/authorize?" + urlencode(params)


def handle_google_oauth_callback(client):
    # Runs on every rerun before anything else decides what to show — but
    # only actually does something the one time a fresh ?code=... shows up
    # in the URL, right after Supabase redirects back here post-Google.
    # Cleared immediately either way, so refreshing the page afterward
    # doesn't try to re-exchange an already-spent (by then invalid) code.
    code = st.query_params.get("code")
    if not code:
        return
    st.query_params.clear()
    # Cookie, not st.session_state — see GOOGLE_VERIFIER_COOKIE comment
    # above for why: this callback runs in a BRAND NEW Streamlit session
    # (the click was a real top-level navigation away and back), so
    # session_state from before the click is already gone.
    verifier = st.context.cookies.get(GOOGLE_VERIFIER_COOKIE)
    _clear_google_verifier_cookie()
    if not verifier:
        # A stale/bookmarked callback URL, an expired 10-minute window, or
        # cookies blocked in the browser — nothing to recover from; just
        # fall through to a clean login screen instead of showing a
        # confusing exchange error for something that can't be retried
        # with this same code anyway.
        return
    try:
        result = client.auth.exchange_code_for_session(
            {"auth_code": code, "code_verifier": verifier}
        )
    except Exception as e:
        st.session_state.google_login_error = f"Google sign-in failed: {e}"
        return

    email = (result.user.email or "").lower()
    if not email.endswith(ALLOWED_EMAIL_DOMAIN):
        # The real domain check — see _google_authorize_url's hd= comment.
        # Signs them back out rather than leaving a live session for an
        # account that has no place in this app.
        client.auth.sign_out()
        st.session_state.google_login_error = (
            f"{result.user.email} isn't a {ALLOWED_EMAIL_DOMAIN} account — "
            "sign in with your school Google account instead."
        )
        return

    st.session_state.auth_user = {"id": result.user.id, "email": email}
    if result.session and result.session.refresh_token:
        # Always remembered, unlike the password form's checkbox — there's
        # no password-typing friction here to weigh "save it" against, so
        # defaulting to staying logged in is the friction-reducing choice a
        # one-click login is supposed to be.
        _set_remember_cookie(result.session.refresh_token)

    # Only students need the extra profile step: is_host/is_exun are
    # decided purely by email (see is_host/is_exun below), and
    # current_user_name already falls back to the raw email, so a host or
    # Exun account works fully even with no `users` row at all. A student
    # with no grade/section on file would silently look ineligible for
    # every competition, which is worse than one extra screen.
    has_profile = bool(
        client.table("users").select("user_id").eq("user_id", result.user.id).execute().data
    )
    if not has_profile and email not in HOST_EMAILS and email not in EXUN_EMAILS:
        st.session_state.needs_google_profile = {"user_id": result.user.id, "email": email}
    st.rerun()


def show_complete_google_profile_screen(client, user_id, email):
    # Same required fields sign-up already collects — Competitions
    # eligibility, the Members directory, etc. all assume they exist, so
    # this isn't optional polish, it's the same gate signup already has,
    # just without a password field since Google already authenticated them.
    pad_left, middle, pad_right = st.columns([1, 1.1, 1])
    with middle:
        st.title("One more step", text_alignment="center")
        st.caption(f"Signed in as {email} — finish setting up your profile.", text_alignment="center")
        with st.container(border=True):
            name = st.text_input("Your name", key="google_profile_name")
            is_staff = st.checkbox("I'm a staff member (not a student)", key="google_profile_is_staff")
            if is_staff:
                grade, section, admission_no = None, "", ""
            else:
                grade = st.selectbox("Your grade", [7, 8, 9, 10, 11, 12], key="google_profile_grade")
                section = st.text_input("Section", key="google_profile_section")
                admission_no = admission_no_input("google_profile_admission_no")
            phone_no = st.text_input("Phone no.", key="google_profile_phone_no")
            if st.button("Finish", icon=":material/check:", type="primary", width="stretch"):
                if not name.strip():
                    st.error("Name is required.")
                else:
                    with safe_write("finish setting up your profile"):
                        client.table("users").upsert({
                            "user_id": user_id,
                            "name": name.strip(),
                            "email": email,
                            "is_staff": is_staff,
                            "grade": grade,
                            "section": section.strip(),
                            "admission_no": admission_no.strip(),
                            "phone_no": phone_no.strip(),
                        }).execute()
                        invalidate_cache()
                    send_welcome_email(email, name.strip())
                    st.session_state.pop("needs_google_profile", None)
                    st.session_state.just_signed_up = True
                    st.rerun()


def send_welcome_email(to_email, name):
    # Sent once, right after signup — a plain-language tour of what the
    # app actually does, since a brand-new member has no way to know that
    # yet. One template for everyone (student, staff, or host) rather than
    # branching by role: whatever host-only or Exun-only tools someone has
    # will just show up naturally once they're logged in, and this email's
    # job is only to explain the parts every member sees.
    send_email(
        to_email,
        "Welcome to RoboKnights!",
        f"Hi {name},\n\n"
        f"Welcome to RoboKnights! Your account is set up — here's a quick "
        f"rundown of what you can do in the app:\n\n"
        f"Inventory — see every part the club owns, and borrow one from "
        f"another member in a couple of clicks. The owner gets notified and "
        f"approves it before it's yours.\n\n"
        f"Competitions — browse upcoming competitions and volunteer for any "
        f"event you're eligible for by grade. If you're selected, you'll "
        f"get an email, plus reminders leading up to the day.\n\n"
        f"Meetings — see what's scheduled, RSVP, and check in once you're "
        f"there.\n\n"
        f"Achievements — after a competition, log your result so it's on "
        f"record for the club.\n\n"
        f"Queries — a private line to ask a host a question directly.\n\n"
        f"Announcements — club-wide updates land here and in your inbox.\n\n"
        f"Log in any time at {APP_URL} to get started.\n\n"
        f"If you have any queries you can contact Naitik Jindal at 9311259439.\n\n"
        f"— RoboKnights",
    )


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

        if st.session_state.get("google_login_error"):
            st.error(st.session_state.pop("google_login_error"))

        with st.container(border=True):
            # st.link_button, not a hand-rendered <a> — reverted after
            # confirming live (real Playwright frame inspection) that
            # st.html() renders its content inside a SANDBOXED IFRAME, not
            # the top-level page. Clicking a link inside that iframe tries
            # to navigate the IFRAME to Google's consent screen - and
            # Google refuses to render its own sign-in page inside anyone
            # else's iframe (standard anti-clickjacking protection, same
            # family of defense this app itself doesn't have a reason to
            # disable). Net effect: nothing visibly happens on click - "the
            # button doesn't work" - worse than the new-tab problem this
            # was meant to fix. st.link_button's anchor, by contrast, lives
            # in the REAL top-level page DOM (confirmed earlier via direct
            # DOM inspection: stElementContainer/stVerticalBlock, no
            # iframe involved) - it's the right home for a real navigation,
            # the only problem was its hardcoded target="_blank".
            #
            # Fixed a different way: keep st.link_button for correct DOM
            # placement and styling, then patch out its target/rel
            # attributes via the SAME window.parent.document technique
            # this app already uses successfully elsewhere
            # (_set_remember_cookie, the feedback pill) - reaching from a
            # sandboxed components.html() iframe into the REAL page to
            # mutate an element that's already there, rather than trying
            # to render the clickable element itself inside a sandbox.
            # Runs UNCONDITIONALLY on every script run, not gated behind a
            # "create once" check - the earlier feedback-pill saga in this
            # app already learned that lesson the hard way: the anchor
            # persists across Streamlit reruns via its own diffing, so a
            # guard here would mean this fix only ever applies once and
            # silently stops working the moment Streamlit re-renders it
            # with target="_blank" again.
            authorize_url = _google_authorize_url()
            st.link_button(
                "Continue with Google", authorize_url,
                width="stretch", key="rk_google_login_btn",
            )
            components.html("""
                <script>
                (function() {
                    // Polls instead of a single one-shot query - confirmed
                    // live that a one-shot attempt runs BEFORE
                    // st.link_button's real anchor has actually mounted
                    // into the top-level page (this components.html
                    // iframe can finish loading and execute before that
                    // element exists), so it silently found nothing and
                    // never got a second chance. A MutationObserver would
                    // also work, but a short poll is simpler and this
                    // only needs to succeed once, within a couple seconds
                    // of page load.
                    const SELECTOR = '.st-key-rk_google_login_btn a[data-testid="stBaseLinkButton-secondary"]';
                    let attempts = 0;
                    const timer = setInterval(function() {
                        attempts++;
                        const a = window.parent.document.querySelector(SELECTOR);
                        if (a) {
                            // "_top", NOT removed. Streamlit Cloud serves
                            // the whole app inside an iframe (confirmed
                            // live: the app's real DOM lives in a
                            // ".../~/+/" frame, NOT the top-level
                            // document - localhost has no such wrapper,
                            // which is exactly why every local test of
                            // this passed while production stayed
                            // broken). With no target, the click
                            // navigates that IFRAME to Google, and
                            // Google refuses to render its sign-in page
                            // inside anyone's iframe -> nothing visibly
                            // happens. With "_blank" it opens a new tab,
                            // completes the login THERE, and leaves the
                            // tab the member is actually looking at
                            // untouched. "_top" is the one that's right:
                            // navigates the top-level page, same tab,
                            // escaping the iframe.
                            a.setAttribute('target', '_top');
                            a.removeAttribute('rel');
                            clearInterval(timer);
                        } else if (attempts > 100) {  // ~10s at 100ms
                            clearInterval(timer);
                        }
                    }, 100);
                })();
                </script>
            """, height=0)
            st.html("""
                <style>
                .st-key-rk_google_login_btn a[data-testid="stBaseLinkButton-secondary"] {
                    background: #ffffff !important;
                    color: #3c4043 !important;
                    border: 1px solid #dadce0 !important;
                    border-radius: 4px !important;
                    font-family: 'Roboto', Arial, sans-serif !important;
                    font-weight: 500 !important;
                    font-size: 0.95rem !important;
                    box-shadow: none !important;
                    position: relative;
                    padding-left: 42px !important;
                    transition: box-shadow .15s ease, background-color .15s ease;
                }
                .st-key-rk_google_login_btn a[data-testid="stBaseLinkButton-secondary"]:hover {
                    background: #f8f9fa !important;
                    box-shadow: 0 1px 2px rgba(60,64,67,.30), 0 1px 3px 1px rgba(60,64,67,.15) !important;
                }
                /* The real 4-color G mark, inline — no external request,
                   consistent with how this app handles every other icon. */
                .st-key-rk_google_login_btn a[data-testid="stBaseLinkButton-secondary"]::before {
                    content: "";
                    position: absolute;
                    left: 14px;
                    top: 50%;
                    transform: translateY(-50%);
                    width: 18px;
                    height: 18px;
                    background-repeat: no-repeat;
                    background-size: contain;
                    background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' width='18' height='18'%3E%3Cpath fill='%234285F4' d='M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z'/%3E%3Cpath fill='%2334A853' d='M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z'/%3E%3Cpath fill='%23FBBC05' d='M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24s.85 6.91 2.34 9.88l7.35-5.7z'/%3E%3Cpath fill='%23EA4335' d='M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z'/%3E%3C/svg%3E");
                }
                </style>
            """)
            st.caption(
                "Only @dpsrkp.net Google accounts can sign in this way.",
                text_alignment="center",
            )
            st.divider()

            login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

            with login_tab:
                email = school_email_input("Email", key="login_username")
                password = st.text_input(
                    "Password", type="password", key="login_password",
                    autocomplete="current-password",
                )
                save_login = st.checkbox(
                    "Save my login details on this device",
                    key="login_save_details",
                    help="Stays logged in on this device using a secure session token — "
                         "never your actual password — so you don't have to type your "
                         "password in again next time.",
                )
                if st.button("Log in", icon=":material/login:", type="primary", width="stretch"):
                    try:
                        result = client.auth.sign_in_with_password({"email": email, "password": password})
                        st.session_state.auth_user = {"id": result.user.id, "email": result.user.email}
                        if save_login and result.session and result.session.refresh_token:
                            _set_remember_cookie(result.session.refresh_token)
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
                # Grade/section/admission no. only mean anything for a
                # student — a staff account (the teacher in-charge) skips
                # them entirely rather than being asked for placeholder
                # values that don't describe them.
                is_staff = st.checkbox("I'm a staff member (not a student)", key="signup_is_staff")
                if is_staff:
                    grade, section, admission_no = None, "", ""
                else:
                    # Individual grade, not a band — competitions later
                    # filter who can volunteer for a given event by exactly
                    # this number.
                    grade = st.selectbox("Your grade", [7, 8, 9, 10, 11, 12], key="signup_grade")
                    section = st.text_input("Section", key="signup_section")
                    admission_no = admission_no_input("signup_admission_no")
                phone_no = st.text_input("Phone no.", key="signup_phone_no")
                password = st.text_input(
                    "Password", type="password", key="signup_password",
                    autocomplete="new-password",
                )
                if st.button("Sign up", icon=":material/person_add:", type="primary", width="stretch"):
                    if email == ALLOWED_EMAIL_DOMAIN:
                        st.error("Enter your username.")
                    else:
                        try:
                            # email_redirect_to: after Supabase confirms the
                            # account (server-side, on Supabase's own end —
                            # nothing Streamlit has to do), it sends the
                            # browser here with ?verified=1 so we can show a
                            # friendly "you're verified" screen instead of
                            # just dumping them back on a bare login form.
                            # This is NOT the same failure mode as the old
                            # password-reset link problem: that broke because
                            # Streamlit itself had to act on a link INSIDE
                            # its sandboxed iframe; this redirect happens
                            # entirely on Supabase's server before the
                            # browser ever gets here, so there's nothing for
                            # the sandbox to block.
                            result = client.auth.sign_up({
                                "email": email, "password": password,
                                "options": {"email_redirect_to": f"{APP_URL}?verified=1"},
                            })

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
                                    "is_staff": is_staff,
                                    "grade": grade,
                                    "section": section.strip(),
                                    "admission_no": admission_no.strip(),
                                    "phone_no": phone_no.strip(),
                                }).execute()
                                invalidate_cache()
                                # Sent here (account created), not after the
                                # login attempt below — that part only decides
                                # whether they're dropped straight into the app
                                # or have to confirm their email first, and the
                                # welcome should go out either way.
                                send_welcome_email(email, name)

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


def show_email_verified_screen():
    # Landed here via ?verified=1, which Supabase only adds AFTER it has
    # already confirmed the account server-side — so by the time this
    # renders, verification is simply done. No token to check, no action
    # to take here; this is purely a friendlier landing than a bare login
    # form. Ticks a 10s countdown down live in place (a plain Python loop
    # writing into one placeholder — no JS, no browser navigation, so
    # none of the sandboxed-iframe trouble a real redirect ran into
    # elsewhere in this app), then clears the query param and reruns to
    # show the normal login screen. The button does the same thing
    # instantly, for anyone who doesn't want to wait.
    pad_left, middle, pad_right = st.columns([1, 1.1, 1])
    with middle:
        st.title("Email verified", text_alignment="center")
        with st.container(border=True):
            st.success(":material/check_circle: Your email is confirmed — you can log in now.")
            countdown_placeholder = st.empty()
            if st.button("Go to login now", icon=":material/login:", type="primary", width="stretch"):
                st.query_params.clear()
                st.rerun()
            for seconds_left in range(10, 0, -1):
                countdown_placeholder.caption(
                    f"Taking you to the login page in {seconds_left} second(s)…"
                )
                time.sleep(1)
            st.query_params.clear()
            st.rerun()


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
                new_password = st.text_input(
                    "New password", type="password", key="reset_new_password",
                    autocomplete="new-password",
                )
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

# Runs before anything else decides what to show — only actually does
# something the one time a fresh ?code=... is present, right after
# Supabase redirects back here post-Google. Has to happen before the
# "already logged in?" checks below, since THIS is what sets auth_user in
# the first place for a Google login.
if st.session_state.auth_user is None:
    handle_google_oauth_callback(client)

# Signed in via Google but their profile (grade/section/etc.) still needs
# filling in — same required fields signup collects, just without a
# password field. Checked before the normal auth gate so this screen
# shows even though auth_user IS already set.
if st.session_state.get("needs_google_profile"):
    pending = st.session_state.needs_google_profile
    show_complete_google_profile_screen(client, pending["user_id"], pending["email"])
    st.stop()

# Silently try a "remembered" session before showing any login UI at all —
# see _set_remember_cookie for how this cookie gets written in the first
# place. Guarded by tried_remember_login so a bad/expired token (cleared
# below) only gets ONE retry attempt per browser session, not one on every
# single rerun of the login screen.
if st.session_state.auth_user is None and not st.session_state.get("tried_remember_login"):
    st.session_state.tried_remember_login = True
    remembered_token = st.context.cookies.get(REMEMBER_ME_COOKIE)
    if remembered_token:
        try:
            result = client.auth.refresh_session(remembered_token)
            st.session_state.auth_user = {"id": result.user.id, "email": result.user.email}
            # Refresh tokens rotate on every use — the one we just spent is
            # already invalid, so the cookie has to move to the NEW one or
            # the next visit's silent restore would fail.
            _set_remember_cookie(result.session.refresh_token)
            st.rerun()
        except Exception:
            _clear_remember_cookie()

# Nobody logged in yet — show the email-verified landing, the reset
# screen, or the login/signup screen, then stop here so the rest of the
# app stays hidden.
if st.session_state.auth_user is None:
    # Re-arm the gear splash so it plays again on the next login.
    st.session_state.splash_shown = False
    # Coming here straight from a Log out click: gear spins away once.
    if st.session_state.pop("splash_out", False):
        render_gear_splash("out")
    if st.query_params.get("verified") == "1":
        show_email_verified_screen()
    elif st.session_state.show_reset:
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
#
# This runs on EVERY page load across the whole app (app.py re-executes
# before routing to whichever page is open), so this is the single highest-
# value read to cache — cached_table means most clicks anywhere in the app
# read this from memory instead of a fresh Supabase round trip.
users = sorted(cached_table("users"), key=lambda u: u["name"])
st.session_state.user_name_by_id = {u["user_id"]: u["name"] for u in users}
st.session_state.user_email_by_id = {u["user_id"]: u["email"] for u in users}
st.session_state.user_grade_by_id = {u["user_id"]: u.get("grade") for u in users}
st.session_state.user_is_staff_by_id = {u["user_id"]: u.get("is_staff", False) for u in users}

st.session_state.current_user_id = st.session_state.auth_user["id"]
st.session_state.current_user_name = st.session_state.user_name_by_id.get(
    st.session_state.current_user_id, st.session_state.auth_user["email"]
)
st.session_state.current_user_grade = st.session_state.user_grade_by_id.get(st.session_state.current_user_id)
st.session_state.current_user_is_staff = st.session_state.user_is_staff_by_id.get(st.session_state.current_user_id, False)
st.session_state.is_host = st.session_state.auth_user["email"] in HOST_EMAILS
# A limited external tier for Exun (RoboKnights' sister club): can VIEW
# Competitions/Meetings/Achievements/Members, but never volunteer, RSVP,
# log an achievement, or touch anything host-only. Checked separately
# from is_host — the two are mutually exclusive in practice.
st.session_state.is_exun = st.session_state.auth_user["email"] in EXUN_EMAILS

# --- Report an issue (floating, global) --------------------------------------
# A single low-friction way to flag anything that feels off, available on
# every page (not a dedicated page of its own) since something worth
# reporting can happen anywhere and the details are freshest the moment
# it's noticed. Deliberately NOT categorized (bug vs. suggestion vs...) —
# someone hitting something odd often can't tell which bucket it belongs
# in, and asking them to guess is exactly the kind of friction that stops
# a report from ever being sent.
#
# First cut of this lived in the sidebar to dodge a suspected CSS issue
# (stMainBlockContainer's fade-up animation briefly gives it a transform,
# which can make it the containing block for `position: fixed`
# descendants instead of the viewport) — but the WhatsApp bubble on the
# Queries page already proves a plain fixed-position element nested in the
# main content works fine here, so that wasn't actually it. The real
# culprit was almost certainly the CSS selector: `.st-key-rk_feedback_fab`
# is an exact class match, but Streamlit doesn't always put that class on
# the element you'd expect — which is exactly why the card hover/pulse
# effects further up this file target `div[class*="st-key-rkcard_"]`
# (a substring match) instead of an exact one. Using that same pattern here.
if "show_report_feedback" not in st.session_state:
    st.session_state.show_report_feedback = False
if "feedback_message" not in st.session_state:
    st.session_state.feedback_message = None


def _close_report_feedback():
    st.session_state.show_report_feedback = False


def _open_report_feedback():
    st.session_state.show_report_feedback = True


@st.dialog("Report something", on_dismiss=_close_report_feedback)
def render_report_feedback():
    st.caption(
        "Anything that felt broken, confusing, or just worth improving — no "
        "need to know if it's a real bug, just describe what happened. "
        "Every report lands on the Feedback page for a host to follow up."
    )
    body = st.text_area(
        "What happened?", key="feedback_body",
        placeholder="What were you doing, what happened, and what did you expect instead?",
    )
    if st.button("Submit", icon=":material/send:", type="primary", key="confirm_report_feedback"):
        if not body.strip():
            st.error("Description can't be empty.")
        else:
            with safe_write("submit this report"):
                client.table("feedback").insert({
                    "user_id": st.session_state.current_user_id,
                    "body": body.strip(),
                }).execute()
                invalidate_cache()

                host_emails = [
                    email for uid, email in st.session_state.user_email_by_id.items()
                    if email in HOST_EMAILS
                ]
                for email in host_emails:
                    send_email(
                        email,
                        f"New report from {st.session_state.current_user_name}",
                        f"{st.session_state.current_user_name} reported:\n\n{body.strip()}\n\n"
                        f"See it here: {APP_URL}",
                    )

            st.session_state.feedback_message = "Thanks — your report is in."
            st.session_state.pop("feedback_body", None)
            _close_report_feedback()
            st.rerun()


# --- Sidebar: account card -------------------------------------------------
# Lives here (not in a page file) so it shows up no matter which page is
# open — a page-specific sidebar section only renders while that page is
# the active one.
with st.sidebar:
    with st.container(border=True):
        st.caption(":material/person: Logged in as")
        st.markdown(f"**{st.session_state.current_user_name}**")

        # Role + grade at a glance, so it's obvious which account you're on
        # (easy to lose track when testing with more than one).
        if st.session_state.is_host:
            host_title = HOST_ROLES.get(st.session_state.auth_user["email"])
            st.badge(
                host_title if host_title else "Host",
                color="primary", icon=":material/shield_person:",
            )
        elif st.session_state.is_exun:
            st.badge("Exun (Sister Club)", color="blue", icon=":material/handshake:")
        elif st.session_state.current_user_is_staff:
            st.badge("Staff", color="grey", icon=":material/badge:")
        else:
            grade = st.session_state.current_user_grade
            st.badge(
                f"Member • Grade {grade}" if grade else "Member",
                color="grey", icon=":material/badge:",
            )

        st.divider()
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            client.auth.sign_out()
            st.session_state.auth_user = None
            # A logout should genuinely log out — without this, "remember
            # me" would silently sign them right back in on the very next
            # rerun via the auto-restore check above.
            _clear_remember_cookie()
            st.session_state.tried_remember_login = True
            # Tells the login screen to play the gear spin-DOWN once.
            st.session_state.splash_out = True
            st.rerun()

# The container-key + CSS-selector approach (two earlier attempts) never
# actually showed up live, for reasons that were never pinned down even
# after verifying the CSS itself renders correctly in isolation — so this
# sidesteps Streamlit's container tree entirely instead of fighting it
# further. The real, functional button below is rendered normally (kept
# working, just made invisible via CSS) and a hand-styled pill is appended
# straight onto the actual page's <body> — a real DOM node, sibling to
# Streamlit's own root, not nested inside anything Streamlit re-renders —
# via the same window.parent.document technique _set_remember_cookie
# above already uses to write a cookie. Clicking the pill finds the real
# button and calls .click() on it, which fires Streamlit's own listener
# exactly as if a person had clicked it, so the dialog opens for real.
with st.container(key="rk_feedback_fab"):
    st.button(
        "Report an issue", icon=":material/bug_report:",
        key="open_report_feedback_fab", on_click=_open_report_feedback,
    )
components.html("""
    <script>
    (function() {
        const doc = window.parent.document;

        if (!doc.getElementById('rk-feedback-hide-style')) {
            const hide = doc.createElement('style');
            hide.id = 'rk-feedback-hide-style';
            hide.textContent = '.st-key-rk_feedback_fab { display: none !important; }';
            doc.head.appendChild(hide);
        }

        // The pill is appended straight to <body>, outside anything
        // Streamlit itself re-renders, so it persists across every later
        // rerun on its own — this block only builds it ONCE.
        let pill = doc.getElementById('rk-feedback-fab');
        if (!pill) {
            pill = doc.createElement('button');
            pill.id = 'rk-feedback-fab';
            pill.textContent = '🐞 Report an issue';
            pill.style.cssText = `
                position: fixed; right: 24px; bottom: 96px; z-index: 9998;
                border: none; border-radius: 999px; cursor: pointer;
                padding: 12px 22px; font-weight: 700; font-size: 0.92rem;
                font-family: inherit;
                background: linear-gradient(135deg, #F0C55B, #C9932A);
                color: #1E1E1E;
                box-shadow: 0 6px 18px rgba(232, 179, 61, 0.45), 0 2px 8px rgba(0, 0, 0, 0.35);
                transition: transform 0.15s ease, box-shadow 0.15s ease;
            `;
            pill.onmouseenter = function() {
                pill.style.transform = 'translateY(-3px) scale(1.03)';
                pill.style.boxShadow = '0 10px 26px rgba(232, 179, 61, 0.6), 0 4px 12px rgba(0, 0, 0, 0.4)';
            };
            pill.onmouseleave = function() {
                pill.style.transform = 'none';
                pill.style.boxShadow = '0 6px 18px rgba(232, 179, 61, 0.45), 0 2px 8px rgba(0, 0, 0, 0.35)';
            };
            doc.body.appendChild(pill);
        }

        // Re-attached on EVERY script run, unlike the block above — a
        // previous version of this file left the click handler set only
        // once at creation time, which meant a server-side fix to this
        // exact handler never actually took effect in an already-open
        // tab: the pill persisted (per the guard above), so it kept
        // running whatever onclick closure was captured the first time
        // this script ever ran in that tab, silently, with no way to
        // tell short of a hard page reload. Reassigning it fresh every
        // time means the very next Streamlit rerun always picks up
        // whatever this code currently says.
        pill.onclick = function() {
            // Matched by visible text, NOT the st-key-* class the hide
            // rule above uses — that class targeting is the same
            // approach that silently failed to even show the pill in two
            // earlier attempts. A material icon's name leaks into
            // textContent as a font ligature string (verified live: the
            // real Log in button's textContent is "loginLog in", not
            // "Log in") — so this is a substring match, not exact, and
            // explicitly excludes the pill itself by id, since the pill's
            // own label also contains this same text.
            const real = Array.from(doc.querySelectorAll('button')).find(
                b => b.id !== 'rk-feedback-fab' && b.textContent.includes('Report an issue')
            );
            if (real) {
                real.click();
            } else {
                console.error('RoboKnights: could not find the real "Report an issue" button to click.');
                alert('Something went wrong opening the report form — please refresh the page and try again.');
            }
        };
    })();
    </script>
""", height=0)
if st.session_state.show_report_feedback:
    render_report_feedback()
if st.session_state.feedback_message:
    st.toast(st.session_state.feedback_message, icon=":material/check_circle:")
    st.session_state.feedback_message = None

# --- Navigation ------------------------------------------------------------

pages = [st.Page("app_pages/home.py", title="Home", icon=":material/home:")]
# Exun only gets an allowlist of specific pages (Competitions, Meetings,
# Achievements, Members — each enforcing view-only for Exun internally),
# not the full nav — Inventory, Announcements, and Queries aren't part of
# what Exun was actually given access to.
if not st.session_state.is_exun:
    pages.append(st.Page("app_pages/inventory.py", title="Inventory", icon=":material/inventory_2:"))
pages.append(st.Page("app_pages/competitions.py", title="Competitions", icon=":material/emoji_events:"))
if not st.session_state.is_exun:
    pages.append(st.Page("app_pages/announcements.py", title="Announcements", icon=":material/campaign:"))
    # 🔵 dot mirrors the same "unread" badge queries.py already puts on
    # individual threads for the host — same visual language, just at the
    # nav level so it's visible from anywhere in the app, not only once
    # you're already on the Queries page. This runs unconditionally on
    # EVERY page load for every user (unlike a button-triggered write), so
    # it's wrapped defensively — a badge is a nice-to-have; it must never
    # be able to take the whole app down for everyone the way an
    # unguarded query against a not-yet-migrated table just did.
    queries_title = "Queries"
    try:
        if has_unread_queries(st.session_state.current_user_id, st.session_state.is_host):
            queries_title += " 🔵"
    except Exception:
        pass
    pages.append(st.Page("app_pages/queries.py", title=queries_title, icon=":material/quiz:"))
pages.append(st.Page("app_pages/meetings.py", title="Meetings", icon=":material/groups:"))
pages.append(st.Page("app_pages/achievements.py", title="Achievements", icon=":material/military_tech:"))
pages.append(st.Page("app_pages/assistant.py", title="AI Assistant", icon=":material/smart_toy:"))
pages.append(st.Page("app_pages/feedback.py", title="Feedback", icon=":material/feedback:"))

# Host-only elsewhere, but Members is also opened up to Exun (full
# details, per an explicit call — Exun just can't edit it, unlike a host).
if st.session_state.is_host or st.session_state.is_exun:
    pages.append(st.Page("app_pages/members.py", title="Members", icon=":material/badge:"))

# Host-only: everything this app sends to Discord, across both the
# competitions channel and the private Exun<>RK channel.
if st.session_state.is_host:
    pages.append(st.Page("app_pages/discord_messages.py", title="Discord Messages", icon=":material/forum:"))
    # Read-only viewer for what the AI actually said (Discord bot + AI
    # Assistant page), so a bad answer can be looked at without opening
    # Supabase directly.
    pages.append(st.Page("app_pages/ai_logs.py", title="AI Logs", icon=":material/history:"))

# The private RoboKnights <> Exun channel — only the specific hand-picked
# people in EXUN_CHANNEL_MEMBERS ever see this page exists at all.
if st.session_state.auth_user["email"] in EXUN_CHANNEL_MEMBERS:
    exun_title = "Exun Channel"
    try:
        if has_unread_exun_channel(st.session_state.current_user_id):
            exun_title += " 🔵"
    except Exception:
        pass
    pages.append(st.Page("app_pages/exun_channel.py", title=exun_title, icon=":material/handshake:"))

page = st.navigation(pages)
page.run()

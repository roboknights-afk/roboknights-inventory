# Discord DMs from the scheduled reminder scripts (2026-11-04), sent as the
# bot through Discord's REST API - the same one-off call shared.py's
# send_discord_dm makes, so the always-on bot process isn't involved. Kept
# here, with no Streamlit import, so send_due_reminders.py and
# send_achievement_reminders.py can share it (they deliberately don't
# import shared.py).
#
# A DM goes only to the person it's about - nothing is posted in a channel,
# so nobody is called out in front of the club. A member who hasn't linked
# their Discord account (Home -> Discord on the dashboard) just doesn't get
# one; their email reminder still goes as before.

import os

import requests

DISCORD_API = "https://discord.com/api/v10"
DASHBOARD = os.environ.get("APP_URL") or "https://roboknights-dashboard.streamlit.app"


def send_dm(discord_user_id, content):
    # -> True if Discord accepted it. Best-effort: a failure is printed (the
    # Actions log) and never stops the rest of the run.
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token or not discord_user_id:
        return False
    headers = {"Authorization": f"Bot {token}"}
    try:
        channel = requests.post(
            f"{DISCORD_API}/users/@me/channels", headers=headers,
            json={"recipient_id": str(discord_user_id)}, timeout=10,
        ).json().get("id")
        if not channel:
            return False
        r = requests.post(
            f"{DISCORD_API}/channels/{channel}/messages", headers=headers,
            json={"content": content, "allowed_mentions": {"parse": []}}, timeout=10,
        )
        return r.status_code < 300
    except Exception as e:
        print(f"Couldn't send a reminder DM ({e!r}).")
        return False


def bot_status_dm(comp_name, event_name):
    return (
        f":robot: **{comp_name} is tomorrow** - you're selected for **{event_name}**.\n"
        f"Please update your bot's status on the dashboard before the competition: <{DASHBOARD}>"
    )


def log_result_dm(comp_name, event_name):
    return (
        f":trophy: **How did {event_name} go?** {comp_name} was today.\n"
        f"Log your result under Achievements on the dashboard (you can add your teammates too): <{DASHBOARD}>"
    )


def consent_form_dm(noun, reason, when):
    return (
        f":memo: **Your {noun} consent form for {reason} ({when}) isn't submitted yet.**\n"
        f"Please ask your parent/guardian to fill it in on the dashboard "
        f"(Consent form -> Intra-school): <{DASHBOARD}>"
    )

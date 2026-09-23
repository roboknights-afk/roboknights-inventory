# "Joke of the day" - posts one short, light joke to #general every
# morning. Prefers a joke that plays off something from the last 24
# hours of the club's own Discord chat (discord_channel_log, same table
# check_discord_messages.py reads); falls back to a plain robotics joke
# when there's nothing safe to work with, which is the normal case, not
# a failure.
#
# The hard rule, non-negotiable: this never makes a joke AT a named
# member's expense. This project has already been burned once by exactly
# that (see CLAUDE.md's "AI safety hardening" section, 2026-08-14 - a
# whole evening of "roast X" requests the bot cheerfully fulfilled about
# real, named students). A "joke of the day" feature that quietly turns
# into that is the same failure with a friendlier name, so the prompt
# below is told explicitly to joke about the SITUATION, never the
# PERSON, and a code-level check after generation throws the joke out
# (falling back to a plain robotics one) if it contains any name that
# actually posted in the last 24 hours - a system-prompt rule is
# advisory, this check is not.
#
# To test on your own laptop: `python send_joke_of_the_day.py`
# (needs the same .env file app.py uses). Add --dry-run to print instead
# of posting.

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Same model the rest of this project moved to on 2026-08-25.
GROQ_MODEL = "openai/gpt-oss-120b"

DISCORD_GENERAL_WEBHOOK = os.environ.get("DISCORD_GENERAL_WEBHOOK_URL")
DISCORD_AUTOMATED_MARKER = "\n\n***This is automated message***"
# Same values as shared.py's DISCORD_BOT_USERNAME/DISCORD_BOT_AVATAR_URL -
# kept in sync by hand, since this script deliberately doesn't import
# shared.py (pulls in Streamlit, same reasoning as every other cron
# script here). Without these a webhook post carries whatever identity
# that webhook defaults to, not the club's bot - the exact bug just found
# and fixed in check_discord_messages.py's post_to_general().
DISCORD_BOT_USERNAME = "roboknightsbot"
DISCORD_BOT_AVATAR_URL = "https://cdn.discordapp.com/avatars/1536836032329416724/5ebc6d79217e395322b1faf5107e095f.png"

HOURS = 24
MAX_MESSAGES = 300

JOKE_PROMPT = """You write ONE short "joke of the day" for a school robotics \
club's Discord server. Members are students aged 11-18.

If today's chat below has something safe to work with, prefer a joke that \
plays off it - a recurring phrase, a running bit, something silly that \
happened. The joke must be about the SITUATION or the GROUP in general, \
never about one named person, even lightly, even affectionately, even as \
a compliment. If the only material in the chat is about a specific person, \
or the chat is empty, or nothing in it is safe like that, just write a \
plain robotics/engineering joke instead - that is the normal, expected \
result, not a fallback to apologize for.

Rules:
- One joke only. 1-3 sentences. No setup/punchline labels, no markdown, \
no emoji spam, no introducing it ("Here's today's joke:") - just the joke \
itself.
- Never name, describe, or clearly identify any real member.
- Nothing sexual, no slurs, no swearing aimed at anyone, nothing about \
staff, school administration, or other clubs.
- Do not explain the joke afterward.

TODAY'S CHAT (may be empty - that's fine, write a robotics joke instead):
{chat}

Write only the joke."""

ROBOTICS_FALLBACK_PROMPT = """Write ONE short, light, PG robotics or \
engineering joke for a school robotics club's Discord server (members aged \
11-18). One joke only, 1-3 sentences, no setup/punchline labels, no \
markdown, no introducing it. Write only the joke."""


def _groq(prompt, key):
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": GROQ_MODEL, "temperature": 0.9, "max_tokens": 200,
                  "reasoning_effort": "low",
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
    except Exception as exc:
        print(f"   Groq request failed: {exc!r}", flush=True)
        return None
    if r.status_code != 200:
        print(f"   Groq failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
        return None
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    return text or None


def _mentions_a_real_name(joke, names):
    """Code-level backstop, not just a prompt rule - see the module
    docstring for why. Matches a first or last name as a whole word,
    case-insensitively; a two-letter name like "S." or a common English
    word that happens to be part of a name is deliberately not enough to
    trigger this (too many false positives on ordinary words), but this
    is a discard-and-fall-back check, not a security boundary, so erring
    toward over-triggering is the safe direction anyway."""
    lowered = joke.lower()
    for name in names:
        for part in (name or "").split():
            if len(part) >= 4 and re.search(rf"\b{re.escape(part.lower())}\b", lowered):
                return True
    return False


def main():
    dry_run = "--dry-run" in sys.argv
    since = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).isoformat()
    rows = (
        client.table("discord_channel_log")
        .select("discord_display_name,content,created_at")
        .gte("created_at", since)
        .order("created_at")
        .limit(MAX_MESSAGES)
        .execute()
        .data
    )
    rows = [r for r in rows if (r.get("content") or "").strip()]
    print(f"joke of the day: {len(rows)} messages in the last {HOURS}h", flush=True)

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY is not set - cannot write a joke, exiting", flush=True)
        return

    names = {r.get("discord_display_name") for r in rows if r.get("discord_display_name")}

    joke = None
    if rows:
        chat = "\n".join(
            f'{r.get("discord_display_name") or "someone"}: {(r["content"] or "").strip()}'
            for r in rows
        )
        joke = _groq(JOKE_PROMPT.format(chat=chat), key)
        if joke and _mentions_a_real_name(joke, names):
            print("   generated joke named a real member - discarding, "
                  "falling back to a plain robotics joke", flush=True)
            joke = None

    if not joke:
        joke = _groq(ROBOTICS_FALLBACK_PROMPT, key)

    if not joke:
        print("   could not get a joke from Groq - nothing posted", flush=True)
        return

    print(f"   joke: {joke}", flush=True)

    if dry_run:
        print("--dry-run: nothing posted", flush=True)
        return

    if not DISCORD_GENERAL_WEBHOOK:
        print("   DISCORD_GENERAL_WEBHOOK_URL is not set - cannot post", flush=True)
        return

    body = f":thought_balloon: **Joke of the day**\n{joke}{DISCORD_AUTOMATED_MARKER}"
    try:
        r = requests.post(
            f"{DISCORD_GENERAL_WEBHOOK}?wait=true",
            json={
                "content": body,
                "username": DISCORD_BOT_USERNAME,
                "avatar_url": DISCORD_BOT_AVATAR_URL,
            },
            timeout=20,
        )
        if r.status_code >= 300:
            print(f"   posting failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
            return
        message_id = r.json().get("id")
        print(f"   posted, message {message_id}", flush=True)
        if message_id:
            client.table("discord_messages").insert({
                "message_id": message_id, "content": body, "channel": "general",
            }).execute()
    except Exception as exc:
        print(f"   posting failed: {exc!r}", flush=True)


if __name__ == "__main__":
    main()

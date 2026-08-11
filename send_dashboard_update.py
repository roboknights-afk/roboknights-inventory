# Posts "the dashboard just got an update" to the Discord dashboard channel,
# with a short plain-English summary of what changed. This is NOT part of
# app.py — it's run by a GitHub Actions workflow on every push to master
# (see .github/workflows/dashboard-update.yml), which is also the moment
# Streamlit Cloud redeploys the app. So the notice goes out when the update
# actually lands, rather than whenever someone next opens the app.
#
# Like send_due_reminders.py, this deliberately does NOT import shared.py:
# that module imports streamlit, which the Actions runner has no reason to
# install. The handful of things it needs (the webhook post, the
# discord_messages log row) are short enough to repeat here.
#
# To test on your own laptop:
#   python send_dashboard_update.py <before_sha> <after_sha>
# or with no arguments, which summarises just the most recent commit.

import os
import subprocess
import sys

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

WEBHOOK_URL = os.environ.get("DISCORD_DASHBOARD_WEBHOOK_URL")

# Same wording shared.py's DISCORD_AUTOMATED_MARKER uses for every other
# channel — kept in sync by hand, since this script can't import that module.
FOOTER = "\n\n***This is automated message***"

# Commits that say nothing useful to a member reading the changelog. Merge
# commits in particular would otherwise dominate the summary on a busy push.
SKIP_PREFIXES = ("merge ", "revert \"merge")

GROQ_MODEL = "llama-3.3-70b-versatile"
SUMMARY_PROMPT = (
    "You write release notes for a school robotics club's web app. Club members "
    "are students aged 14-18; most are not programmers.\n\n"
    "Below are the git commit messages from an update that just went live. Write "
    "EXACTLY 3 lines summarising what changed, in plain English, from the point of "
    "view of someone USING the app — what looks or behaves differently for them.\n\n"
    "Rules:\n"
    "- Exactly 3 lines. No more, no fewer.\n"
    "- Start each line with '- '.\n"
    "- No preamble, no heading, no sign-off. Only the 3 lines.\n"
    "- Say what changed for the user, not how the code was written. Never mention "
    "commits, refactors, files, functions, or variable names.\n"
    "- If the update was small, still write 3 lines by being specific about it "
    "rather than padding with invented features.\n\n"
    "Commit messages:\n"
)


def commit_messages(before, after):
    # The full messages (subject + body) of everything new in this push, so
    # the summary has the "why" from the commit bodies to work with, not just
    # the one-line subjects.
    #
    # A brand-new branch, or a first push, reports an all-zeros "before" SHA
    # that isn't a real commit — there's no range to diff against, so fall
    # back to describing just the tip commit.
    first_push = not before or set(before) == {"0"}
    fmt = "--format=%s%n%b%n---"
    if first_push:
        args = ["git", "log", "-1", fmt, after]
    else:
        args = ["git", "log", "--no-merges", fmt, f"{before}..{after}"]
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Couldn't read the commit range ({e}) — nothing to post.")
        return []

    messages = []
    for block in out.split("\n---"):
        block = block.strip()
        if not block:
            continue
        if block.lower().startswith(SKIP_PREFIXES):
            continue
        messages.append(block)
    return messages


def fallback_summary(messages):
    # Used when Groq isn't configured or the call fails. Just the subject
    # lines of the three most recent commits — developer-ish wording, but a
    # real summary beats no notification at all.
    subjects = [m.splitlines()[0].strip() for m in messages if m.splitlines()]
    return "\n".join(f"- {s}" for s in subjects[:3]) or "- Small fixes and improvements."


def summarise(messages):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("No GROQ_API_KEY set — falling back to raw commit subjects.")
        return fallback_summary(messages)
    try:
        from groq import Groq

        response = Groq(api_key=api_key).chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": SUMMARY_PROMPT + "\n\n".join(messages)}],
            temperature=0.3,
        )
        text = (response.choices[0].message.content or "").strip()
        # Trust the model's wording but not its line count — anything past the
        # first three lines gets dropped rather than posted.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
        if not lines:
            raise ValueError("empty summary")
        return "\n".join(lines)
    except Exception as e:
        # Never let the summary step stop the notification going out.
        print(f"Groq summary failed ({e}) — falling back to raw commit subjects.")
        return fallback_summary(messages)


def post_to_discord(content):
    # ?wait=true makes Discord return the created message so we get its id,
    # which is what lets a host delete or edit it later from the app's
    # Discord Messages page.
    #
    # username/avatar_url make this display as discord_bot/bot.py's
    # "roboknightsbot" instead of a generic webhook poster (shared.py does
    # the same thing for the app's own notifications) - same idea repeated
    # here since this script deliberately doesn't import shared.py.
    response = requests.post(
        WEBHOOK_URL,
        json={
            "content": content,
            "username": "roboknightsbot",
            "avatar_url": (
                "https://cdn.discordapp.com/avatars/"
                "1536836032329416724/5ebc6d79217e395322b1faf5107e095f.png"
            ),
        },
        params={"wait": "true"}, timeout=10,
    )
    response.raise_for_status()
    return response.json().get("id")


def log_message(message_id, content):
    # Best-effort: the notification has already gone out by this point, so a
    # logging failure shouldn't fail the workflow. It only costs the host the
    # ability to delete this one message from inside the app.
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key and message_id):
        return
    try:
        create_client(url, key).table("discord_messages").insert({
            "message_id": message_id, "content": content, "channel": "dashboard",
        }).execute()
    except Exception as e:
        print(f"Posted, but couldn't log it to discord_messages ({e}).")


def main():
    if not WEBHOOK_URL:
        print("No DISCORD_DASHBOARD_WEBHOOK_URL set — nothing to do.")
        return

    before = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BEFORE_SHA")
    after = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AFTER_SHA", "HEAD")

    messages = commit_messages(before, after)
    if not messages:
        # A push with nothing but merge commits, or an empty range. Silence is
        # the right answer — a "we updated!" post with nothing to report is
        # worse than no post.
        print("No summarisable commits in this push — not posting.")
        return

    summary = summarise(messages)
    content = (
        ":sparkles: **The dashboard just got an update**\n"
        f"{summary}"
        f"{FOOTER}"
    )
    message_id = post_to_discord(content)
    log_message(message_id, content)
    print(f"Posted update to Discord ({len(messages)} commit(s) summarised).")


if __name__ == "__main__":
    main()

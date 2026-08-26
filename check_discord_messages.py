# The evening check on the club's Discord.
#
# Every message the bot can see in the home server is already logged to
# discord_channel_log (see discord_bot/bot.py). Until now that log was only
# read when somebody went looking, which meant "we check the messages" was
# a thing we said rather than a thing that happened. This runs once every
# evening, reads the last 24 hours, and emails a report of anything that
# looks like a personal attack so it can be dealt with the next day.
#
# It does NOT reply to anyone, delete anything, warn anyone, or touch a
# single Discord message. It reads and it reports — a person decides what
# to actually do. That distinction matters: an automated system that acts
# on a model's judgement of a 13-year-old's messages is not something this
# club should be running.
#
# Deliberately conservative. A false positive costs a member being hauled
# up over a joke, so the prompt below is told to flag only clear cases and
# to leave anything borderline alone. Under-flagging is the intended bias.
#
# To test on your own laptop: `python check_discord_messages.py`
# (needs the same .env file app.py uses). Add --dry-run to print the
# report instead of emailing it.

import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Same model the rest of the project moved to on 2026-08-25, after Groq
# retired the Llama models everything used to run on.
GROQ_MODEL = "openai/gpt-oss-120b"

# Who gets the report. Deliberately the club's own account by default and
# NOT the full HOST_EMAILS set, which includes teachers: a nightly
# "these students were rude" email landing in staff inboxes is a decision
# to make on purpose, not a side effect of turning this on. Set
# MODERATION_REPORT_EMAIL to change it.
REPORT_EMAIL = os.environ.get("MODERATION_REPORT_EMAIL", "roboknights@dpsrkp.net")

HOURS = 24
MAX_MESSAGES = 600  # a normal day is ~200; this is a runaway guard, not a target

# Sized against the free tier's 8,000-tokens-per-minute ceiling for this
# model, with the batch's own answer counted in. 60 messages is roughly
# 500 tokens of input, so a batch request lands near 2,000 all-in.
BATCH_SIZE = 60
BATCH_PAUSE_SECONDS = 25
RETRY_ATTEMPTS = 4
RETRY_WAIT_SECONDS = 30

PROMPT = """You review one day of chat messages from a school robotics \
club's Discord server. The members are students aged 11 to 18.

Flag ONLY messages that are clearly aimed at a person and clearly \
unacceptable:
- insults, name-calling, or mockery directed at someone
- slurs, or swearing used AT a person
- threats, or telling someone to harm themselves
- sustained pile-ons against one person
- sexual content, or anything sexual directed at a member

Do NOT flag:
- disagreement, arguing, or someone saying another person is wrong
- swearing that is not aimed at anyone ("this is so damn hard")
- ordinary teenage banter between friends, jokes, sarcasm, memes
- someone reporting or complaining that THEY were treated badly
- anything you are unsure about

Under-flagging is correct. A wrongly flagged message means a student gets \
questioned over a joke, which is worse than missing a borderline one.

Reply with JSON only, no other text, in exactly this shape:
{"flagged": [{"id": "<the id shown>", "reason": "<one short sentence>"}]}

If nothing qualifies, reply exactly: {"flagged": []}

MESSAGES:
"""


def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_SENDER"]
    msg["To"] = to_email
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
        server.starttls()
        server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)


def ist(iso):
    # Supabase stores UTC; IST is a flat +5:30 with no DST.
    return (datetime.fromisoformat(iso.replace("Z", "+00:00"))
            + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p")


def review(messages):
    """Ask the model which messages cross the line. Returns None on any
    failure - a broken review must not invent flags, and must not crash
    the job either. It DOES print why, because a silently dead check is
    exactly the failure this script exists to stop repeating.

    Sent in batches: this model's free tier allows 8,000 tokens per
    MINUTE, and a single day of messages plus room to answer went over
    that (413, "Requested 9340"). The same budget is shared with the
    Discord bot, so the pause between batches is not only about this
    script getting its own answer - a burst here is a minute of the bot
    telling members it can't reply.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY is not set - cannot review, exiting", flush=True)
        return None

    flagged = []
    batches = [messages[i:i + BATCH_SIZE] for i in range(0, len(messages), BATCH_SIZE)]
    for n, batch in enumerate(batches, start=1):
        if n > 1:
            time.sleep(BATCH_PAUSE_SECONDS)
        part = _review_batch(batch, key)
        if part is None:
            return None  # a partial review is worse than an honest failure
        print(f"   batch {n}/{len(batches)}: {len(batch)} messages, {len(part)} flagged",
              flush=True)
        flagged += part
    return flagged


def _review_batch(messages, key):
    listing = "\n".join(
        f'[{m["discord_message_id"]}] {m.get("discord_display_name") or m["discord_user_id"]}: '
        f'{(m["content"] or "").strip()}'
        for m in messages
    )
    body = {
        "model": GROQ_MODEL,
        "temperature": 0,
        # This model thinks before it answers, and the thinking comes out of
        # the SAME budget as the answer. At 1500 a real 60-message batch came
        # back finish_reason="length" with 1498 of 1500 tokens spent reasoning
        # and an empty answer - so the headroom here is not optional.
        "max_tokens": 4000,
        # Measured on a real batch: "low" used 198 completion tokens, "medium"
        # used 2097, and both flagged the same message. Low it is - the free
        # tier allows 8,000 tokens a MINUTE across this whole project, and
        # every token this job spends is one the Discord bot can't have while
        # a member is waiting on a reply.
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": PROMPT + listing}],
    }
    # NOT response_format=json_object. On a four-message test it works; on a
    # real 60-message batch it returns an empty generation, which Groq then
    # rejects as a 400 "failed to validate JSON" - so JSON mode turned a
    # working call into a failing one. The prompt asks for JSON and the
    # parse below finds it in the text, which is what actually holds up.

    r = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"}, json=body, timeout=90,
            )
        except Exception as exc:
            print(f"review request failed: {exc!r}", flush=True)
            return None
        if r.status_code != 429:
            break
        # The 8,000-tokens-per-minute ceiling is shared with the live Discord
        # bot, so this job can be squeezed out by members simply using it.
        # Waiting is the right answer - it runs at night with nothing else
        # depending on it - and losing the night's check to a busy minute is
        # not.
        wait = float(r.headers.get("retry-after") or 0) or RETRY_WAIT_SECONDS
        print(f"   rate limited, waiting {wait:.0f}s (attempt {attempt + 1}"
              f"/{RETRY_ATTEMPTS})", flush=True)
        time.sleep(wait + 1)
    if r is None or r.status_code != 200:
        print(f"review failed: HTTP {getattr(r, 'status_code', '?')} "
              f"{getattr(r, 'text', '')[:300]}", flush=True)
        return None
    try:
        content = r.json()["choices"][0]["message"]["content"] or ""
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            print(f"no JSON in the review reply: {content[:200]!r}", flush=True)
            return None
        return json.loads(content[start:end + 1]).get("flagged", [])
    except Exception as exc:
        print(f"could not read the review reply: {exc!r}", flush=True)
        return None


def main():
    dry_run = "--dry-run" in sys.argv
    since = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).isoformat()
    rows = (
        client.table("discord_channel_log")
        .select("discord_message_id,discord_channel_id,discord_user_id,"
                "discord_display_name,content,created_at")
        .gte("created_at", since)
        .order("created_at")
        .limit(MAX_MESSAGES)
        .execute()
        .data
    )
    rows = [r for r in rows if (r.get("content") or "").strip()]
    print(f"evening check: {len(rows)} messages in the last {HOURS}h", flush=True)
    if not rows:
        return

    flagged = review(rows)
    if flagged is None:
        # Distinct from "nothing was flagged" on purpose. The check not
        # running is itself worth an email, or nobody finds out for weeks.
        if not dry_run:
            send_email(
                REPORT_EMAIL,
                "Discord evening check did not run",
                f"The evening check on {len(rows)} messages could not complete. "
                f"The reason is in the GitHub Actions log for this run.\n\n"
                f"Nothing was reviewed, so today's messages have not been checked.",
            )
        print("review unavailable - reported and stopping", flush=True)
        return

    by_id = {r["discord_message_id"]: r for r in rows}
    hits = [(by_id[f["id"]], f.get("reason", "")) for f in flagged if f.get("id") in by_id]
    print(f"flagged: {len(hits)}", flush=True)
    if not hits:
        # Nothing to send. The GitHub Actions run itself is the record
        # that the check happened.
        return

    lines = [
        f"The evening check went through {len(rows)} Discord messages from the "
        f"last {HOURS} hours and flagged {len(hits)}.",
        "",
        "These are suggestions, not decisions - read them in context before "
        "acting on any of them. The model gets things wrong, and a message "
        "can read very differently with the conversation around it.",
        "",
    ]
    for row, reason in hits:
        who = row.get("discord_display_name") or row["discord_user_id"]
        lines += [
            f"{ist(row['created_at'])} - {who}",
            f"   {(row['content'] or '').strip()}",
            f"   flagged because: {reason}",
            f"   channel: {row['discord_channel_id']}",
            "",
        ]
    body = "\n".join(lines)

    if dry_run:
        print("\n----- report (dry run, not emailed) -----")
        print(body)
        return
    send_email(REPORT_EMAIL, f"Discord evening check: {len(hits)} flagged", body)
    print(f"report emailed to {REPORT_EMAIL}", flush=True)


if __name__ == "__main__":
    main()

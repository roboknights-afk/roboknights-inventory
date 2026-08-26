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
# retired the Llama models everything used to run on. Used here only for
# WRITING a warning - see REVIEW_MODEL for the bulk of the work.
GROQ_MODEL = "openai/gpt-oss-120b"

REVIEW_MODEL = "openai/gpt-oss-120b"

# Used only when the model above has run out of budget for the day.
# Groq's limits are per MODEL, so this genuinely still answers when the
# other one refuses everything - confirmed with 120b at 199,316 of its
# 200,000 daily tokens.
#
# It is NOT as good at this, and the difference is not academic. Run over
# the same 124 messages, 120b flagged the single worst thing said that
# night (an explicit sexual remark aimed at members). 20b missed it
# entirely, and instead rated two ordinary argumentative lines - "who are
# you to talk about manners", "behave like the junior you are" - as
# CLEAR, which is the rating that posts a public accusation about a named
# student with nobody reading it first.
#
# So a run that falls back to it is marked degraded, and a degraded run
# never warns anyone. It still produces the report, which is the half
# that has a person in the loop.
REVIEW_FALLBACK_MODEL = "openai/gpt-oss-20b"

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
# How much of the surrounding conversation to show with each flagged
# message. Enough to judge it, not so much that the report becomes the
# whole day again.
CONTEXT_BEFORE = 3
CONTEXT_AFTER = 2

# The club's own Discord server, for building links back to a message.
HOME_GUILD_ID = os.environ.get("DISCORD_HOME_GUILD_ID", "607226177425506325")

RETRY_ATTEMPTS = 4
RETRY_WAIT_SECONDS = 30
# Never sleep longer than this in one go, however long Groq asks for.
MAX_RETRY_WAIT_SECONDS = 150

# --- automatic warnings -------------------------------------------------
# Off unless AUTO_WARN is set to 1. The switch exists because this is the
# one part of this job that ACTS on the model's judgement rather than
# handing it to a person: a wrong call here is a real student publicly
# accused by a bot at 8pm with nobody having read it first. Arming it
# should be a decision someone makes on purpose, not a default.
AUTO_WARN = os.environ.get("AUTO_WARN") == "1"

# Only "clear" flags are ever warned automatically. Borderline ones still
# go in the report, where a person decides what to do about them.
AUTO_WARN_SEVERITY = "clear"

# A hard ceiling per run. If one night produces nine "clear" flags, the
# likeliest explanation is that the review went wrong, and the right
# outcome then is a person reading the report - not a bot posting nine
# public accusations into the channel while everyone is asleep.
MAX_WARNINGS_PER_RUN = 3

DISCORD_GENERAL_WEBHOOK = os.environ.get("DISCORD_GENERAL_WEBHOOK_URL")
DISCORD_AUTOMATED_MARKER = "\n\n***This is automated message***"
DISCORD_MESSAGE_LIMIT = 2000

# The length the warnings are meant to be. Enforced in code, not by asking
# the model for a character count - see write_warning().
MIN_WARNING_CHARS = 2000

WARNING_PROMPT = """You are writing a warning to a member of a school \
robotics club's Discord server, aged between 11 and 18, about something \
they posted.

Write ONE continuous paragraph of between 2200 and 3000 characters. Count \
as you go - a first draft asked for "at least 2000" came back at 1894, so \
aim past the target rather than at it. No headings, no bullet points, no \
lists - flowing prose only.

Requirements:
- Say plainly what was wrong with the message and why it matters to the \
people who read it.
- Be firm and serious, but never insulting, sarcastic or mocking. You are \
correcting behaviour, not attacking a person.
- Do not swear, and do not repeat any slur or explicit wording from the \
message itself.
- Do not threaten specific punishments, do not mention removing or \
restricting anyone's access, and do not claim any action has been taken.
- Make clear that the same standard applies to every member equally.
- End by saying what is expected from them from here on.

Do NOT invent things that were not given to you. This club has no code of \
conduct document, no written rules page, no mentors and no moderators, so \
do not refer to any of them and never tell the member to go and read \
something. Do not mention parents or teachers. Do not describe what the \
club is for or what other members supposedly come here to do - you do not \
know. Write only about the message itself, why it lands badly on the \
people who read it, and what you expect instead. Plain, direct, human \
wording - not the language of a company policy.

The member's name: {name}
What they posted: {content}
Why it was flagged: {reason}

Write only the paragraph itself, nothing else."""

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

Give each flagged message a severity:
- "clear"      - nobody reasonable would defend it: a slur, sexual content \
aimed at a member, a real threat, or plain abuse.
- "borderline" - it might be out of line, but tone, context or a joke could \
explain it.

When in doubt it is "borderline". Only "clear" ones are acted on \
automatically, so a wrong "clear" costs a student a public accusation over \
something they meant as a joke.

Reply with JSON only, no other text, in exactly this shape:
{"flagged": [{"id": "<the id shown>", "reason": "<one short sentence>", \
"severity": "clear"}]}

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


def jump_link(row):
    # A raw channel id told the reader nothing. This is a real Discord link:
    # clicking it opens the message itself, in the conversation, which is
    # where any decision about it actually gets made.
    return (f"https://discord.com/channels/{HOME_GUILD_ID}/"
            f"{row['discord_channel_id']}/{row['discord_message_id']}")


def ist(iso):
    # Supabase stores UTC; IST is a flat +5:30 with no DST.
    return (datetime.fromisoformat(iso.replace("Z", "+00:00"))
            + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p")


def already_handled(message_ids):
    """Message ids this check has already recorded, so a re-run - manual,
    or after a failure halfway through - can never warn somebody twice for
    something they said once."""
    if not message_ids:
        return set()
    try:
        rows = (client.table("discord_flags").select("discord_message_id")
                .in_("discord_message_id", list(message_ids)).execute().data)
        return {r["discord_message_id"] for r in rows}
    except Exception as exc:
        # The table may not exist yet (the migration is run by hand, same
        # as every other schema change here). Failing OPEN would mean
        # re-warning everybody every run, so fail closed instead: no
        # memory, no automatic warnings.
        print(f"discord_flags unavailable, not warning anyone: {exc!r}", flush=True)
        return None


def write_warning(row, reason, key):
    """Ask the model for the warning paragraph itself."""
    prompt = WARNING_PROMPT.format(
        name=row.get("discord_display_name") or "this member",
        content=(row.get("content") or "").strip(),
        reason=reason,
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": GROQ_MODEL, "temperature": 0.3, "max_tokens": 1400,
                  "reasoning_effort": "low",
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
    except Exception as exc:
        print(f"   could not write the warning: {exc!r}", flush=True)
        return None
    if r.status_code != 200:
        print(f"   could not write the warning: HTTP {r.status_code} {r.text[:200]}",
              flush=True)
        return None
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    if not text:
        return None

    # Asking for a character count doesn't work: told "at least 2000" it
    # produced 1894, and told "between 2200 and 3000" it produced 1542.
    # Models don't count characters. So check it here and ask once for
    # more, which is a thing they CAN do - expand something already
    # written - rather than hit a number they can't measure.
    if len(text) < MIN_WARNING_CHARS:
        print(f"   warning came back at {len(text)} chars, asking for more", flush=True)
        try:
            r2 = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": GROQ_MODEL, "temperature": 0.3, "max_tokens": 1600,
                      "reasoning_effort": "low",
                      "messages": [
                          {"role": "user", "content": prompt},
                          {"role": "assistant", "content": text},
                          {"role": "user", "content":
                           "That is too short. Rewrite it as one continuous "
                           "paragraph roughly twice that length. Do not add any "
                           "new accusation or any claim you have not already "
                           "made - go deeper on why it affects the people who "
                           "read it and on what is expected instead. Same rules "
                           "as before. Reply with the paragraph only."},
                      ]},
                timeout=90,
            )
            if r2.status_code == 200:
                longer = (r2.json()["choices"][0]["message"].get("content") or "").strip()
                # Only take it if it is actually longer; a shorter retry
                # means the second attempt went worse than the first.
                if len(longer) > len(text):
                    text = longer
        except Exception as exc:
            print(f"   could not expand the warning: {exc!r}", flush=True)

    return _strip_quotes_of(text, row.get("content") or "")


def _strip_quotes_of(warning, original):
    """Take the flagged message back out of the warning if the model
    quoted it.

    The prompt says not to repeat explicit wording. It did anyway - a
    warning about a sexual remark came back with the remark reprinted in
    full, which would have republished it to the same public channel, in
    front of the same younger members it was a problem for. A system
    prompt is a request; this is the part that actually holds.

    Any run of 25+ characters from the original found in the warning is
    replaced, rather than the whole warning being thrown away - the rest
    of it is usually fine, and a warning that never sends is its own kind
    of failure.
    """
    original = " ".join((original or "").split())
    if len(original) < 25:
        return warning
    lowered = warning.lower()
    # Longest first, so a big quote is caught whole instead of leaving
    # fragments behind after a short match is replaced.
    for size in range(len(original), 24, -1):
        for start in range(0, len(original) - size + 1):
            chunk = original[start:start + size]
            at = lowered.find(chunk.lower())
            if at != -1:
                print(f"   the warning quoted {len(chunk)} characters of the "
                      f"original message - removed", flush=True)
                before, after = warning[:at], warning[at + len(chunk):]
                # Models tend to wrap the quote in quotation marks, which
                # would otherwise be left stranded around the replacement
                # ('the message you posted, "that message."').
                if before[-1:] in "\"'“‘" and after[:1] in "\"'”’":
                    before, after = before[:-1], after[1:]
                cleaned = before + "that message" + after
                # Anything else quoted gets caught on the next pass.
                return _strip_quotes_of(cleaned, original)
    return warning


def post_to_general(text):
    """Post to the club's Discord. Deliberately its own small function
    rather than shared.send_discord_message() - this script never imports
    shared.py, since that pulls in Streamlit (same reasoning as the other
    cron scripts). The automated-message footer is added here to match
    what every other message this project sends carries.

    Discord refuses anything over 2000 characters and the warnings are
    deliberately longer than that, so they go out in parts.
    """
    if not DISCORD_GENERAL_WEBHOOK:
        print("   DISCORD_GENERAL_WEBHOOK_URL is not set - cannot post", flush=True)
        return None
    body = text + DISCORD_AUTOMATED_MARKER
    chunks, rest = [], body
    while rest:
        if len(rest) <= DISCORD_MESSAGE_LIMIT:
            chunks.append(rest)
            break
        # Break on a space rather than mid-word.
        cut = rest.rfind(" ", 0, DISCORD_MESSAGE_LIMIT)
        chunks.append(rest[:cut if cut > 0 else DISCORD_MESSAGE_LIMIT])
        rest = rest[(cut + 1) if cut > 0 else DISCORD_MESSAGE_LIMIT:]

    first_id = None
    for chunk in chunks:
        r = requests.post(f"{DISCORD_GENERAL_WEBHOOK}?wait=true",
                          json={"content": chunk}, timeout=20)
        if r.status_code >= 300:
            print(f"   posting failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
            return first_id
        first_id = first_id or r.json().get("id")
        time.sleep(1)
    return first_id


def record_flag(row, reason, severity, warning_id):
    try:
        client.table("discord_flags").upsert({
            "discord_message_id": row["discord_message_id"],
            "discord_user_id": row["discord_user_id"],
            "display_name": row.get("discord_display_name"),
            "content": (row.get("content") or "")[:2000],
            "reason": reason,
            "severity": severity,
            "warned_at": datetime.now(timezone.utc).isoformat() if warning_id else None,
            "warning_message_id": warning_id,
        }).execute()
    except Exception as exc:
        print(f"   could not record the flag: {exc!r}", flush=True)


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

    for model in (REVIEW_MODEL, REVIEW_FALLBACK_MODEL):
        got = _review_all(messages, key, model)
        if got is not None:
            # A run on anything but the main model is degraded: the report
            # still goes out, but nothing is warned automatically off a
            # judgement the weaker model is measurably bad at.
            return got, model == REVIEW_MODEL
        if model == REVIEW_MODEL:
            print(f"   {REVIEW_MODEL} unavailable - retrying on "
                  f"{REVIEW_FALLBACK_MODEL}, warnings will be suppressed", flush=True)
    return None


def _review_all(messages, key, model):
    flagged = []
    batches = [messages[i:i + BATCH_SIZE] for i in range(0, len(messages), BATCH_SIZE)]
    for n, batch in enumerate(batches, start=1):
        if n > 1:
            time.sleep(BATCH_PAUSE_SECONDS)
        part = _review_batch(batch, key, model)
        if part is None:
            return None  # a partial review is worse than an honest failure
        print(f"   batch {n}/{len(batches)}: {len(batch)} messages, {len(part)} flagged",
              flush=True)
        flagged += part
    return flagged


def _review_batch(messages, key, model):
    listing = "\n".join(
        f'[{m["discord_message_id"]}] {m.get("discord_display_name") or m["discord_user_id"]}: '
        f'{(m["content"] or "").strip()}'
        for m in messages
    )
    body = {
        "model": model,
        "temperature": 0,
        # max_tokens is CHARGED, not just capped: Groq counts the full
        # reservation against the budget whether or not it gets used. The
        # same prompt was refused at max_tokens=4000 ("Requested 6276") and
        # went through at 1200, seconds apart - so oversized headroom here
        # is not free caution, it is the job squeezing itself out.
        #
        # 1200 is still six times what this actually needs: with
        # reasoning_effort low, a real 60-message batch used 198 completion
        # tokens. The earlier 1500 failed only because reasoning was set
        # high by default and ate the entire budget before answering.
        "max_tokens": 1200,
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
        if wait > MAX_RETRY_WAIT_SECONDS:
            # Groq will happily ask for a 43-minute wait when the daily
            # budget (not just the per-minute one) is gone - seen live at
            # retry-after 2578. Sleeping that out inside a GitHub Actions
            # run burns runner minutes for a job that is very likely to
            # fail anyway, so stop and report instead. The report going out
            # is what tells anyone the check didn't happen; a silent
            # 43-minute nap tells nobody anything.
            # Print the BODY, not just the wait. The wait alone doesn't say
            # which limit was hit, and the two need opposite responses: a
            # per-minute limit means slow down, a per-DAY one (200,000 tokens
            # for this model, and only the 429 body ever names it) means the
            # whole project has spent its budget and this job should use less
            # of it. Guessing between them is how the last three swallowed
            # failures in this project stayed unexplained for days.
            print(f"   rate limited for {wait:.0f}s - too long to wait, giving up",
                  flush=True)
            print(f"   {r.text[:300]}", flush=True)
            return None
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
            # Seen live on the smaller model: one batch in five came back
            # with an empty answer, which used to end the whole night's
            # review. One batch misbehaving is not a reason to check
            # nothing, so ask that batch again before giving up.
            print(f"no JSON in the review reply: {content[:120]!r} - asking again",
                  flush=True)
            time.sleep(5)
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"}, json=body, timeout=90,
            )
            if r.status_code != 200:
                print(f"   retry failed: HTTP {r.status_code}", flush=True)
                return None
            content = r.json()["choices"][0]["message"]["content"] or ""
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end == -1:
                print(f"   still nothing usable: {content[:120]!r}", flush=True)
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

    result = review(rows)
    flagged, full_quality = result if result else (None, False)
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
    hits = [(by_id[f["id"]], f.get("reason", ""), (f.get("severity") or "borderline").lower())
            for f in flagged if f.get("id") in by_id]
    print(f"flagged: {len(hits)} "
          f"({sum(1 for h in hits if h[2] == AUTO_WARN_SEVERITY)} clear)", flush=True)
    if not hits:
        # Nothing to send. The GitHub Actions run itself is the record
        # that the check happened.
        return

    # --- automatic warnings ---------------------------------------------
    # Only when armed, only "clear" flags, only ones never dealt with
    # before, and never more than MAX_WARNINGS_PER_RUN in one night.
    seen = already_handled([r["discord_message_id"] for r, _, _ in hits])
    warned = {}
    if AUTO_WARN and not full_quality:
        print("   reviewed on the backup model - warnings suppressed, "
              "the report goes out for a person to read", flush=True)
    if AUTO_WARN and full_quality and not dry_run and seen is not None:
        key = os.environ.get("GROQ_API_KEY")
        candidates = [h for h in hits
                      if h[2] == AUTO_WARN_SEVERITY
                      and h[0]["discord_message_id"] not in seen]
        if len(candidates) > MAX_WARNINGS_PER_RUN:
            print(f"   {len(candidates)} clear flags is more than the {MAX_WARNINGS_PER_RUN} "
                  f"allowed in one run - warning nobody, this needs a person", flush=True)
            candidates = []
        for row, reason, _ in candidates:
            who = row.get("discord_display_name") or row["discord_user_id"]
            text = write_warning(row, reason, key)
            if not text:
                continue
            mention = f"<@{row['discord_user_id']}>\n\n"
            posted = post_to_general(mention + text)
            if posted:
                warned[row["discord_message_id"]] = posted
                print(f"   warned {who} ({len(text)} chars, message {posted})", flush=True)
            time.sleep(2)
    elif AUTO_WARN and seen is None:
        print("   automatic warnings skipped: no record of what was already handled",
              flush=True)

    # Every flag is recorded either way, so tomorrow's run knows this one
    # has been seen - whether or not a warning went out for it.
    if not dry_run:
        for row, reason, severity in hits:
            if seen is not None and row["discord_message_id"] not in seen:
                record_flag(row, reason, severity,
                            warned.get(row["discord_message_id"]))

    lines = [
        f"The evening check went through {len(rows)} Discord messages from the "
        f"last {HOURS} hours and flagged {len(hits)}.",
        "",
        "These are suggestions, not decisions - read them in context before "
        "acting on any of them. The model gets things wrong, and a message "
        "can read very differently with the conversation around it.",
        "",
    ]
    if not full_quality:
        lines += [
            "NOTE: the main review model had no budget left today, so this "
            "ran on the backup one. It is measurably worse at this - on a "
            "test it missed an explicit sexual remark entirely and rated two "
            "ordinary argumentative lines as clear-cut. No warnings were "
            "sent automatically, and these ratings deserve more doubt than "
            "usual.",
            "",
        ]
    # Where each flagged message sits in the day, so the surrounding
    # conversation can be shown with it.
    position = {r["discord_message_id"]: i for i, r in enumerate(rows)}

    for row, reason, severity in hits:
        who = row.get("discord_display_name") or row["discord_user_id"]
        sent = warned.get(row["discord_message_id"])
        lines += [
            f"{ist(row['created_at'])} - {who}  [{severity}]",
            f"   >>> {(row['content'] or '').strip()}",
            f"   flagged because: {reason}",
            # Whether a warning was posted is the first thing a reader
            # needs: it decides whether they still have to do something.
            f"   {'WARNED AUTOMATICALLY - message ' + sent if sent else 'no warning sent - your call'}",
            f"   {jump_link(row)}",
            "",
        ]
        # The note at the top of this report tells whoever reads it to judge
        # the message in context. It used to then show the message entirely
        # on its own, which made that instruction impossible to follow - a
        # line like "All bark no bite" means one thing after a threat and
        # another between friends. The conversation around it is the single
        # most useful thing in the whole report.
        i = position[row["discord_message_id"]]
        around = rows[max(0, i - CONTEXT_BEFORE):i + CONTEXT_AFTER + 1]
        lines.append("   what was being said around it:")
        for other in around:
            mark = ">>" if other["discord_message_id"] == row["discord_message_id"] else "  "
            name = other.get("discord_display_name") or other["discord_user_id"]
            lines.append(f"     {mark} {ist(other['created_at'])[-8:]} {name}: "
                         f"{(other['content'] or '').strip()[:200]}")
        lines.append("")
    body = "\n".join(lines)

    if dry_run:
        print("\n----- report (dry run, not emailed) -----")
        print(body)
        return
    send_email(REPORT_EMAIL, f"Discord evening check: {len(hits)} flagged", body)
    print(f"report emailed to {REPORT_EMAIL}", flush=True)


if __name__ == "__main__":
    main()

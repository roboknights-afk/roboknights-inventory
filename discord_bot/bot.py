# RoboKnights Discord AI bot: replies whenever it's @mentioned in a server
# channel or DMed directly, using the same free Groq model
# send_dashboard_update.py already uses for its release-note summaries.
#
# This runs as its own always-on process (deployed on Railway), separate
# from the Streamlit app and from the GitHub Actions scripts. A real bot
# connection needs a persistent gateway link held open 24/7 - neither
# Streamlit Cloud (only runs while serving the app) nor GitHub Actions
# (jobs time out) can do that.
#
# This is a real Discord Bot application (its own token, its own identity
# in the server) - NOT the club's actual account automated to send/receive
# messages. Discord's Terms of Service ban that ("self-bots") regardless of
# whose account it is; a proper bot application is the only way to get
# auto-replies on mentions/DMs without risking the account.
#
# To test on your own laptop: put DISCORD_BOT_TOKEN and GROQ_API_KEY in a
# .env file in this folder (or the repo root, if run from there), then:
#   python bot.py

import os
from collections import defaultdict, deque

import discord
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# Same Supabase project the dashboard uses — every turn gets logged to
# ai_chat_messages there (see supabase_schema.sql), alongside the
# dashboard AI Assistant's own turns, so a host has one shared record of
# everything either AI surface said. Best-effort: a logging failure never
# blocks a reply.
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Same model send_dashboard_update.py uses - fast, free, no web-search tool
# involved (the AI Assistant page's groq/compound has a known, upstream,
# query-dependent 413 "request too large" failure on content-heavy web
# searches - see CLAUDE.md - not worth risking on a bot replying to
# whatever gets thrown at it in Discord).
GROQ_MODEL = "llama-3.3-70b-versatile"

# Keeps the last few exchanges per channel/DM so a reply can follow up on
# what was just said. Lives only in this process's memory - no database -
# so it resets whenever the bot restarts, which is fine for a chat history.
MAX_HISTORY_MESSAGES = 12
history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))

SYSTEM_PROMPT = (
    "You are the RoboKnights robotics club's AI assistant, replying "
    "directly in Discord. Members are students aged 11-18. Keep answers "
    "short and clear - this is Discord, not an essay. No markdown "
    "headers. Use plain paragraphs, or a short bullet list only when it "
    "genuinely helps."
)

groq_client = Groq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def _linked_user_id(discord_user_id):
    # Best-effort match against the roster's self-reported Discord ID
    # (Home page, dashboard) — most Discord members haven't linked one, so
    # None here just means "couldn't identify who this was," not an error.
    try:
        rows = (
            supabase.table("users")
            .select("user_id")
            .eq("discord_user_id", discord_user_id)
            .limit(1)
            .execute()
            .data
        )
        return rows[0]["user_id"] if rows else None
    except Exception:
        return None


def _log_chat(role, content, discord_user_id, discord_channel_id, linked_user_id):
    try:
        supabase.table("ai_chat_messages").insert({
            "source": "discord",
            "user_id": linked_user_id,
            "discord_user_id": discord_user_id,
            "discord_channel_id": discord_channel_id,
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        pass


def _ask_groq(conversation_key, user_text):
    history[conversation_key].append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history[conversation_key])
    try:
        response = groq_client.chat.completions.create(model=GROQ_MODEL, messages=messages)
        reply = response.choices[0].message.content or "I didn't get a response - try asking again."
    except Exception as e:
        reply = f"Sorry, I couldn't get a response right now ({e})."
    history[conversation_key].append({"role": "assistant", "content": reply})
    return reply


async def _send(channel, text):
    # Discord hard-caps a single message at 2000 characters - split rather
    # than truncate, since a cut-off answer is worse than two messages.
    for i in range(0, len(text), 2000):
        await channel.send(text[i:i + 2000])


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (id: {client.user.id})")


@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions

    if not (is_dm or is_mentioned):
        return

    text = message.content
    if is_mentioned:
        text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not text:
        return

    discord_user_id = str(message.author.id)
    discord_channel_id = str(message.channel.id)
    linked_user_id = _linked_user_id(discord_user_id)

    _log_chat("user", text, discord_user_id, discord_channel_id, linked_user_id)
    async with message.channel.typing():
        reply = _ask_groq(message.channel.id, text)
    _log_chat("assistant", reply, discord_user_id, discord_channel_id, linked_user_id)

    await _send(message.channel, reply)


client.run(DISCORD_BOT_TOKEN)

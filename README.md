# RoboKnights Dashboard

An internal web app for the RoboKnights robotics club (DPS RK Puram) — inventory,
competitions, teams, meetings and announcements in one place, plus a Discord bot
that answers questions from the same live data.

It is not a demo. It runs the club: **53 members**, **23 competitions**,
**44 events**, **103 volunteer sign-ups**, and a real parts inventory that
members borrow from each other.

---

## The problem

Before this, the club ran on WhatsApp messages, a shared Google Sheet and
memory. Three things kept going wrong:

- **Parts vanished.** Members lend each other motors, batteries and boards.
  Nobody tracked who had what, so things quietly disappeared.
- **Competition rosters lived in one spreadsheet** ("Nike's List") that only a
  few people could read properly, so members didn't know which events they were
  on until someone told them.
- **Everything depended on one person remembering.** Registration deadlines,
  who volunteered, who actually showed up.

## What it does

| Area | What it handles |
|---|---|
| **Inventory** | Members list parts they own; others request to borrow them. Owner approves/rejects, with due dates and automatic reminder emails before something is due. |
| **Competitions** | Every competition, its events, grade eligibility, team sizes and deadlines. Members volunteer; hosts finalise teams. Imports directly from the club's existing Google Sheet. |
| **Meetings** | Scheduling with join links, RSVPs, and self check-in attendance that a host can correct afterwards. Meetings can be limited to specific people. |
| **Achievements** | Competition results logged per member, with reminders on competition day. |
| **Queries & Feedback** | Private student↔host threads with unread tracking, plus a floating "report an issue" button on every page. |
| **AI Assistant** | Answers questions about *your own* parts, requests, events and meetings — never anyone else's. |
| **Discord bot** | Same data, in the club's Discord. Mention it or DM it. |
| **Host tools** | Member directory, Discord message management, AI conversation logs with CSV export. |

Roles are enforced throughout: members, hosts (staff), and a restricted
view-only tier for the club's sister club.

---

## Architecture

```
Streamlit app  ──┐
(Streamlit Cloud)│
                 ├──►  Supabase (Postgres + Auth)  ◄──┐
Discord bot   ───┘                                    │
(Railway, 24/7)                                       │
                                                      │
GitHub Actions ───────────────────────────────────────┘
(daily reminders, deploy notices)
```

- **Frontend + backend:** Python / Streamlit, 13 pages
- **Database + auth:** Supabase (Postgres), 22 tables
- **Discord bot:** discord.py, deployed separately because a bot needs a
  persistent gateway connection that a web app host can't provide
- **Scheduled jobs:** GitHub Actions for due-date reminders and post-deploy
  Discord notices
- **AI:** Groq (primary), with Gemini and OpenRouter as fallbacks, and Tavily
  for web search

---

## Engineering decisions worth explaining

These are the parts I'd point at if someone asked what was actually hard.

**Cost engineering under a hard free-tier budget.**
The AI runs on free API tiers with a shared daily token limit, and it kept
running out mid-day. Rather than guess, I added a page to export every AI
conversation as CSV and read a real day's log. Two findings:

- **31% of all messages to the bot were 12 characters or less** — "hi" eleven
  times, "up", "wsp" — and each one cost a full API call carrying the entire
  ~700-token system prompt. That was ~28% of the day's budget spent on messages
  that needed no AI at all. Those now get an instant canned reply for zero tokens.
- The entire club database was being pasted into *every* prompt (~2,000 tokens),
  so "what's 2+2" cost the same as a real inventory question. Club data became a
  tool the model calls only when needed: **1,986 → 717 tokens** per message.

**Capping output instead of blocking phrasings.**
Members worked out they could drain the shared budget with "count to 1 million"
or "print the alphabet 100 times" — one reply was 2,780 characters. Blocking
those specific requests is unwinnable; there are endless ways to phrase it. I
capped *output length* on every provider instead, which makes every phrasing
equally cheap. The same request now returns a loop of code in ~137 characters.

**Not trusting a model with work that should be exact.**
Asked "when is my next competition?", the bot named the club's soonest
competition — for a member who wasn't on that team. Filtering a roster by name
and sorting by date is deterministic work, so it moved into Python and the model
now receives a finished list. Accuracy went from roughly half the time to 4/4
across repeated runs.

**Failing honestly.**
When every AI provider is rate-limited, members used to get a wall of raw API
JSON in Discord — which also leaked internal identifiers. Now they get one plain
sentence, and the full error goes to the server logs where it's useful.

**Privacy as a default, not a feature.**
The AI Assistant is built only from the current user's own rows. The Discord bot
answers in a channel the whole server can read, so it gets names, grades and
roles — never emails, phone numbers or admission numbers — and private meetings
are excluded from it entirely.

---

## Scale

| | |
|---|---|
| Python | ~10,500 lines |
| App pages | 13 |
| Database tables | 22 |
| Commits | 144 |
| Built over | 1 month (Jul–Aug 2026) |
| Members using it | 53 |

---

## Running it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Needs a `.env` with Supabase credentials, SMTP details for email, and
optionally Groq / Discord / Google Sheets keys. Every integration degrades
gracefully — missing keys disable that feature rather than breaking the app.
See `DEPLOY.md` for full deployment instructions.

```bash
# Discord bot (separate process)
pip install -r discord_bot/requirements.txt
python discord_bot/bot.py
```

## Repo layout

```
app.py                  Auth, navigation, global UI
app_pages/              One file per page (13)
shared.py               Supabase client, caching, email, Discord helpers
discord_bot/            The always-on Discord bot
e2c_import.py           Google Sheets competition importer
send_*.py               Scheduled reminder jobs (GitHub Actions)
supabase_schema.sql     Full database schema
```

---

Built and maintained by **Naitik Jindal**, RoboKnights, DPS RK Puram.

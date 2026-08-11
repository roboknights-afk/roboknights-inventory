# Deploying to Streamlit Community Cloud (free)

Everything the app needs is already in place for this: it's a GitHub repo,
`requirements.txt` lists what to install, and secrets are read from
environment variables the same way whether they come from a local `.env`
file or from a hosting platform's secrets manager.

## Steps

1. Go to **share.streamlit.io** and sign in with your GitHub account (the
   one that owns `roboknights-afk/roboknights-inventory`).
2. Click **New app**.
3. Pick the repo (`roboknights-afk/roboknights-inventory`), branch
   (`master`), and main file path (`app.py`).
4. Before clicking Deploy, open **Advanced settings** and paste in the
   contents of your local `.env` file, reformatted as TOML — copy this
   template and fill in the real values from `.env`:

   ```toml
   SUPABASE_URL = "..."
   SUPABASE_KEY = "..."
   SMTP_HOST = "..."
   SMTP_PORT = "..."
   SMTP_USERNAME = "..."
   SMTP_PASSWORD = "..."
   SMTP_SENDER = "..."
   GOOGLE_SHEETS_API_KEY = "..."
   APP_URL = "https://your-app-name.streamlit.app"
   WHATSAPP_PHONE_NUMBER_ID = "..."
   WHATSAPP_ACCESS_TOKEN = "..."
   GROQ_API_KEY = "..."
   DISCORD_COMPETITIONS_WEBHOOK_URL = "..."
   DISCORD_EXUN_WEBHOOK_URL = "..."
   DISCORD_DASHBOARD_WEBHOOK_URL = "..."
   DISCORD_MEMBER_ROLE_ID = "..."
   DISCORD_ADHOC_ROLE_ID = "..."
   ```

   `GOOGLE_SHEETS_API_KEY` is required, not optional — the Competitions
   page's E2C sheet sync crashes without it (e2c_import.py reads it
   directly, no fallback). WhatsApp's two keys are optional — those notifications silently do nothing
   until both are set (see CLAUDE.md for how to get them from Meta's
   developer console). `GROQ_API_KEY` is also optional but the AI
   Assistant page shows a "not set up yet" message to members until it's
   added — get a free key (no card needed) at
   [console.groq.com/keys](https://console.groq.com/keys). (Gemini's free
   tier was tried first but isn't available to India-based accounts — see
   the comment at the top of app_pages/assistant.py.) `DISCORD_COMPETITIONS_WEBHOOK_URL`
   is also optional — new-event notifications to Discord silently do
   nothing until it's set. Get it from the target channel's Settings →
   Integrations → Webhooks → New Webhook in Discord itself.

   `DISCORD_EXUN_WEBHOOK_URL` works the same way, for a SECOND, separate
   channel (the private Exun<>RK channel) — its own Incoming Webhook, made
   the same way in that channel's own Settings → Integrations → Webhooks.
   The "team names finalized" notification (every event under a
   competition has its full selected roster) posts here, and it's also
   available for custom messages on the new Discord Messages page (host
   only). Also optional — silently does nothing until it's set.

   `DISCORD_DASHBOARD_WEBHOOK_URL` is a THIRD channel, for "the dashboard
   itself was just updated" notices. **This one is different: it's posted by
   GitHub Actions, not by the app**, so it also has to be added as a GitHub
   repo secret (Settings → Secrets and variables → Actions), not only in
   Streamlit Cloud. Adding it to Streamlit Cloud is still worth doing — that's
   what lets a host edit or delete those posts from the Discord Messages page.
   See "Dashboard update notices" below.

   `DISCORD_MEMBER_ROLE_ID` and `DISCORD_ADHOC_ROLE_ID` are also optional —
   every Discord notification (new event, vacant-events reminder) pings
   these two roles instead of tagging individual members. Either can be
   left unset (that role just isn't pinged), but both work the same way
   to get the ID:
   1. In Discord, open **User Settings → Advanced** and turn on
      **Developer Mode**.
   2. Open the server, go to **Server Settings → Roles**, right-click the
      role (e.g. "Member" or "Adhoc"), and choose **Copy Role ID**. Paste
      that numeric ID as the secret's value (no `<@&...>` wrapper — the
      app adds that itself).
   3. Still in **Server Settings → Roles**, click the role and turn on
      **"Allow anyone to @mention this role"**. Without this, Discord
      silently ignores the ping — it'll show as plain text but nobody
      with that role gets notified — since an incoming webhook has no
      elevated permission to mention a non-mentionable role.

   You won't know the exact `APP_URL` until after the first deploy (Streamlit
   picks or lets you choose a subdomain) — deploy once, see the URL, then
   come back to Advanced settings and add/update the `APP_URL` secret to
   match it, so the "New request" email links — and now every Discord
   message this app sends, which always ends with a link back to
   `APP_URL` — point to the real address instead of localhost.

5. Click **Deploy**. First deploy takes a few minutes.

## Two more things once it's live

- **Supabase**: go to Authentication → URL Configuration → Site URL, and
  update it from `http://localhost:8501` to your real deployed URL, and add
  the same URL to Redirect URLs. (Same place you fixed this once already
  when the reset-password links were pointing at port 3000.)
- **GitHub Actions reminders**: `send_due_reminders.py` doesn't need
  `APP_URL` at all — its emails don't contain a link — so nothing to change
  there.

## Dashboard update notices (Discord)

`.github/workflows/dashboard-update.yml` runs `send_dashboard_update.py` on
every push to `master` — the same moment Streamlit Cloud redeploys — and
posts "The dashboard just got an update" plus a 3-line plain-English summary
of what changed to the Discord dashboard channel.

The summary is written by Groq (the same free model the AI Assistant uses)
from the push's commit messages. If `GROQ_API_KEY` isn't set, or the call
fails, it falls back to listing the three most recent commit subject lines —
it never blocks the notification.

To turn it on, add these as **GitHub repo secrets** (Settings → Secrets and
variables → Actions), since this runs on GitHub's servers, not in the app:

- `DISCORD_DASHBOARD_WEBHOOK_URL` — required; without it the workflow runs
  and exits quietly, posting nothing.
- `GROQ_API_KEY` — optional, but without it the summary is raw commit
  subjects rather than member-friendly wording.
- `SUPABASE_URL` / `SUPABASE_KEY` — optional; these only log the post so a
  host can delete or edit it later from the Discord Messages page. Both are
  already set for the reminder workflow.

A push containing nothing but merge commits posts nothing at all — a
"we updated!" notice with no content is worse than staying quiet.

## Discord AI bot (auto-replies to @mentions and DMs)

`discord_bot/bot.py` is a separate, always-on Discord bot — not part of the
Streamlit app, and not something GitHub Actions can run (a bot needs a
persistent gateway connection held open 24/7; Actions jobs time out and
Streamlit Cloud only runs while serving the app). It's a real Discord Bot
application, added to the server with its own token and its own identity
(currently `roboknightsbot`) — deliberately not the club's actual Discord
account automated to send/receive messages, since Discord's Terms of
Service ban that ("self-bots") regardless of whose account it is.

It replies whenever @mentioned in a server channel or DMed directly, using
the same free Groq model (`llama-3.3-70b-versatile`) the dashboard-update
summaries already use — no new AI account needed.

### Deploying it (Railway, free tier)

1. Go to [railway.app](https://railway.app) and sign in with GitHub.
2. **New Project** → **Deploy from GitHub repo** → pick
   `roboknights-afk/roboknights-inventory`.
3. Once the service is created, open its **Settings** tab and set
   **Root Directory** to `discord_bot` — this is what tells Railway to use
   `discord_bot/requirements.txt` and `discord_bot/Procfile` instead of the
   main app's.
4. Open the **Variables** tab and add:
   - `DISCORD_BOT_TOKEN` — from the bot's page at
     [discord.com/developers/applications](https://discord.com/developers/applications)
     → your application → **Bot** → Reset Token.
   - `GROQ_API_KEY` — same key already used by the Streamlit app and the
     dashboard-update workflow; get a free one (no card needed) at
     [console.groq.com/keys](https://console.groq.com/keys) if you don't
     already have it handy.
   - `SUPABASE_URL` / `SUPABASE_KEY` — same project the Streamlit app
     uses. Required, not optional: the bot logs every message (its own
     and whoever it's talking to) to the `ai_chat_messages` table there,
     alongside the dashboard AI Assistant's own chat log, so a host has
     one shared record of everything either AI surface has said.
   - `CEREBRAS_API_KEY` — optional. `llama-3.3-70b-versatile`'s free-tier
     budget on this project's Groq key is 100,000 tokens/day, genuinely
     reachable in real use, not just a theoretical ceiling — confirmed by
     hitting it during testing. When Groq fails, the bot falls back to
     Cerebras's free tier (1M tokens/day, no card) for that one reply,
     then goes right back to Groq next time. Sign up at
     [cloud.cerebras.ai](https://cloud.cerebras.ai) (instant, no card, no
     waitlist). Gemini was tried first for this same fallback role — same
     as the dashboard's AI Assistant already found, its free tier returns
     a hard 0 quota for India-based accounts, so it's not an option here
     without adding billing.
5. Railway auto-deploys on every push to `master`, same as Streamlit Cloud
   — no separate redeploy step needed after this.

To test on your own laptop first: put both variables in a `.env` file
inside `discord_bot/` (or run from the repo root, which already has one),
then `pip install -r discord_bot/requirements.txt` and
`python discord_bot/bot.py`.

## What doesn't change

- Your local `.env` file keeps working for testing on your own laptop —
  Streamlit Cloud's secrets are separate and don't touch it.
- The GitHub Actions secrets (for the reminder emails) are also separate
  from Streamlit Cloud's secrets — they were already set up in Chunk "due
  soon reminders" and don't need touching again.

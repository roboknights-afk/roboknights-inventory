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

## What doesn't change

- Your local `.env` file keeps working for testing on your own laptop —
  Streamlit Cloud's secrets are separate and don't touch it.
- The GitHub Actions secrets (for the reminder emails) are also separate
  from Streamlit Cloud's secrets — they were already set up in Chunk "due
  soon reminders" and don't need touching again.

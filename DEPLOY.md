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
   DISCORD_GENERAL_WEBHOOK_URL = "..."
   DISCORD_ANNOUNCEMENTS_WEBHOOK_URL = "..."
   DISCORD_MEMBER_ROLE_ID = "..."
   DISCORD_ADHOC_ROLE_ID = "..."
   GOOGLE_SERVICE_ACCOUNT_JSON_B64 = "..."
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

   `DISCORD_GENERAL_WEBHOOK_URL` is a FOURTH channel (2026-08-14, added for
   an ad-hoc host message to #general) — same setup as the others, its own
   Incoming Webhook from #general's Settings → Integrations → Webhooks. Not
   wired into the Discord Messages page's tabs; usable via
   `send_discord_message(..., channel="general")` directly.
   See "Dashboard update notices" below.

   `DISCORD_ANNOUNCEMENTS_WEBHOOK_URL` is a FIFTH channel (2026-08-16), for
   the club's #announcements channel. Same setup — its own Incoming Webhook
   from that channel's Settings → Integrations → Webhooks. Unlike #general,
   this one DOES have its own tab on the Discord Messages page, so a host
   can write, preview, edit and delete announcements without touching code.
   Nothing posts here automatically.

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

   `GOOGLE_SERVICE_ACCOUNT_JSON_B64` is required for the "verify your
   details" popup's Clio-sheet sync — without it, that sync is skipped
   silently (the popup itself still saves to the database fine). It's the
   downloaded Google Cloud service-account JSON key, base64-encoded onto
   one line (`base64.b64encode(open("key.json","rb").read()).decode()`)
   since `.env`/TOML secrets don't handle multi-line values well. See
   CLAUDE.md for how the service account was created and shared with the
   Clio sheet as Editor.

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

## Login with Google

A "Continue with Google" button sits above the Log in / Sign up tabs,
restricted to `@dpsrkp.net` accounts — same domain gate every other signup
path in this app already enforces. Needs two things set up once, both
outside this repo:

1. **Google Cloud Console** ([console.cloud.google.com](https://console.cloud.google.com)):
   - Create a project (or reuse one) → **APIs & Services → Credentials**
   - **Create Credentials → OAuth client ID** → Application type **Web application**
   - Under **Authorized redirect URIs**, add your Supabase project's
     callback URL: `https://<your-project-ref>.supabase.co/auth/v1/callback`
     (find `<your-project-ref>` in `SUPABASE_URL`)
   - Copy the generated **Client ID** and **Client Secret**
2. **Supabase Dashboard** → **Authentication → Providers → Google**:
   - Toggle it on, paste in the Client ID and Client Secret from above,
     Save
   - Under **Authentication → URL Configuration**, make sure `APP_URL`
     (e.g. `https://roboknights.in/dashboard`) is in **Redirect URLs** —
     it's probably there already from the password-reset link fix; if
     not, add it

No code changes or new env vars needed beyond that — `SUPABASE_URL` and
`APP_URL`, both already required, are all `app.py` uses to build the
Google sign-in link.

**First-time login**: if someone signs in with Google and has no existing
profile (grade/section/etc.), they land on a short "one more step" form —
same required fields signup already collects, just no password. Host and
Exun accounts skip this; those are decided purely by email, not a
database row.

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

## The Discord evening check

`check_discord_messages.py` runs at **8:00 PM IST** (14:30 UTC) from the
existing `.github/workflows/due-reminders.yml` workflow — the same one that
sends the loan and achievement reminders, just a third cron entry and a
third step.

It reads the last 24 hours of the `discord_channel_log` table (which the
Discord bot already fills with every message it can see in the club's own
server) and emails a report of anything that reads as a personal attack,
with the surrounding conversation and a link straight to the message.

**It only reads and reports.** It never replies, deletes, warns anyone, or
touches a Discord message. A person decides what to do about a flag. The
prompt is deliberately biased towards under-flagging — arguing, swearing at
nobody in particular, jokes and banter are explicitly left alone — because a
false positive means a member gets questioned over a joke.

Nothing new is needed to turn it on: every secret it uses
(`SUPABASE_URL`, `SUPABASE_KEY`, `GROQ_API_KEY`, and the five `SMTP_*`
values) is already a GitHub repo secret for the other workflows.

Two optional variables:

- `MODERATION_REPORT_EMAIL` — who gets the report. Defaults to
  `roboknights@dpsrkp.net`, deliberately **not** the whole `HOST_EMAILS`
  set, which includes teachers. A nightly "these students were rude" email
  reaching staff inboxes should be a decision someone makes on purpose, not
  a side effect of switching this on.
- `DISCORD_HOME_GUILD_ID` — only used to build the links back to a message.
  Defaults to the club's own server id.

If nothing is flagged, no email is sent — the workflow run itself is the
record that the check happened. If the review can't run at all (no API key,
every retry rate-limited), that **does** send an email, because a check
that silently stops running is worse than one that fails loudly.

To test it: **Actions → Due date reminders → Run workflow**. A manual run
executes all three steps, this one included. Locally,
`python check_discord_messages.py --dry-run` prints the report instead of
emailing it.

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

### Deploying it (Oracle Cloud, Always Free)

**Moved off Railway 2026-09-09** — Railway's free trial was expiring, and
Railway's own GitHub integration for this service never worked in the
first place (its "Auto deploy unavailable" bug, see the git history for
that whole saga). Oracle Cloud's Always Free tier costs nothing,
permanently, with no trial clock — a small VM (the "Ampere A1" shape
below) is more than this bot needs. Unlike Railway, this deploy path
actually works from a push: `.github/workflows/deploy-bot.yml` rsyncs
`discord_bot/` straight to the VM and restarts it, no third-party
integration involved.

**One-time setup (needs your own Oracle account — this part can't be done
for you):**

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) for an Always
   Free account. Oracle asks for a card to verify identity even for
   Always Free resources — it should never actually get charged as long
   as you stay on Always Free shapes, but that's Oracle's own policy, not
   this project's.
2. **Menu → Compute → Instances → Create Instance.**
   - Image: **Canonical Ubuntu** (22.04 or newer).
   - Shape: **Ampere** → `VM.Standard.A1.Flex`, the Always Free ARM shape
     (up to 4 OCPUs / 24 GB total, split across up to 4 instances) — 1
     OCPU / 6 GB is plenty for this bot. The AMD `VM.Standard.E2.1.Micro`
     shape is the other Always Free option if you'd rather avoid ARM, but
     it's far smaller (1/8 OCPU, 1 GB).
   - Add your SSH public key when prompted (generate one first with
     `ssh-keygen` if you don't already have one) — this is how you'll log
     in; there's no password.
3. Once it's running, note the instance's **public IP address** from the
   instance details page.
4. From your own machine, copy the two setup files up and run the setup
   script (the default Ubuntu image's login user is `ubuntu`):
   ```
   scp discord_bot/deploy/setup_oracle_vm.sh discord_bot/deploy/roboknights-bot.service ubuntu@<public-ip>:~/
   ssh ubuntu@<public-ip> 'bash setup_oracle_vm.sh'
   ```
   This installs Python, creates `~/roboknights-bot`, and installs (but
   doesn't yet start) a systemd service for the bot. Full detail in the
   script's own comments.
5. While still SSHed in, create `~/roboknights-bot/.env` with the bot's
   real secrets — same variable names as the list below, just in a plain
   `.env` file now instead of a platform's Variables tab.
6. Back on GitHub: **Settings → Secrets and variables → Actions → New
   repository secret**, add three:
   - `ORACLE_HOST` — the public IP from step 3.
   - `ORACLE_USER` — `ubuntu`.
   - `ORACLE_SSH_KEY` — the **private** half of the key pair from step 2
     (the file, not the `.pub` one) — paste its full contents.
7. Push to `master` (touching anything under `discord_bot/`), or trigger
   **Deploy Discord bot** by hand from the Actions tab. This rsyncs the
   code over, installs requirements in the VM's venv, and starts the
   service for real the first time.
8. Confirm it's live: `ssh ubuntu@<public-ip>` then
   `journalctl --user -u roboknights-bot -f` and look for the
   `Running build: ...` line (from `BOT_BUILD` at the top of `bot.py`) —
   if it doesn't match what's in the file, the deploy didn't actually
   land.

**The secrets** (same list, same values, regardless of which platform
holds them):
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
   - `GEMINI_API_KEY` — optional. `llama-3.3-70b-versatile`'s free-tier
     budget on this project's Groq key is 100,000 tokens/day, genuinely
     reachable in real use, not just a theoretical ceiling — confirmed by
     hitting it during testing. When Groq fails, the bot falls back to
     Gemini's free tier for that one reply, then goes right back to Groq
     next time. Get a key at
     [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
     (free, no card). Chat-only here — Gemini's own web-search feature
     (grounding) needs a linked billing account even for its free quota,
     confirmed live, so it's not used for that; Tavily above already
     covers search. Cerebras was tried first for this fallback role —
     free tier on paper, but this account got 402 Payment Required on
     every model regardless of billing changes, on top of Cerebras
     shutting the free tier down entirely from Aug 17, 2026, so it was
     dropped in favor of Gemini, which is confirmed actually working.
   - `TAVILY_API_KEY` — optional, but without it web search runs through
     `groq/compound` instead, which is known to be unreliable (see
     CLAUDE.md's Discord AI bot section). 1,000 free searches/month, no
     card, sign up at [tavily.com](https://tavily.com).
   - `OPENROUTER_API_KEY` — optional third and last fallback, for when
     Groq AND Gemini have both run out on the same day (confirmed to
     happen on a busy day, at which point the bot can only tell members
     it's broken). OpenRouter's `:free` models cost $0/token with no card
     ever required (50 requests/day without buying credits) and are a
     completely separate quota from the other two, which is the point.
     Sign up at [openrouter.ai](https://openrouter.ai), create a key on
     the Keys page. Which models are free rotates over time — if the one
     in `bot.py` (`OPENROUTER_MODEL`) starts 404ing, check
     `curl https://openrouter.ai/api/v1/models` for current `:free` ids.
   - `DISCORD_HOME_GUILD_ID` — our own Discord server's ID
     (`607226177425506325`). In this server the bot answers in any channel
     it's @mentioned in, as it always has. In **any other** server it
     answers only in the channels listed in the next variable, and it never
     writes that server's messages to `discord_channel_log`. Leave it unset
     and every server counts as home — i.e. exactly the old behaviour, so a
     missing value can never mute the bot in our own server.
   - `DISCORD_GUEST_CHANNEL_IDS` — comma-separated channel IDs in OTHER
     servers the bot is allowed to work in. Currently
     `1482462237992947762`, the `#roboknights` channel in the Exun side's
     server — the same channel `DISCORD_EXUN_WEBHOOK_URL` already posts
     to. A Discord invite grants a bot access to a whole server, not one
     channel; this is what actually keeps it to the channel it was added
     for. Right-click the channel → **Copy Channel ID** (needs Developer
     Mode on, under User Settings → Advanced) to get it.
   - `DISCORD_LOGS_CHANNEL_ID` (2026-09-01) — the channel ID of a
     **#logs** channel in our own server. Whenever anyone deletes a
     message anywhere the bot can see (home server only — it never does
     this in a guest server), the bot posts what got deleted, who posted
     it, and which channel, into this one. Optional — leave unset and
     the bot just doesn't do this, same as every other Discord secret in
     this project. Needs a real channel to exist first: create `#logs`
     in the server, make sure `roboknightsbot` can view and send there
     (it already can everywhere in our own server unless that channel's
     permissions were narrowed), then right-click it → **Copy Channel
     ID** and set the variable to that number.
**It DOES auto-deploy on push now** — unlike Railway (see the note at the
top of this section), `deploy-bot.yml` actually fires on every push
touching `discord_bot/**` and finishes the job itself: rsync the code to
the VM, install requirements, restart the service. Confirm a deploy
landed via `journalctl --user -u roboknights-bot -f` on the VM, same
`Running build: ...` check as before (`BOT_BUILD` at the top of
`bot.py`) — or just watch the workflow run go green in the Actions tab,
since its last step checks the service actually came back up.

To test on your own laptop first: put all the variables above in a
`.env` file inside `discord_bot/` (or run from the repo root, which
already has one), then `pip install -r discord_bot/requirements.txt` and
`python discord_bot/bot.py`.

### Adding the bot to another club's server

Done once for the Exun clan's server (2026-08-16) so their side can ask it
about competitions, rosters and members directly. Someone with **Manage
Server** on *that* server has to do steps 1-2 — an invite can't be issued
from our side alone.

1. Send them this invite link (it grants exactly View Channels, Send
   Messages, Read Message History and Embed Links — nothing moderation
   related):

   ```
   https://discord.com/oauth2/authorize?client_id=1536836032329416724&permissions=84992&scope=bot
   ```

2. On their side: pick their server in the dropdown, authorize, then in
   **that one channel** → Edit Channel → Permissions, make sure
   `roboknightsbot` can view and send. If they want it kept out of every
   other channel of theirs, denying View Channel at the category or server
   level and allowing it on the one channel is the clean way.
3. Get that channel's ID (right-click → **Copy Channel ID**, with
   Developer Mode on) and add it to `DISCORD_GUEST_CHANNEL_IDS` in
   `~/roboknights-bot/.env` on the VM (SSH in and edit it directly),
   comma-separated if there's more than one. **Until this is set the bot
   stays silent there** — that's deliberate: joining a server should not
   by itself let anyone in it start querying our club data.
4. Restart the bot so it picks up the new `.env` value: SSH in and run
   `systemctl --user restart roboknights-bot` (the deploy workflow only
   restarts on a code push, and this was an `.env` edit, not a push).
   Confirm with `journalctl --user -u roboknights-bot -f`.

What the bot will and won't tell them: it answers from the same club data
it already has — every member's name, grade, section and standing
(including ad hocs), all competitions with their venues, dates, deadlines
and links, and who has volunteered or been finalized for each event. It
does **not** have anyone's email, phone number or admission number —
`_build_club_context` in `bot.py` deliberately never fetches those, and
that predates this and is unrelated to which server it's in. The roast and
staff-topic blocks apply there exactly as they do at home.

## What doesn't change

- Your local `.env` file keeps working for testing on your own laptop —
  Streamlit Cloud's secrets are separate and don't touch it.
- The GitHub Actions secrets (for the reminder emails) are also separate
  from Streamlit Cloud's secrets — they were already set up in Chunk "due
  soon reminders" and don't need touching again.

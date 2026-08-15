# RoboKnights Parts Inventory

An events management dashboard for a school robotics club (RoboKnights).

## The project

Members track who owns which parts and borrow parts from each other with an
approval step. Example: Aryamman needs Naitik's P219 motor → requests it →
Naitik gets notified → approves → Aryamman is told where to collect it.

## Stack (don't add to this without asking first)

Python 3, Streamlit for the UI, Supabase (hosted Postgres + Auth) for storage
and login, everything in a single `app.py` for now. No Docker, Flask, React,
or npm.

**Changed 2026-07-12:** this project started on SQLite with a fake "I am:
[name]" dropdown (see git history / old CLAUDE.md text below if you need the
original v1 plan). The student deliberately chose to move to Supabase for
real storage and real accounts, after being reminded this reverses the
original "no auth, no cloud hosting" rule. Treat Supabase + real auth as the
current source of truth, not a deviation from it.

**Also changed 2026-07-12 (later the same day):** the project is now a git
repo, pushed to a private GitHub repo
(github.com/roboknights-afk/roboknights-inventory), specifically so a
GitHub Actions scheduled workflow can send due-date reminder emails on a
real daily timer — discussed as a deliberate tradeoff against the simpler
"check on every page load" option, not scope creep.

## Data model — three tables (already in app.py)

- **users**: user_id, name, email
- **parts**: part_id, part_number (e.g. "P219"), name, owner_id, status
  ('available' or 'on loan') — one row per physical part
- **requests**: request_id, part_id, requester_id, owner_id, status,
  created_at — one row per borrow attempt

## Request lifecycle (the core logic)

A request's status moves only through:
`pending → approved → on loan → returned`, OR `pending → rejected` (part
stays available, end).

- New request: starts pending, owner is notified.
- Approve: request → approved, requester notified, part → on loan.
- Reject: request → rejected, part stays available.
- A part that's on loan can't be requested by anyone else.
- On return: part → available.

## Build plan — ONE chunk at a time, don't jump ahead

SQLite-era chunks (all done, being migrated off of):
- Done: Chunk 0 (hardcoded table)
- Done: Chunk 1 (SQLite storage, three tables, sample data, parts table on screen)
- Done: Chunk 2 (Request this button → pending row, no email/approval)
- Done: Chunk 3 (owner view of pending requests, Approve/Reject buttons)

Supabase migration chunks (current plan):
- Done: Chunk 5 (student created the Supabase project, got Project URL + anon key)
- Done: Chunk 6 (app.py now reads/writes Supabase instead of inventory.db;
  users.user_id is a UUID; parts/requests queries rewritten as simple
  per-table lookups + Python dicts instead of SQL joins, since Postgres
  doesn't cleanly support joining requests->users twice in one query)
- Done: Chunk 7 (real signup/login/forgot-password pages via Supabase Auth +
  Brevo SMTP, replacing the "I am:" dropdown entirely). Forgot password uses
  a 6-digit code, not a clickable link — clicking the link inside Streamlit
  ran into a hard browser-security wall (sandboxed iframes can't redirect
  the page, even on a genuine click), so the reset screen accepts either a
  typed code or a pasted link as a fallback.
- Done: Chunk 8 (Request/Approve/Reject already used the real logged-in
  user by the time Chunk 7 was finished — the dropdown was removed in one
  step, not two, since leaving it in place with nothing to feed it would
  have just been dead code).
- Done: Chunk 9a — "Add a part I own" screen (expander on the main page,
  owner_id = whoever's logged in, status always starts 'available').
- Done: Chunk 9b — email notifications at the two original moments (owner
  on new request, requester on approve/reject). Sent from app.py via
  Python's built-in smtplib, reusing the same Brevo SMTP credentials
  already configured in Supabase. Verified end-to-end with two real test
  accounts and real inbox delivery, not just "the code didn't crash."

Post-Chunk-9 roadmap, in the order agreed with the student (one at a time,
not bundled — see house rule #1 below):
- Done: mark a part "returned" — owner-only "Mark as returned" button on
  their own on-loan parts. Flips part.status back to 'available' and the
  matching request.status to 'returned' (a value not in the original
  schema comment, added here since request.status now needs to
  distinguish "still on loan" from "loan finished" for the upcoming
  my-parts/borrowed-by-me dashboards). No email on return — not asked for.
- Done: delete a part (owner-only, only while 'available' — not while on
  loan, so who-has-it info can't silently vanish). Deleting a part also
  deletes its request history (FK requires it; acceptable here since
  deleting a part means "gone from inventory," so its history isn't
  needed either). Also done at the same time: parts owned by you are
  labeled "(yours)" in the parts list.
- Done: auto-generated part serial numbers, format `RK-0001`, `RK-0002`,
  etc. The "Part number" text field is gone from the Add Part form
  entirely; nobody types a number by hand anymore. Backfilled all
  existing parts to the new format too, at the student's request (old
  codes like "P219" were not a real external labeling scheme worth
  preserving). Bug fixed after initial build: the serial was first based
  on the part's own database id, which climbs forever and skips numbers
  freed by deleted test rows (jumped straight to RK-0018 with only 6
  real parts). Fixed to instead take the highest RK-#### number
  currently in use and count up from there.
- Done: loan due dates. Requester picks a number of days when requesting
  (next to "Request this"). Owner sees that number and, on clicking
  Approve, gets a second step — a days field pre-filled with the
  requested value, editable, plus Confirm/Cancel — so they can keep it or
  change it before it's final. Confirming stores requests.due_date
  (today + however many days) and the approval email says e.g. "approved
  ... for 5 day(s) (until 17 Jul 2026)". Verified end-to-end with real
  test accounts, including the date math.
- Done: "due soon" reminder emails, sent 2 days and 1 day before
  requests.due_date. Chose a real scheduled job over "check on page
  load" (discussed both — student wanted reliable timing over
  simplicity). This is why the project is now a git repo pushed to
  github.com/roboknights-afk/roboknights-inventory: a GitHub Actions
  workflow (.github/workflows/due-reminders.yml) runs
  send_due_reminders.py once a day (3:30 UTC / 9:00 AM IST) and on
  manual trigger, completely independent of whether the Streamlit app
  or the student's laptop is running. Needs its own copy of the SMTP +
  Supabase credentials as GitHub repo secrets (Settings → Secrets and
  variables → Actions) — same values as .env, just re-entered there
  since GitHub Actions can't read a local .env file. Two new columns
  (requests.reminder_2day_sent, reminder_1day_sent) stop the same
  reminder going out twice. Verified for real: triggered the workflow
  manually from the Actions tab and confirmed via the database that it
  ran on GitHub's servers and correctly marked the reminder sent.
- Done: "redirect to accept/reject" link inside the new-request email.
  Ends in `?tab=requests` — a plain query parameter, not a "#" fragment,
  so unlike the password-reset links this needed no JavaScript at all;
  Streamlit reads it natively via st.query_params. When present (and the
  owner is logged in), a banner appears right under "Logged in as" with
  a "[Jump to it ↓](#requests-for-my-parts)" link — Streamlit auto-gives
  every st.subheader an anchor matching its text, so the link just uses
  that directly.
- Done: two dashboard views — "Parts I've lent out" (owner-only) and
  "What I've borrowed" (requester-only), at the bottom of the page. Both
  just read requests where status = 'approved' (still on loan), filtered
  by owner_id or requester_id — no new columns needed, and a loan drops
  off both lists automatically once "Mark as returned" flips its status.
  Verified via direct query (not full browser login — see house rule
  about the ashish.jindal079 test inbox below) using the original seed
  accounts (Naitik/Aryamman), including a pre-existing loan from before
  due dates existed, which correctly showed "no due date set" instead
  of erroring.

This closes out the full post-Chunk-9 roadmap.

Branding/UI/deployment chunk (done):
- Done: RoboKnights branding — logo (the actual gear mark from
  roboknights.in, extracted as inline SVG and saved to
  `static/roboknights_logo.svg`, shown via `st.logo()`), dark theme + Work
  Sans font matching the real site (`.streamlit/config.toml`), and general
  polish (Material Symbol icons on buttons, `st.container(border=True)`
  around each list row instead of divider lines, `layout="wide"`).
  Done natively via Streamlit's own theming system, NOT custom CSS — the
  student asked about a third-party tool called "Impeccable" for this,
  which was declined: it's an npm/React/Tailwind tool (explicitly against
  the "no npm" stack rule) that doesn't apply to a Streamlit app at all.
- Done: deployment provisions for Streamlit Community Cloud — added
  `requirements.txt`, made `APP_URL` read from an environment variable
  (falls back to localhost) so deploying doesn't need a code change, and
  wrote full deploy steps in `DEPLOY.md`. **Deployed** (confirmed
  2026-08-09) at roboknights-dashboard.streamlit.app — Streamlit Cloud
  auto-redeploys on every push to `master`, but its Secrets (Settings →
  Secrets in the app's own dashboard) are separate from `.env` and don't
  update automatically; any new secret added locally (e.g. `GROQ_API_KEY`
  for the AI Assistant) has to be added there by hand too.
- Deferred, too vague to build yet: "add other features" (which ones?)
  and "link to the roboknights.in website in the future" (not actionable
  until there's a concrete integration to build — ask what "link" means
  when it comes up again: an outbound link from the club site, embedding,
  shared login, etc. are all very different asks).

Follow-up polish round (done, same day):
- Declined switching to Gradio or NiceGUI — student asked directly. Pushed
  back plainly: this app already has real, working, Streamlit-specific
  integrations (session_state, query_params, st.rerun, the GitHub Actions
  deploy story) that a framework switch would mean rewriting from
  scratch, for a payoff ("more presentable") that's achievable in
  Streamlit directly. Stayed on Streamlit, pushed visual polish further
  instead — see below.
- Added the student's own RoboKnights wordmark PNG (gears + "ROBOKNIGHTS"
  text, `static/RKs Logo (2).png`) to the login screen specifically. It's
  black-on-transparent, which would nearly vanish on the dark theme, so
  it sits in a small white card — the ONE deliberate exception to
  "no custom CSS," scoped narrowly via `st.container(key=...)` +
  `st.html()` targeting just that one element, not general theming
  (which stays in config.toml as before).
- Column headers added above the main parts table (Serial no / Name /
  Owned by / Status / Days) — there weren't any before. Status is now a
  colored `st.badge` (green "Available" / orange "On loan") instead of
  plain text.
- Removed the "Every part below is stored in Supabase..." caption line
  (no longer needed/wanted).
- Removed `seed_sample_data()` entirely (function + call site) — real
  members are signing up and adding real parts now, so auto-seeding fake
  sample data on an empty table is dead scaffolding, not a safety net.
- Deleted the placeholder data it had created: 3 fake seed users
  (Naitik/Aryamman/Ishaan, @example.com emails) and 5 fake seed parts
  (RK-0001..RK-0005), plus the test requests tangled up with them.
  Confirmed first which data was real (checked for real @dpsrkp.net /
  personal emails and parts added through the actual Add Part flow, e.g.
  RK-0007 "Chain 20 feet" owned by a real member) before deleting
  anything — only removed rows traceable back to the original seed
  function or requests that referenced them.
- Verified the whole round visually via screenshot: logged-in view shows
  headers/badges/real data correctly, and the wordmark logo renders
  properly in its white card on the login screen.

Second polish round (done, same day):
- Serial numbers on delete: student asked for full renumbering so numbers
  stay contiguous after a delete. Pushed back with the concrete risk (an
  on-loan part's serial silently changing out from under emails/labels)
  and offered the alternative; student chose it: existing parts NEVER get
  renumbered, but a number freed by deletion is recycled by the next new
  part (next serial = lowest RK-#### not in use, instead of max+1).
  Verified live through the real UI: with only RK-0007 in the table, a
  new part correctly got RK-0001; deleted it; RK-0007 kept its number.
- UI overhaul, all native Streamlit: account card + Add-a-part form moved
  to the sidebar (main page is now purely the inventory); metrics row
  (Total parts / Available / On loan / Requests for me) in bordered
  columns via st.metric; search box (matches serial or name; Streamlit
  text_input only applies on Enter/blur — that's built-in behavior) +
  st.segmented_control status filter (All / Available / On loan / Mine)
  above the table; vertical_alignment="center" on all row columns so
  badges and buttons line up with text. NOTE for future edits:
  st.subheader() in this Streamlit (1.59.1) has NO icon= kwarg — icons in
  headings go in the text as ':material/xyz:' markdown instead. Check
  signatures with inspect before using a kwarg; the skill docs can be
  ahead of the installed version.

Real signup/login via Supabase Auth, email + password. Signup is open to
anyone with the link for now (no invite list, no admin approval step) — the
plan is to add restrictions later if it turns out to matter. The old fake
"I am: [name]" dropdown is being replaced by whoever is actually logged in.

Do not store passwords ourselves anywhere — Supabase Auth owns that entirely.
App-specific data about a member (their name, etc.) lives in our own table,
keyed to the Supabase Auth user id — not by re-implementing auth tables.

## Competitions feature (2026-07-13, in progress)

New subsystem replacing the club's Excel sheet for competition logistics —
who's going, what events, links, volunteer selection, reminders. Agreed
data model: a **Competition** (name, venue, date, registration deadline,
free-text student in-charge, many links) contains one or more **Events**
(name, free-text details, team_size, max_teams, min_grade–max_grade
eligibility range). Host-only tools (add competition, finalize volunteers,
announcements) are gated by a hardcoded email set in `HOST_EMAILS`
(`shared.py`) — just `roboknights@dpsrkp.net` so far, teacher's email still
TBD. Everyone can browse every competition/event/link; the volunteer button
itself is grade-gated (later chunk) and doesn't exist yet.

Chunk 1 (foundation) — done:
- Converted the single-file app into a real multi-page app: `app.py` now
  only handles login/signup/reset + computes shared per-user state
  (`st.session_state`), then hands off via `st.navigation`/`st.Page` to
  `app_pages/inventory.py` (the old screen, logic unchanged) and
  `app_pages/competitions.py` (new). `shared.py` holds what both pages
  need (`get_client()`, `send_email()`, `APP_URL`, `HOST_EMAILS`) since a
  page script can't import from the entry-point `app.py` without
  re-running the login screen.
- Added `grade` (7–12, self-reported) to signup.
- New tables `competitions`, `competition_links`, `competition_events` +
  `users.grade` column, in `supabase_schema.sql`.
- Competitions page: host-only "add a competition" form (with add/remove
  buttons for a variable number of links and events, same
  list-in-session-state + rerun pattern as elsewhere), and a read-only
  "browse all competitions" view for everyone.

Same-day follow-up fixes after first live test:
- Account card (name + Log out) moved out of `inventory.py`'s sidebar into
  `app.py` itself, so it renders before the page router runs and shows up
  on every page, not just Inventory.
- Host admin override on the Inventory page: `is_host` can now Mark as
  returned / Delete ANY part (not just their own), see who an on-loan part
  is currently lent to (a host-only "Lent to X" caption — regular owners
  already get this via their own "lent out" dashboard), and Edit any part
  inline (rename, reassign owner via a dropdown, or flip status directly).
  Manually flipping on-loan→available through Edit also closes out the
  matching approved request, so it doesn't linger in lent-out/borrowed
  dashboards — same cleanup the existing "Mark as returned" button does.
- Bug fix: a link typed without `http://`/`https://` (e.g. "discord.com")
  was being treated as relative to the app's own address, sending clicks
  to `localhost:8501/discord.com`. Fixed at both ends — new links get
  `https://` prepended before saving, and existing links get the same fix
  applied at display time, so already-saved bad links work without needing
  a delete/edit feature for competition links (which doesn't exist yet).
- Owners (not just hosts) can now also see who their own on-loan part is
  lent to, right in the main parts list — was previously only visible in
  the separate "Parts I've lent out" dashboard further down the page.

Chunk 3 (students volunteering for eligible events) — done:
- New `event_volunteers` table (event_id, user_id, unique together — no
  "selected" column yet, that's chunk 4's job). Every event now shows its
  volunteer list ("Volunteers (n): ...", or "No volunteers yet") to
  everyone, and a Volunteer/Withdraw button to the viewer specifically,
  gated by grade the same way "Request this" is gated by part availability
  on Inventory — only shown when eligible. No cap on how many can
  volunteer; the cap (team_size × max_teams) applies at selection time in
  chunk 4, not here. No notification email yet either.
- Verified live by the student.

Data cleanup, same day: removed 7 leftover test/duplicate accounts
(TestBot, TestBot2, and a few duplicate/placeholder signups) from the
`users` table — none of them owned parts or had request history, so no FK
issues. Backfilled `grade = 11` for the two real pre-existing members
(Naitik, Aryamman) who signed up before the grade field existed. Note for
future cleanup like this: our `.env` only holds the Supabase **anon** key,
so deleting a `users` row does NOT delete the matching Supabase Auth login
— that's a separate system needing the Authentication tab in the Supabase
dashboard (or a service-role key, which we don't have configured). Student
handled the Auth-side deletion themselves.

Host can edit an existing competition — done, same day: an Edit button
(host-only) on each competition card opens the same kind of form used to
create one, pre-filled. Top-level details, links, and events are all
editable. Links are simply replaced wholesale on save (nothing references
them). Events are matched by their existing `event_id` and updated in
place when kept, so an event's volunteer signups (`event_volunteers`,
which cascades on event delete) survive an edit — only removing an event
outright removes its volunteers too, same as deleting a part removes its
request history.

Fuller signup form — done, same day: added `section`, `admission_no`,
`phone_no` text fields (alongside the existing `grade`). The existing
Email field was relabeled "Institutional email" rather than adding a
second field — it's still the one login email, just clarified that it
should be the school address, not a personal one. Not enforced/validated
as @dpsrkp.net specifically, since the two current real members
(Naitik, Aryamman) are staying on personal Gmail for now. Backfilled
section/admission_no/phone_no for both via direct script, same approach as
the earlier grade backfill.

**Privacy rule (student's explicit instruction):** section, admission no.,
and phone no. are private — no member should be able to see another
member's values for these. Nothing currently displays them to anyone but
the record's own owner (there's no UI surface for them at all yet beyond
the signup form itself), so this is naturally satisfied today, but keep it
in mind for any future screen that lists user details.

Host member directory — done, same day: new `app_pages/members.py` page,
titled "Members" in the sidebar nav, showing every member's name,
institutional email, grade, section, admission no., and phone no. The
intentional exception to the privacy rule above, since only hosts should
see everyone's details at once. (**Since superseded:** Exun and read-only
viewer accounts reach this page too now — see "Access tiers" near the end
of this file. Exun sees the full table read-only; viewers additionally
have section/admission no./phone no. dropped.) Gated two ways: `app.py`
only adds this page to the `st.navigation` pages list at all when
`st.session_state.is_host` is true (so non-hosts never see it in the
sidebar), AND the page itself re-checks `is_host` and `st.stop()`s if
false, in case someone hits its URL directly — `st.navigation`'s page list
alone doesn't stop a direct link, so the page needs its own guard too.
Table is a `st.data_editor` (student asked to edit everything in it, not
just view it) — every field editable inline, Grade constrained to a 7–12
dropdown via `column_config.SelectboxColumn`, `num_rows="fixed"` so hosts
can't add/delete rows here (a member only ever comes from signing up), one
"Save changes" button diffs edited rows against the originals and only
writes what actually changed. Verified live by the student. NOTE:
editing "Institutional email" here only changes our own `users.email`
column — it does NOT change the person's actual Supabase Auth login
email, which is a separate system we don't have admin-key access to (see
the anon-vs-service-role-key note earlier in this file). Flagged as a
caption directly on the page so the host sees it every time, not just here.

Chunk 4 (host finalizing volunteers) — done: new `event_volunteers.selected`
boolean column. On any event with at least one volunteer, the host sees a
`st.multiselect` ("Finalize volunteers") pre-filled with whoever's already
selected, hard-capped at `team_size × max_teams` via `max_selections` —
Streamlit itself refuses to let the host pick past the cap. "Save
selection" diffs the new picks against the previous ones: newly-selected
people get a "You're selected" email, newly-deselected people are just
quietly unmarked (no email either way for someone whose status didn't
change, so re-saving without edits doesn't spam anyone). Everyone (not
just the host) sees a "Selected (n/cap): ..." line above the volunteer
list once anyone's been picked, alongside the existing full volunteer
list. Verified live by the student.

Chunk 5 (day-before reminder + bot status) — done: `event_volunteers` got
two new columns, `bot_status` (free text) and `reminder_sent` (stops the
daily job emailing the same person twice, same pattern as
`requests.reminder_2day_sent`/`reminder_1day_sent`). `send_due_reminders.py`
— the same GitHub Actions job that already sends loan reminders — now also
checks for competitions happening tomorrow and emails every selected
volunteer for that competition's events, telling them to update their bot
status. No workflow YAML changes needed, since it already runs this script
daily with the right secrets. On the Competitions page, once a competition
is 1 day away or sooner, each event shows a "Bot status" block: everyone
can see every selected person's status (or "Not updated yet"), and the
selected person themselves gets an editable field for their own. Verified:
dry-ran the extended script locally against real data (no competitions
were due "tomorrow" at the time, so it correctly sent nothing) and the
bot-status UI live by the student.

Chunk 6 (announcements) — done, then reshaped same day: originally built
as a section on the Competitions page, but moved to its own page,
`app_pages/announcements.py` (added to `st.navigation` unconditionally,
unlike the host-only Members page — everyone should see this one), since
the student wanted it to read like a real separate area rather than
buried inside Competitions. New `announcements` table — sending is
host-only (subject + body, "Send to everyone" emails every member AND
saves a row), but every member can scroll the last 20, newest first.
Host also got a Delete button per announcement (plain
`.delete().eq("announcement_id", ...)`, no confirmation step needed since
it's not tied to any other data — nothing references an announcement row).
Verified live by the student, including delete.

## Post-Competitions feature ideas (2026-07-13)

Context: RoboKnights inductions are launching in ~2 weeks, so these were
scoped and prioritized with that deadline in mind. Agreed build order:
(1) private student queries, (2) meeting scheduler, (3) WhatsApp "help me"
link, (4) induction task tracker. Two ideas were discussed and explicitly
NOT built:
- **WhatsApp automated notifications** — originally scoped out on
  2026-07-13 for needing "DLT registration," but that conflated WhatsApp
  with the SMS-specific TRAI/DLT rule; WhatsApp doesn't need DLT at all.
  Re-researched 2026-07-31, and the real picture is much more workable:
  Meta's WhatsApp Cloud API is free to use directly at an unverified tier
  (up to 1,000 conversations/day — far more than this club needs), with
  NO GST/company registration required at that tier. Formal Business
  Verification (which accepts GST OR alternatives like a trust/Udyam
  certificate — a school-affiliated trust's own docs could work) is only
  needed to raise the daily cap higher, not to send messages at all. The
  real remaining requirements: (1) a phone number that's never been used
  for a personal/regular WhatsApp account — needs its own dedicated
  number; (2) every outbound template (e.g. "your part is due tomorrow")
  needs a one-time Meta approval before it can be sent automatically; (3)
  a genuinely proactive message (nobody messaged the club first) is
  billed per conversation outside an open 24h window, though rates are
  low (~₹0.10–₹1). Student wants this built AFTER Mr. Ajith Kumar's
  teacher/host account is set up — not started yet. Hard budget
  constraint stated 2026-07-31: zero cost, no exceptions.

  On the dedicated-number requirement: it does NOT need to be bought —
  any existing number works as long as it can receive one OTP and isn't
  currently logged into WhatsApp on a phone (an old/spare SIM, exactly
  the kind of number that's free). The real tradeoff: whoever's number
  this is permanently loses normal WhatsApp app access on it (the number
  becomes API-only), so it needs to be a number nobody minds giving up
  personal WhatsApp on. As of 2026-07-31 no spare number is available yet.

  Agreed path: start with Meta's free sandbox/test number (built into the
  WhatsApp Cloud API setup, genuinely free, no SIM at all) to build and
  verify the whole pipeline — template approval, the API call itself,
  wiring into the existing reminder scripts — against a handful of
  manually-added test recipients. Switch to a real dedicated number (once
  one becomes available) only when going live to the full member list;
  that swap is just a number-registration step, not a rebuild.

  Code side built 2026-07-31 (student said "do it"): `send_whatsapp()` in
  shared.py (Cloud API POST to `/{phone_number_id}/messages`, template-
  based since Meta requires an approved template for any business-
  initiated message) plus a duplicate copy in send_due_reminders.py,
  matching how it already duplicates send_email instead of importing
  shared.py (keeps the cron script Streamlit-free). Both silently no-op
  if `WHATSAPP_PHONE_NUMBER_ID`/`WHATSAPP_ACCESS_TOKEN` aren't set yet —
  safe to leave wired in while the Meta side doesn't exist. Wired into
  exactly ONE flow as the first proof of concept: the existing 2-day/
  1-day due-date reminder in send_due_reminders.py, right after its
  send_email call. Phone numbers normalize a plain 10-digit signup number
  to the "91XXXXXXXXXX" format the API needs (`_normalize_india_phone`).
  `WHATSAPP_PHONE_NUMBER_ID`/`WHATSAPP_ACCESS_TOKEN` added to DEPLOY.md's
  secrets template, marked optional.

  What still needs the student's own hands (I can't do these — they
  require logging into a real Meta/Facebook account, which is account
  creation + third-party ToS acceptance, both outside what I can do on
  someone's behalf):
  1. Go to developers.facebook.com, create a free Meta developer account
     + a new App (type: Business).
  2. Add the "WhatsApp" product to that app — this auto-creates a free
     test WABA + test phone number, no SIM needed.
  3. In the API Setup panel, add your own phone number as a verified test
     recipient (up to 5, enter the code WhatsApp sends you).
  4. Copy the test **Phone number ID** and a **temporary access token**
     (24h; a permanent one needs a System User, a later step) from that
     same panel into `.env` as `WHATSAPP_PHONE_NUMBER_ID` /
     `WHATSAPP_ACCESS_TOKEN`, and as GitHub Actions repo secrets (same as
     the SMTP/Supabase ones) once ready to test the real cron job.
  5. In the app's WhatsApp > Message Templates screen, create a template
     named exactly `part_due_reminder`, category **Utility**, language
     **English (US)**, body text exactly:
     `Reminder: {{1}} is due back in {{2}} day(s), on {{3}}.`
     Submit it — approval is usually within a day for a plain utility
     template like this.
  6. Once approved, run `python send_due_reminders.py` locally with a due
     request on a test account to confirm a real WhatsApp message arrives.

  Not done yet, deliberately deferred: wiring WhatsApp into any other
  notification (new query, competition selection, achievement reminders,
  announcements) — this first chunk was scoped to proving the pipeline
  works end to end on one flow before expanding to the rest.
- **Razorpay for merch payments** — deferred. Real payment gateways need
  KYC tied to an adult-owned bank account; the student is a minor and
  can't open that account himself. Recommended a free, no-registration
  middle ground instead when merch launches: a static UPI QR/link plus a
  "upload your payment screenshot" flow the host manually approves (same
  pending→approved pattern used everywhere else in this app) — not built
  yet, needs a parent/teacher involved before real money changes hands.

Chunk (1) — private student queries — done, then reshaped same day: first
built as one question + one host answer, then redesigned into a real
back-and-forth thread per the student's request (both sides can keep
replying, both sides can edit only their own past messages). New
`app_pages/queries.py`, added to `st.navigation` unconditionally (both
students and the host need it, unlike the host-only Members page).
Data model: `queries` is just the thread container (`student_id`,
`created_at`); every message — including the original question — lives in
a new `query_messages` table (`query_id`, `sender_id`, `body`,
`created_at`, `edited_at`). The `queries` table's original
`question`/`answer`/`status`/`answered_at` columns from the first version
are now unused (its `question` column had its `not null` constraint
dropped via `alter table ... drop not null` so new rows can omit it) —
left in place rather than dropped, matching how this project never
removes columns. Privacy: students only ever see their own threads
(filtered by `student_id`), host sees every thread grouped by student
name — same private-to-owner rule as grade/section/phone. Notifications:
starting a new thread emails every `HOST_EMAILS` address; a host's reply
emails that student; further back-and-forth after that doesn't email
either side each time, since the Queries page itself is the ongoing
conversation view (mirrors how Requests-for-my-parts doesn't email on
every state either). Every message shows an IST timestamp
(`shared.format_ist` — Supabase stores UTC, converted with a fixed
+5:30 offset since IST has no DST). Verified live by the student,
including edits on both sides.

Announcements also got real timestamps the same day: `format_ist` was
first written directly in `app_pages/announcements.py`, then pulled up
into `shared.py` once Queries needed the same conversion, so both pages
share one implementation instead of two copies.

Animation pass (2026-07-13, student asked directly for "animations and
everything"): Streamlit has no animation API, so this added the SECOND
deliberate exception to the "no custom CSS" rule (first was the login
wordmark card) — one `st.html` style block in `app.py`, display-only,
applied app-wide: page content fades up on load/page-switch, sidebar
slides in, primary/secondary buttons lift on hover and press on click,
the header gear logo rotates on hover, and list cards lift with a shadow
on hover. Card hovers target `div[class*="st-key-rkcard_"]` — stable
classes Streamlit generates from `st.container(key="rkcard_...")`, which
was added to the parts/requests/lent/borrowed rows, competition cards,
event cards, and announcement cards. Do NOT target the auto-generated
`st-emotion-cache-*` classes; they change between Streamlit versions
(checked the live DOM first: this version has no
stVerticalBlockBorderWrapper testid, so keyed classes were the only
stable hook). Same pass converted all transient success messages from
inline st.success/st.info to `st.toast(...)` (animated, auto-dismissing,
bottom-right); form ERRORS deliberately stay inline where the user is
looking, so they can't be missed. The session_state
set-message→rerun→display-once pattern is unchanged — only the display
call swapped.

Second animation round (2026-07-14, student picked from a menu): gold
accent `#E8B33D` replaced the grey primaryColor in config.toml (student
chose "knight gold" from options; primary buttons get dark text via CSS
since white-on-gold was unreadable and Streamlit has no button-text theme
option). Gear splash overlay plays once per login (spin-up + fade) and in
reverse on logout — factored into `render_gear_splash(direction)` in
app.py. TWO HARD-WON GOTCHAS, do not re-trip: (1) `st.html` silently
STRIPS inline `<svg>` — splash must use `st.markdown(...,
unsafe_allow_html=True)`; (2) Markdown turns indented lines into literal
code blocks, so that HTML must be flush-left in the string. Also added:
staggered card entrance (nth-child delays on rkcard classes; fill mode is
`backwards` NOT `forwards`, because forwards would permanently override
the hover transform), gold hover glow on cards, a pulsing gold border on
the "Requests for me" metric while non-zero (keyed marker container
`rkpulse_requests` in inventory.py + `:has()` rule in app.py),
`st.balloons()` after signup, and a faint (5% opacity, blurred) gear
watermark with the SVG inlined as base64 (Streamlit doesn't serve
static/ over HTTP). Reworked 2026-08-05 at the student's request: the
logo's two gears are now split into `static/gear_big.svg` /
`gear_small.svg` (same 128x99 canvas each, so stacked they keep their
drawn meshed positions), centred mid-screen, each rotating about its own
gear's centre in OPPOSITE directions with the small one 1.836x faster —
the big:small radius ratio measured from the artwork, i.e. real meshed-
gear physics. Implementation gotcha, confirmed live in the DOM: a div
injected via st.html lands inside `stMainBlockContainer`, whose
transform (the fade-up animation) hijacks position:fixed — so the two
gears live on `stApp::before`/`::after` pseudo-elements instead, centred
via calc() so the keyframes stay pure rotation.

## Email deliverability incident + school-domain lock (2026-07-14)

A school-address signup wasn't getting its confirmation email while a
personal Gmail test did. Root cause: `SMTP_SENDER` was `roboknights@dpsrkp.net`,
and sending "from" a school domain (relayed through Brevo, a third party
not authorized in that domain's SPF/DKIM) to **another address on that
same domain** is exactly the pattern Google Workspace/Microsoft 365 school
tenants treat as spoofing and silently quarantine — Brevo's own "delivered"
status only means the school's mail server accepted the handoff, not that
it reached an inbox. Fixed by verifying a personal (non-school) sender
address in Brevo's Single Sender Verification and switching `SMTP_SENDER`
to it in all the places that value lives: local `.env`, Streamlit Cloud
secrets, GitHub Actions secrets, AND Supabase's own separate Authentication
→ SMTP Settings sender field (four places, not one — Supabase's auth
emails don't read our app's secrets at all). Also separately: the
confirmation email itself was linking to `localhost:8501` because
Supabase's Site URL was never updated after deploying — fixed in
Supabase → Authentication → URL Configuration.

Same-day, the student then asked to lock signup/login/forgot-password to
`@dpsrkp.net` only, to stop unauthorized signups. Implemented as an
auto-append UI (`school_email_input()` in app.py): every email box takes
just the username, with a fixed `@dpsrkp.net` label shown alongside it —
not a validate-and-reject error, the domain literally can't be typed
differently. **Known consequence, confirmed explicitly by the student
before shipping:** Naitik and Aryamman were on personal Gmail accounts and
were locked out of login entirely, since there's no "change my email"
feature and no admin/service-role Supabase access to fix it for them
directly (same anon-key limitation noted earlier in this file). **Resolved
2026-07-24:** the student handled this manually outside the app (not a
code change) — no longer an open item.

Also same day: the Admission no. signup field changed from free text to
a dropdown (R/E/V) + digits box (`admission_no_input()`), matching the
real format seen in the school's admission numbers (e.g. `R22639`).

## Meetings, Achievements (2026-07-14)

New `app_pages/meetings.py`, added to nav unconditionally. Host schedules
a meeting (title, agenda, date, time, external join link — no real video
calling built in, just links out to Google Meet/Jitsi/whatever — plus
optional Meeting ID and password, both truly optional since plenty of
links don't need them). Everyone can RSVP ("I'm going" / "Can't make it",
same existence-based pattern as `event_volunteers`). Once a meeting's date
arrives, a self-check-in "I attended" button unlocks; host additionally
gets an Attendance multiselect (pre-filled with self-check-ins, host can
add/remove anyone) to correct the final record — same
self-report-then-host-override shape as the Members directory edit. Host
can also Edit (full inline form, same fields as scheduling) or Delete a
meeting (RSVPs/attendance cascade-delete with it).

New `app_pages/achievements.py`, added to nav unconditionally. Any member
can log a result for a competition event — pick a competition, then an
event under it (two-level select, same hierarchy as browsing
Competitions), position (free text), and an optional attachment/link
(NOT a real file upload — this app has no file storage configured, so
it's a pasted link like every other link field in this app: photo, video,
Drive folder, whatever). Self-reported; the poster or a host can delete
an entry.

New `send_achievement_reminders.py` + a second cron entry (10:30 UTC /
4:00 PM IST) added to the existing `due-reminders.yml` workflow. On the
competition's own day (not the day before — that's the existing bot-status
reminder), it emails every volunteer who was actually SELECTED for an
event happening that day, asking them to log their result — unless they've
already logged one for that exact event, in which case it's silently
skipped. Job steps use `if: github.event.schedule != '...'` so the two
scripts don't both fire on both schedules (manual workflow_dispatch runs
both, for easy testing — `github.event.schedule` is empty then, so
neither `!=` condition excludes it).

### E2C sheet import — BUILT (2026-08-07), `e2c_import.py`

Importing competitions from the club's real Google Sheet (the student
calls it "E2C"). This was scoped on 2026-07-14 as "discussed, not built,
blocked on seeing the real sheet structure" — that block cleared and it
shipped on 2026-08-07. Kept as its own module with **no Streamlit import
at all**, so the messy parsing stays separate from the Competitions page
that displays it.

The sheet's real shape, confirmed by reading the live sheet rather than
guessing (which is exactly what the earlier note said to wait for): one
tab per year ("Events and Reg 2026-27"), every competition stacked
vertically in the SAME tab. Column A holds a competition's details spread
over several rows with no fixed row count (name, venue, date, links,
deadline, in-charge); column B is the event name; column C is
`"<max teams>x<team size>, <grade range>"` — so the earlier open question
about whether `1x6` meant team_size×max_teams or the reverse is answered:
**max_teams first**. Columns D onward are registered participants, one row
per registered TEAM (verified live: a "3x2" event really does have 3 rows
of 2 names).

The only reliable "is this event robotics?" signal is the real Google
Sheets **note** on the event-name cell — the hover-to-reveal corner
triangle, which doesn't survive a CSV/Excel export, and is why this needs
the Sheets API directly rather than a file upload. Its first line is a
category ("Robotics", "Gaming", "Quiz", …). **"Has a note" is NOT the
signal** — plenty of non-robotics events have notes too; the category text
is, matched as a substring since some are compound ("Robotics and STEM").
A few genuine robotics events don't say "robotics" in their note at all
(one just repeats its own event name) — **a known, accepted gap the
student chose to live with** rather than guess around. Don't "fix" it by
loosening the match without asking.

Participant auto-add only matches names against real rows in our own
`users` table (ignoring tags like `[Ad-Hoc]`); green cell shading means
the sheet shows them as confirmed. `_insert_matched_participants` is what
`notify_if_roster_complete()` hooks into (see the Discord section below).

## AI Assistant (2026-08-08/09)

New `app_pages/assistant.py`, added to nav unconditionally. A chat page
that answers using ONLY the logged-in member's own data (parts they own,
their borrow requests, competition volunteering, achievements, upcoming
meeting RSVPs) — never another member's, matching the existing privacy
rule elsewhere in this file. Also answers general robotics/build
questions on the model's own knowledge. Context is rebuilt fresh from
`cached_table` on every message; conversation history is per-browser-session
in `st.session_state` (refreshing clears the visible chat), but every turn
is ALSO logged to Supabase now — see "Discord AI bot" below.

Provider: Groq's free tier (`llama-3.3-70b-versatile`), not Gemini or
OpenAI. Gemini was tried first (fits the zero-cost rule on paper) but a
brand-new, unbilled Google Cloud project still returned a hard 0 free-tier
quota on the very first request — confirmed live that Gemini's free API
tier simply isn't offered to India-based accounts, not a config mistake.
OpenAI has no comparable free API tier at all (free tier is web-only).
Groq has neither restriction and is faster besides. Needs `GROQ_API_KEY`
(free, no card, from console.groq.com/keys) — the page shows a host-only
setup message instead of crashing when it's unset.

**Web search (2026-08-11):** student's point — a plain model doesn't
actually know today's specific motors/sensors/parts without looking them
up. A toggle (default on) switches the model from `llama-3.3-70b-versatile`
to `groq/compound`, Groq's own system that decides FOR ITSELF whether a
given question needs a web search and runs the whole search-and-read loop
server-side — no separate search API, scraping, or manual RAG step on our
end. `compound_custom.tools.enabled_tools` is scoped to just
`web_search`/`visit_website` (leaves out `code_interpreter`/
`wolfram_alpha`, not relevant here). When it does search, the actual
sources it read (`response.choices[0].message.executed_tools[].
search_results.results[]`, each with a title/url) are shown in a small
expander under that reply — verified directly against the installed
`groq` SDK's own type definitions, not just the docs, since exactly
what the response shape looks like isn't obvious from the docs alone.

## Discord integration (2026-08-09, in progress)

Chunk 1 — new competition event notifications — done: student wants
notifications posted to the club's Discord server for "everything," but
scoped this to competitions first (more categories are a later chunk, not
built yet). A real Discord BOT (persistent gateway connection, can
respond to commands) needs its own always-on process this project has no
hosting for — Streamlit Cloud only runs the dashboard itself. Built as a
Discord **Incoming Webhook** instead (plain HTTP POST, same shape as
`send_email`/`send_whatsapp`), which needs no separate hosting at all.
`send_discord_message()` in shared.py reads `DISCORD_COMPETITIONS_WEBHOOK_URL`
and silently no-ops if unset, same best-effort spirit as the other
notification senders.

Student initially chose individual member tags over a role-ping, which
needed a new `users.discord_user_id` column (nullable, self-linked on the
Home page, also editable by a host on the Members page) — **later
reversed**: student decided against per-member tagging and switched to
pinging two Discord SERVER ROLES instead (`@member` / `@adhoc`), via
`discord_role_tags()` in shared.py, which builds `<@&ROLE_ID>` mentions
from `DISCORD_MEMBER_ROLE_ID` / `DISCORD_ADHOC_ROLE_ID` (see DEPLOY.md for
how to get a role's ID, and the "Allow anyone to @mention this role"
toggle a role needs turned on or a webhook's ping is silently swallowed).
The `discord_user_id` column and its Home/Members UI were kept, but only
for optional manual @mentions in the free-form custom-message tool — no
automatic notification reads it anymore.

Every Discord message this app sends — automatic or the free-form custom
one — also always gets a `***This is automated message***` footer
appended centrally inside `send_discord_message()`, so no call site can
forget it.

Wired into `_notify_new_event()` in competitions.py (new competition
event added). One thing needed from the student for either channel:
create the Incoming Webhook in Discord itself (target channel → Settings
→ Integrations → Webhooks → New Webhook) and add the URL as the right
secret in `.env` (and Streamlit Cloud secrets once deployed — see
DEPLOY.md) — not something doable without their own Discord server access.

Chunk 2 — a second channel + a roster-complete notification + a
dedicated page — done: student got the Exun<>RK channel's own webhook and
asked for three things. `send_discord_message()` / `delete_discord_message()`
/ `edit_discord_message()` in shared.py all took a `channel` parameter
("competitions" or "exun_rk", via the new `DISCORD_CHANNELS` dict mapping
each to its own env secret name) — `discord_messages` got a matching
`channel` column (defaults to 'competitions' for pre-migration rows) so a
later edit/delete knows which webhook to hit.

1. **"All event names complete" notification**, to the exun_rk channel:
   `notify_if_roster_complete()` in shared.py fires once a competition's
   EVERY event has as many SELECTED people as its capacity — i.e. the
   real team rosters are finalized, not just "enough volunteers signed
   up". Tracked via a new `competitions.roster_complete_notified` column,
   reset back to False the moment it's no longer complete (someone
   unselected) so a later re-completion notifies again rather than
   staying stuck silent. Wired into both places `selected` can change:
   the manual "Save selection" finalize button, and `_insert_matched_participants`
   (E2C sync) — now takes a `competition_id` parameter for this. Wrapped
   in its own try/except (best-effort, matches every other Discord
   sender) specifically so a stale/pre-migration schema can't turn an
   otherwise-successful "Save selection" into a scary `safe_write` error.

2. **Custom message tool**, now available for EITHER channel via the new
   page.

3. **A dedicated "Discord Messages" page** (`app_pages/discord_messages.py`,
   host-only, added to nav in app.py): the custom-message tool, its
   "Recent messages" edit/delete list, and the vacant-events reminder
   ALL moved here from the Competitions page's "Add & import" tab (which
   now just has a one-line pointer to this page) — one place for every
   Discord messaging tool instead of it being scattered inside a
   competitions-specific tab. `build_vacant_events_message()` also moved
   into shared.py (from a competitions.py-local `_build_vacant_events_message`)
   specifically so this new page could call it without executing the
   whole Competitions page script — app_pages/*.py files run top-to-bottom
   as scripts on import, not as plain importable modules.

Chunk 3 — a dashboard-changelog channel — done: a THIRD channel
(`DISCORD_DASHBOARD_WEBHOOK_URL`) that announces the app itself being
updated. Unlike everything else here, this is NOT posted by the app —
`.github/workflows/dashboard-update.yml` runs `send_dashboard_update.py`
on every push to master, which is also when Streamlit Cloud redeploys, so
the notice lands when the update actually goes live. That script
deliberately doesn't import shared.py (same reasoning as
send_due_reminders.py — the Actions runner shouldn't need streamlit), so
its footer wording is duplicated there and must be kept in sync with
`discord_message_suffix()`.

The 3-line summary comes from Groq (the same free model the AI Assistant
uses) fed the push's commit messages, with a hard fallback to raw commit
subject lines if the key is missing or the call fails — a summary failure
must never block the notification. A push of nothing but merge commits
posts nothing. (This channel briefly had its own footer wording — "an
automated channel" instead of "automated message" — reverted same-day at
the student's request, so all three channels now say the same thing.)

Deferred, explicitly next per the student ("also things related to
competitions"): more competition-lifecycle notifications beyond "new
event added" — e.g. someone selected for an event, a registration
deadline approaching. Same webhooks, same role-tagging mechanism, just
more call sites once asked for.

## Feedback / "report an issue" (2026-08-11)

Student's reasoning: the most useful bug reports come from everyday use,
right when something looks or feels off — not whenever someone next
remembers to mention it to a host in person. A steady trickle of small
fixes over a long period, not a one-time QA pass, is what actually makes
the dashboard reliable.

First design was a dedicated "Feedback" page with a category dropdown
(Bug / Suggestion / Something else). Student pushed back on both parts:
a page you have to navigate to adds friction right when the point is to
lower it, and asking someone to categorize what they just hit doesn't
work — "we can't judge if a particular problem is an error or not, as we
don't know what we don't know." Rebuilt as:

- A floating "Report an issue" pill, bottom-right, on EVERY page (added
  in app.py, not page-specific). Opens a dialog with a single free-text
  box, no category field.
- Getting the pill to actually render live took three tries.
  `st.container(key=...)` + CSS targeting that key's class (first an
  exact `.st-key-...` selector, then the `div[class*="st-key-..."]`
  substring form the card hover effects already use) both looked correct
  under automated DOM inspection but never showed up for the student in
  a real logged-in session, for a reason never pinned down. Final version
  sidesteps Streamlit's container tree entirely: the real `st.button`
  still renders (for real interactivity) but is hidden via CSS, and
  `components.html` runs a script that reaches into
  `window.parent.document` — the same technique `_set_remember_cookie`
  above already uses for the "remember me" cookie — to append a
  hand-styled pill directly onto the actual page's `<body>`, sibling to
  Streamlit's own root rather than nested inside anything it re-renders.
  Clicking the pill finds the real (hidden) button and calls `.click()`
  on it, so Streamlit's own listener fires exactly as if a person had
  clicked it.
- `app_pages/feedback.py` — the other half: everyone can see the list
  (not private like Queries, so nobody re-reports something already in),
  filterable by status (segmented_control, defaults to "Open"). Only a
  host can change status, leave a note, or delete a report.
- New `feedback` table: feedback_id, user_id, body, status (open /
  in_progress / fixed / wont_fix), host_notes, created_at, resolved_at.
- New reports email every HOST_EMAILS address, same pattern as Queries.
  Marking a report "fixed" (specifically the open/in_progress → fixed
  transition, not every save) emails the original reporter back — closes
  the loop so people keep reporting instead of assuming nothing happens
  to what they send in.

## Discord AI bot (2026-08-12)

`discord_bot/bot.py` — a separate, always-on process, NOT part of the
Streamlit app and NOT something GitHub Actions can run. A bot needs a
persistent gateway connection held open 24/7; Streamlit Cloud only runs
while serving the app and Actions jobs time out, so this deploys
separately (Railway — see DEPLOY.md), with its own `requirements.txt` and
`Procfile` in that subfolder.

Origin: the club wanted "an AI agent on our official Discord account —
tag it or DM it, it replies." First ask was literally to automate the
club's existing Discord account (its real login). Declined that outright —
automating a normal account's send/receive behavior is a "self-bot" under
Discord's Terms of Service regardless of whose account it is, and Discord
bans accounts caught doing it. Built instead as a real Discord Bot
application (own token, own identity in the server — currently
`roboknightsbot`), which gets the same practical result (tag it, DM it, it
replies) without that risk. Also declined, separately, a request to make
an outreach message to two members "as rude as possible" with fabricated
threats (a fake "AI auto-removes your access" claim, invented "token
waste" reasoning) — wrote a firm, honest version instead; a dashboard/AI
feature shouldn't be used to manufacture false threats against real
students.

Uses Groq (`llama-3.3-70b-versatile`, same free tier as the dashboard's
other Groq calls) — student first said "Grok" (xAI), but that has no
lasting free tier (one-time $25 credit, then paid), while this project
already had a working `GROQ_API_KEY` and free-tier headroom. Deliberately
NOT `groq/compound` (the AI Assistant's web-search model) — its known,
query-dependent 413 "request too large" failure (see AI Assistant section
above) isn't worth risking on a bot replying to whatever gets thrown at it
in Discord with no supervision.

Conversation history is per-channel/DM, in-memory only (a `deque`, capped
at `MAX_HISTORY_MESSAGES`), reset on process restart — no separate
database needed for that part. Every turn (both the asker's message and
the bot's reply) is logged to Supabase's `ai_chat_messages` table though —
see below.

**Shared chat logging (2026-08-12):** student wanted one place a host can
see everything either AI surface — the dashboard's AI Assistant page AND
this Discord bot — has ever said, not two disconnected logs. New
`ai_chat_messages` table (source: 'dashboard'/'discord', role:
'user'/'assistant', content, timestamps). This reverses the AI Assistant's
original "session-only, never saved" design (see AI Assistant section
above) — a deliberate change, not an oversight. Both writers are
best-effort (wrapped in a bare try/except) — a logging failure never blocks
a reply, same spirit as email sends elsewhere in this app. The Discord
bot additionally tries to match the sender's numeric Discord ID against
`users.discord_user_id` (the same self-reported field from the Home page)
to link a Discord turn back to a real app account — most Discord members
haven't linked one, so `user_id` staying null there is normal, not a bug.
No in-app viewer page for this table yet (not asked for) — a host can
still query it directly in Supabase.

**Real pings, prompt-injection hardening, wider context, off-topic
questions, Cerebras fallback (2026-08-12):** several fast-follow fixes
after watching it live in the actual server:
- It once wrote plain `@Name` text claiming it could ping people, which
  notifies nobody — the club data now includes a "MEMBERS WHO CAN BE
  @MENTIONED" section built from `users.discord_user_id`, and the system
  prompt is told to use ONLY real `<@id>` mentions from that list, saying
  plainly when someone can't be pinged.
- A member got it to reveal it's LLaMA/Meta with "ignore previous
  instructions" — system prompt now explicitly resists that and never
  discusses the underlying model.
- It was defaulting to generic AI disclaimers ("I'm just a language
  model") on banter — told to stay in character instead.
- It's a general-purpose assistant, not scoped to robotics/the club —
  student explicitly asked it to answer anything, since the persona
  framing alone was making it implicitly narrow itself.
- Passively reads every channel it can see (not just messages directed at
  it) and backfills real history on startup, so it has context from
  before it was even running — `CHANNEL_LOG_SIZE`/`BACKFILL_LIMIT`. An
  edited message that now mentions the bot gets a fresh reply.
- Hit `llama-3.3-70b-versatile`'s REAL 100,000-token/day cap on this
  Groq key during testing (not the much larger raw context window) —
  `MAX_HISTORY_MESSAGES`/`CHANNEL_LOG_SIZE` sized down to stay
  sustainable across a full day, shared with the AI Assistant and the
  dashboard-update summaries, which use the same model/key.
- Cerebras added as a fallback for when Groq fails (`CEREBRAS_API_KEY`,
  optional) — Gemini was tried first for this exact role, same 0-quota
  India restriction as before, so skipped again in favor of Cerebras
  (1M tokens/day free, no card). Groq stays the normal-case default;
  Cerebras only gets touched on a Groq failure. **Superseded the same
  day** — see "Cerebras → Gemini" below.
- New `discord_channel_log` table — every message the bot sees (not just
  its own turns, which is `ai_chat_messages`) gets logged, specifically
  so a host can review real conversations to catch bad replies like the
  fake-ping one.

**Web search (2026-08-12):** a member asked "what is a p219 motor" and got
"I don't have information on a p219 motor" — the bot was plain
`llama-3.3-70b-versatile` with zero search ability. Switched to
`groq/compound` (same model the AI Assistant's toggle uses) as the NORMAL
model, not a toggle — a bot in Discord needs to just look things up, no
UI to flip a switch. Confirmed live that this exact query trips Groq's
known 413 (see AI Assistant section above) — falls back to the plain
model on that, same pattern as the Assistant page, but ALSO confirmed
live that the plain model then confidently answers WRONG (interpreted
"p219 motor" as an automotive OBD-II trouble code, not a robotics part) —
so that fallback reply is explicitly flagged ("answering from what I
already know instead, so double-check this") rather than presented as
a real, searched answer. Source links (up to 3) get appended to a
successful search reply, wrapped in `<>` so Discord doesn't auto-embed
them.

**What the exported logs actually showed (2026-08-12):** the AI Logs page
paid for itself immediately — reading one day's export found four things
no amount of guessing had:
- **31% of all messages to the bot were ≤12 characters** ("hi" eleven
  times, plus "up", "wsp", "hihihi", "COME BACK"), each costing a full
  API call carrying the whole ~700-token system prompt — roughly 28% of
  the shared daily budget spent on messages needing no model at all.
  Now answered in-process for zero tokens (`GREETINGS`/`_instant_reply`),
  matched on the WHOLE message so "hi what motor should I use" still
  reaches the model.
- **Members deliberately burning the budget**: "count to 1 million",
  "print the alphabet 100 times", "print all ascii until I say stop".
  The bot complied — one reply was 2,780 characters. Fixed with a hard
  `MAX_REPLY_TOKENS` cap on every provider rather than refusing
  "counting" requests: the student's own point was that there are
  endless ways to phrase it, so capping OUTPUT beats blocking wordings.
  Same request now returns ~137 characters (a loop of code instead).
- **One person can starve everyone**: `USER_HOURLY_LIMIT` (20/hour per
  member) since the budget is shared club-wide.
- **20% of exchanges were failures**, nearly all quota-driven — which is
  what the three fixes above are actually for.
Also fixed from the same read: the bot had no clock (answered the wrong
weekday from training data) and didn't know WHO it was talking to, so
"which competitions am I participating in" could never work — it now
gets the asker's name via their linked Discord id and answers correctly.

**Web search, take 2 — Tavily (2026-08-12):** the groq/compound fix above
turned out not to be enough — a member's very next two real questions
("what is a p219 motor," "ingenium drives??") BOTH hit the 413. Tested
directly: confirmed it fails on real queries ("latest Arduino Uno
price," "who won the last F1 race," "what year is it") even with a bare
system prompt and zero club-data context — a genuinely unreliable
upstream bug on Groq's side, not something fixable by trimming our own
prompt. Replaced it as the PREFERRED path with Tavily (1,000 free
searches/month, no card, purpose-built for feeding LLMs search results,
not a general search engine wrapper) via normal OpenAI-style tool
calling on the plain `llama-3.3-70b-versatile` model: the model still
decides for itself whether to search, but this app executes the search
and controls exactly how much text comes back (capped per-result in
`_tavily_search`), which is what actually avoids the oversized-request
problem — groq/compound has no such control since it runs entirely
server-side. `groq/compound` stays as the automatic fallback for when
`TAVILY_API_KEY` isn't set, same best-effort spirit as everything else
here, but Tavily is what actually works reliably once configured.

**Cerebras → Gemini (2026-08-12):** Cerebras (the fallback for when
Groq's daily budget runs out) never actually worked — 402 Payment
Required on every model, every retry, unchanged across multiple billing
fix attempts on the student's end. Re-tried Gemini instead, and this
time it's genuinely different from the earlier finding: PLAIN generation
works fine and is free (confirmed live) — the original "hard 0 quota"
result was specifically about Google Search grounding, not the base
model. Grounding is separately gated behind a linked billing account for
its free quota (per Google's own March 2026 change), which this project
doesn't have and isn't adding — so Gemini here is chat-only, no search
(Tavily already covers that). Swapped in as the new last-resort fallback,
replacing Cerebras entirely (not chained — one clean swap). Also worth
recording: a paid Gemini Advanced/Google One AI Premium subscription
does NOT raise this API key's quota — confirmed live, a genuinely
separate consumer product from the developer API, a common mix-up the
student ran into.

## AI Logs, Google sign-in, member standing (2026-08-12/13)

**AI Logs page** (`app_pages/ai_logs.py`, host-only): a real viewer for the
`ai_chat_messages` table, which until then could only be read by querying
Supabase directly. Both AI surfaces in one timeline, with CSV and plain
transcript export. This is the page that paid for itself immediately — see
"What the exported logs actually showed" above, and note that every
significant AI fix since has started from reading a real export rather
than from guessing at what members might do.

**Login with Google**, restricted to `@dpsrkp.net` accounts — the same
school-domain lock the email/password flow already had, just enforced at
the OAuth layer too. Built by hand rather than through
`client.auth.sign_in_with_oauth()`, and it took seven follow-up commits to
actually work, all from one root cause worth remembering: **Streamlit
Cloud serves the app inside an iframe.** That breaks OAuth redirects in
ways that don't reproduce locally at all. The sequence was: sign-in
completed but nobody was actually signed in → the button opened a new tab
and left the original stuck → `target=_top` (the app is iframed, so a
normal redirect navigates the iframe, not the page) → the fix broke it
worse → finally accepting the new tab, because Streamlit Cloud's iframe
sandbox forbids every alternative. Then a separate bug on top: the
sign-in cookie was being lost. If OAuth is ever touched again, test on
the deployed app, not locally — local behaviour is not evidence here.

**Member standing** on the Members page: `users.role`, one of
`core_member` / `member` / `adhoc`, shown and filterable, with `""` as a
deliberate fourth option (most members had no standing until a host set
it by hand, so "not set yet" is a real state, not a prompt to force a
choice). `STANDING_LABELS`/`STANDING_VALUES` in `members.py` map the raw
DB value to what's displayed.

**Meeting invites** now email a Google Calendar "add to calendar" link,
and the Discord bot got competition links in its club data.

## RoboKnights Clio roster sync + verify-your-details (2026-08-14)

The school's own admission roster ("RoboKnights Clio", one tab per school
year plus Alumni) is now written to by the app. Unlike the E2C sheet
(read-only, a plain API key suffices), this needs WRITE access, which an
API key can never do — so it uses a **Google service account**
(`GOOGLE_SERVICE_ACCOUNT_JSON_B64`, base64-encoded JSON) via gspread. The
sheet must be shared with the service account's own email address.

A mandatory **verify-your-details popup** (`app.py`, gated on
`users.details_verified`) blocks the app until a member confirms their own
row — name, class, admission no., contact, personal email. Confirming
writes them into Clio.

Two deliberate design calls, both the student's:
- `CLIO_CURRENT_TAB` points at a **test tab** ("RK Verify (Test)"), not
  the real "2026-2027" roster, so this could run live without touching
  the school's actual admission records. Same column layout, so going
  live is a one-constant change. **Still not switched over.**
- Ad-hoc members get a labeled block in the SAME tab rather than their own
  tab, at a **fixed row** (`CLIO_ADHOC_MARKER_ROW`, moved 500 → 50) so
  adding a main-section member never shifts the ad-hoc block down and
  risks corrupting it. Row 50 leaves room for ~47 main members; there were
  30 at the time. Revisit if it fills up.

**Outage worth remembering:** verified members silently weren't reaching
the sheet for days. Cause: `GOOGLE_SERVICE_ACCOUNT_JSON_B64` was in `.env`
but never added to **Streamlit Cloud's Secrets**, and the sync was wrapped
in a try/except that swallowed the failure. Two lessons, both already
recorded elsewhere in this file and both re-learned the hard way:
Streamlit Cloud's secrets are a separate store, and a best-effort
try/except with no logging turns a broken feature into an invisible one.
Backfilling 22 verified members afterwards hit Google Sheets' per-minute
write quota (429) — each sync is 3 write calls.

Also: **host-controlled account disable** (`users.is_disabled`). A
disabled account is logged out on its next page load and shown "This
account has been disabled. Please contact the admin." Hosts toggle it from
the Members page, which also flags disabled members in a banner. Note this
disables the APP account only — the Supabase Auth login still exists, same
anon-key limitation as everywhere else in this file. Members also gained a
"Verified" column so a host can see who has confirmed their details.

## AI safety hardening (2026-08-14/15)

A long evening of "roast X" requests, all of which the bot cheerfully
fulfilled about real, named students aged 11-18, ended with the student
reversing course entirely and asking for it all to be deleted (24
messages) and blocked. Two standing rules came out of it, in stages:

1. **Never roast, insult, mock, or disrespect anyone** — members, staff,
   other clubs, outsiders, or someone asking about themselves.
2. **Never discuss school staff/administration or other school entities
   at all** — by name, nickname, abbreviation, title, or description.

**The central lesson: a system-prompt rule is a soft guardrail.** Both
rules were added to both AI surfaces' system prompts, and the bot kept
roasting anyway — partly Railway deploy lag (see the next section, which
turned out to be the real story), partly the weak OpenRouter fallback
model simply ignoring its instructions once Groq's daily budget ran out.
Prompt rules are advisory to a model; **code that runs before the model is
not**. Enforcement now lives in `_roast_request()`, duplicated in
`shared.py` and `discord_bot/bot.py` (which can't import shared.py), and
runs BEFORE any API call — so refusing costs nothing and never eats
someone's rate-limit allowance. The rejected turn is deliberately kept out
of conversation history so the next reply has nothing to build on.

Keyword blocking is inherently one phrasing behind, and this got tested by
a room full of students actively probing it. Each bypass and its fix:
- "make a joke on exun" → joke/meme/comeback/one-liner + preposition
- "say something funny about naitik" → funny-about patterns, plus
  **checking the real member roster from Supabase**, since the giveaway is
  the target, not the verb
- "a script on <person> in carryminati's humorous parody style" → matching
  the FORMAT (roast/parody/diss paired with script/video/style), because
  the person asking wasn't on the roster at all — he had a Discord account
  but no dashboard signup, so name-matching could never have seen him

Ambiguous words are deliberately excluded ("burn" as in a bootloader,
"flame" as in the sensor, "destroy"), and there's a regression suite of
real phrasings from the exported logs: 43 requests blocked, 30 legitimate
questions still passing, including "what is a rap battle" and "write a
python script for line following", both of which an earlier draft caught
by mistake. **Run it after any change to these patterns.**

The open question, raised and not yet decided: keyword blocking is a
blocklist, and the durable fix is inverting the default — the bot answers
club/robotics/general questions and refuses anything aimed at a person.
That's a bigger behaviour change and needs the student's call.

**The AI is READ-ONLY, and now says so.** A member asked the bot to remove
his L298N motor driver from sale; it replied "I removed your L298N Motor
Driver from sale as per your request." All of it was invented — the bot
has only read tools, there is no such part, that member has no linked
Discord ID, and **there is no buying/selling feature in this app at all**.
This is the same hallucination class as the fabricated inventory recorded
above, but worse: it claimed to have performed an action, so the member
stopped checking. Both system prompts now state plainly what the
assistant cannot do and cite this incident. There is no keyword to block
here — a false confirmation has no trigger word — so this one genuinely
does rest on the prompt.

Also from this period: Lav/Kush's shared account banned from both AI
surfaces (`AI_ASSISTANT_BANNED_EMAILS` / `AI_ASSISTANT_BANNED_DISCORD_IDS`);
a fourth Discord channel (`general`); the bot replying to **replies**, not
just @mentions and DMs; a 15/hour rate limit on the Discord Messages
page's custom-message tool (deliberately NOT on `send_discord_message()`
itself, so automated notifications are unaffected); and the AI Assistant
page no longer leaking raw provider errors (a Groq 429 was showing
students the org ID and a billing URL).

## The deploy that was never happening (2026-08-15)

The single most important operational fact learned in this project, and
the reason the fixes above appeared not to work for days.

**Railway had no GitHub repo attached to the bot service at all**
(`source: null`, confirmed from Railway's API). It only ever received code
when someone ran `railway up` from a laptop. The live deployment was four
days old. Every commit — the bans, the roast blocks, the staff rules — sat
on GitHub doing nothing, while `DEPLOY.md` claimed "Railway auto-deploys
on every push to master." That sentence was wrong, and trusting it instead
of checking sent every diagnosis in the wrong direction.

**Three lessons, in order of how much time each cost:**
1. **Verify the deploy before debugging the code.** "Is the running
   process actually the code I'm reading?" is the first question, not the
   last. `BOT_BUILD` now prints on startup for exactly this — the Railway
   logs will say which build is live, so this is answerable in seconds.
2. **`railway up` uploads the whole repo, not the folder you run it
   from.** Running it from `discord_bot/` still uploaded the root, so
   Railway found `app.py` and started the **Streamlit dashboard** instead
   of the bot, which crash-looped. Fixed with `railway.toml` at the repo
   root pinning `buildCommand`/`startCommand`, so the deploy no longer
   depends on anyone's working directory.
3. **Railway's GitHub integration never did start working.** The repo and
   branch connect in their UI, but it reports "Auto deploy unavailable"
   ("No project member has access to this GitHub repository") and no build
   fires. Granting the Railway GitHub App access to the private repo did
   not fix it. Rather than keep poking at a third party's permission
   plumbing, `.github/workflows/deploy-bot.yml` runs the same `railway up`
   from GitHub Actions on any push touching `discord_bot/**` or
   `railway.toml`. Needs a `RAILWAY_TOKEN` repo secret. **Verified end to
   end** — a push shipped a marker to the live bot with nobody running
   anything by hand. If Railway's own integration is ever fixed, delete
   this workflow so two things aren't deploying one service.

**Separately, a real concurrency bug found the same day.** The bot went
completely silent on one member's message — no reply, no error. `_ask_llm`
was being called directly on the asyncio event loop, and everything under
it (Groq, Gemini, Tavily, Supabase) is ordinary blocking HTTP. Groq's
daily budget was exhausted, it fell through to Gemini, that call hung, and
the gateway heartbeat blocked for 60+ seconds — so the bot answered
nobody, not just the member who asked. Now `await asyncio.to_thread(...)`,
plus a 30-second timeout on the Gemini call so a hung request degrades to
the honest "I'm maxed out" instead of silence. **Any blocking call added
to this bot must go through a thread.**

## Smaller failures worth remembering (2026-08-14/15)

Not big enough for their own sections, all real, all cost time. Added
2026-08-16 when an audit found them missing.

**The whole login screen crashed with `JWT expired` (PGRST303).** Nobody
could log in at all. Cause: `SUPABASE_KEY` on **Streamlit Cloud** was a
stale key, while the local `.env` one was fine (it decodes to an `exp` in
2036). This is the **third** outage in this project from the same root
cause — Streamlit Cloud's Secrets are a completely separate store from
`.env`, and nothing syncs them. The other two were `GOOGLE_SERVICE_ACCOUNT_JSON_B64`
(Clio sync silently doing nothing for days) and `GROQ_API_KEY` when the AI
Assistant first deployed. **When something works locally and not in
production, check the secret store before reading any code.** There are
now four separate places a secret may need to exist: `.env`, Streamlit
Cloud Secrets, GitHub Actions secrets, and Railway variables.

**A Discord message posted under the wrong identity.** A host-authored
update went out as "rk bot" with a default avatar instead of
`roboknightsbot`, because it was sent by a hand-rolled `requests.post` to
the webhook rather than through `send_discord_message()` — which sets
`username` and `avatar_url`. Discord **ignores both fields on an edit**;
the identity is fixed when the message is created, so the only fix was
delete and repost. **Always send through `send_discord_message()`**; it
exists precisely so no call site has to remember the footer, the identity,
or the `discord_messages` bookkeeping.

**The bot couldn't delete its own messages: `403 Missing Access`.** It
lacks *Read Message History* on that channel, so it can't fetch the
message IDs it would need. A bot editing/deleting its own posts needs that
permission, not just Send Messages. Still outstanding at time of writing.

**Leftover test data reached the real Clio sheet** — a row for "R77777
Adhoc Test Student" from an earlier trial was sitting in the ad-hoc block.
Cleared, but only after asserting the target row actually contained the
test row first, rather than blind-deleting a range in a sheet that mirrors
the school's admission records. **Any destructive sheet write should check
what it's about to overwrite.**

**A member's verified personal email never reached the sheet.** Medhansh
had entered it, the app had it, Clio didn't — a casualty of the silent
service-account failure above. Fixed by resetting his `details_verified`
flag so the popup ran again and rewrote the row. Worth knowing that flag
is the intended lever for re-running verification for one person.

**`git add -A` committed two scratch diagnostic scripts** into the repo.
No secrets in them, but they don't belong. Stage deliberately when the
working tree has throwaway files in it.

## Read-only viewer tier (2026-08-16)

`VIEWER_EMAILS` in `shared.py` — a look-around account, first used for
Kiara Kapoor (`r24334kiara@dpsrkp.net`). Full details in "Access tiers"
below; the short version is that it sees the club side of the app and
none of the private side, and cannot write anything.

The scope was narrowed deliberately when the request came in as "view
only but permission to see everything". "Everything a host sees" would
have included 53 members' phone numbers and admission numbers, the
private student<>host query threads, and every AI conversation anyone has
had — real data belonging to students, most of them minors, none of whom
agreed to a visitor account reading it, and none of it needed to evaluate
how the dashboard works. Asked, and the student chose club content only.
**Apply the same test to any future tier: what does this account actually
need to see, not what is it technically allowed to see.**

Account creation still has to be done by the person themselves — Supabase
Auth owns signup and this project has only the anon key. Google sign-in is
the smoother path for a new tier account, since viewers skip the profile
screen entirely (see "Access tiers") and it avoids depending on the
confirmation email, historically the flakier half of this app.

**Open item: `TAVILY_API_KEY` is set nowhere** — not Railway, not `.env`.
Both AI surfaces have been silently falling back to `groq/compound`, the
search path recorded above as unreliable, which is why members see "Groq's
search hit its own size limit". The Tavily code path exists and works on
both surfaces; it just has no key. Adding one to **both** Railway and
Streamlit Cloud Secrets (separate stores — this gap has now caused three
outages) is the fix.

## Pages and infrastructure that were never written up

Added 2026-08-16 after an audit found these missing from this file
entirely, despite being real, shipped, load-bearing parts of the app.
If you're changing any of them, this is the only documentation there is.

### `app_pages/home.py` — the Home page

The landing page after login: "what's relevant to you right now", so
nobody has to click through every other page checking for anything new. A
metrics strip across the top, then a wide left column for things needing
action and a narrow right column for announcements plus navigation. Any
section with nothing to show is skipped entirely, so it stays a summary
rather than becoming a copy of every other page.

**It writes nothing and tracks nothing new** — every section reads data
the other pages already maintain (a query thread's "new reply" uses the
real `host_read_at`/`student_read_at` columns `queries.py` keeps). Keep it
that way; the moment Home needs its own state, it stops being a view.

Gotcha already hit: `st.page_link` to a page that isn't in the current
user's nav **crashes the whole page** for them. Exun and viewer accounts
don't get Inventory/Announcements/Queries, so every `page_link` here is
tier-guarded. Adding a new link means adding the matching guard.

### `app_pages/exun_channel.py` — the private RoboKnights <> Exun channel

One shared thread between the two clubs' leadership, visible only to the
hand-picked addresses in `EXUN_CHANNEL_MEMBERS` — **not every host and not
every member**. `app.py` only adds the page to the nav for those people,
and the page re-checks on load, since `st.navigation`'s page list alone
doesn't stop a direct URL hit (same two-layer pattern as the Members
page). Messages live in `exun_channel_messages`; read receipts are one row
per member, since this is a single flat channel everyone reads
independently rather than a per-thread conversation.

`EXUN_CHANNEL_STUDENT_EMAILS` is **derived** (`EXUN_CHANNEL_MEMBERS -
HOST_EMAILS - EXUN_EMAILS`), not hand-maintained, so it can't drift out of
sync. It scopes the "unread message" nudge to students only — per the
student's explicit instruction that **staff never get nagged** about
unread messages.

### Access tiers, all of them

Five, and they are not a hierarchy — each is a separate email set in
`shared.py`, checked independently:

- `HOST_EMAILS` — full access, every host-only page and control.
- `HOST_ROLES` — display titles for specific host accounts (Vice
  Principal, HOD Computer Science, Robotics In-Charge), shown as the badge
  on the account card instead of a generic "Host".
- `EXUN_EMAILS` — sister club. Views Competitions, Meetings, Achievements,
  Members; never volunteers, RSVPs, logs an achievement, or touches
  anything host-only.
- `VIEWER_EMAILS` — read-only look-around account (2026-08-16, added for
  Kiara Kapoor's test account). Wider page access than Exun — also
  Inventory, Announcements, Feedback — but excluded from the private
  Queries threads, the AI chat logs, the Discord messaging tools, and
  members' section/admission no./phone no., which are dropped from the
  Members table for viewers specifically.
- `EXUN_CHANNEL_MEMBERS` — the private channel above, orthogonal to the
  rest.

`is_read_only = is_exun or is_viewer` is the single flag a page should ask
before showing a write control.

**The enforcement that actually matters is in `safe_write()`, not on the
buttons.** Inventory alone has 21 write controls; gating each by hand is
how a view-only account eventually writes real data through the one that
got missed. A read-only session hits `st.stop()` inside `safe_write`, so
the caller's `with` body never executes — verified with Streamlit's
`AppTest`, not assumed. Every page is covered by that automatically,
including pages added later. Button-level hiding/disabling is for clarity
on top of it, never instead of it. **If you add a new write path, route it
through `safe_write`** or it bypasses this entirely.

Note `st.stop()` and not `return`: a bare return before the `yield` makes
`@contextmanager` raise "generator didn't yield".

An account in `HOST_EMAILS`, `EXUN_EMAILS` or `VIEWER_EMAILS` **works with
no `users` row at all** — `current_user_id` comes from the auth user, and
the display name falls back to the email. That's why those tiers skip both
the Google profile-completion screen and the verify-your-details popup.
Consequence: they don't appear in the Members directory, which is correct
for a visitor account but surprising if you don't expect it.

### `cached_table()` — the 8-second cache every page depends on

Pages were making a fresh Supabase round trip per lookup, which is what
made the app feel slow (not Streamlit). `cached_table(name)` fetches a
whole table once per 8-second window (`CACHE_TTL`) and everything filters
it in Python. `invalidate_cache()` runs right after any insert/update/
delete so your own change shows up on the very next rerun rather than
waiting out the TTL — and it clears every table, not just the one written,
since most actions touch several.

The short TTL is deliberate: if a write ever forgets to invalidate, the
page self-corrects within seconds instead of staying wrong indefinitely.
Don't raise it to "forever".

### Private meetings (`meeting_invitees`)

A meeting with no rows in `meeting_invitees` is open to the whole club;
one with rows is visible only to those users (plus hosts). "Absent means
open to everyone" is what `meeting_invited_ids()` relies on, so an empty
invitee list must never be written as a "nobody" marker.

`meeting_invitee_rows()` swallows its own exception and returns `[]` — the
table was added after the pages that read it, and an unrun migration must
degrade to the old behaviour (every meeting club-wide) rather than crash
the Home page for everyone. Same defensive reasoning as the Queries nav
badge.

### Staff accounts (`users.is_staff`)

Self-declared at signup ("I'm a staff member (not a student)"). Staff skip
grade/section/admission no. entirely rather than being asked for
placeholder values that don't describe them, are skipped by the
verify-your-details popup, and show a "Staff" badge. Anything that filters
by grade must tolerate `None`.

## Explicitly NOT in v1

No PDF-to-spreadsheet feature. (WhatsApp notifications used to be listed
here too, ruled out over a DLT-registration concern that turned out not to
actually apply to WhatsApp — see the corrected research note in the
Post-Competitions feature ideas section above. It's a planned, deferred
feature now, not ruled out.)

## How to work with this student — most important part

1. One small change per request. When asked for a chunk, change only what
   that chunk needs. Don't rewrite the whole file or "improve" unrelated
   parts. Don't refactor unless asked.
2. Explain before and after, in plain English. If the student can't explain
   the change back, slow down.
3. Keep code beginner-readable and commented. Simple over clever.
4. After every chunk, say exactly what to click or run to test it before
   moving on.
5. Push back plainly when the student is wrong or overcomplicating.
6. Never assume approval for the next chunk. Finish the current one, say
   it's done, and stop.
7. Don't access the ashish.jindal079@gmail.com inbox (or its +testbot /
   +testbot2 aliases) for verification — the student asked for this
   directly. Verify features another way instead: direct database
   queries that replicate the exact app logic, or ask the student to
   test and report back.

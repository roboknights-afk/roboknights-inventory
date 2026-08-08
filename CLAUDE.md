# RoboKnights Parts Inventory

A parts-inventory web app for a school robotics club (RoboKnights). Built by a
Class XI student new to programming, with heavy AI help — the working rules
below matter as much as the technical spec.

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
see everyone's details at once. Gated two ways: `app.py` only adds this
page to the `st.navigation` pages list at all when
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

Discussed and NOT yet built: importing competitions from the club's real
Google Sheet (student called it "E2C") — the sheet marks which rows are
robotics competitions using actual Google Sheets **notes** (the
hover-to-reveal corner-triangle annotation, confirmed via a screenshot —
NOT a plain text column), which don't survive a CSV/Excel export, so this
needs the Google Sheets API directly (a free API key, sheet shared as
"anyone with the link can view") rather than a simple file upload. Blocked
on: seeing the real sheet structure (only one competition block seen so
far, "EDEN 6.0 THE NEXUS" — venue/date/links in one area, events + a
team-format code like `1x6` + grade range in another; unclear whether
`1x6` means team_size×max_teams or the reverse, and unclear how multiple
competitions repeat across the sheet — separate blocks per tab, or
stacked/side-by-side in one tab). Planned shape once unblocked: host
pastes the sheet link → "Scan for robotics competitions" button → preview
list with checkboxes for which ones to actually import → "Import
selected" writes to `competitions`/`competition_events`/
`competition_links`. Do not build the parser until the real structure is
confirmed — a wrong guess here means either missed competitions or
garbage rows getting imported.

## AI Assistant (2026-08-08/09)

New `app_pages/assistant.py`, added to nav unconditionally. A chat page
that answers using ONLY the logged-in member's own data (parts they own,
their borrow requests, competition volunteering, achievements, upcoming
meeting RSVPs) — never another member's, matching the existing privacy
rule elsewhere in this file. Also answers general robotics/build
questions on the model's own knowledge. Context is rebuilt fresh from
`cached_table` on every message; conversation history is session-only
(not saved to Supabase).

Provider: Groq's free tier (`llama-3.3-70b-versatile`), not Gemini or
OpenAI. Gemini was tried first (fits the zero-cost rule on paper) but a
brand-new, unbilled Google Cloud project still returned a hard 0 free-tier
quota on the very first request — confirmed live that Gemini's free API
tier simply isn't offered to India-based accounts, not a config mistake.
OpenAI has no comparable free API tier at all (free tier is web-only).
Groq has neither restriction and is faster besides. Needs `GROQ_API_KEY`
(free, no card, from console.groq.com/keys) — the page shows a host-only
setup message instead of crashing when it's unset.

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

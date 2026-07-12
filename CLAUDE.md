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
  wrote full deploy steps in `DEPLOY.md`. Not yet actually deployed —
  that's the student's call to make (needs their own
  share.streamlit.io login), instructions are just ready to go.
- Deferred, too vague to build yet: "add other features" (which ones?)
  and "link to the roboknights.in website in the future" (not actionable
  until there's a concrete integration to build — ask what "link" means
  when it comes up again: an outbound link from the club site, embedding,
  shared login, etc. are all very different asks).

## Identity — real accounts now

Real signup/login via Supabase Auth, email + password. Signup is open to
anyone with the link for now (no invite list, no admin approval step) — the
plan is to add restrictions later if it turns out to matter. The old fake
"I am: [name]" dropdown is being replaced by whoever is actually logged in.

Do not store passwords ourselves anywhere — Supabase Auth owns that entirely.
App-specific data about a member (their name, etc.) lives in our own table,
keyed to the Supabase Auth user id — not by re-implementing auth tables.

## Explicitly NOT in v1

No SMS/WhatsApp (India needs DLT registration / paid business API), no
PDF-to-spreadsheet feature.

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

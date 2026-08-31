# Moving off Streamlit: the plan

**Decided 2026-08-30 by the student.** Phase 2 does not build anything else
on Streamlit. The dashboard is rebuilt as a real web app on Vercel, with a
new design, and the existing features are moved across to it.

This file is the plan of record. `PHASE2_SUGGESTIONS.txt` stays the list of
*what* Phase 2 delivers; this is *how* the platform underneath it changes.

---

## The one thing that must not be got wrong

**There is no row-level security on any of the 28 tables.** Every privacy
rule this app has is enforced in Python, before a query is sent:

- hosts are `HOST_EMAILS` checked in `shared.py`
- a student only sees their own query threads because `queries.py` filters
  by `student_id`
- section / admission no. / phone no. are dropped for viewer accounts in
  `members.py`
- every write goes through `safe_write()`, which calls `st.stop()` for a
  read-only tier

That works today for one reason and one reason only: **Streamlit is a
server.** The Supabase key never reaches anyone's browser, so a member
cannot ask Supabase for data the Python code would not have given them.

A normal Next.js + Supabase app talks to Supabase **from the browser**. If
this port does that before RLS exists, then anyone who opens devtools can
read every one of 53 members' phone numbers, admission numbers and
sections, every private student-host query thread, every AI conversation,
the Exun channel, and the moderation flags naming individual students. Most
of these members are minors. This is not a theoretical risk; it is the
default behaviour of the stack we are moving to.

**So: all Supabase access goes through server-side Next.js code.** Server
Components and Route Handlers, with the key held on the server exactly as
Streamlit holds it now. The browser calls our own API; it never calls
Supabase directly. The permission checks that live in `shared.py` today get
ported into one server-side module and are the only place data is fetched.

Writing real RLS policies for 28 tables is the better long-term answer and
should happen eventually as defence in depth. It is deliberately **not**
part of this migration — doing a platform move and a security redesign at
the same time means neither gets verified properly.

---

## What is actually being rewritten

About 10,000 lines of Python across 15 pages. This is a rewrite, not a
port; nothing below survives as code, only as behaviour to reproduce.

| File | Lines |
| --- | --- |
| `app_pages/competitions.py` | 2199 |
| `app.py` | 1660 |
| `shared.py` | 1322 |
| `app_pages/inventory.py` | 1251 |
| `app_pages/assistant.py` | 569 |
| `app_pages/meetings.py` | 496 |
| `app_pages/achievements.py` | 398 |
| `app_pages/members.py` | 357 |
| `app_pages/home.py` | 344 |
| the remaining 6 pages | ~1140 |

Being honest about that number is the point of writing it down. This is
weeks of work, not a weekend, and it is why it happens in chunks with the
old app still running.

---

## What does NOT move

Worth being clear, because it is most of the automation and it all keeps
working untouched:

- **Every GitHub Actions job** — `send_due_reminders.py`,
  `send_achievement_reminders.py`, `check_discord_messages.py`,
  `nike_list_sync.py`, `send_dashboard_update.py`. All plain Python talking
  to Supabase directly. None of them import Streamlit. Nothing to change.
- **The Discord bot** (`discord_bot/`) — separate process, separate host.
- **`e2c_import.py`** — plain Python, no Streamlit import, by design. It is
  called from a page today, so it becomes a server-side call in the new app.
- **Supabase itself** — all 28 tables, all data, Auth, the Google provider.
  Nothing is migrated or re-shaped. The new app reads the same rows.
- **Brevo SMTP, Groq, Tavily, the Google service account, every webhook.**

---

## Ground rules for the whole migration

Set by the student 2026-08-30, and they override anything below that
disagrees:

- **Nothing goes live.** No Vercel deploy, no domain change, no push. The
  new app runs on `localhost:3000` and is committed locally only, the same
  way `ds2-website` is.
- **The Streamlit app is not touched.** No changes to `app.py`,
  `app_pages/`, or `shared.py` for the duration. It stays live and in use.
  This repo keeps working exactly as it does today.
- **Step one is a straight transfer**: get everything Streamlit does
  working in the new app, and tested, before anything new is added.
- **Every chunk carries one website piece and one dashboard piece.** The
  club site and the dashboard are built together so they end up looking
  like one thing, not two.

## The stack

- **Next.js (App Router) + TypeScript**, deployed on **Vercel**. This is
  what Vercel is built for and is the only reason the hosting decision was
  ever a rewrite rather than a redeploy.
**Two repos, exactly like Domain Square already does it:**

| | Website | Dashboard |
| --- | --- | --- |
| Repo | `Documents\GitHub\RoboKnights-Clan.github.io` (already exists) | `C:\Users\gogof\roboknights-dashboard-web` (new, created 2026-08-30) |
| Origin | `iamnaitiknj/…` (fork), **upstream `RoboKnights-Clan/…` is the live one** | none yet — local only |
| Branch | `deploy`, at `eac6f4d` = `upstream/deploy` | `master` |
| Stack | Next 12, **Pages Router**, React 17, Tailwind 3 | Next 16, **App Router**, React 19, Tailwind 4 |
| Deployed | yes — roboknights.in, via Vercel | no, and not until chunk 9 |

- The dashboard is **not** a folder inside `roboknights-inventory`: that
  repo stays pure Python, so its GitHub Actions jobs and the Streamlit
  deploy keep working untouched.
- The dashboard's versions match `ds2-dashboard-web` deliberately, so
  there is one modern Next.js setup to learn and not two.
- The two sites are different Next.js generations and that is fine — they
  are separate deploys that only have to agree on how they *look*. Chunk 1
  already lifted the site's palette (`#242424`, `#101010`, Work Sans)
  straight out of its `tailwind.config.js` into the dashboard's tokens, so
  they start matching rather than being reconciled later.

**Which website code to work from — settled 2026-08-30.** The student's
instruction was to use the live code, not the local working copy, and
those were three different things:

- `upstream/deploy` on `RoboKnights-Clan/RoboKnights-Clan.github.io` is
  **the live site.** Work from this.
- `origin/deploy` (the `iamnaitiknj` fork) was **3 commits behind it**.
  The local checkout was sitting on that stale point. Fast-forwarded.
- On top of that were ~4 months of uncommitted experiments from May 2026.
  They are **parked on the local branch `wip-may-2026`**, not deleted, and
  `deploy` is now clean at `eac6f4d`, matching live exactly. `npm ci` was
  re-run so `node_modules` matches the live lockfile too.

Two things were wrong with those experiments, recorded here so they are
not re-introduced: `data/achievements.ts` had been overwritten with a copy
of the alumni data, so the site did not build at all; and
`pages/alumni.tsx` did not add its new hero component to the page, it
replaced the page with it — no nav, no search, no batch grouping, names
legible only sideways along animated panels.

**How website changes have historically shipped:** commits land on the
fork's `deploy`, then reach the org repo as a pull request (`Merge pull
request #14 from iamnaitiknj/deploy`). So the flow for a website chunk is
branch off `upstream/deploy` → commit locally → and only open a PR when
the student asks. Nothing is pushed during Phase 2.
- **Tailwind CSS** for styling. The new design gets defined once as tokens
  and applied everywhere — the Phase 2 sheet's complaint is that pages
  built at different times look like it.
- **`@supabase/ssr`** for cookie-based auth sessions, `@supabase/supabase-js`
  for queries, both **server-side only** per the section above.
- Python stays for everything in "what does not move".

This overrides the stack rule in `CLAUDE.md` ("No Docker, Flask, React, or
npm"), which has been updated. That rule was right for a single-file
Streamlit app and is not right for this.

**One genuine cost, stated plainly:** the student writes Python. This
introduces TypeScript, React and npm all at once. Every chunk below should
be explained in the same before/after way the Python chunks were, and the
first chunk deliberately does the least interesting thing possible so the
shape of a Next.js page is learned before anything important depends on it.

---

## Things that get better for free

Not padding — these are real problems in the current app that stop existing:

- **The iframe.** Streamlit Cloud serves the app inside an iframe, which
  cost seven follow-up commits to get Google sign-in working and forced the
  "opens in a new tab" compromise. On Vercel the app is the page. OAuth
  becomes ordinary.
- **The two custom-CSS exceptions** (the login wordmark card, the animation
  block) and the `st.html` strips-SVG / markdown-indentation gotchas. All of
  it is just CSS in a real frontend.
- **`cached_table()`'s 8-second window** — a workaround for a page script
  re-running top to bottom on every interaction. React does not do that.
- **`st.session_state` + `st.rerun()` bookkeeping**, roughly 20 flags in
  `competitions.py` alone, exists to keep a dialog open across reruns.
- **Mobile.** Most members open this on a phone. Streamlit's layout was
  never really ours to control; Tailwind is.

## Things that get harder

- No `st.data_editor`. The Members page's editable table has to be built.
- No `st.dialog`, `st.segmented_control`, `st.metric`, `st.toast`. All
  ordinary components to write, but they are not free any more.
- `safe_write()` currently backstops **every** write in the app from one
  place, including pages written later. That guarantee has to be
  deliberately rebuilt server-side, not left to whoever writes each route.

---

## Order of work

Nine chunks. Each one ships **one website piece and one dashboard piece**,
is checked before the next starts, and stays on localhost. The Streamlit
app is live and untouched the whole way through; only chunk 9 changes
anything real.

Website halves land in `RoboKnights-Clan.github.io`; dashboard halves in
`roboknights-dashboard-web`.

| # | Website half | Dashboard half |
| --- | --- | --- |
| 1 | *(done differently — see below)* | ✅ Inventory parts list, read-only, server-side from Supabase |
| 2 | ✅ Homepage rebuild (gear train, five machines, results ticker) | ✅ The dashboard shell + shared components in the same language |
| 3 | ✅ About + Contact pages | ✅ Auth, access tiers, and the write backstop |
| 4 | ✅ Members page (public roster) | ✅ Inventory writes + the dashboard on the site's palette |
| 5 | ✅ Achievements page (public) | ✅ Competitions browse/sign-up — host tools + E2C import still to come |
| 6 | Alumni page | Home, Meetings, Announcements, Members (with the privacy rules) |
| 7 | Resources + Blogs | Queries, Feedback, Messages, Exun channel |
| 8 | 404 / error states, mobile pass, favicon + link previews | AI Logs, Discord Messages, Assistant |
| 9 | Point roboknights.in at the new app | Retire Streamlit after a week of overlap |

Notes on the ordering, since it isn't arbitrary:

- **Chunk 1 is deliberately boring, and its website half was deliberately
  skipped.** The dashboard half proved the whole chain end to end - repo,
  build, Tailwind, a server route reading Supabase. The website half was
  *reading* the existing repo rather than writing to it, because of the
  uncommitted work sitting in it; the first real website edit is chunk 2,
  once that is resolved.
- **Chunk 2 settles the design once**, on two real pages, rather than
  fifteen pages in. The Phase 2 sheet's complaint about the current app is
  that pages built at different times look like it.
- **Chunk 3 is the gate.** Auth, `HOST_EMAILS` / `EXUN_EMAILS` /
  `VIEWER_EMAILS` / `EXUN_CHANNEL_MEMBERS`, and the server-side replacement
  for `safe_write()` all land as one module, and nothing that writes gets
  built before it exists. It also adds the DASHBOARD link on the site, the
  same way `ds2-website` links to its dashboard.
- **Chunks 4 and 5 are the big ones** - Inventory and Competitions are 3,450
  lines of Python between them and the two pages members actually use.
  Chunk 5 will likely need two sittings.
- **Chunk 9 is the only one that touches production**, and it is a separate
  decision the student makes then, not now.

Testing, every chunk: run it locally (`npm run dev`), open the pages, and
compare against the same page in the live Streamlit app side by side. The
transfer is done when the new app does everything the old one does - that
is the bar for chunk 9, and the reason nothing is added until then.

---

## Open decisions

- **What the new design actually is.** "Very better" is the brief so far.
  Settled in chunk 2 against something real on screen, not described in
  advance.
- **Whether the AI Assistant page moves at all**, or whether members just
  use the Discord bot, which already answers the same questions.
- **Whether anything on `wip-may-2026` gets salvaged later.** Not blocking
  anything - it is parked and the live code is what gets built on. The one
  piece worth revisiting is the mobile dark-mode toggle, which on the live
  site is a `<button>` nested inside another `<button>`; that is invalid
  HTML and worth fixing properly in a website chunk of its own.
- **Which GitHub account and remote the dashboard repo gets**, when it
  eventually gets one. `roboknights-afk` is the obvious choice, but the
  website lives under `RoboKnights-Clan` / `iamnaitiknj`, so this is worth
  deciding rather than defaulting.

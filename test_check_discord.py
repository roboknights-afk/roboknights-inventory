# Checks that the Discord evening check still FLAGS messages but never posts
# anything about it to Discord (host, 2026-10-20: "messages should be flagged
# but nothing should be posted abt that in discord").
#
#   python test_check_discord.py
#
# Everything is faked: no Supabase, no Groq, no email, no Discord. Any HTTP call
# at all fails the test - the review (the only thing that talks to Groq) is
# replaced, so a request here could only be a Discord post. Messages and names
# below are made up.

import os
import sys
import types

import requests
import supabase

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, wanted {want!r}")


# --- a fake database ---------------------------------------------------------------------------
ROWS = [
    {"discord_message_id": f"m{i}", "discord_channel_id": "c1", "discord_user_id": f"u{i % 3}",
     "discord_display_name": f"Member{i % 3}", "content": f"made-up message number {i}",
     "created_at": f"2026-10-19T1{i}:00:00+00:00"}
    for i in range(6)
]


class Store:
    def __init__(self, flags_table_works=True, already_flagged=()):
        self.upserts, self.flags_table_works, self.already_flagged = [], flags_table_works, set(already_flagged)


class Query:
    def __init__(self, store, name):
        self.store, self.name, self.op = store, name, "select"

    def select(self, *a, **k): self.op = "select"; return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def upsert(self, row): self.op = "upsert"; self.row = row; return self

    def execute(self):
        if self.name == "discord_channel_log":
            return types.SimpleNamespace(data=ROWS)
        if self.name == "discord_flags":
            if not self.store.flags_table_works:
                raise RuntimeError("relation discord_flags does not exist")
            if self.op == "upsert":
                self.store.upserts.append(self.row)
                return types.SimpleNamespace(data=[self.row])
            return types.SimpleNamespace(data=[{"discord_message_id": m} for m in self.store.already_flagged])
        return types.SimpleNamespace(data=[])


store = Store()
supabase.create_client = lambda *a, **k: types.SimpleNamespace(table=lambda name: Query(store, name))
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "not-a-real-key")
sys.path.insert(0, ".")
import check_discord_messages as cdm  # noqa: E402

http_calls, emails = [], []
requests.post = lambda *a, **k: http_calls.append(a[0] if a else k) or (_ for _ in ()).throw(AssertionError("an HTTP request was made"))
requests.get = requests.post
cdm.send_email = lambda to, subject, body: emails.append((to, subject, body))

# --- the pieces that posted to Discord are gone -----------------------------------------------------
for name in ("post_to_general", "write_warning", "_strip_quotes_of", "WARNING_PROMPT", "AUTO_WARN",
             "DISCORD_GENERAL_WEBHOOK", "MAX_WARNINGS_PER_RUN"):
    check(f"{name} no longer exists", hasattr(cdm, name), False)


def run(flags, full_quality=True):
    cdm.review = lambda rows: (flags, full_quality)
    del emails[:]
    store.upserts.clear()
    cdm.main()


# --- a night with two flags, one of them "clear": exactly what AUTO_WARN used to act on -----------------------
os.environ["AUTO_WARN"] = "1"                     # even if the old switch is still set somewhere
os.environ["DISCORD_GENERAL_WEBHOOK_URL"] = "https://example.invalid/hook"
run([{"id": "m2", "reason": "aimed at a member", "severity": "clear"},
     {"id": "m4", "reason": "might be banter", "severity": "borderline"}])
check("nothing was posted anywhere, even with AUTO_WARN=1 and a webhook set", http_calls, [])
check("both messages were still flagged (recorded)", sorted(r["discord_message_id"] for r in store.upserts), ["m2", "m4"])
check("recorded rows carry no warning fields",
      any("warned_at" in r or "warning_message_id" in r for r in store.upserts), False)
check("recorded severity kept", {r["discord_message_id"]: r["severity"] for r in store.upserts}, {"m2": "clear", "m4": "borderline"})
check("one report email", len(emails), 1)
to, subject, body = emails[0]
check("report subject counts the flags", subject, "Discord evening check: 2 flagged")
check("report shows both flagged messages", "made-up message number 2" in body and "made-up message number 4" in body, True)
check("report links back to the messages", "https://discord.com/channels/" in body and "/c1/m2" in body, True)
check("report no longer claims a warning went out or didn't", "WARNED" in body or "warning sent" in body, False)
check("report still shows the surrounding conversation", "what was being said around it:" in body, True)

# --- a re-run does not record the same flags twice -------------------------------------------------------------
store.already_flagged = {"m2"}
run([{"id": "m2", "reason": "aimed at a member", "severity": "clear"}])
check("already-recorded flag is not recorded again", store.upserts, [])
check("but it is still in the report", len(emails) == 1 and "made-up message number 2" in emails[0][2], True)
store.already_flagged = set()

# --- the backup model: the report says so ----------------------------------------------------------------------
run([{"id": "m1", "reason": "r", "severity": "clear"}], full_quality=False)
check("degraded run still emails", len(emails), 1)
check("degraded run says the ratings deserve doubt", "backup one" in emails[0][2] and "more doubt than usual" in emails[0][2], True)
check("degraded run says nothing about warnings", "warning" in emails[0][2].lower(), False)
check("degraded run posted nothing", http_calls, [])

# --- the flags table missing: report still goes out, nothing recorded, nothing posted ----------------------------
store.flags_table_works = False
run([{"id": "m3", "reason": "r", "severity": "clear"}])
check("without the table: still emailed", len(emails), 1)
check("without the table: nothing recorded", store.upserts, [])
check("without the table: nothing posted", http_calls, [])
store.flags_table_works = True

# --- nothing flagged: nothing sent --------------------------------------------------------------------------------
run([])
check("nothing flagged -> no email", emails, [])
check("nothing flagged -> no flags", store.upserts, [])

# --- the review failing is still reported by email (unchanged) ----------------------------------------------------
cdm.review = lambda rows: None
del emails[:]
cdm.main()
check("a failed review is emailed, not posted", len(emails) == 1 and emails[0][1] == "Discord evening check did not run" and http_calls == [], True)

# --- and the workflow no longer hands this step a webhook or the switch ---------------------------------------------
workflow = open(".github/workflows/due-reminders.yml", encoding="utf-8").read()
step = workflow.split("Discord evening check (8pm IST)", 1)[1]
step = step.split("- name:", 1)[0]
check("workflow step has no AUTO_WARN variable", "AUTO_WARN:" in step, False)
check("workflow step has no Discord webhook", "DISCORD_GENERAL_WEBHOOK_URL" in step, False)
check("workflow step still has the secrets the check needs", all(k in step for k in ("SUPABASE_URL", "GROQ_API_KEY", "SMTP_HOST")), True)

if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("all discord-check checks passed")

# Checks for the bot's slash commands (discord_bot/club_commands.py): what each
# one says for a given set of rows. No Discord, no database - names and data
# below are made up.
#
#   python test_club_commands.py

import sys
from datetime import date

sys.path.insert(0, "discord_bot")
import club_commands as cc  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


TODAY = date(2026, 11, 4)
COMPS = [
    {"competition_id": 1, "name": "Past Cup", "competition_date": "2026-10-01", "is_past": True},
    {"competition_id": 2, "name": "Test Cup", "competition_date": "2026-11-20", "venue": "Test Hall",
     "registration_deadline": "2026-11-10", "is_past": False},
    {"competition_id": 3, "name": "Later Fest", "competition_date": "2026-12-05", "is_past": False,
     "registration_deadline": "2026-11-05"},
    {"competition_id": 4, "name": "Undated Expo", "competition_date": None, "is_past": False},
    {"competition_id": 5, "name": "Yesterday Meet", "competition_date": "2026-11-03", "is_past": False},
]
EVENTS = [
    {"event_id": 10, "competition_id": 2, "name": "Line Follower"},
    {"event_id": 11, "competition_id": 2, "name": "Robo Race"},
    {"event_id": 12, "competition_id": 3, "name": "Maze"},
    {"event_id": 13, "competition_id": 1, "name": "Old Event"},
]
VOLS = [
    {"event_id": 10, "user_id": "u1", "selected": True},
    {"event_id": 12, "user_id": "u1", "selected": False},
    {"event_id": 13, "user_id": "u1", "selected": True},
    {"event_id": 11, "user_id": "u2", "selected": True},
]

# --- upcoming: not past, today or later, undated last -------------------------------
check("upcoming order", [c["name"] for c in cc.upcoming_competitions(COMPS, TODAY)],
      ["Test Cup", "Later Fest", "Undated Expo"])

# --- /nextcomp ----------------------------------------------------------------------
text = cc.next_comp_text(COMPS, EVENTS, TODAY)
check("nextcomp names the next one", text.startswith("**Test Cup**"), True)
check("nextcomp days away", "(in 16 days)" in text, True)
check("nextcomp venue", "Venue: Test Hall" in text, True)
check("nextcomp deadline", "Registration deadline: Tue 10 Nov 2026 (in 6 days)" in text, True)
check("nextcomp events sorted", "Events: Line Follower, Robo Race" in text, True)
check("nextcomp mentions the one after", "After that: Later Fest" in text, True)
check("nextcomp none", cc.next_comp_text(COMPS[:1], EVENTS, TODAY), "No upcoming competitions on the dashboard right now.")

# --- /deadlines ---------------------------------------------------------------------
text = cc.deadlines_text(COMPS, TODAY)
check("deadlines soonest first", text.index("Later Fest") < text.index("Test Cup"), True)
check("deadlines tomorrow", "Later Fest: Thu 05 Nov 2026 (tomorrow)" in text, True)

# --- /mycomps -----------------------------------------------------------------------
text = cc.my_comps_text("u1", COMPS, EVENTS, VOLS, TODAY)
check("mycomps selected", "- Line Follower at Test Cup, Fri 20 Nov 2026 (in 16 days): selected" in text, True)
check("mycomps signed up", "Maze at Later Fest" in text and "signed up (not selected yet)" in text, True)
check("mycomps skips past events", "Old Event" in text, False)
check("mycomps not someone else's", "Robo Race" in text, False)
check("mycomps not linked", cc.my_comps_text(None, COMPS, EVENTS, VOLS, TODAY), cc.NOT_LINKED)
check("mycomps nothing", cc.my_comps_text("u9", COMPS, EVENTS, VOLS, TODAY, name="Testy Kumar"),
      cc.linked_as("Testy Kumar") + "\nYou're not signed up for any upcoming competition events.")
# Every personal reply says which account it's for, so a wrong link shows.
check("mycomps says who", cc.my_comps_text("u1", COMPS, EVENTS, VOLS, TODAY, name="Testy Kumar")
      .startswith("*Linked as Testy Kumar."), True)

# --- /meetings: private ones only for invitees, never a link ------------------------
MEETINGS = [
    {"meeting_id": 1, "title": "Open Meet", "meeting_date": "2026-11-06", "meeting_time": "16:00:00"},
    {"meeting_id": 2, "title": "Core Meet", "meeting_date": "2026-11-05", "meeting_time": "15:30:00"},
    {"meeting_id": 3, "title": "Old Meet", "meeting_date": "2026-11-01", "meeting_time": "10:00:00"},
]
INVITEES = [{"meeting_id": 2, "user_id": "u1"}]
text = cc.meetings_text("u1", MEETINGS, INVITEES, TODAY)
check("meetings invitee sees private", "Core Meet (private): Thu 05 Nov 2026 (tomorrow) at 15:30" in text, True)
check("meetings soonest first", text.index("Core Meet") < text.index("Open Meet"), True)
check("meetings skip past", "Old Meet" in text, False)
check("meetings never a join link", "meet.google" in text or "password" in text.lower(), False)
text = cc.meetings_text("u2", MEETINGS, INVITEES, TODAY)
check("meetings non-invitee doesn't see private", "Core Meet" in text, False)
check("meetings unlinked sees open ones", "Open Meet" in cc.meetings_text(None, MEETINGS, INVITEES, TODAY), True)
check("meetings says who", cc.meetings_text("u1", MEETINGS, INVITEES, TODAY, name="Testy Kumar")
      .startswith("*Linked as Testy Kumar."), True)
check("meetings unlinked has no line", "Linked as" in cc.meetings_text(None, MEETINGS, INVITEES, TODAY), False)

# --- the bot's status countdown -----------------------------------------------------
check("countdown next dated comp", cc.countdown_status(COMPS, TODAY), "\N{CHEQUERED FLAG} Test Cup in 16 days")
check("countdown tomorrow", cc.countdown_status(COMPS, date(2026, 11, 19)), "\N{CHEQUERED FLAG} Test Cup tomorrow")
check("countdown today", cc.countdown_status(COMPS, date(2026, 11, 20)), "\N{CHEQUERED FLAG} Test Cup today!")
check("countdown skips not attending",
      cc.countdown_status([dict(COMPS[1], not_attending=True), COMPS[2]], TODAY), "\N{CHEQUERED FLAG} Later Fest in 31 days")
check("countdown none", cc.countdown_status(COMPS[:1], TODAY), None)

# --- length cap ---------------------------------------------------------------------
many = [{"competition_id": i, "name": f"Comp {i} " + "x" * 60, "is_past": False,
         "registration_deadline": "2026-12-01"} for i in range(100)]
check("long replies are clipped under Discord's limit", len(cc.deadlines_text(many, TODAY)) <= cc.MAX_LEN + 4, True)

if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("all slash-command checks passed")

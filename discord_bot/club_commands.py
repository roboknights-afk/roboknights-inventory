# The bot's slash commands (2026-11-04): /nextcomp, /mycomps, /meetings,
# /deadlines, /help. Answered straight from the club's own data - no AI -
# so they're instant, always right, and cost none of the free models'
# daily limits. bot.py fetches the rows and sends the reply; everything
# here is plain functions of those rows, testable with no Discord and no
# database (see test_club_commands.py).
#
# Read-only like the rest of the bot. /meetings never includes a join
# link, meeting ID or password - same rule as the meeting announcements:
# those stay on the dashboard.

from datetime import date

MAX_LEN = 1900  # Discord's limit is 2000; leave room
DASHBOARD = "https://roboknights-dashboard.streamlit.app"

HELP_TEXT = (
    "**What I can do**\n"
    "- `/nextcomp`: the club's next competition and its events\n"
    "- `/mycomps`: the upcoming events you're signed up or selected for\n"
    "- `/meetings`: upcoming meetings you're invited to\n"
    "- `/deadlines`: upcoming registration deadlines\n"
    "- Tag me with a question: club stuff, robotics help, or anything else\n"
    "- Tag me and say *summarise*: a summary of the channel's recent messages\n\n"
    "I can only look things up. To sign up, RSVP or change anything, use the "
    f"dashboard: <{DASHBOARD}>"
)
NOT_LINKED = (
    "I don't know which member you are yet. Link your Discord account on the "
    f"dashboard (Home -> Discord), then try again: <{DASHBOARD}>"
)


def _d(value):
    return date.fromisoformat(value[:10]) if value else None


def _when(day, today):
    if day is None:
        return "date not set"
    gap = (day - today).days
    label = day.strftime("%a %d %b %Y")
    if gap == 0:
        return f"{label} (today)"
    if gap == 1:
        return f"{label} (tomorrow)"
    return f"{label} (in {gap} days)"


def _clip(text):
    return text if len(text) <= MAX_LEN else text[:MAX_LEN].rsplit("\n", 1)[0] + "\n..."


def upcoming_competitions(competitions, today):
    # Not marked past, and dated today or later - or not dated yet (those
    # go last).
    found = [
        c for c in competitions
        if not c.get("is_past") and (_d(c.get("competition_date")) is None or _d(c["competition_date"]) >= today)
    ]
    return sorted(found, key=lambda c: (_d(c.get("competition_date")) or date.max, c.get("name") or ""))


def next_comp_text(competitions, events, today):
    upcoming = upcoming_competitions(competitions, today)
    if not upcoming:
        return "No upcoming competitions on the dashboard right now."
    comp = upcoming[0]
    lines = [f"**{comp['name']}**", _when(_d(comp.get("competition_date")), today)]
    if comp.get("venue"):
        lines.append(f"Venue: {comp['venue']}")
    deadline = _d(comp.get("registration_deadline"))
    if deadline and deadline >= today:
        lines.append(f"Registration deadline: {_when(deadline, today)}")
    names = sorted(e["name"] for e in events if e["competition_id"] == comp["competition_id"])
    if names:
        lines.append("Events: " + ", ".join(names))
    if len(upcoming) > 1:
        lines.append(f"\nAfter that: {upcoming[1]['name']} ({_when(_d(upcoming[1].get('competition_date')), today)})")
    return _clip("\n".join(lines))


def countdown_status(competitions, today):
    # The bot's Discord status line (2026-11-04), e.g. "NYRC 2027 in 26
    # days". The next dated competition the club is going to; None when
    # there isn't one, which clears the status.
    going = [c for c in upcoming_competitions(competitions, today)
             if c.get("competition_date") and not c.get("not_attending")]
    if not going:
        return None
    comp = going[0]
    gap = (_d(comp["competition_date"]) - today).days
    when = "today!" if gap == 0 else "tomorrow" if gap == 1 else f"in {gap} days"
    return f"\N{CHEQUERED FLAG} {comp['name']} {when}"[:128]


def deadlines_text(competitions, today):
    found = sorted(
        (c for c in competitions
         if not c.get("is_past") and _d(c.get("registration_deadline")) and _d(c["registration_deadline"]) >= today),
        key=lambda c: _d(c["registration_deadline"]),
    )
    if not found:
        return "No upcoming registration deadlines on the dashboard."
    return _clip("**Registration deadlines**\n" + "\n".join(
        f"- {c['name']}: {_when(_d(c['registration_deadline']), today)}" for c in found
    ))


def my_comps_text(user_id, competitions, events, volunteers, today):
    if not user_id:
        return NOT_LINKED
    upcoming = {c["competition_id"]: c for c in upcoming_competitions(competitions, today)}
    by_event = {e["event_id"]: e for e in events if e["competition_id"] in upcoming}
    mine = []
    for v in volunteers:
        event = by_event.get(v["event_id"])
        if v["user_id"] == user_id and event:
            mine.append((upcoming[event["competition_id"]], event, v))
    if not mine:
        return "You're not signed up for any upcoming competition events."
    mine.sort(key=lambda x: (_d(x[0].get("competition_date")) or date.max, x[0]["name"], x[1]["name"]))
    lines = ["**Your upcoming events**"]
    for comp, event, v in mine:
        status = "selected" if v.get("selected") else "signed up (not selected yet)"
        lines.append(f"- {event['name']} at {comp['name']}, {_when(_d(comp.get('competition_date')), today)}: {status}")
    return _clip("\n".join(lines))


def meetings_text(user_id, meetings, invitees, today, limit=5):
    # A meeting with invitees is private to them (hosts see those on the
    # dashboard); one with none is open to the whole club - the same rule
    # the dashboard uses.
    invited = {}
    for row in invitees:
        invited.setdefault(row["meeting_id"], set()).add(row["user_id"])
    found = sorted(
        (m for m in meetings
         if _d(m.get("meeting_date")) and _d(m["meeting_date"]) >= today
         and (m["meeting_id"] not in invited or user_id in invited[m["meeting_id"]])),
        key=lambda m: (m["meeting_date"], m.get("meeting_time") or ""),
    )[:limit]
    if not found:
        return "No upcoming meetings for you on the dashboard."
    lines = ["**Upcoming meetings**"]
    for m in found:
        at = f" at {m['meeting_time'][:5]}" if m.get("meeting_time") else ""
        private = " (private)" if m["meeting_id"] in invited else ""
        lines.append(f"- {m['title']}{private}: {_when(_d(m['meeting_date']), today)}{at}")
    lines.append(f"\nJoin links are on the dashboard: <{DASHBOARD}>")
    return _clip("\n".join(lines))

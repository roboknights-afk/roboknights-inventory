# "@bot details of X" answered straight from the database (2026-10-28). No
# model call, so it works the same when the AI budget is spent: the backup
# model is deliberately handed the roster WITHOUT contact details (they made
# its requests too big), so it was answering "Not available right now" for
# phone, email and admission no. - caught live on a real lookup.
#
# Same split as channel_summary.py: the pure parts live here so
# test_member_details.py can run them without starting the bot.
#
# Matching is strict on purpose - every word typed must be a whole word of
# the member's name, ignoring case. A name that matches nobody returns no
# card and the question goes on to the assistant as before, which can still
# cope with a typo or a nickname.

import re

ROLE_LABELS = {"core_member": "Core", "member": "Member", "adhoc": "Adhoc"}
MAX_NAME_WORDS = 4
NOT_ON_FILE = "Not on file"

_WHAT = r"(?:full\s+|all\s+|contact\s+|personal\s+)?(?:details?|info|information|contacts?|deets|phone\s*(?:no\.?|number)|number|mobile\s*(?:no\.?|number)?)"
_LEAD = r"(?:(?:pls|plz|please|can\s+you|could\s+you|bro|bhai|yo|hey|give|gimme|send|show|tell|share|get|need|want|me|the|his|her|their)\s+)*"
_TAIL = r"(?:\s+(?:pls|plz|please|bro|bhai|do|de|dedo|bhejo|batao|chahiye))*"

_PATTERNS = [
    # "details of X", "give me the contact details for X", "phone number of X pls"
    re.compile(rf"^{_LEAD}{_WHAT}\s+(?:of|for|about)\s+(?P<name>.+?){_TAIL}$"),
    # "X details", "X's phone number", "give X's contact"
    re.compile(rf"^{_LEAD}(?P<name>.+?)(?:'s|’s)?\s+{_WHAT}{_TAIL}$"),
]
_NAME_WORD = re.compile(r"^[a-z][a-z.]*$")


def parse_request(text):
    """The name asked about, or None if `text` (the message with the bot's
    @mention already removed) isn't a request for someone's details."""
    lowered = re.sub(r"[?!,]+", " ", (text or "").lower()).strip()
    lowered = re.sub(r"\s+", " ", lowered)
    for pattern in _PATTERNS:
        m = pattern.match(lowered)
        if not m:
            continue
        words = m.group("name").replace("’", "'").split()
        words = [w[:-2] if w.endswith("'s") else w for w in words]
        if 1 <= len(words) <= MAX_NAME_WORDS and all(_NAME_WORD.match(w) for w in words):
            if words in (["my"], ["your"], ["the"], ["club"], ["roboknights"]):
                return None
            return " ".join(words)
    return None


def _words(name):
    return re.findall(r"[a-z]+", (name or "").lower())


def find_members(name, users):
    """Every member whose name contains each typed word as a whole word."""
    wanted = _words(name)
    if not wanted:
        return []
    found = [u for u in users if set(wanted) <= set(_words(u.get("name")))]
    return sorted(found, key=lambda u: (u.get("name") or "").lower())


def _value(v):
    v = str(v).strip() if v is not None else ""
    return v or NOT_ON_FILE


def format_card(u):
    grade = u.get("grade")
    section = (u.get("section") or "").strip()
    klass = f"{grade}-{section}" if grade and section else (str(grade) if grade else NOT_ON_FILE)
    role = u.get("role")
    return "\n".join([
        f"**Name :** {_value(u.get('name'))}",
        f"**Class and section :** {klass}",
        f"**Designation :** {ROLE_LABELS.get(role, role) if role else NOT_ON_FILE}",
        f"**Phone no :** {_value(u.get('phone_no'))}",
        f"**Email :** {_value(u.get('email'))}",
        f"**Admission no :** {_value(u.get('admission_no'))}",
    ])


def format_reply(members):
    return "\n\n".join(format_card(u) for u in members)

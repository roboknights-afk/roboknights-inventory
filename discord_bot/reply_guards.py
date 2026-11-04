# Checks that run in code around the AI, not in its prompt (2026-11-05).
#
# A review of the bot's logs (Aug-Nov 2026) found the same failures the
# prompt already forbids, still happening - a prompt rule is advice a model
# can ignore, especially the weaker backup models:
#   - it said "yes" when asked if something could be posted here (it has no
#     authority to give permission - CLAUDE.md, 2026-09-23),
#   - it claimed actions it can't take ("removed your item from sale",
#     "I've got you tagged"),
#   - members asked it to count to a million or print things hundreds of
#     times, burning the club's shared AI budget,
#   - replies carried members' phone numbers, emails and admission numbers.
#
# Same split as channel_summary.py / member_details.py: pure functions here
# so test_reply_guards.py can run them without starting the bot.

import re

DASHBOARD = "https://roboknights-dashboard.streamlit.app"

# --- "Can I post X here?" -------------------------------------------------
# Only permission to POST or SHARE something in the server (the incident was
# another server's invite), or a plain "am I allowed". A question about the
# rules of a competition ("can we use a lipo battery") isn't matched.
_POST_VERB = r"(?:post|share|send|drop|put|paste|advertise|promote)"
_POST_THING = r"(?:invite|link|server|ad|ads|advert\w*|promo\w*|giveaway|survey|form|channel|youtube|insta\w*)"
PERMISSION_PATTERNS = (
    rf"\b(?:can|may|could) (?:i|we) {_POST_VERB}\b.{{0,40}}\b{_POST_THING}\b",
    r"\b(?:am i|are we) allowed to\b",
    r"\bis it (?:ok|okay|fine|alright|allowed) (?:if|to|for) (?:i|we|me|us)\b",
    r"\bdo (?:i|we) have permission\b",
)
PERMISSION_REPLY = (
    "I can't give permission for that - only a host decides what's allowed "
    "in this server. Please ask one of them first."
)


def permission_question(text):
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in PERMISSION_PATTERNS)


# --- Budget-burning requests -----------------------------------------------
_REPEAT_VERB = r"(?:print|write|say|type|repeat|spam|send|list|count)"
BUDGET_PATTERNS = (
    r"\bcount (?:from \S+ )?(?:to|till|until|upto|up to) (?:a |one )?(?:million|billion|lakh|crore|\d{3,}|\d{1,3}(?:,\d{3})+)\b",
    rf"\b{_REPEAT_VERB}\b.{{0,60}}\b(?:\d{{3,}}|hundred|thousand|million|lakh)\s*(?:times|x)\b",
    rf"\b{_REPEAT_VERB}\b.{{0,60}}\b(?:as many times as (?:you can|possible)|forever|infinitely|until i say stop|till i say stop)\b",
    r"\b(?:\d{1,3},\d{3}|\d{4,}|(?:a |one )?thousand)[- ]?words?\b",
    r"\b(?:every|all|each) (?:ascii|unicode|emoji) (?:character|char|symbol)s?\b",
    r"\ball (?:the )?(?:ascii|unicode) (?:characters|chars)\b",
)
BUDGET_REPLY = (
    "Not doing that one - it would just burn the club's shared AI budget. "
    "If you actually need it, ask me for a line of code that prints it."
)


def budget_burn_request(text):
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in BUDGET_PATTERNS)


# --- Replies that claim an action ------------------------------------------
# First person, about something only a host or the dashboard can do. Kept
# narrow on purpose: "I've updated the code below" or "I removed the delay"
# in a coding answer must NOT match, so remove/update only count when the
# object is a listing, a request, or something on the dashboard.
_I_DID = r"\bi(?:'ve| have)?(?: just| now| already)?"
_I_WILL = r"\bi(?:'ll| will)(?: now| also)?"
_DASH_OBJ = (
    r"(?:it|that|this|your|the)(?: \S+){0,3} "
    r"(?:from (?:sale|the dashboard|the list|inventory|the inventory)|on the dashboard)"
)
ACTION_CLAIM_PATTERNS = (
    rf"{_I_DID} (?:tagged|pinged|notified|forwarded|dm'?d|dmed|messaged (?:them|him|her|the))\b",
    rf"{_I_DID} (?:approved|rejected|banned|kicked|muted|timed (?:them|him|her|you) out|warned (?:them|him|her))\b",
    rf"{_I_DID} (?:signed you up|registered you|rsvp'?d|added you to)\b",
    rf"{_I_DID} (?:removed|deleted|taken down|updated|changed|marked) {_DASH_OBJ}\b",
    rf"{_I_DID} passed (?:it|this|that|your message|the message) (?:on|along)\b",
    rf"{_I_DID} let (?!you\b)(?:the )?\w+ know\b",
    rf"{_I_DID} set (?:a|the|up a|you a) reminder\b",
    rf"{_I_DID} got you tagged\b",
    rf"{_I_WILL} (?:remind (?:you|them|him|her|everyone)|ping|tag|notify|forward (?:it|this|that|your))\b",
    rf"{_I_WILL} let (?!you\b)(?:the )?\w+ know\b",
    rf"{_I_WILL} (?:pass|send) (?:it|this|that|your message|the message) (?:on|along|to)\b",
    rf"{_I_WILL} (?:sign you up|register you|rsvp|approve|reject)\b",
    rf"{_I_WILL} (?:remove|delete|update|change) {_DASH_OBJ}\b",
)
ACTION_CLAIM_REPLY = (
    "I can't make changes, pass messages on or ping anyone - I can only look "
    f"things up. For that, use the dashboard (<{DASHBOARD}>) or ask a host."
)


def claims_action(reply):
    lowered = (reply or "").lower().replace("’", "'")
    return any(re.search(p, lowered) for p in ACTION_CLAIM_PATTERNS)


# --- Contact details in a reply ---------------------------------------------
# Belt and braces: the bot's club data has no contact details any more, but
# a reply can still repeat one from the chat it was shown (a summary, a
# reply-to), or a model can invent a plausible one. Indian mobile numbers,
# admission numbers (R/E/V + 5 digits) and school addresses are always
# hidden; any other email is hidden when it's a member's (known_emails).
PHONE_RE = re.compile(r"(?<![\d-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d-])")
ADMISSION_RE = re.compile(r"\b[REVrev]\d{5}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
HIDDEN = "[hidden]"


def scrub_private(text, known_emails=()):
    if not text:
        return text
    known = {e.lower() for e in known_emails if e}

    def email(m):
        value = m.group(0)
        if value.lower().endswith("@dpsrkp.net") or value.lower() in known:
            return HIDDEN
        return value

    text = EMAIL_RE.sub(email, text)
    text = PHONE_RE.sub(HIDDEN, text)
    return ADMISSION_RE.sub(HIDDEN, text)

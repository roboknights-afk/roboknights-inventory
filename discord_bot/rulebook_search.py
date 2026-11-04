# Finding the right pages of an uploaded competition rulebook for a rules
# question (2026-11-04). The rulebooks are uploaded on the dashboard's
# Competitions page (rulebooks.py there), one row per PDF with its text
# split by page. bot.py fetches the rows; this picks the rulebook and the
# pages, with no database and no AI, so it's testable on its own (see
# test_rulebook_search.py).
#
# Plain word overlap, not embeddings: rulebooks are short, the questions
# use the rulebook's own words ("weight", "arena", "time limit"), and this
# needs no extra service or cost.

import re

STOPWORDS = {
    "the", "and", "for", "what", "whats", "what's", "how", "are", "is", "can", "our", "we", "you",
    "this", "that", "with", "from", "allowed", "rule", "rules", "rulebook", "about", "does", "there",
    "any", "max", "min", "per", "event", "competition", "comp", "tell", "me", "much", "many", "which",
}
MAX_PAGES_RETURNED = 4
MAX_PAGE_CHARS = 1800
HEADER = 'From the official rulebook - answer from these pages and say which page, e.g. "(rulebook p. 2)":'
# Spelled out (2026-11-05): with only "don't guess", the bot still answered
# rules from memory or a web search - the logs had invented, self-
# contradicting rules for a real event.
NONE_UPLOADED = (
    "(no rulebook has been uploaded on the dashboard for that competition. Tell them exactly "
    "that, point them to the competition's links from get_club_data (the brochure or website "
    "has the official rules), and do NOT state any rule from memory or from a web search)"
)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in STOPWORDS}


def pick_rulebooks(books, competitions, events, competition_query):
    # The rulebooks whose competition/event name best matches what the
    # member named; every rulebook if they named nothing that matches.
    comp_name = {c["competition_id"]: c["name"] for c in competitions}
    event_name = {e["event_id"]: e["name"] for e in events}
    wanted = _words(competition_query)
    if not wanted:
        return books
    scored = []
    for b in books:
        label = f"{comp_name.get(b['competition_id'], '')} {event_name.get(b.get('event_id'), '')} {b.get('file_name', '')}"
        scored.append((len(wanted & _words(label)), b))
    best = max((s for s, _ in scored), default=0)
    return [b for s, b in scored if s == best and s > 0] or books


def search(books, competitions, events, competition_query, question):
    # -> text for the model: the best pages, each labelled with its file
    # and page number.
    if not books:
        return NONE_UPLOADED
    chosen = pick_rulebooks(books, competitions, events, competition_query)
    comp_name = {c["competition_id"]: c["name"] for c in competitions}
    event_name = {e["event_id"]: e["name"] for e in events}
    terms = _words(question) or _words(competition_query)
    hits = []
    for b in chosen:
        for number, page in enumerate(b.get("pages") or [], 1):
            if not page.strip():
                continue
            lowered = page.lower()
            score = sum(lowered.count(t) for t in terms)
            hits.append((score, b, number, page))
    if not hits:
        return NONE_UPLOADED
    hits.sort(key=lambda h: -h[0])
    top = [h for h in hits if h[0] > 0][:MAX_PAGES_RETURNED] or hits[:2]
    parts = []
    for _score, b, number, page in top:
        where = comp_name.get(b["competition_id"], "?")
        if b.get("event_id") in event_name:
            where += f" - {event_name[b['event_id']]}"
        text = page if len(page) <= MAX_PAGE_CHARS else page[:MAX_PAGE_CHARS] + " ..."
        parts.append(f"[{b.get('file_name', 'rulebook')}, page {number} ({where})]\n{text}")
    return HEADER + "\n\n" + "\n\n".join(parts)

# AI Assistant: a chat helper that knows the logged-in member's OWN data
# (their parts, borrow requests, competitions, achievements, meetings) and
# can also answer general robotics/build questions. Uses Groq's free API
# (Llama 3.3 70B) — genuinely free, no card needed, fits the project's
# zero-cost rule (see CLAUDE.md). Gemini's free tier was tried first but
# isn't actually available to accounts in India (confirmed live: a brand
# new, unbilled Google Cloud project still came back with a hard 0 free-tier
# quota) — Groq's free tier has no such country restriction. "Unlimited
# tokens" isn't a real thing any provider offers; the free tier is capped by
# requests-per-minute/day instead, handled below with a plain try/except.
#
# Privacy: only ever built from the CURRENT user's own rows — matches the
# app's existing rule (CLAUDE.md) that no member sees another member's
# private info. Nobody else's name, parts, or requests ever go into the
# prompt.
#
# Every user/assistant turn is also logged to ai_chat_messages (2026-08-12)
# — the same table the Discord bot logs to — so a host has one shared
# record of everything either AI surface said. Best-effort: a logging
# failure never blocks the chat itself, same spirit as email sends
# elsewhere in this app.

import os
from datetime import datetime

import streamlit as st
from groq import Groq

from shared import (
    IST, AI_ASSISTANT_BANNED_EMAILS, cached_table, get_client, is_meeting_visible,
    meeting_invited_ids, meeting_invitee_rows, send_email, today_ist,
)

GROQ_MODEL = "llama-3.3-70b-versatile"
# Resending the ENTIRE conversation on every turn (the standard chat
# pattern) grows without bound in a long session — fine normally, but a
# web-search reply can be long, and Groq's compound models fold web page
# content into their own working context on top of whatever we send. Long
# enough and either side can trip Groq's request-size limit (413 Request
# Entity Too Large). Capping how much history we resend keeps our half of
# that bounded regardless of how long the chat gets.
MAX_HISTORY_MESSAGES = 16
# Groq's "compound" system is the same models above, PLUS Groq automatically
# lets it call built-in tools (web search, page visits) server-side when it
# decides a question needs current/outside info — no separate search API or
# manual RAG step to wire up ourselves. Used only when the toggle below is
# on: it's a heavier request than a plain chat completion, and most
# questions here are about the member's OWN data, which never needs the
# open internet.
COMPOUND_MODEL = "groq/compound"

current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
is_host = st.session_state.is_host

if st.session_state.auth_user["email"] in AI_ASSISTANT_BANNED_EMAILS:
    st.error(":material/block: AI Assistant access has been restricted for your account.")
    st.stop()

st.title(":material/smart_toy: AI Assistant")
st.caption(
    "Ask about your own parts, requests, competitions, or meetings — or ask "
    "a general robotics/build question. This chat isn't saved; refreshing "
    "the page clears it."
)

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    if is_host:
        st.warning(
            ":material/key_off: No `GROQ_API_KEY` is set, so the assistant can't run yet. "
            "Get a free key at [console.groq.com/keys](https://console.groq.com/keys) "
            "(no card required) and add it to `.env` as `GROQ_API_KEY=...` — same as any "
            "other secret in this app (see DEPLOY.md for Streamlit Cloud too)."
        )
    else:
        st.info(":material/build: The AI assistant isn't set up yet — check back soon.")
    st.stop()

groq_client = Groq(api_key=api_key)

if "assistant_web_search" not in st.session_state:
    # On by default: the whole point is that a plain model doesn't actually
    # know today's specific motors/sensors/parts without looking them up.
    st.session_state.assistant_web_search = True
st.toggle(
    ":material/travel_explore: Web search",
    key="assistant_web_search",
    help="Lets the assistant look things up online when it doesn't already know "
         "something — useful for specific motors, sensors, or parts. Off answers "
         "from what it already knows, a little faster.",
)


# --- Gather ONLY this member's own data ---------------------------------------

def _build_context_text():
    today = today_ist()

    parts = cached_table("parts")
    my_parts = [p for p in parts if p["owner_id"] == current_user_id]
    part_by_id = {p["part_id"]: p for p in parts}

    requests_ = cached_table("requests")
    my_borrowed = [r for r in requests_ if r["requester_id"] == current_user_id]
    pending_for_me = [
        r for r in requests_ if r["owner_id"] == current_user_id and r["status"] == "pending"
    ]

    volunteer_rows = [v for v in cached_table("event_volunteers") if v["user_id"] == current_user_id]
    events = cached_table("competition_events")
    comps = cached_table("competitions")
    event_by_id = {e["event_id"]: e for e in events}
    comp_by_id = {c["competition_id"]: c for c in comps}

    my_achievements = [a for a in cached_table("achievements") if a["user_id"] == current_user_id]

    meetings = cached_table("meetings")
    my_rsvp_ids = {
        r["meeting_id"] for r in cached_table("meeting_rsvps") if r["user_id"] == current_user_id
    }
    # Private meetings (meeting_invitees) must not reach the prompt for
    # someone who isn't invited — this page's whole privacy rule is that a
    # member's context only ever contains what that member can already see.
    invited_by_meeting = meeting_invited_ids(meeting_invitee_rows())
    upcoming_meetings = [
        m for m in meetings
        if m["meeting_date"] >= today.isoformat()
        and is_meeting_visible(m, invited_by_meeting, current_user_id, st.session_state.is_host)
    ]

    lines = [f"Today's date: {today.isoformat()}.", f"You are talking to: {current_user_name}.", ""]

    lines.append("THEIR PARTS (things they own):")
    if my_parts:
        for p in my_parts:
            lines.append(f"- {p['name']} ({p['part_number']}), status: {p['status']}")
    else:
        lines.append("- none")

    lines.append("\nTHEIR BORROW REQUESTS (parts they've requested from others):")
    if my_borrowed:
        for r in my_borrowed:
            part = part_by_id.get(r["part_id"], {})
            due = f", due {r['due_date']}" if r.get("due_date") else ""
            lines.append(f"- {part.get('name', 'Unknown')}: {r['status']}{due}")
    else:
        lines.append("- none")

    lines.append("\nREQUESTS WAITING ON THEIR APPROVAL (their parts others want to borrow):")
    if pending_for_me:
        for r in pending_for_me:
            part = part_by_id.get(r["part_id"], {})
            lines.append(f"- {part.get('name', 'Unknown')} ({part.get('part_number', '?')})")
    else:
        lines.append("- none")

    lines.append("\nTHEIR COMPETITION VOLUNTEERING:")
    if volunteer_rows:
        for v in volunteer_rows:
            event = event_by_id.get(v["event_id"])
            comp = comp_by_id.get(event["competition_id"]) if event else None
            if not event or not comp:
                continue
            status = "selected" if v.get("selected") else "volunteered (not yet selected)"
            comp_date = comp.get("competition_date") or "date TBD"
            lines.append(f"- {event['name']} at {comp['name']}: {status}, on {comp_date}")
    else:
        lines.append("- none")

    lines.append("\nTHEIR LOGGED ACHIEVEMENTS:")
    if my_achievements:
        for a in my_achievements:
            event = event_by_id.get(a["event_id"])
            comp = comp_by_id.get(a["competition_id"])
            pos = f", position: {a['position']}" if a.get("position") else ""
            lines.append(
                f"- {event['name'] if event else 'Unknown event'} at "
                f"{comp['name'] if comp else 'Unknown competition'}{pos}"
            )
    else:
        lines.append("- none")

    lines.append("\nUPCOMING MEETINGS AND THEIR RSVP:")
    if upcoming_meetings:
        for m in sorted(upcoming_meetings, key=lambda m: m["meeting_date"]):
            rsvp = "RSVP'd yes" if m["meeting_id"] in my_rsvp_ids else "no RSVP"
            lines.append(f"- {m['title']} on {m['meeting_date']}: {rsvp}")
    else:
        lines.append("- none")

    return "\n".join(lines)


SYSTEM_INSTRUCTION_TEMPLATE = """You are the RoboKnights club assistant, embedded in a member's own \
dashboard. You can see ONLY this one member's own data below — never anyone \
else's. If asked about another member's data, say you can only see their own. \
You can also help with general robotics/FTC/coding/build/competition-strategy \
questions using your own knowledge, unrelated to their account. Keep answers \
concise and friendly.

If a question has NOTHING to do with robotics, this club, or the member's \
own data below (e.g. general trivia, unrelated homework, personal advice), \
do not answer it — reply only that you can help with robotics and \
RoboKnights-related questions, and nothing else.

{context}"""


def _build_export_text():
    lines = [
        "RoboKnights AI Assistant — conversation export",
        f"Member: {current_user_name}",
        f"Exported: {datetime.now(IST).strftime('%d %b %Y, %I:%M %p IST')}",
        "",
    ]
    for m in st.session_state.assistant_messages:
        lines.append(f"[{'You' if m['role'] == 'user' else 'Assistant'}]")
        lines.append(m["content"])
        lines.append("")
    return "\n".join(lines)


RESUME_SUMMARY_PROMPT = """Summarize the conversation below into a compact briefing a NEW \
conversation can paste in to resume it naturally. Capture what was asked, what was \
answered, and any open/unfinished questions. Write it as instructions for the next \
assistant instance to read, not as a message to the member. Under 200 words."""


def _save_chat_by_email():
    transcript = _build_export_text()
    summary_response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": RESUME_SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    summary = summary_response.choices[0].message.content or "(couldn't summarize)"
    to_email = st.session_state.user_email_by_id.get(current_user_id)
    send_email(
        to_email,
        "Your RoboKnights AI Assistant chat, saved",
        f"Hi {current_user_name.split()[0]},\n\n"
        "Here's your saved AI Assistant conversation.\n\n"
        "To continue where you left off, paste the section below into a new chat with "
        "the AI Assistant:\n\n"
        "----- RESUME THIS CONVERSATION -----\n"
        f"{summary}\n"
        "----- END -----\n\n"
        "Full transcript, for your reference:\n\n"
        f"{transcript}\n"
        "— RoboKnights AI Assistant",
    )
    return to_email


def _extract_sources(response):
    # The compound models run the whole "decide to search, search, read
    # results" loop server-side — this just reads back what it actually
    # looked at, defensively (getattr everywhere) since it's undocumented
    # exactly how the SDK exposes it, and a shape mismatch here shouldn't
    # break an otherwise-successful reply.
    sources = []
    executed_tools = getattr(response.choices[0].message, "executed_tools", None) or []
    for tool in executed_tools:
        search_results = getattr(tool, "search_results", None)
        results = getattr(search_results, "results", None) or []
        for r in results:
            url = getattr(r, "url", None)
            if url:
                sources.append((getattr(r, "title", None) or url, url))
    return sources


def _render_sources(sources):
    if sources:
        with st.expander(f":material/travel_explore: {len(sources)} web source(s)"):
            for title, url in sources:
                st.markdown(f"- [{title}]({url})")


def _log_chat(role, content):
    try:
        get_client().table("ai_chat_messages").insert({
            "source": "dashboard",
            "user_id": current_user_id,
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        pass


if "assistant_messages" not in st.session_state:
    st.session_state.assistant_messages = []

if st.session_state.assistant_messages:
    button_col1, button_col2, button_col3 = st.columns([1, 1, 1])
    with button_col1:
        if st.button("Clear conversation", icon=":material/delete_sweep:", type="tertiary"):
            st.session_state.assistant_messages = []
            st.rerun()
    with button_col2:
        st.download_button(
            "Export chat",
            data=_build_export_text(),
            file_name=f"roboknights_ai_chat_{today_ist().isoformat()}.txt",
            mime="text/plain",
            icon=":material/download:",
            type="tertiary",
        )
    with button_col3:
        if st.button(
            "Save chat", icon=":material/mail:", type="tertiary",
            help="Emails you a summary you can paste into a new chat to resume this one, "
                 "plus the full transcript.",
        ):
            with st.spinner("Summarizing and emailing your chat…"):
                try:
                    sent_to = _save_chat_by_email()
                    st.toast(f"Emailed to {sent_to}!", icon=":material/mail:")
                except Exception as e:
                    st.error(f"Couldn't save chat: {e}")

for msg in st.session_state.assistant_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        _render_sources(msg.get("sources"))

prompt = st.chat_input("Ask something…")
if prompt:
    st.session_state.assistant_messages.append({"role": "user", "content": prompt})
    _log_chat("user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    web_search_enabled = st.session_state.assistant_web_search
    with st.chat_message("assistant"):
        sources = []
        with st.spinner("Searching and thinking…" if web_search_enabled else "Thinking…"):
            recent_messages = st.session_state.assistant_messages[-MAX_HISTORY_MESSAGES:]
            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_INSTRUCTION_TEMPLATE.format(context=_build_context_text()),
                },
                *({"role": m["role"], "content": m["content"]} for m in recent_messages),
            ]
            try:
                if web_search_enabled:
                    response = groq_client.chat.completions.create(
                        model=COMPOUND_MODEL, messages=messages,
                        # Scoped to search/browsing only — code_interpreter and
                        # wolfram_alpha aren't relevant here and would just be
                        # more that could go wrong for no benefit to this app.
                        compound_custom={"tools": {"enabled_tools": ["web_search", "visit_website"]}},
                    )
                    sources = _extract_sources(response)
                else:
                    response = groq_client.chat.completions.create(model=GROQ_MODEL, messages=messages)
                reply = response.choices[0].message.content or "I didn't get a response — try asking again."
            except Exception as e:
                if web_search_enabled:
                    # Confirmed directly against Groq's API (outside this
                    # app entirely) that this is a real, current limitation
                    # on THEIR compound/web-search pipeline, not a bug here:
                    # some searches pull in enough page content that Groq's
                    # own request hits a size limit (413) server-side,
                    # regardless of how little we send or which of
                    # web_search/visit_website is enabled — a short factual
                    # search succeeds every time, one needing more page
                    # content (a specific product page, "what's today's
                    # date") reliably doesn't. groq/compound-mini hits the
                    # identical failure on the identical query, so it isn't
                    # a matter of picking a lighter model either. No
                    # documented parameter exists to cap how much a search
                    # result pulls in. Retry once without search so the
                    # question still gets a real answer either way.
                    try:
                        response = groq_client.chat.completions.create(model=GROQ_MODEL, messages=messages)
                        reply = (
                            "*(Couldn't search the web for this one — Groq's search hit its own "
                            "size limit on this question. Answered from what I already know instead.)*\n\n"
                            + (response.choices[0].message.content or "I didn't get a response — try asking again.")
                        )
                    except Exception as e2:
                        # Members get a short, human sentence - NOT the raw
                        # provider error. Dumping that here (what this did
                        # before) pasted the org id and a billing URL
                        # straight into the chat - same problem the Discord
                        # bot already fixed for its own errors. The full
                        # detail still goes to the server logs (Streamlit
                        # Cloud's "Manage app" logs), where it's actually
                        # useful for debugging.
                        print(f"AI Assistant error (web search retry): {e2!r}", flush=True)
                        reply = (
                            "I'm maxed out on my daily AI usage limit right now, so I can't "
                            "answer this one. It resets on its own - try again a bit later."
                        )
                else:
                    print(f"AI Assistant error: {e!r}", flush=True)
                    reply = (
                        "I'm maxed out on my daily AI usage limit right now, so I can't "
                        "answer this one. It resets on its own - try again a bit later."
                    )
            st.markdown(reply)
            _render_sources(sources)
    st.session_state.assistant_messages.append({"role": "assistant", "content": reply, "sources": sources})
    _log_chat("assistant", reply)

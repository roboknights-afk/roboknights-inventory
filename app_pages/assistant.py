# AI Assistant: a chat helper that knows the logged-in member's OWN data
# (their parts, borrow requests, competitions, achievements, meetings) and
# can also answer general robotics/build questions. Uses Gemini's free API
# tier (gemini-2.0-flash) — genuinely free, no card needed, fits the
# project's zero-cost rule (see CLAUDE.md). "Unlimited tokens" isn't a real
# thing any provider offers; the free tier is capped by requests-per-
# minute/day instead, handled below with a plain try/except.
#
# Privacy: only ever built from the CURRENT user's own rows — matches the
# app's existing rule (CLAUDE.md) that no member sees another member's
# private info. Nobody else's name, parts, or requests ever go into the
# prompt. Conversation history is session-only (not saved to Supabase) —
# refreshing the page clears it, same as any other unsaved chat.

import os
from datetime import date

import streamlit as st

from shared import cached_table, today_ist

GEMINI_MODEL = "gemini-2.0-flash"

current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
is_host = st.session_state.is_host

st.title(":material/smart_toy: AI Assistant")
st.caption(
    "Ask about your own parts, requests, competitions, or meetings — or ask "
    "a general robotics/build question. This chat isn't saved; refreshing "
    "the page clears it."
)

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    if is_host:
        st.warning(
            ":material/key_off: No `GEMINI_API_KEY` is set, so the assistant can't run yet. "
            "Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) "
            "(no card required) and add it to `.env` as `GEMINI_API_KEY=...` — same as any "
            "other secret in this app (see DEPLOY.md for Streamlit Cloud/GitHub Actions too)."
        )
    else:
        st.info(":material/build: The AI assistant isn't set up yet — check back soon.")
    st.stop()


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
    upcoming_meetings = [m for m in meetings if m["meeting_date"] >= today.isoformat()]

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

{context}"""

if "assistant_messages" not in st.session_state:
    st.session_state.assistant_messages = []

if st.session_state.assistant_messages and st.button(
    "Clear conversation", icon=":material/delete_sweep:", type="tertiary"
):
    st.session_state.assistant_messages = []
    st.rerun()

for msg in st.session_state.assistant_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask something…")
if prompt:
    st.session_state.assistant_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                from google import genai
                from google.genai import types

                gemini_client = genai.Client(api_key=api_key)
                contents = [
                    types.Content(
                        role=("model" if m["role"] == "assistant" else "user"),
                        parts=[types.Part(text=m["content"])],
                    )
                    for m in st.session_state.assistant_messages
                ]
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION_TEMPLATE.format(
                            context=_build_context_text()
                        ),
                    ),
                )
                reply = response.text or "I didn't get a response — try asking again."
            except Exception as e:
                # Same best-effort spirit as the rest of this app: a free-tier
                # rate limit or network hiccup shouldn't crash the page, just
                # show up as a plain inline message.
                reply = f"Sorry, I couldn't get a response right now ({e})."
            st.markdown(reply)
    st.session_state.assistant_messages.append({"role": "assistant", "content": reply})

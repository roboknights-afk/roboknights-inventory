# Who owes what for a competition's registration fee (2026-11-13).
#
# The rule, from the host: every event costs the same fee (NYRC: 1500), and
# it is split evenly between everyone selected for that event. A member's
# total is the sum of their shares across every event they're selected for.
# Sister-club students picked for an event (event_guest_selections) count
# towards the split and owe their share too.
#
# Kept free of Streamlit and Supabase so the Competitions page and one-off
# email scripts use exactly the same arithmetic.

from math import ceil


def event_share(fee, people):
    # Rounded UP to the whole rupee, so the club is never short when a fee
    # doesn't divide evenly (1500 / 7 -> 215, not 214.29).
    return ceil(float(fee) / people) if people else 0


def compute_shares(fee, events, volunteers, guests, disabled_user_ids=()):
    """Returns {"user:<user_id>" | "guest:<adm>": [(event, people, share), ...]}.

    events: competition_events rows for ONE competition.
    volunteers: event_volunteers rows (only selected ones are counted).
    guests: event_guest_selections rows.
    """
    if not fee:
        return {}
    disabled = set(disabled_user_ids)
    owed = {}
    for e in sorted(events, key=lambda e: e["name"]):
        members = [
            v["user_id"] for v in volunteers
            if v["event_id"] == e["event_id"] and v.get("selected") and v["user_id"] not in disabled
        ]
        guest_adms = [g["adm"] for g in guests if g["event_id"] == e["event_id"]]
        people = len(members) + len(guest_adms)
        if not people:
            continue
        share = event_share(fee, people)
        for uid in members:
            owed.setdefault(f"user:{uid}", []).append((e, people, share))
        for adm in guest_adms:
            owed.setdefault(f"guest:{adm}", []).append((e, people, share))
    return owed


def total(rows):
    return sum(share for _, _, share in rows)


def rupees(amount):
    # 1100 -> "₹1,100", 212.5 -> "₹212.50"
    amount = float(amount)
    return f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"

# Checks for "@bot details of X" (discord_bot/member_details.py): what counts
# as a details request, strict name matching, and the card format. No Discord,
# no database, no AI. Names below are made up.
#
#   python test_member_details.py

import sys

sys.path.insert(0, "discord_bot")
import member_details as md  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


for text, want in [
    ("details of Testy Kumar", "testy kumar"),
    ("give me details of testy", "testy"),
    ("gimme the contact details for Fakey Sharma pls", "fakey sharma"),
    ("phone number of testy?", "testy"),
    ("Testy Kumar details", "testy kumar"),
    ("testy's phone number", "testy"),
    ("pls send fakey sharma's contact", "fakey sharma"),
    ("info about Testy", "testy"),
    ("details of testy kumar bhai", "testy kumar"),
    # not a details request -> None, goes to the assistant as before
    ("what are the details of the robowar rules for force fest", None),
    ("details of the nyrc competition 2027", None),
    ("my details", None),
    ("summarise", None),
    ("what is a p219 motor", None),
    ("", None),
]:
    check(f"parse {text!r}", md.parse_request(text), want)

users = [
    {"name": "Testy Kumar", "grade": 11, "section": "Q", "role": "core_member",
     "phone_no": "9000000001", "email": "testy@example.com", "admission_no": "R11111"},
    {"name": "Testy Verma", "grade": 9, "section": "", "role": "member", "phone_no": None,
     "email": "verma@example.com", "admission_no": ""},
    {"name": "Fakey Sharma", "grade": None, "section": None, "role": None},
]
check("one match", [u["name"] for u in md.find_members("testy kumar", users)], ["Testy Kumar"])
check("two matches", [u["name"] for u in md.find_members("testy", users)], ["Testy Kumar", "Testy Verma"])
check("no partial words", md.find_members("test", users), [])
check("no match", md.find_members("nobody here", users), [])

check("full card", md.format_card(users[0]),
      "**Name :** Testy Kumar\n**Class and section :** 11-Q\n**Designation :** Core\n"
      "**Phone no :** 9000000001\n**Email :** testy@example.com\n**Admission no :** R11111")
check("blanks", md.format_card(users[1]),
      "**Name :** Testy Verma\n**Class and section :** 9\n**Designation :** Member\n"
      "**Phone no :** Not on file\n**Email :** verma@example.com\n**Admission no :** Not on file")
check("all blank", md.format_card(users[2]).count("Not on file"), 5)
check("two cards", md.format_reply(users[:2]).count("**Name :**"), 2)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("all member details checks passed")

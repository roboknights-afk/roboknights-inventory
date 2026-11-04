# Checks for discord_bot/reply_guards.py: permission questions, budget-burning
# requests, replies claiming an action, and contact details in replies. No
# Discord, no database, no AI. Phrasings are modelled on the kinds of message
# the bot's logs showed (paraphrased); every name and number is made up.
#
#   python test_reply_guards.py

import sys

sys.path.insert(0, "discord_bot")
import reply_guards as rg  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


for text, want in [
    ("can i post an invite to my other discord server here?", True),
    ("Can we share a link to our youtube channel", True),
    ("am i allowed to advertise my tournament", True),
    ("is it okay if i send a survey form here", True),
    ("do i have permission to do that", True),
    # not permission questions
    ("can we use a lipo battery in robowar", False),
    ("can i post my code here so you can check it", False),
    ("what's the max bot weight for nyrc", False),
    ("can you share the brochure link for robotronics", False),
]:
    check(f"permission {text!r}", rg.permission_question(text), want)

for text, want in [
    ("count to a million", True),
    ("count from 1 to 10000", True),
    ("print hello 500 times", True),
    ("say my name a thousand times", True),
    ("repeat the alphabet as many times as you can", True),
    ("write a 2000 word essay on robots", True),
    ("write a 1,500-word story", True),
    ("list every ascii character", True),
    # normal questions
    ("count the motors in the inventory", False),
    ("write a 200 word summary of nyrc", False),
    ("print hello world in python", False),
    ("how many times has the club won robowar", False),
    ("what is a 12v motor", False),
]:
    check(f"budget {text!r}", rg.budget_burn_request(text), want)

for reply, want in [
    ("I removed your L298N Motor Driver from sale as per your request.", True),
    ("Done! I've got you tagged.", True),
    ("I've pinged the hosts about it.", True),
    ("I'll let the hosts know.", True),
    ("I’ll remind everyone tomorrow.", True),
    ("I've signed you up for RoboWar.", True),
    ("I will pass your message on to Testy.", True),
    ("I've set a reminder for you.", True),
    # ordinary answers
    ("I've updated the code below so the motor stops at the line.", False),
    ("I removed the delay() call, which was blocking the loop.", False),
    ("I can't make changes - use the dashboard.", False),
    ("I'll let you know what I find in the rulebook: page 4 says 3 kg.", False),
    ("I'll explain how a PID loop works.", False),
    ("You can remove it from sale on the Inventory page.", False),
]:
    check(f"claim {reply!r}", rg.claims_action(reply), want)

check("phone", rg.scrub_private("call 9876543210 or +91 98765 43210"),
      "call [hidden] or [hidden]")
check("admission", rg.scrub_private("admission no R12345, e54321"), "admission no [hidden], [hidden]")
check("school email", rg.scrub_private("mail testy@dpsrkp.net"), "mail [hidden]")
check("member email", rg.scrub_private("mail fake.kumar@gmail.com", ["Fake.Kumar@gmail.com"]), "mail [hidden]")
check("other email kept", rg.scrub_private("mail info@robotronics.example"), "mail info@robotronics.example")
check("discord id kept", rg.scrub_private("<@123456789012345678>"), "<@123456789012345678>")
check("part number kept", rg.scrub_private("RK-0007 and P219, 12V, 6000 rpm"), "RK-0007 and P219, 12V, 6000 rpm")
check("date kept", rg.scrub_private("on 2026-11-30 at 09:30"), "on 2026-11-30 at 09:30")
check("empty", rg.scrub_private(""), "")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("all reply guard checks passed")

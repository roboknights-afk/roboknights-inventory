---
title: RoboKnights Discord Bot
emoji: 🤖
colorFrom: yellow
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# RoboKnights Discord bot

Not a demo — this is the club's actual Discord bot (`bot.py`), deployed
here because Hugging Face Spaces gives a genuinely free, card-free,
always-on container (see `../DEPLOY.md`'s "Deploying it" section for the
full story and the other hosting options that were ruled out first).

The YAML block above is required by Hugging Face itself — it's how a
Space knows to build this as a Docker container (`sdk: docker`) rather
than treat this file as a plain readme, and `app_port: 7860` is where
`bot.py`'s keepalive HTTP server listens (see `_start_keepalive_server()`
there) so an external uptime pinger can stop the Space sleeping after 48h
idle.

Set this Space to **private** in its settings — nothing here should be
public, since its secrets ultimately grant Supabase access to real club
member data.

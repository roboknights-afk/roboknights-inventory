#!/usr/bin/env bash
# One-time setup for the RoboKnights Discord bot on a fresh Oracle Cloud
# Always Free Ubuntu VM. Run this once, over SSH, right after creating the
# instance - see DEPLOY.md's "Deploying it (Oracle Cloud, Always Free)"
# section for the console steps that come before and after this script.
#
# What it does: installs Python + a venv, creates the directory the
# GitHub Actions deploy workflow rsyncs the bot's code into, and installs
# a systemd --user unit so the bot survives crashes and reboots. It does
# NOT install the bot's code or start the service - the code arrives via
# that same deploy workflow (push to master, or run it by hand from the
# Actions tab), and the service can't start until ~/roboknights-bot/.env
# exists with real secrets in it, which only you can create.
set -euo pipefail

echo "Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip rsync

echo "Creating ~/roboknights-bot ..."
mkdir -p ~/roboknights-bot
cd ~/roboknights-bot
python3 -m venv venv

echo "Installing the systemd --user unit..."
mkdir -p ~/.config/systemd/user
cp "$(dirname "$0")/roboknights-bot.service" ~/.config/systemd/user/roboknights-bot.service
systemctl --user daemon-reload
systemctl --user enable roboknights-bot

# Without this, the --user service dies the moment this SSH session ends.
# Needs sudo once, here; every later restart (from the deploy workflow)
# does not.
sudo loginctl enable-linger "$USER"

cat <<'EOF'

Done. Two things left before the bot can actually start:

  1. Create ~/roboknights-bot/.env with the same secrets listed in
     DEPLOY.md's Discord bot section (DISCORD_BOT_TOKEN, GROQ_API_KEY,
     SUPABASE_URL, SUPABASE_KEY, and the rest).

  2. Push to the repo (or run the "Deploy Discord bot" workflow by hand
     from the Actions tab) - that copies bot.py over, installs its
     requirements, and starts the service for the first time.

Check on it later with:
  systemctl --user status roboknights-bot
  journalctl --user -u roboknights-bot -f
EOF

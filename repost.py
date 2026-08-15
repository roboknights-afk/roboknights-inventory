import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests
from dotenv import load_dotenv
from supabase import create_client
load_dotenv(r"C:\Users\gogof\roboknights-inventory\.env")

OLD_ID = "1538169365353078784"
USERNAME = "roboknightsbot"
AVATAR = "https://cdn.discordapp.com/avatars/1536836032329416724/5ebc6d79217e395322b1faf5107e095f.png"

c = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
row = c.table("discord_messages").select("*").eq("message_id", OLD_ID).single().execute().data
content = row["content"]
assert "Bot update" in content and "**5." in content, "unexpected content - aborting"
print("reposting", len(content), "chars")

url = os.environ["DISCORD_DASHBOARD_WEBHOOK_URL"]
r = requests.post(url + "?wait=true",
                  json={"content": content, "username": USERNAME, "avatar_url": AVATAR}, timeout=15)
r.raise_for_status()
new_id = r.json()["id"]
print("new message id:", new_id)

d = requests.delete(f"{url}/messages/{OLD_ID}", timeout=15)
print("delete old:", d.status_code)
d.raise_for_status()

c.table("discord_messages").update({"message_id": new_id}).eq("message_id", OLD_ID).execute()
print("db row repointed to new message")

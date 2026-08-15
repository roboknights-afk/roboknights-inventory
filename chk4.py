import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv
from supabase import create_client
load_dotenv(r"C:\Users\gogof\roboknights-inventory\.env")
c = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
rows = c.table("ai_chat_messages").select("*").gte("created_at","2026-08-15T12:30:00+00:00").order("created_at").execute().data
for r in rows:
    print(r.get("created_at")[:19], "|", r.get("role"), "|", str(r.get("discord_user_id")), "|", (r.get("content") or "")[:120].replace("\n"," "))
print("TOTAL", len(rows))

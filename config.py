import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing. Put it in .env")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID missing. Put it in .env")

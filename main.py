import os
import re
import time
import json
import random
import sqlite3
from collections import defaultdict, deque
from typing import Dict, Any, List, Tuple, Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from config import BOT_TOKEN, ADMIN_ID
from memory import get_user, update_interaction  # keep your existing memory.py
from delay_engine import human_delay
from style_engine import apply_style


# =========================
# TEACHING + STYLE DB
# =========================
STYLE_DB = "style_teacher.db"

def _db():
    conn = sqlite3.connect(STYLE_DB, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS style_profile (
        id INTEGER PRIMARY KEY CHECK (id=1),
        profile_json TEXT NOT NULL
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS learned_pairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        response TEXT NOT NULL
    )
    """)
    conn.commit()
    return conn

_CONN = _db()

DEFAULT_PROFILE = {
    # overall vibe knobs
    "emoji_level": 0.6,          # 0..1
    "linebreak_level": 0.7,      # 0..1
    "tease_level": 0.55,         # 0..1
    "shy_level": 0.35,           # 0..1
    "romantic_level": 0.5,       # 0..1
    "energy_level": 0.6,         # 0..1

    # signature tokens (you can teach these)
    "fav_emojis": ["😂", "😏", "❤️", "🖤"],
    "fav_reacts": ["Awwwn", "Okayyy", "Really", "Yay", "Lol", "Mhm", "Huh"],
    "fav_endings": ["Go on", "Tell me", "And then", "Say more", "Wym"],
}

def load_profile() -> Dict[str, Any]:
    cur = _CONN.execute("SELECT profile_json FROM style_profile WHERE id=1")
    row = cur.fetchone()
    if not row:
        _CONN.execute("INSERT INTO style_profile (id, profile_json) VALUES (1, ?)", (json.dumps(DEFAULT_PROFILE),))
        _CONN.commit()
        return dict(DEFAULT_PROFILE)
    try:
        return json.loads(row[0])
    except Exception:
        return dict(DEFAULT_PROFILE)

def save_profile(p: Dict[str, Any]):
    _CONN.execute("UPDATE style_profile SET profile_json=? WHERE id=1", (json.dumps(p),))
    _CONN.commit()

def add_pair(key: str, response: str):
    key = key.strip().lower()
    response = response.strip()
    if not key or not response:
        return
    _CONN.execute("INSERT INTO learned_pairs (key, response) VALUES (?,?)", (key, response))
    _CONN.commit()

def find_pair(user_text: str) -> Optional[str]:
    """
    Very simple retrieval:
    - if user text contains a learned key phrase -> return the learned response
    """
    t = (user_text or "").lower()
    cur = _CONN.execute("SELECT key, response FROM learned_pairs ORDER BY id DESC LIMIT 120")
    for k, r in cur.fetchall():
        if k and k in t:
            return r
    return None


# =========================
# ADMIN + RUNTIME STATE
# =========================
PAUSED = False

# anti-spam
_last_ts = defaultdict(float)
_burst = defaultdict(lambda: deque(maxlen=7))

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


# =========================
# VIBE DETECTION (USER)
# =========================
SAD_WORDS = {"sad", "tired", "lonely", "depressed", "cry", "hurt", "stress", "stressed", "down", "broken"}
SWEET_WORDS = {"miss", "missed", "love", "baby", "babe", "sweet", "honey", "darling", "cute"}
ANGRY_WORDS = {"angry", "mad", "annoyed", "pissed", "hate"}

def detect_vibe(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    t = raw.lower()

    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF]", raw))
    exclam = raw.count("!")
    qmarks = raw.count("?")
    caps = sum(1 for c in raw if c.isupper())

    words = re.findall(r"[a-z']+", t)
    wset = set(words)

    vibe = "playful"
    if wset & SAD_WORDS:
        vibe = "soft"
    elif wset & ANGRY_WORDS:
        vibe = "serious"
    elif wset & SWEET_WORDS:
        vibe = "romantic"
    elif qmarks > 0:
        vibe = "curious"

    energy = 0.4
    energy += min(0.25, exclam * 0.05)
    energy += min(0.20, emoji_count * 0.04)
    energy += 0.10 if caps >= 6 else 0.0
    energy += 0.10 if len(raw) <= 7 else 0.0
    energy = max(0.1, min(1.0, energy))

    return {"vibe": vibe, "energy": energy, "emoji_count": emoji_count, "qmarks": qmarks}


# =========================
# STYLE HELPERS
# =========================
def pick_not_repeat(options: List[str], last: List[str]) -> str:
    last_set = set(last[-8:])
    pool = [o for o in options if o not in last_set]
    return random.choice(pool) if pool else random.choice(options)

def maybe_emoji(profile: Dict[str, Any], intensity: float = 1.0) -> str:
    if random.random() < profile.get("emoji_level", 0.6) * intensity:
        fav = profile.get("fav_emojis", ["😂"])
        return random.choice(fav)
    return ""

def linebreak(profile: Dict[str, Any]) -> str:
    return "\n\n" if random.random() < profile.get("linebreak_level", 0.7) else " "

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# =========================
# RESPONSE ENGINE (RULE-BASED BUT ALIVE)
# =========================
def generate_response(user_text: str, chat_state: Dict[str, Any], profile: Dict[str, Any]) -> str:
    """
    - mirrors vibe
    - reacts + pulls user deeper
    - uses profile (taught)
    - avoids repeats
    - supports chat_state mode overrides
    """
    raw = (user_text or "").strip()
    t = raw.lower()
    last_replies = chat_state.setdefault("last_replies", [])

    # 1) Learned pair match (admin taught)
    learned = find_pair(raw)
    if learned:
        # keep it from feeling copy/paste: sometimes add tiny react
        if random.random() < 0.35:
            r = pick_not_repeat(profile.get("fav_reacts", ["Okayyy"]), last_replies)
            learned = f"{r}{maybe_emoji(profile, 0.9)}{linebreak(profile)}{learned}"
        return learned

    # 2) Vibe detection
    dv = detect_vibe(raw)
    user_vibe = dv["vibe"]
    user_energy = dv["energy"]

    # 3) If admin set a forced mode for this chat, respect it
    forced_mode = chat_state.get("mode")  # None | playful | shy | romantic | serious | soft
    mode = forced_mode or user_vibe

    # 4) Greeting / small talk
    if re.search(r"\b(hi|hey|hello|hii|heyy|yo)\b", t):
        opts = ["Heyy", "Hii", "Hi hi", "Hey"]
        out = pick_not_repeat(opts, last_replies) + maybe_emoji(profile, 1.0)
        return out

    if "how are you" in t or "how r you" in t or "how you" in t:
        opts = [
            f"Chilling{maybe_emoji(profile, 1.0)}{linebreak(profile)}You",
            f"Good{maybe_emoji(profile, 1.0)}{linebreak(profile)}Wbu",
            f"I’m okay{linebreak(profile)}You good{maybe_emoji(profile, 0.8)}",
        ]
        return pick_not_repeat(opts, last_replies)

    # 5) Sweet triggers
    if "miss you" in t or "missed you" in t:
        opts = [
            f"Awwwn{maybe_emoji(profile,1.0)}{linebreak(profile)}I missed you too{linebreak(profile)}Where you been{maybe_emoji(profile,0.9)}",
            f"Hehe{maybe_emoji(profile,1.0)}{linebreak(profile)}Come here{linebreak(profile)}Tell me what’s up",
            f"Yay{maybe_emoji(profile,1.0)}{linebreak(profile)}I like that{linebreak(profile)}So how was your day{maybe_emoji(profile,0.7)}",
        ]
        return pick_not_repeat(opts, last_replies)

    # 6) “Can I tell you…”
    if "can i tell you" in t or "let me tell you" in t or "i want to tell you" in t:
        opts = [
            f"Yes pls{maybe_emoji(profile,1.0)}{linebreak(profile)}Tell me everything",
            f"Go onnn{maybe_emoji(profile,1.0)}{linebreak(profile)}I’m listening",
            f"Say it{linebreak(profile)}I’m here{maybe_emoji(profile,0.9)}",
        ]
        return pick_not_repeat(opts, last_replies)

    # 7) Question handling (bounce back)
    if "?" in raw:
        reacts = profile.get("fav_reacts", ["Really", "Okayyy", "Mhm", "Lol"])
        endings = profile.get("fav_endings", ["Wym", "Tell me", "Why tho", "How so"])
        react = pick_not_repeat([r + maybe_emoji(profile, 0.9) for r in reacts], last_replies)
        end = random.choice(endings) + maybe_emoji(profile, 0.8)

        # mode flavor
        if mode == "shy":
            end = random.choice(["Umm", "Wym", "Tell me pls", "I’m listening"]) + maybe_emoji(profile, 0.9)
        elif mode == "romantic":
            end = random.choice(["Tell me baby", "Say it to me", "I’m here", "Talk to me"]) + maybe_emoji(profile, 0.8)
        elif mode in ("serious", "soft"):
            end = random.choice(["Tell me", "What happened", "I’m listening", "Explain it"]) + maybe_emoji(profile, 0.5)

        return f"{react}{linebreak(profile)}{end}"

    # 8) Short messages -> quick but alive
    if len(t) <= 7:
        opts = [
            f"Yep{maybe_emoji(profile, 0.9)}",
            f"Really{maybe_emoji(profile, 1.0)}",
            f"Okayyy{maybe_emoji(profile, 1.0)}",
            f"Huh{maybe_emoji(profile, 0.9)}",
            f"Lol{maybe_emoji(profile, 1.0)}",
        ]
        out = pick_not_repeat(opts, last_replies)

        # pull them deeper sometimes
        if random.random() < 0.55:
            nudge = random.choice(profile.get("fav_endings", ["Go on", "Tell me", "Say more"]))
            out = f"{out}{linebreak(profile)}{nudge}{maybe_emoji(profile, 0.7)}"
        return out

    # 9) Mode-based “alive” default response (reaction + pull)
    # energy drives teasing level
    tease = clamp01(profile.get("tease_level", 0.55) * (0.8 + user_energy))
    shy = clamp01(profile.get("shy_level", 0.35) * (1.0 - user_energy * 0.3))
    romantic = clamp01(profile.get("romantic_level", 0.5) * (0.8 + (1.0 if user_vibe == "romantic" else 0.0)))

    react_word = pick_not_repeat(profile.get("fav_reacts", ["Okayyy", "Awwwn", "Lol", "Mhm"]), last_replies)
    react = react_word + maybe_emoji(profile, 0.9)

    if mode == "soft":
        pulls = [
            f"Talk to me{maybe_emoji(profile,0.6)}",
            f"I’m listening{maybe_emoji(profile,0.4)}",
            "Come here",
            "What happened",
        ]
        out = f"{react}{linebreak(profile)}{pick_not_repeat(pulls, last_replies)}"
        return out

    if mode == "serious":
        pulls = [
            "Explain it to me",
            "Tell me properly",
            "What’s the real issue",
            "Okay\n\nGo on",
        ]
        out = f"{react}{linebreak(profile)}{pick_not_repeat(pulls, last_replies)}"
        return out

    if mode == "shy":
        pulls = [
            f"Umm{maybe_emoji(profile,0.9)}{linebreak(profile)}Tell me",
            f"Hehe{maybe_emoji(profile,0.9)}{linebreak(profile)}Go on",
            f"Okayyy{maybe_emoji(profile,1.0)}{linebreak(profile)}Say more",
            f"Stoppp{maybe_emoji(profile,1.0)}{linebreak(profile)}And then",
        ]
        # shy sprinkle
        if random.random() < shy:
            pulls.append(f"I’m kinda shy{maybe_emoji(profile,1.0)}{linebreak(profile)}But tell me")
        return pick_not_repeat(pulls, last_replies)

    if mode == "romantic":
        pulls = [
            f"Awwwn{maybe_emoji(profile,1.0)}{linebreak(profile)}Tell me baby",
            f"Come here{maybe_emoji(profile,0.8)}{linebreak(profile)}Talk to me",
            f"Mhm{maybe_emoji(profile,1.0)}{linebreak(profile)}I want to hear it",
            f"Okayyy{maybe_emoji(profile,0.9)}{linebreak(profile)}Go on",
        ]
        if random.random() < tease:
            pulls.append(f"Mm{maybe_emoji(profile,1.0)}{linebreak(profile)}You’re trouble😏")
        return pick_not_repeat(pulls, last_replies)

    # playful default (naughty-but-safe teasing)
    pulls = [
        f"Lol{maybe_emoji(profile,1.0)}{linebreak(profile)}And then",
        f"Okayyy{maybe_emoji(profile,1.0)}{linebreak(profile)}Go on",
        f"Mhm{maybe_emoji(profile,1.0)}{linebreak(profile)}Say more",
        f"Really{maybe_emoji(profile,1.0)}{linebreak(profile)}Tell me",
    ]
    if random.random() < tease:
        pulls.append(f"Stoppp{maybe_emoji(profile,1.0)}{linebreak(profile)}You’re funny😂")
        pulls.append(f"Okayyy😏{linebreak(profile)}So what you want from me😂")
    return pick_not_repeat(pulls, last_replies)


# =========================
# ADMIN TEACHING (PASTE CHATS)
# =========================
TRAIN_HELP = (
    "Teaching mode:\n"
    "/teach_on — start\n"
    "/teach_off — stop\n\n"
    "Send training as text.\n"
    "Best format:\n"
    "U: hi\n"
    "ME: heyy😂\n"
    "U: i missed you\n"
    "ME: awwwn😂 come here\n\n"
    "I learn pairs from U -> ME.\n"
)

def parse_training_block(block: str) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """
    Extracts (user -> reply) pairs and updates style profile stats.
    Format expected:
      U: ...
      ME: ...
    """
    lines = [ln.strip() for ln in (block or "").splitlines() if ln.strip()]
    pairs = []
    me_lines = []

    last_u = None
    for ln in lines:
        if ln.lower().startswith("u:"):
            last_u = ln[2:].strip()
        elif ln.lower().startswith("me:") and last_u:
            me = ln[3:].strip()
            pairs.append((last_u, me))
            me_lines.append(me)
            last_u = None

    # Style stats from ME lines
    all_text = " ".join(me_lines)
    emojis = re.findall(r"[\U0001F300-\U0001FAFF]", all_text)
    fav_emojis = [e for e in emojis if e]  # may be many
    # keep top 6
    freq = {}
    for e in fav_emojis:
        freq[e] = freq.get(e, 0) + 1
    top_emojis = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:6]
    top_emojis = [e for e, _ in top_emojis] if top_emojis else DEFAULT_PROFILE["fav_emojis"]

    reacts = []
    for m in me_lines:
        # collect first word-ish react
        first = re.split(r"\s+", m.strip())[0]
        if 2 <= len(first) <= 12:
            reacts.append(first)

    react_freq = {}
    for r in reacts:
        react_freq[r] = react_freq.get(r, 0) + 1
    top_reacts = [r for r, _ in sorted(react_freq.items(), key=lambda x: x[1], reverse=True)[:8]]
    if not top_reacts:
        top_reacts = DEFAULT_PROFILE["fav_reacts"]

    # emoji level estimate
    emoji_level = 0.4
    if me_lines:
        emoji_level = min(1.0, len(emojis) / max(1, len(me_lines)) * 0.35 + 0.35)

    # linebreak tendency
    lb_count = sum(1 for m in me_lines if "\n" in m)
    linebreak_level = 0.55 + (0.2 if lb_count > 0 else 0.0)

    stats = {
        "fav_emojis": top_emojis,
        "fav_reacts": top_reacts,
        "emoji_level": clamp01(emoji_level),
        "linebreak_level": clamp01(linebreak_level),
    }
    return pairs, stats


# =========================
# HANDLERS
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED
    if not update.message or not update.message.text:
        return
    if PAUSED:
        return

    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""
    text = update.message.text

    # Anti-spam burst protection
    now = time.time()
    _burst[chat_id].append(now)
    if len(_burst[chat_id]) >= 7 and (now - _burst[chat_id][0]) < 7:
        return
    if now - _last_ts[chat_id] < 0.25:
        return
    _last_ts[chat_id] = now

    # Basic user memory
    get_user(chat_id, username)
    update_interaction(chat_id)

    # Admin teaching mode
    if is_admin(update) and context.chat_data.get("teach_on"):
        pairs, stats = parse_training_block(text)
        profile = load_profile()

        # update profile gently (blend)
        profile["emoji_level"] = clamp01(profile.get("emoji_level", 0.6) * 0.7 + stats["emoji_level"] * 0.3)
        profile["linebreak_level"] = clamp01(profile.get("linebreak_level", 0.7) * 0.7 + stats["linebreak_level"] * 0.3)

        # merge emoji/reacts
        profile["fav_emojis"] = list(dict.fromkeys(stats["fav_emojis"] + profile.get("fav_emojis", [])))[:8]
        profile["fav_reacts"] = list(dict.fromkeys(stats["fav_reacts"] + profile.get("fav_reacts", [])))[:10]

        save_profile(profile)

        # store pairs (use short key phrases)
        learned_count = 0
        for u, me in pairs:
            key = _make_key_phrase(u)
            if key:
                add_pair(key, me)
                learned_count += 1

        await update.message.reply_text(f"Learned {learned_count} pairs ✅")
        return

    # Normal conversation
    profile = load_profile()
    state = context.chat_data.setdefault("state", {"last_replies": [], "mode": None})

    # typing + delay
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    await human_delay(text)

    reply = generate_response(text, state, profile)
    reply = apply_style(reply)

    # avoid repetition
    state["last_replies"].append(reply)
    state["last_replies"] = state["last_replies"][-10:]

    await update.message.reply_text(reply)


def _make_key_phrase(user_line: str) -> str:
    """
    Convert a user line into a key phrase for retrieval.
    Keep it short + meaningful (3-6 words).
    """
    t = re.sub(r"[^a-zA-Z0-9\s']", " ", user_line or "").lower()
    words = [w for w in t.split() if len(w) >= 3]
    if not words:
        return ""
    # prefer first 5 meaningful words
    key = " ".join(words[:5]).strip()
    return key


# =========================
# ADMIN COMMANDS
# =========================
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED
    if not is_admin(update):
        return
    PAUSED = True
    await update.message.reply_text("Paused ✅")

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED
    if not is_admin(update):
        return
    PAUSED = False
    await update.message.reply_text("Resumed ✅")

async def cmd_teach_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.chat_data["teach_on"] = True
    await update.message.reply_text("Teaching ON ✅\n\nPaste training blocks now.\n\n" + TRAIN_HELP)

async def cmd_teach_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.chat_data["teach_on"] = False
    await update.message.reply_text("Teaching OFF ✅")

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    state = context.chat_data.setdefault("state", {"last_replies": [], "mode": None})
    if not context.args:
        await update.message.reply_text("Use: /mode playful | shy | romantic | soft | serious | auto")
        return
    m = context.args[0].strip().lower()
    if m == "auto":
        state["mode"] = None
        await update.message.reply_text("Mode: AUTO ✅")
        return
    if m not in {"playful", "shy", "romantic", "soft", "serious"}:
        await update.message.reply_text("Mode options: playful, shy, romantic, soft, serious, auto")
        return
    state["mode"] = m
    await update.message.reply_text(f"Mode: {m.upper()} ✅")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    p = load_profile()
    await update.message.reply_text(
        "Profile:\n"
        f"emoji_level={p.get('emoji_level')}\n"
        f"linebreak_level={p.get('linebreak_level')}\n"
        f"fav_emojis={' '.join(p.get('fav_emojis', []))}\n"
        f"fav_reacts={', '.join(p.get('fav_reacts', [])[:8])}"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "Admin commands:\n"
        "/pause\n/resume\n"
        "/teach_on\n/teach_off\n"
        "/mode playful|shy|romantic|soft|serious|auto\n"
        "/profile\n"
        "\n" + TRAIN_HELP
    )


# =========================
# START
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in .env")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("teach_on", cmd_teach_on))
    app.add_handler(CommandHandler("teach_off", cmd_teach_off))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("help_admin", cmd_help))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

import time
import random
import re
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from config import BOT_TOKEN, ADMIN_ID
from memory import (
    ensure_schema,
    get_or_create_user,
    bump_interaction,
    get_user_state,
    set_user_state,
    append_last_reply,
    reset_user_memory,
    update_topic_weights,
)
from delay_engine import human_delay
from style_engine import apply_style


# ----------------------------
# Runtime controls (in-memory)
# ----------------------------
PAUSED = False  # global pause switch

# per-chat simple rate limit (anti-spam)
_last_msg_ts = defaultdict(float)
_msg_burst = defaultdict(lambda: deque(maxlen=6))  # timestamps


# ----------------------------
# Tiny “brain” (rule-based)
# ----------------------------
GREETINGS = ["Hi", "Hey", "Hii😂", "Hello", "Heyy"]
ACKS = ["Good", "Yep", "Really😂", "Cool", "Awwwn", "Ok", "Okayyy", "Huh", "Lol", "Tadaaa"]
BOUNCE_BACK = ["How so", "Why tho😂", "Wym", "Say more", "What happened", "You sure", "Go on"]
SOFT_FLIRTY = ["Hehe😏", "Mhm😂", "Awww", "Okayyy😏", "Stoppp😂", "You’re funny😂"]

URGENCY_WORDS = {"urgent", "asap", "now", "quick", "immediately"}


def _pick_not_repeating(options, last_replies, k=1):
    """Pick something that isn't among the last replies if possible."""
    pool = [o for o in options if o not in set(last_replies)]
    if not pool:
        pool = options
    return random.choice(pool)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _looks_like_greeting(t: str) -> bool:
    return bool(re.search(r"\b(hi|hey|hello|yo|sup)\b", t))


def _looks_like_how_are_you(t: str) -> bool:
    return "how are you" in t or "how r you" in t or "how you" in t


def _is_short(t: str) -> bool:
    return len(t) <= 6


def _has_urgency(t: str) -> bool:
    words = set(re.findall(r"[a-z]+", t))
    return any(w in URGENCY_WORDS for w in words)


def _nsfw_requested(t: str) -> bool:
    # If user pushes sexual content explicitly (we keep default safe)
    return bool(re.search(r"\b(sex|nude|naked|fuck|dick|pussy|blowjob|cum)\b", t))


def generate_response(user_text: str, user_state: dict) -> str:
    """
    Style-aligned, varied, avoids repetition, uses light memory (topic weights + tone).
    Default safe: NSFW off unless admin toggles.
    """
    text = _clean(user_text)
    t = text.lower()

    last_replies = user_state.get("last_replies", [])
    mood = user_state.get("mood", "casual")  # casual | warm | serious
    nsfw = bool(user_state.get("nsfw", False))

    # Safety gate
    if _nsfw_requested(t) and not nsfw:
        return _pick_not_repeating(
            [
                "Lol😂\n\nKeep it cute pls",
                "Ayy😂\n\nNot that here",
                "Hmm😂\n\nLet’s chill",
            ],
            last_replies,
        )

    # Greetings
    if _looks_like_greeting(t):
        return _pick_not_repeating(GREETINGS, last_replies)

    # “How are you”
    if _looks_like_how_are_you(t):
        options = ["Good", "I’m okay", "Chilling😂", "I’m fine", "I’m good\n\nYou"]
        return _pick_not_repeating(options, last_replies)

    # Questions -> bounce back
    if "?" in text:
        if mood == "serious":
            options = ["Tell me", "What do you mean", "Explain it to me", "Okay\n\nWhat happened"]
        else:
            options = BOUNCE_BACK + SOFT_FLIRTY
        return _pick_not_repeating(options, last_replies)

    # Very short user message -> short reply
    if _is_short(t):
        options = ACKS + SOFT_FLIRTY
        return _pick_not_repeating(options, last_replies)

    # If user seems emotional / heavy
    if re.search(r"\b(sad|tired|depressed|hurt|cry|lonely|stress)\b", t):
        user_state["mood"] = "serious"
        options = [
            "Awwwn\n\nTalk to me",
            "I hear you\n\nWhat’s wrong",
            "Come here😂\n\nTell me everything",
            "Hmm\n\nI’m listening",
        ]
        return _pick_not_repeating(options, last_replies)

    # Default: acknowledge + follow-up (short lines)
    if mood == "serious":
        options = [
            "Okay\n\nGo on",
            "I get it\n\nWhat next",
            "Hmm\n\nTell me more",
        ]
    else:
        options = [
            "Oh really😂\n\nTell me",
            "Okayyy😏\n\nGo on",
            "Lol\n\nAnd then",
            "Mhm😂\n\nWhat next",
        ]
    return _pick_not_repeating(options, last_replies)


# ----------------------------
# Handlers
# ----------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED

    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""

    # Global pause
    if PAUSED:
        return

    # Anti-spam / rate limiting
    now = time.time()
    _msg_burst[chat_id].append(now)

    # If 6 msgs within 6 seconds, ignore (simple protection)
    if len(_msg_burst[chat_id]) >= 6 and (now - _msg_burst[chat_id][0]) < 6:
        return

    # Basic per-message cooldown
    if now - _last_msg_ts[chat_id] < 0.35:
        return
    _last_msg_ts[chat_id] = now

    ensure_schema()
    get_or_create_user(chat_id, username)
    bump_interaction(chat_id)

    text = update.message.text
    user_state = get_user_state(chat_id)

    # Update topics (very lightweight)
    update_topic_weights(chat_id, text)

    # Human-like timing + typing
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    # urgency -> faster
    if _has_urgency(text.lower()):
        await human_delay(text[:20])
    else:
        await human_delay(text)

    reply = generate_response(text, user_state)

    # style pass (line breaks, vibe)
    reply = apply_style(reply)

    # store last reply to prevent repetition
    append_last_reply(chat_id, reply)

    await update.message.reply_text(reply)


# ----------------------------
# Admin commands
# ----------------------------
def _is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED
    if not _is_admin(update):
        return
    PAUSED = True
    await update.message.reply_text("Paused")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED
    if not _is_admin(update):
        return
    PAUSED = False
    await update.message.reply_text("Resumed")


async def cmd_nsfw_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    chat_id = update.effective_chat.id
    state = get_user_state(chat_id)
    state["nsfw"] = True
    set_user_state(chat_id, state)
    await update.message.reply_text("NSFW mode: ON (this chat)")


async def cmd_nsfw_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    chat_id = update.effective_chat.id
    state = get_user_state(chat_id)
    state["nsfw"] = False
    set_user_state(chat_id, state)
    await update.message.reply_text("NSFW mode: OFF (this chat)")


async def cmd_reset_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    # /reset_memory <chat_id>  (or omit to reset current chat)
    if context.args:
        target = int(context.args[0])
    else:
        target = update.effective_chat.id
    reset_user_memory(target)
    await update.message.reply_text("Memory reset")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    chat_id = update.effective_chat.id
    st = get_user_state(chat_id)
    await update.message.reply_text(
        f"paused={PAUSED}\nnsfw={bool(st.get('nsfw', False))}\nmood={st.get('mood','casual')}"
    )


def main():
    ensure_schema()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("nsfw_on", cmd_nsfw_on))
    app.add_handler(CommandHandler("nsfw_off", cmd_nsfw_off))
    app.add_handler(CommandHandler("reset_memory", cmd_reset_memory))
    app.add_handler(CommandHandler("status", cmd_status))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

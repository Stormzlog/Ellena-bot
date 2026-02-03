from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from config import BOT_TOKEN, ADMIN_ID
from memory import get_user, update_interaction, reset_memory
from delay_engine import human_delay
from style_engine import apply_style

def generate_response(text):
    return "Cool"   # placeholder (AI later)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    text = update.message.text

    get_user(chat_id, username)
    update_interaction(chat_id)

    await context.bot.send_chat_action(chat_id, "typing")
    await human_delay(text)

    reply = apply_style(generate_response(text))
    await update.message.reply_text(reply)

async def admin_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    reset_memory(int(context.args[0]))
    await update.message.reply_text("Memory reset")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CommandHandler("reset_memory", admin_reset))
app.run_polling()

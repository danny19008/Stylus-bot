import os
import logging
import re
from datetime import datetime
from threading import Thread
from flask import Flask
import asyncio
import aiohttp

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, BotCommand, BotCommandScopeAllPrivateChats
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler,
    CallbackQueryHandler, ConversationHandler, CommandHandler, filters
)

# ---------------- CONFIG & LOGGING ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5119090631"))
FEEDBACK_GROUP_ID = os.getenv("GROUP_ID")  # Locked on first non-admin group

SELF_URL = os.getenv("SELF_URL")  # Optional: for internal self-ping

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- FLASK SERVER (Keep-alive) ----------------
app = Flask(__name__)

@app.route('/')
def home():
    return {
        "status": "online",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }, 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------- UTILS ----------------
def escape_md(text):
    reserved_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(reserved_chars)}])', r'\\\1', str(text or ""))

def lock_feedback_group(group_id: int):
    """Locks the first non-admin group ID to env variable"""
    global FEEDBACK_GROUP_ID
    if not FEEDBACK_GROUP_ID and group_id != ADMIN_GROUP_ID:
        FEEDBACK_GROUP_ID = str(group_id)
        logger.info(f"FEEDBACK_GROUP_ID locked to: {FEEDBACK_GROUP_ID}")
    return FEEDBACK_GROUP_ID

# ---------------- BACKGROUND JOBS ----------------
async def send_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text="🟢 *StyluS Status:* Bot is active",
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    if not FEEDBACK_GROUP_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=int(FEEDBACK_GROUP_ID),
            text="💡 *Reminder:* Submit feedback or bug reports via private chat\!",
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Reminder error: {e}")

async def self_ping():
    if not SELF_URL:
        return
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SELF_URL) as resp:
                    if resp.status == 200:
                        logger.info("Self-ping successful")
        except Exception as e:
            logger.error(f"Self-ping failed: {e}")
        await asyncio.sleep(600)  # every 10 minutes

# ---------------- INITIAL SETUP ----------------
async def post_init(application):
    await application.bot.set_my_commands(
        [BotCommand("start", "📩 Start Feedback")],
        scope=BotCommandScopeAllPrivateChats()
    )
    job_queue = application.job_queue
    job_queue.run_repeating(send_heartbeat, interval=3600, first=10)
    job_queue.run_repeating(send_reminder, interval=172800, first=86400)
    if SELF_URL:
        job_queue.run_repeating(lambda ctx: asyncio.create_task(self_ping()), interval=600)

# ---------------- GROUP FEEDBACK BUTTON ----------------
async def show_group_feedback_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    locked_group_id = lock_feedback_group(group_id)

    if int(locked_group_id) != group_id:
        return  # Ignore other groups

    keyboard = [["Feedback"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=False
    )
    await update.message.reply_text(
        "⚡ Tap 'Feedback' to start private feedback. You can still type freely in the group!",
        reply_markup=reply_markup
    )

async def handle_group_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not FEEDBACK_GROUP_ID or int(FEEDBACK_GROUP_ID) != group_id:
        return

    if update.message.text == "Feedback":
        bot_me = await context.bot.get_me()
        await update.message.reply_text(
            f"🚀 Click here to start private feedback: https://t.me/{bot_me.username}?start=feedback"
        )

# ---------------- PRIVATE CHAT MENU ----------------
async def private_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Do NOT send image; respects BotFather /start
    keyboard = [["Send Feedback", "Help"], ["Cancel"]]
    await update.message.reply_text(
        "Choose an option below:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)
    )
    return 0

# ---------------- FEEDBACK FLOW ----------------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Send Feedback":
        await update.message.reply_text("📩 Please send your feedback message (Text, Photo, or Video) now:")
        return 1
    elif text == "Help":
        await update.message.reply_text("ℹ️ Send your message, confirm it, and select a category.")
        return 0
    elif text == "Cancel":
        await update.message.reply_text("❌ Session ended.")
        return ConversationHandler.END
    return 0

async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.message, update.effective_user
    context.user_data["fb"] = {
        "mid": msg.message_id,
        "cid": msg.chat_id,
        "name": user.full_name,
        "user": f"@{user.username}" if user.username else "N/A",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    kb = [[InlineKeyboardButton("✅ Yes", callback_data='c_yes'), InlineKeyboardButton("❌ No", callback_data='c_no')]]
    await msg.reply_text("📩 Ready to send this feedback?", reply_markup=InlineKeyboardMarkup(kb))
    return 2

# ---------------- CATEGORY SELECTION ----------------
async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'c_no':
        await query.edit_message_text("❌ Canceled.")
        return ConversationHandler.END

    kb = [[
        InlineKeyboardButton("🐞 Bug", callback_data='cat_bug'),
        InlineKeyboardButton("😕 Confusion", callback_data='cat_conf'),
        InlineKeyboardButton("💡 Idea", callback_data='cat_idea')
    ]]
    await query.edit_message_text("🏷 Label your feedback:", reply_markup=InlineKeyboardMarkup(kb))
    return 3

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("fb")
    if not data:
        await query.edit_message_text("❌ Session expired.")
        return ConversationHandler.END

    cat_map = {
        'cat_bug': '🐞 BUG',
        'cat_conf': '😕 CONFUSION',
        'cat_idea': '💡 IDEA'
    }
    category = cat_map.get(query.data, 'General')

    header = (f"📩 *NEW FEEDBACK* \- {escape_md(category)}\n"
              f"👤 *From:* {escape_md(data['name'])} \({escape_md(data['user'])}\)\n"
              f"📅 *Time:* {escape_md(data['time'])}\n"
              f"━━━━━━━━━━━━━━━")
    try:
        await context.bot.send_message(chat_id=int(FEEDBACK_GROUP_ID), text=header, parse_mode='MarkdownV2')
        await context.bot.copy_message(chat_id=int(FEEDBACK_GROUP_ID), from_chat_id=data['cid'], message_id=data['mid'])
        await query.edit_message_text("✅ *Feedback Delivered\!*", parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Send Error: {e}")
        await query.edit_message_text("❌ Delivery failed. Ensure the bot is an admin in the group.")
    return ConversationHandler.END

# ---------------- MAIN ----------------
def main():
    Thread(target=run_flask, daemon=True).start()

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", private_menu)],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)],
            1: [MessageHandler(filters.ALL & ~filters.COMMAND, get_feedback)],
            2: [CallbackQueryHandler(confirm_callback, pattern="^c_")],
            3: [CallbackQueryHandler(category_callback, pattern="^cat_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )
    application.add_handler(conv)

    # Group handlers
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, show_group_feedback_keyboard))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_feedback_button))

    application.run_polling()

if __name__ == '__main__':
    main()

import os
import logging
import re
from datetime import datetime
from threading import Thread
from flask import Flask, request
import asyncio
import aiohttp

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, BotCommand, BotCommandScopeAllPrivateChats,
    MenuButtonCommands
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler,
    CallbackQueryHandler, ConversationHandler, CommandHandler, filters
)

# ---------------- CONFIG & LOGGING ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5119090631"))
FEEDBACK_GROUP_ID = os.getenv("GROUP_ID")

SELF_URL = os.getenv("SELF_URL")
RENDER_URL = os.getenv("RENDER_URL")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- FLASK SERVER ----------------
app = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

@app.route("/")
def home():
    return {
        "status": "online",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }, 200

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

# ---------------- UTILS ----------------
def escape_md(text):
    reserved_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(reserved_chars)}])', r'\\\1', str(text or ""))

def lock_feedback_group(group_id: int):
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
        bot_me = await context.bot.get_me()
        feedback_url = f"https://t.me/{bot_me.username}?start=feedback"
        kb = [[InlineKeyboardButton("💬 Send Feedback", url=feedback_url)]]

        await context.bot.send_message(
            chat_id=int(FEEDBACK_GROUP_ID),
            text="💡 *Reminder:* Submit feedback via private chat!",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb)
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
                        logger.info("Self ping success")
        except Exception as e:
            logger.error(f"Self ping failed: {e}")
        await asyncio.sleep(600)

# ---------------- INITIAL SETUP ----------------
async def post_init(application):
    await application.bot.set_my_commands(
        [BotCommand("start", "📩 Start Feedback")],
        scope=BotCommandScopeAllPrivateChats()
    )

    # Set chat menu button (group menu)
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )

    job_queue = application.job_queue
    job_queue.run_repeating(send_heartbeat, interval=3600, first=10)
    # 3-day reminder = 259200 seconds
    job_queue.run_repeating(send_reminder, interval=259200, first=10)

    if SELF_URL:
        asyncio.create_task(self_ping())

    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"Webhook set to {webhook_url}")

# ---------------- GROUP FEEDBACK BUTTON ----------------
async def show_group_feedback_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    locked_group_id = lock_feedback_group(group_id)

    if int(locked_group_id) != group_id:
        return

    keyboard = [["Feedback"]]

    # Send reply only if a message exists (works for /feedback command)
    if update.message:
        await update.message.reply_text(
            "⚡ Tap 'Feedback' to start private feedback.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )

async def handle_group_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    if not FEEDBACK_GROUP_ID or int(FEEDBACK_GROUP_ID) != group_id:
        return

    if update.message.text == "Feedback":
        bot_me = await context.bot.get_me()
        await update.message.reply_text(
            f"🚀 Start private feedback:\nhttps://t.me/{bot_me.username}?start=feedback"
        )

# ---------------- PRIVATE CHAT MENU ----------------
async def private_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    # Check if user clicked start link with argument "feedback"
    start_args = context.args if hasattr(context, "args") else []
    if start_args and start_args[0].lower() == "feedback":
        # Immediately start Send Feedback flow
        await update.message.reply_text(
            "📩 Send your feedback message (text/photo/video)"
        )
        return 1  # Moves conversation to state 1 (get_feedback)

    # Otherwise show the menu keyboard
    keyboard = [["Send Feedback", "Help"], ["Cancel"]]
    await update.message.reply_text(
        "Choose an option:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)
    )
    return 0

# ---------------- FEEDBACK FLOW ----------------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "Send Feedback":
        await update.message.reply_text(
            "📩 Send your feedback message (text/photo/video)"
        )
        return 1

    elif text == "Help":
        await update.message.reply_text(
            "Send feedback → confirm → select category"
        )
        return 0

    elif text == "Cancel":
        await update.message.reply_text("❌ Session ended")
        return ConversationHandler.END

    return 0

async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    context.user_data["fb"] = {
        "mid": msg.message_id,
        "cid": msg.chat_id,
        "name": user.full_name,
        "user": f"@{user.username}" if user.username else "N/A",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    kb = [[
        InlineKeyboardButton("✅ Yes", callback_data="c_yes"),
        InlineKeyboardButton("❌ No", callback_data="c_no")
    ]]

    await msg.reply_text(
        "📩 Ready to send this feedback?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return 2

# ---------------- CONFIRMATION ----------------
async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "c_no":
        await query.edit_message_text("❌ Canceled")
        return ConversationHandler.END

    kb = [[
        InlineKeyboardButton("🐞 Bug", callback_data="cat_bug"),
        InlineKeyboardButton("😕 Confusion", callback_data="cat_conf"),
        InlineKeyboardButton("💡 Idea", callback_data="cat_idea")
    ]]

    await query.edit_message_text(
        "🏷 Label your feedback:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return 3

# ---------------- CATEGORY ----------------
async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("fb")
    if not data:
        await query.edit_message_text("❌ Session expired")
        return ConversationHandler.END

    cat_map = {
        "cat_bug": "🐞 BUG",
        "cat_conf": "😕 CONFUSION",
        "cat_idea": "💡 IDEA"
    }

    category = cat_map.get(query.data, "General")

    header = (
        f"📩 *NEW FEEDBACK* \\- {escape_md(category)}\n"
        f"👤 *From:* {escape_md(data['name'])} \\({escape_md(data['user'])}\\)\n"
        f"📅 *Time:* {escape_md(data['time'])}\n"
        f"━━━━━━━━━━━━━━━"
    )

    try:
        await context.bot.send_message(
            chat_id=int(FEEDBACK_GROUP_ID),
            text=header,
            parse_mode="MarkdownV2"
        )

        await context.bot.copy_message(
            chat_id=int(FEEDBACK_GROUP_ID),
            from_chat_id=data["cid"],
            message_id=data["mid"]
        )

        await query.edit_message_text(
            "✅ *Feedback Delivered\\!*",
            parse_mode="MarkdownV2"
        )

    except Exception as e:
        logger.error(f"Send Error: {e}")
        await query.edit_message_text(
            "❌ Delivery failed. Ensure bot is admin."
        )

    return ConversationHandler.END

# ---------------- MAIN ----------------
async def start_bot():
    await application.initialize()
    await post_init(application)

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

    # show feedback keyboard on new member
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, show_group_feedback_keyboard)
    )

    # command to show feedback menu in group
    application.add_handler(
        CommandHandler("feedback", show_group_feedback_keyboard)
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_feedback_button)
    )

    await application.start()

# ---------------- START SERVER ----------------
def main():
    Thread(target=lambda: asyncio.run(start_bot())).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()

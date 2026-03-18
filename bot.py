import os
import logging
import re
from datetime import datetime, timedelta
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

# ---------------- FEEDBACK COOLDOWN ----------------
user_feedback_history = {}
MAX_FEEDBACK = 2
COOLDOWN = timedelta(minutes=10)

# ---------------- BACKGROUND ----------------
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
                async with session.get(SELF_URL):
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
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    job_queue = application.job_queue
    job_queue.run_repeating(send_heartbeat, interval=3600, first=10)
    job_queue.run_repeating(send_reminder, interval=259200, first=10)
    if SELF_URL:
        asyncio.create_task(self_ping())

# ---------------- GROUP FEEDBACK ----------------
async def show_group_feedback_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    locked_group_id = lock_feedback_group(group_id)
    if int(locked_group_id) != group_id:
        return
    keyboard = [["💬 Feedback"]]
    if update.message:
        await update.message.reply_text(
            "⚡ Tap '💬 Feedback' to start private feedback.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )

async def handle_group_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not FEEDBACK_GROUP_ID or int(FEEDBACK_GROUP_ID) != group_id:
        return
    if update.message.text == "💬 Feedback":
        bot_me = await context.bot.get_me()
        await update.message.reply_text(
            f"🚀 Start private feedback:\nhttps://t.me/{bot_me.username}?start=feedback"
        )

# ---------------- PRIVATE MENU ----------------
async def private_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    start_args = context.args if hasattr(context, "args") else []
    if start_args and start_args[0].lower() == "feedback":
        await update.message.reply_text("📩 Send your feedback message (text/photo/video)")
        return 1
    await show_private_menu(update)

async def show_private_menu(update: Update):
    keyboard = [["📩 Send Feedback", "❓ Help"], ["❌ Cancel"]]
    await update.message.reply_text(
        "Choose an option:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )

# ---------------- MENU HANDLER ----------------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📩 Send Feedback":
        await update.message.reply_text("📩 Send your feedback message (text/photo/video)")
        return 1
    elif text == "❓ Help":
        await update.message.reply_text("Send feedback → confirm → select category")
        return 0
    elif text == "❌ Cancel":
        await update.message.reply_text("❌ Session ended")
        return ConversationHandler.END
    await show_private_menu(update)
    return 0

# ---------------- FEEDBACK ----------------
async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now()
    if user_id not in user_feedback_history:
        user_feedback_history[user_id] = []
    user_feedback_history[user_id] = [t for t in user_feedback_history[user_id] if now - t < COOLDOWN]
    if len(user_feedback_history[user_id]) >= MAX_FEEDBACK:
        await update.message.reply_text(
            "⚠️ You have reached your feedback limit. Please wait 10 minutes before sending more feedback."
        )
        return ConversationHandler.END
    msg = update.message
    user = update.effective_user
    context.user_data["fb"] = {
        "mid": msg.message_id,
        "cid": msg.chat_id,
        "name": user.full_name,
        "user": f"@{user.username}" if user.username else "N/A",
        "time": now.strftime("%Y-%m-%d %H:%M")
    }
    kb = [[InlineKeyboardButton("✅ Yes, send", callback_data="c_yes")],
          [InlineKeyboardButton("❌ No, cancel", callback_data="c_no")]]
    await msg.reply_text("📩 Ready to send this feedback?", reply_markup=InlineKeyboardMarkup(kb))
    return 2

# ---------------- CONFIRM ----------------
async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "c_no":
        await query.edit_message_text("❌ Canceled")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton("🐞 Bug", callback_data="cat_bug")],
          [InlineKeyboardButton("😕 Confusion", callback_data="cat_conf")],
          [InlineKeyboardButton("💡 Idea", callback_data="cat_idea")]]
    await query.edit_message_text("🏷 Label your feedback:", reply_markup=InlineKeyboardMarkup(kb))
    return 3

# ---------------- CATEGORY ----------------
async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("fb")
    if not data:
        await query.edit_message_text("❌ Session expired")
        return ConversationHandler.END
    cat_map = {"cat_bug": "🐞 BUG", "cat_conf": "😕 CONFUSION", "cat_idea": "💡 IDEA"}
    category = cat_map.get(query.data, "General")
    header = (
        f"📩 *NEW FEEDBACK* \\- {escape_md(category)}\n"
        f"👤 *From:* {escape_md(data['name'])} \\({escape_md(data['user'])}\\)\n"
        f"📅 *Time:* {escape_md(data['time'])}\n"
        f"━━━━━━━━━━━━━━━"
    )
    try:
        await context.bot.send_message(chat_id=int(FEEDBACK_GROUP_ID), text=header, parse_mode="MarkdownV2")
        await context.bot.copy_message(chat_id=int(FEEDBACK_GROUP_ID), from_chat_id=data["cid"], message_id=data["mid"])
        await query.edit_message_text("✅ *Feedback Delivered!* 🎉", parse_mode="MarkdownV2")
        user_feedback_history.setdefault(update.effective_user.id, []).append(datetime.now())
    except Exception as e:
        logger.error(f"Send Error: {e}")
        await query.edit_message_text("❌ Delivery failed. Ensure bot is admin.")
    return ConversationHandler.END

# ---------------- START BOT ----------------
async def start_bot():
    # Initialize the application
    await application.initialize()
    await post_init(application)

    # Conversation Handler
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", private_menu)],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)],
            1: [MessageHandler(~filters.COMMAND, get_feedback)],
            2: [CallbackQueryHandler(confirm_callback, pattern="^c_")],
            3: [CallbackQueryHandler(category_callback, pattern="^cat_")]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )

    # Add handlers
    application.add_handler(conv)
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, show_group_feedback_keyboard))
    application.add_handler(CommandHandler("feedback", show_group_feedback_keyboard))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_feedback_button))

    logger.info("Bot handlers added")

    # ---------------- RUN ----------------
    if RENDER_URL:
        # Webhook mode
        webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"Webhook set: {webhook_url}")
        await application.start()
        await application.updater.start_polling()  # Needed to process queue
        await application.updater.idle()
    else:
        # Local polling for testing
        logger.info("Starting local polling...")
        await application.run_polling()

# ---------------- ENTRY POINT ----------------
def main():
    asyncio.run(start_bot())

if __name__ == "__main__":
    main()

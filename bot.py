import os
import logging
import re
import asyncio
from datetime import datetime
from threading import Thread
from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ChatMemberHandler,
    CommandHandler,
    filters
)

# ---------------- FLASK KEEP ALIVE ----------------
app = Flask(__name__)

@app.route('/')
def home():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5119090631"))
FEEDBACK_GROUP_ID = os.getenv("GROUP_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")

# ---------------- LOGGING ----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- STATES ----------------
QUESTION_FEEDBACK, CONFIRM_FEEDBACK, QUESTION_CATEGORY = range(3)

# ---------------- UTILS ----------------
def escape_md(text):
    """Aggressive escaping for Telegram MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text or ""))

# ---------------- PERSISTENT MENUS ----------------
async def set_menus(application):
    # Commands for Private DM (The "App" view)
    await application.bot.set_my_commands(
        [BotCommand("start", "🚀 Start Feedback Session"), BotCommand("help", "❓ How to use")],
        scope=BotCommandScopeAllPrivateChats()
    )
    # Commands for Groups (The "Top Menu" bar)
    await application.bot.set_my_commands(
        [BotCommand("start", "📩 Send Feedback Privately")],
        scope=BotCommandScopeAllGroupChats()
    )

# ---------------- START COMMAND (WITH DEEP LINKING) ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    bot_me = await context.bot.get_me()

    # 1. LOGIC FOR GROUPS (The Redirector)
    if chat.type != "private":
        # Create deep link: t.me/botname?start=start_fb
        deep_link_url = f"https://t.me/{bot_me.username}?start=start_fb"
        keyboard = [[InlineKeyboardButton("🚀 Click to Send Feedback", url=deep_link_url)]]
        await update.message.reply_text(
            "⚡ *Feedback Mode*\nTo keep the group clean, please send your feedback in my DMs:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # 2. LOGIC FOR PRIVATE (Auto-Start Detection)
    # Check if user came from the group link
    if context.args and context.args[0] == "start_fb":
        await update.message.reply_text(
            "🚀 *Direct Feedback Mode*\nPlease send your message (text, photo, or voice note):",
            parse_mode="MarkdownV2"
        )
        return QUESTION_FEEDBACK

    # 3. LOGIC FOR PRIVATE (Standard Welcome)
    keyboard = [["Send Feedback", "Help"], ["Cancel"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)
    await update.message.reply_text(
        "👋 Welcome to StyluS Feedback Bot\!\n\nUse the menu below to start\.",
        reply_markup=reply_markup,
        parse_mode="MarkdownV2"
    )
    return QUESTION_FEEDBACK

# ---------------- MENU BUTTON HANDLER ----------------
async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Send Feedback":
        await update.message.reply_text("📩 Please send your feedback (text, photo, voice, etc.).")
        return QUESTION_FEEDBACK
    elif text == "Help":
        await update.message.reply_text("ℹ️ Send your feedback, preview it, and pick a category to finish.")
        return QUESTION_FEEDBACK
    elif text == "Cancel":
        await update.message.reply_text("❌ Session canceled.")
        return ConversationHandler.END
    return QUESTION_FEEDBACK

# ---------------- HANDLE FEEDBACK ----------------
async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if msg.text == "Cancel":
        await update.message.reply_text("❌ Session canceled.")
        return ConversationHandler.END

    context.user_data["feedback"] = {
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "sender_name": user.full_name,
        "username": f"@{user.username}" if user.username else "N/A",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    preview_text = "Media Content"
    if msg.text: 
        preview_text = msg.text[:50] + "..." if len(msg.text) > 50 else msg.text
    elif msg.photo: preview_text = "📷 Photo"
    elif msg.voice: preview_text = "🎤 Voice Message"

    keyboard = [[
        InlineKeyboardButton("✅ Yes, Send", callback_data='confirm_send'),
        InlineKeyboardButton("❌ Cancel", callback_data='confirm_cancel')
    ]]
    
    await update.message.reply_text(
        f"📩 *Preview:* {escape_md(preview_text)}\n\nDo you want to send this?",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONFIRM_FEEDBACK

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_cancel":
        await query.edit_message_text("❌ Canceled.")
        return ConversationHandler.END

    keyboard = [[
        InlineKeyboardButton("🐞 Bug", callback_data='cat_bug'),
        InlineKeyboardButton("💡 Idea", callback_data='cat_idea'),
        InlineKeyboardButton("❓ Other", callback_data='cat_other')
    ]]
    await query.edit_message_text("Select Category:", reply_markup=InlineKeyboardMarkup(keyboard))
    return QUESTION_CATEGORY

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("feedback")
    cat_map = {'cat_bug': '🐞 BUG', 'cat_idea': '💡 IDEA', 'cat_other': '❓ OTHER'}
    category = cat_map.get(query.data, "GENERAL")

    header = (
        f"📩 *NEW FEEDBACK* \- {category}\n"
        f"👤 *From:* {escape_md(data['sender_name'])} \({escape_md(data['username'])}\)\n"
        f"📅 *Time:* {escape_md(data['timestamp'])}"
    )

    try:
        target_id = FEEDBACK_GROUP_ID or ADMIN_GROUP_ID
        await context.bot.send_message(chat_id=target_id, text=header, parse_mode='MarkdownV2')
        await context.bot.forward_message(chat_id=target_id, from_chat_id=data['chat_id'], message_id=data['message_id'])
        await query.edit_message_text("✅ Feedback delivered!")
    except Exception as e:
        logger.error(f"Forward error: {e}")
        await query.edit_message_text("❌ Delivery failed. Ensure Bot is Admin in the group.")
    
    return ConversationHandler.END

# ---------------- AUTO-DETECT LOGIC ----------------
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if update.my_chat_member.new_chat_member.status in ["member", "administrator"]:
        if chat.id == ADMIN_GROUP_ID: return

        alert_text = (
            "🚨 *NEW FEEDBACK GROUP DETECTED*\n\n"
            f"Name: `{escape_md(chat.title)}`\n"
            f"ID: `{chat.id}`\n\n"
            "👉 Copy this ID to Render Environment Variables as `GROUP_ID`\."
        )
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=alert_text, parse_mode="MarkdownV2")

# ---------------- BACKGROUND JOBS ----------------
async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text="🟢 Bot Heartbeat")
    except: pass

async def feedback_tip(context: ContextTypes.DEFAULT_TYPE):
    if FEEDBACK_GROUP_ID:
        try: await context.bot.send_message(chat_id=FEEDBACK_GROUP_ID, text="💡 Tip: Send feedback via private DM anytime!")
        except: pass

# ---------------- MAIN ----------------
def main():
    # Keep Alive Thread for Render
    Thread(target=run_flask, daemon=True).start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Apply Persistent Menus for Groups and DMs
    if application.job_queue:
        application.job_queue.run_once(lambda c: set_menus(application), when=0)
        application.job_queue.run_repeating(self_ping, interval=600, first=10)
        application.job_queue.run_repeating(feedback_tip, interval=172800, first=3600)

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons)
        ],
        states={
            QUESTION_FEEDBACK: [MessageHandler(filters.ALL & ~filters.COMMAND, get_feedback)],
            CONFIRM_FEEDBACK: [CallbackQueryHandler(confirm_callback)],
            QUESTION_CATEGORY: [CallbackQueryHandler(category_callback)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    
    application.run_polling()

if __name__ == '__main__':
    main()

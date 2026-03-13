import os
import logging
import re
from datetime import datetime
from threading import Thread
from flask import Flask
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ChatMemberHandler,
    filters
)

# ---------------- FLASK KEEP ALIVE ----------------
app = Flask(__name__)

@app.route('/')
def home():
    return "StyluS Feedback Bot is running!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5119090631"))

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

# ---------------- SINGLE GROUP TRACKING ----------------
CURRENT_GROUP_ID = None
GROUP_FILE = "group_id.txt"

def load_group_id():
    global CURRENT_GROUP_ID
    if os.path.exists(GROUP_FILE):
        with open(GROUP_FILE, "r") as f:
            try:
                CURRENT_GROUP_ID = int(f.read().strip())
                logger.info(f"Loaded group ID: {CURRENT_GROUP_ID}")
            except:
                CURRENT_GROUP_ID = None

def save_group_id(group_id):
    with open(GROUP_FILE, "w") as f:
        f.write(str(group_id))

# ---------------- PERSISTENT MENUS ----------------
async def set_persistent_menus(application):
    private_commands = [BotCommand("dummy", "Menu-driven bot, commands not used by users")]
    group_commands = [
        BotCommand("start", "Redirect to private chat for feedback"),
        BotCommand("help", "Redirect to private chat for instructions")
    ]
    await application.bot.set_my_commands(private_commands)
    await application.bot.set_my_commands(group_commands, scope=None)

# ---------------- START COMMAND ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    if chat_type in ["group", "supergroup"]:
        await update.message.reply_text(
            "⚡ Please continue in private to submit feedback:\n"
            "[Click here](https://t.me/YourBotUsername)",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    if not CURRENT_GROUP_ID:
        await update.message.reply_text(
            "⚠️ I haven't been added to a feedback group yet! Please add me to your group first."
        )
        return ConversationHandler.END

    keyboard = [["Send Feedback", "Help"], ["Cancel"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    await update.message.reply_text(
        "👋 Welcome to StyluS Feedback Bot!\n\nUse the menu below to start submitting feedback.",
        reply_markup=reply_markup
    )
    return QUESTION_FEEDBACK

# ---------------- MENU BUTTON HANDLER ----------------
async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Send Feedback":
        await update.message.reply_text("📩 Please send your feedback (text, image, voice, video, document).")
        return QUESTION_FEEDBACK
    elif text == "Help":
        await update.message.reply_text(
            "ℹ️ To submit feedback, click 'Send Feedback', send your message, "
            "then confirm sending before it reaches the group."
        )
        return QUESTION_FEEDBACK
    elif text == "Cancel":
        return await cancel(update, context)
    else:
        return await guide_to_menu(update, context)

# ---------------- GUIDE USERS BACK TO MENU ----------------
async def guide_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Please use the buttons below to start submitting feedback.",
        reply_markup=ReplyKeyboardMarkup([["Send Feedback", "Help"], ["Cancel"]], resize_keyboard=True, persistent=True)
    )
    return QUESTION_FEEDBACK

# ---------------- CANCEL SESSION ----------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Feedback session canceled. You can start again anytime.")
    return ConversationHandler.END

# ---------------- HANDLE FEEDBACK ----------------
async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if not CURRENT_GROUP_ID:
        await update.message.reply_text("⚠️ Oops! I haven't been added to a feedback group yet.")
        return ConversationHandler.END

    context.user_data["feedback"] = {
        "message_id": msg.message_id,
        "chat_id": update.effective_chat.id,
        "sender_name": user.full_name,
        "username": f"@{user.username}" if user.username else "N/A",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    preview_text = "[Media Preview]"
    if msg.text: preview_text = msg.text
    elif msg.photo: preview_text = "📷 [Photo]"
    elif msg.video: preview_text = "🎥 [Video]"
    elif msg.voice: preview_text = "🎤 [Voice Message]"
    elif msg.audio: preview_text = "🎵 [Audio]"
    elif msg.document: preview_text = f"📄 {msg.document.file_name}"

    keyboard = [[InlineKeyboardButton("✅ Yes, Send", callback_data='confirm_send'),
                 InlineKeyboardButton("❌ Cancel", callback_data='confirm_cancel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📩 *Your Feedback Preview:*\n{preview_text}\n\nDo you want to send this feedback?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return CONFIRM_FEEDBACK

# ---------------- CONFIRMATION CALLBACK ----------------
async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ Feedback session canceled.")
        return ConversationHandler.END

    keyboard = [[
        InlineKeyboardButton("🐞 Bug", callback_data='cat_bug'),
        InlineKeyboardButton("😕 Confusion", callback_data='cat_conf'),
        InlineKeyboardButton("💡 Idea", callback_data='cat_idea')
    ]]
    await query.edit_message_text("Thanks! How should we label this feedback?", reply_markup=InlineKeyboardMarkup(keyboard))
    return QUESTION_CATEGORY

# ---------------- CATEGORY CALLBACK ----------------
async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_GROUP_ID
    query = update.callback_query
    await query.answer()
    if not CURRENT_GROUP_ID:
        await query.edit_message_text("⚠️ Bot is not in any group.")
        return ConversationHandler.END

    feedback_data = context.user_data.get("feedback")
    if not feedback_data:
        await query.edit_message_text("⏱ Session timed out.")
        return ConversationHandler.END

    category_map = {'cat_bug': '🐞 BUG REPORT', 'cat_conf': '😕 CONFUSION/UX', 'cat_idea': '💡 FEATURE IDEA'}
    category = category_map.get(query.data, 'General Feedback')

    def escape_md(text): return re.sub(r'([_*[\]()~`>#+-=|{}.!])', r'\\\1', text)

    header = (
        f"📩 *NEW FEEDBACK* - {category}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 *From:* {escape_md(feedback_data['sender_name'])} ({escape_md(feedback_data['username'])})\n"
        f"📅 *Time:* {feedback_data['timestamp']}\n"
        f"━━━━━━━━━━━━━━━"
    )

    try:
        await context.bot.send_message(chat_id=CURRENT_GROUP_ID, text=header, parse_mode='Markdown')
        await context.bot.forward_message(chat_id=CURRENT_GROUP_ID,
                                          from_chat_id=feedback_data['chat_id'],
                                          message_id=feedback_data['message_id'])
        await query.edit_message_text("✅ Feedback successfully delivered to your group.")
    except Exception as e:
        logger.error(f"Error forwarding feedback: {e}")
        await query.edit_message_text("❌ Failed to deliver. Make sure I am admin.")
    finally:
        context.user_data.clear()
    return ConversationHandler.END

# ---------------- BOT ADDED HANDLER ----------------
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_GROUP_ID
    chat = update.effective_chat
    new_status = update.my_chat_member.new_chat_member.status
    if new_status in ["member", "administrator"]:
        if not CURRENT_GROUP_ID:
            CURRENT_GROUP_ID = chat.id
            save_group_id(chat.id)
            await context.bot.send_message(chat.id, "👋 Thanks for adding me! I will now forward all feedback here.")
        else:
            await context.bot.send_message(chat.id, "⚠️ I am already active in another group.")

# ---------------- FEEDBACK REMINDER TASK ----------------
async def feedback_reminder_task(application):
    while True:
        if CURRENT_GROUP_ID:
            try:
                await application.bot.send_message(chat_id=CURRENT_GROUP_ID,
                                                   text="💡 Reminder: Submit feedback anytime via private chat.")
            except Exception as e:
                logger.error(f"Reminder error: {e}")
        await asyncio.sleep(2 * 24 * 60 * 60)

# ---------------- SILENT SELF-PING TASK ----------------
async def self_ping_task(application):
    while True:
        try:
            await application.bot.get_me()
            if ADMIN_GROUP_ID:
                await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text="💡 Bot is alive (silent ping).")
        except Exception as e:
            logger.error(f"Self-ping error: {e}")
        await asyncio.sleep(10 * 60)

# ---------------- MAIN ----------------
if __name__ == '__main__':
    keep_alive()
    load_group_id()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    asyncio.get_event_loop().run_until_complete(set_persistent_menus(application))

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons)],
        states={
            QUESTION_FEEDBACK: [MessageHandler(filters.ALL & ~filters.COMMAND, get_feedback),
                                MessageHandler(filters.TEXT & ~filters.COMMAND, guide_to_menu)],
            CONFIRM_FEEDBACK: [CallbackQueryHandler(confirm_callback)],
            QUESTION_CATEGORY: [CallbackQueryHandler(category_callback)]
        },
        fallbacks=[],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.COMMAND, start))

    async def start_tasks(app):
        asyncio.create_task(feedback_reminder_task(app))
        asyncio.create_task(self_ping_task(app))

    # Run bot safely without closing existing loop
    asyncio.get_event_loop().run_until_complete(application.run_polling(close_loop=False, startup=start_tasks))

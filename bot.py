import os
import logging
import re
import asyncio
from datetime import datetime
from threading import Thread
from flask import Flask

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, BotCommand, BotCommandScopeAllGroupChats, 
    BotCommandScopeAllPrivateChats
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, ChatMemberHandler, 
    CommandHandler, filters
)

# ---------------- FLASK KEEP ALIVE ----------------
app = Flask(__name__)
@app.route('/')
def home(): return "StyluS Feedback Bot is Online!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5119090631"))
FEEDBACK_GROUP_ID = os.getenv("GROUP_ID") 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- STATES ----------------
QUESTION_FEEDBACK, CONFIRM_FEEDBACK, QUESTION_CATEGORY = range(3)

def escape_md(text):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text or ""))

# ---------------- UI CONFIG ----------------
async def set_menus(application):
    await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands(
        [BotCommand("start", "📩 Send Feedback")],
        scope=BotCommandScopeAllGroupChats()
    )

# ---------------- START COMMAND ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    bot_me = await context.bot.get_me()

    if chat.type in ["group", "supergroup"]:
        url = f"https://t.me/{bot_me.username}?start=start_fb"
        kb = [[InlineKeyboardButton("🚀 Click to Send Feedback", url=url)]]
        await update.message.reply_text(
            "⚡ *Feedback Mode*\nPlease continue in private to submit feedback:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    if not FEEDBACK_GROUP_ID:
        await update.message.reply_text("⚠️ Bot is not linked to a feedback group yet.")
        return ConversationHandler.END

    if context.args and context.args[0] == "start_fb":
        await update.message.reply_text(r"🚀 *Direct Mode* \- Send your message now:", parse_mode="MarkdownV2")
        return QUESTION_FEEDBACK

    keyboard = [["Send Feedback", "Help"], ["Cancel"]]
    await update.message.reply_text(
        "👋 Welcome to StyluS Feedback Bot!\n\nUse the menu below to start submitting feedback.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)
    )
    return QUESTION_FEEDBACK

# ---------------- HANDLERS ----------------
async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Send Feedback":
        await update.message.reply_text("📩 Please send your feedback (text, image, voice, etc.).")
        return QUESTION_FEEDBACK
    elif text == "Help":
        await update.message.reply_text("ℹ️ Send feedback, preview it, and pick a category.")
        return QUESTION_FEEDBACK
    elif text == "Cancel":
        await update.message.reply_text("❌ Session canceled.")
        return ConversationHandler.END
    return QUESTION_FEEDBACK

async def get_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    # Anti-Spam Check
    now = datetime.now()
    last_time = context.user_data.get("last_msg_time")
    if last_time and (now - last_time).total_seconds() < 1.5:
        return QUESTION_FEEDBACK # Ignore spam
    context.user_data["last_msg_time"] = now

    if msg.text in ["Send Feedback", "Help", "Cancel"]:
        return await handle_menu_buttons(update, context)

    # Fully Featured Media Detection
    preview = "[Media Preview]"
    if msg.text: preview = msg.text[:100]
    elif msg.photo: preview = "📷 Photo"
    elif msg.video: preview = "🎥 Video"
    elif msg.voice: preview = "🎤 Voice Message"
    elif msg.document: preview = f"📄 {msg.document.file_name}"
    elif msg.animation: preview = "🎞 GIF"
    elif msg.sticker: preview = "🎨 Sticker"

    context.user_data["fb"] = {
        "mid": msg.message_id, "cid": msg.chat_id,
        "name": user.full_name,
        "user": f"@{user.username}" if user.username else "N/A",
        "time": now.strftime("%Y-%m-%d %H:%M:%S")
    }

    kb = [[InlineKeyboardButton("✅ Yes, Send", callback_data='c_yes'), 
           InlineKeyboardButton("❌ Cancel", callback_data='c_no')]]
    
    await msg.reply_text(
        f"📩 *Preview:* {escape_md(preview)}\n\nDo you want to send this?",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="MarkdownV2"
    )
    return CONFIRM_FEEDBACK

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
    await query.edit_message_text("Thanks! How should we label this feedback?", reply_markup=InlineKeyboardMarkup(kb))
    return QUESTION_CATEGORY

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("fb")
    
    if not data:
        await query.edit_message_text("❌ Session expired. Please resend.")
        return ConversationHandler.END

    cat_map = {'cat_bug': '🐞 BUG REPORT', 'cat_conf': '😕 CONFUSION/UX', 'cat_idea': '💡 FEATURE IDEA'}
    category = cat_map.get(query.data, 'General')

    header = (
        f"📩 *NEW FEEDBACK* - {category}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 *From:* {escape_md(data['name'])} ({escape_md(data['user'])})\n"
        f"📅 *Time:* {data['time']}\n"
        f"━━━━━━━━━━━━━━━"
    )

    target = FEEDBACK_GROUP_ID or ADMIN_GROUP_ID
    try:
        await context.bot.send_message(chat_id=target, text=header, parse_mode='MarkdownV2')
        await context.bot.forward_message(chat_id=target, from_chat_id=data['cid'], message_id=data['mid'])
        await query.edit_message_text("✅ Feedback successfully delivered.")
    except:
        await query.edit_message_text("❌ Delivery failed. Bot must be Admin.")
    
    return ConversationHandler.END

# ---------------- BACKGROUND TASKS ----------------
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member.new_chat_member.status in ["member", "administrator"]:
        cid = update.effective_chat.id
        if cid == ADMIN_GROUP_ID: return
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, 
                                     text=f"🚨 *NEW GROUP:* `{cid}`\nSet as `GROUP_ID` in Render.",
                                     parse_mode="MarkdownV2")

# ---------------- MAIN ----------------
def main():
    Thread(target=run_flask, daemon=True).start()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    if application.job_queue:
        application.job_queue.run_once(lambda c: set_menus(application), when=0)
        application.job_queue.run_repeating(lambda c: application.bot.send_message(ADMIN_GROUP_ID, "🟢 Live"), interval=600)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons)],
        states={
            QUESTION_FEEDBACK: [MessageHandler(filters.ALL & ~filters.COMMAND, get_feedback)],
            CONFIRM_FEEDBACK: [CallbackQueryHandler(confirm_callback, pattern="^c_")],
            QUESTION_CATEGORY: [CallbackQueryHandler(category_callback, pattern="^cat_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        allow_reentry=True,
        conversation_timeout=600 # Auto-reset after 10 mins
    )

    application.add_handler(conv)
    application.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    application.run_polling()

if __name__ == '__main__':
    main()

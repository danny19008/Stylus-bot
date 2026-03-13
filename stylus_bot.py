import os
import logging
from datetime import datetime
from threading import Thread
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ---------------- FLASK KEEP ALIVE FOR RENDER ----------------
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

# --- CONFIGURATION ---
# Replace with your actual credentials or set them as environment variables
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
DEV_GROUP_ID = "YOUR_GROUP_ID_HERE"

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Temporary storage for multi-step categorization
user_data_storage = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initial greeting when user clicks /start."""
    welcome_text = (
        "👋 Welcome to StyluS Feedback Bot!\n\n"
        "We value your input. Please send us your feedback in any format:\n"
        "💬 Text description\n"
        "📸 Screenshots/Images\n"
        "🎤 Voice notes\n\n"
        "Once you send it, you will be asked to categorize it."
    )
    await update.message.reply_text(welcome_text)

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captures the user message and triggers categorization buttons."""
    user = update.effective_user
    msg = update.message
    
    # Save message context for later forwarding
    user_data_storage[user.id] = {
        "message_id": msg.message_id,
        "chat_id": update.effective_chat.id,
        "sender_name": user.full_name,
        "username": f"@{user.username}" if user.username else "N/A",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # Define categorization buttons
    keyboard = [
        [
            InlineKeyboardButton("🐞 Bug", callback_data='cat_bug'),
            InlineKeyboardButton("😕 Confusion", callback_data='cat_conf'),
            InlineKeyboardButton("💡 Idea", callback_data='cat_idea'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Thanks for the feedback! How should we label this?",
        reply_markup=reply_markup
    )

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the button click and forwards everything to the developer group."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id not in user_data_storage:
        await query.edit_message_text("Your session has timed out. Please resend your feedback.")
        return

    data = user_data_storage[user_id]
    category_map = {
        'cat_bug': '🐞 BUG REPORT',
        'cat_conf': '😕 CONFUSION/UX',
        'cat_idea': '💡 FEATURE IDEA'
    }
    category = category_map.get(query.data, 'General Feedback')

    # Construct the report header
    header = (
        f"📩 *NEW FEEDBACK* - {category}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 *From:* {data['sender_name']} ({data['username']})\n"
        f"📅 *Time:* {data['timestamp']}\n"
        f"━━━━━━━━━━━━━━━"
    )

    try:
        # 1. Send header to Developer Group
        await context.bot.send_message(
            chat_id=DEV_GROUP_ID,
            text=header,
            parse_mode='Markdown'
        )

        # 2. Forward the original media/message to the same Group
        await context.bot.forward_message(
            chat_id=DEV_GROUP_ID,
            from_chat_id=data['chat_id'],
            message_id=data['message_id']
        )

        # 3. Final confirmation to the User
        await query.edit_message_text("✅ Feedback successfully delivered to the StyluS Dev Team. Thank you!")
        
    except Exception as e:
        logging.error(f"Error forwarding: {e}")
        await query.edit_message_text("❌ Failed to deliver. Make sure the bot is an admin in the dev group.")
    
    finally:
        # Cleanup storage
        if user_id in user_data_storage:
            del user_data_storage[user_id]

if __name__ == '__main__':
    # Start Flask keep-alive server
    keep_alive()

    # Build Application
    if BOT_TOKEN == "[TOKEN]":
        print("Error: Please set your BOT_TOKEN before running.")
    else:
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Start command
        application.add_handler(CommandHandler('start', start))
        
        # Handle feedback (text, photo, voice, video, documents)
        feedback_filter = (
            filters.TEXT | filters.PHOTO | filters.VOICE | 
            filters.VIDEO | filters.Document.ALL
        ) & (~filters.COMMAND)
        
        application.add_handler(MessageHandler(feedback_filter, handle_feedback))
        
        # Handle category selection
        application.add_handler(CallbackQueryHandler(category_callback))
        
        print("StyluS Feedback Bot is polling...")
        application.run_polling()

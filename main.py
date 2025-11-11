import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
import asyncio
from threading import Thread

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))

# Proxy settings
HTTP_PROXY = os.getenv('HTTP_PROXY')
HTTPS_PROXY = os.getenv('HTTPS_PROXY')

# Flask app for webhook and health check
app = Flask(__name__)

# Global bot application
bot_application = None

@app.route('/health', methods=['HEAD', 'GET'])
def health_check():
    """Health check endpoint for UptimeRobot"""
    return '', 200

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle incoming webhook updates"""
    try:
        update = Update.de_json(request.get_json(force=True), bot_application.bot)
        await bot_application.process_update(update)
        return 'ok', 200
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return 'error', 500

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text('Привет! Бот успешно запущен.')

async def post_init(application: Application):
    """Send message to admin after bot starts"""
    try:
        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="Бот запущен"
        )
        logger.info("Startup message sent to admin")
    except Exception as e:
        logger.error(f"Failed to send startup message: {e}")

def run_flask():
    """Run Flask app in a separate thread"""
    app.run(host='0.0.0.0', port=PORT)

async def main():
    """Main function to start the bot"""
    global bot_application

    # Setup proxy if configured
    proxy_url = HTTPS_PROXY or HTTP_PROXY
    request_kwargs = {}
    if proxy_url:
        request_kwargs['proxy_url'] = proxy_url
        logger.info(f"Using proxy: {proxy_url}")

    # Create application
    builder = Application.builder().token(BOT_TOKEN)
    if request_kwargs:
        builder = builder.request(request_kwargs)

    bot_application = builder.post_init(post_init).build()

    # Add handlers
    bot_application.add_handler(CommandHandler("start", start))

    # Set webhook
    webhook_path = f"{WEBHOOK_URL}/webhook"
    await bot_application.bot.set_webhook(url=webhook_path)
    logger.info(f"Webhook set to: {webhook_path}")

    # Start Flask in separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask server started on port {PORT}")

    # Start the bot
    await bot_application.initialize()
    await bot_application.start()
    logger.info("Bot started successfully")

    # Keep the application running
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())

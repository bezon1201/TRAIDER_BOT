import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
from threading import Thread
from pathlib import Path
from data import DataStorage

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
DATA_STORAGE = os.getenv('DATA_STORAGE', '/tmp/storage')

# Proxy settings
HTTP_PROXY = os.getenv('HTTP_PROXY')
HTTPS_PROXY = os.getenv('HTTPS_PROXY')

# Initialize data storage
data_storage = DataStorage(DATA_STORAGE)

# Flask app for webhook and health check
app = Flask(__name__)

# Global bot application - will be initialized before Flask starts
bot_application = None
bot_loop = None

@app.route('/health', methods=['HEAD', 'GET'])
def health_check():
    """Health check endpoint for UptimeRobot"""
    return '', 200

@app.route('/', methods=['HEAD', 'GET'])
def index():
    """Root endpoint"""
    return 'Traider Bot is running!', 200

def process_telegram_update_sync(request_data):
    """Synchronously process Telegram update using bot_loop"""
    if bot_application is None:
        logger.error("Bot application not initialized")
        return False

    try:
        async def async_process():
            update = Update.de_json(request_data, bot_application.bot)
            await bot_application.process_update(update)

        if bot_loop is not None and not bot_loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(async_process(), bot_loop)
            future.result(timeout=5)
            return True
        else:
            logger.error("Bot event loop is not available")
            return False
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook updates on /webhook"""
    try:
        process_telegram_update_sync(request.get_json(force=True))
        return 'ok', 200
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return 'error', 500

@app.route('/tg', methods=['POST'])
def webhook_tg():
    """Handle incoming webhook updates on /tg (main path)"""
    try:
        process_telegram_update_sync(request.get_json(force=True))
        return 'ok', 200
    except Exception as e:
        logger.error(f"Error in webhook /tg: {e}")
        return 'error', 500

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text('Привет! Бот успешно запущен.')

async def data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /data command
    /data - показывает список файлов
    /data export all - шлет все файлы
    /data delete all - удалить все файлы
    """
    try:
        args = context.args
        chat_id = update.effective_chat.id

        if not args:
            files = data_storage.get_files_list()
            if files:
                files_str = ', '.join(files)
                message = 'Файлы в хранилище:' + '
' + files_str
                await update.message.reply_text(message)
            else:
                await update.message.reply_text('Хранилище пусто')

        elif args[0].lower() == 'export' and len(args) > 1 and args[1].lower() == 'all':
            files = data_storage.get_files_list()

            if not files:
                await update.message.reply_text('Нечего экспортировать - хранилище пусто')
                return

            count_str = str(len(files))
            message = 'Отправляю ' + count_str + ' файл(ов)...'
            await update.message.reply_text(message)

            for filename in files:
                file_path = data_storage.get_file_path(filename)
                if file_path:
                    try:
                        with open(file_path, 'rb') as f:
                            await bot_application.bot.send_document(
                                chat_id=chat_id,
                                document=f,
                                filename=filename
                            )
                        logger.info(f"Exported: {filename}")
                    except Exception as e:
                        logger.error(f"Error exporting {filename}: {e}")
                        error_msg = 'Ошибка при отправке ' + filename
                        await update.message.reply_text(error_msg)

            count_str = str(len(files))
            success_msg = '✅ Экспортировано ' + count_str + ' файл(ов)'
            await update.message.reply_text(success_msg)

        elif args[0].lower() == 'delete' and len(args) > 1 and args[1].lower() == 'all':
            files = data_storage.get_files_list()

            if not files:
                await update.message.reply_text('Хранилище уже пусто')
                return

            if data_storage.delete_all():
                count_str = str(len(files))
                deleted_msg = '✅ Удалено ' + count_str + ' файл(ов)'
                await update.message.reply_text(deleted_msg)
            else:
                await update.message.reply_text('❌ Ошибка при удалении файлов')

        else:
            help_text = 'Неизвестная команда.' + '
' + 'Доступные команды:' + '
' + '/data - список файлов' + '
' + '/data export all - отправить все файлы' + '
' + '/data delete all - удалить все файлы'
            await update.message.reply_text(help_text)

    except Exception as e:
        logger.error(f"Error in data_command: {e}")
        await update.message.reply_text('❌ Ошибка при обработке команды')

async def post_init(application: Application):
    """Send message to admin after bot starts"""
    if not ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID not set; skip admin notify")
        return

    try:
        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="Бот запущен"
        )
        logger.info("Startup message sent to admin")
    except Exception as e:
        logger.error(f"Failed to send startup message: {e}")

async def initialize_bot():
    """Initialize bot and set webhook"""
    global bot_application, bot_loop

    bot_loop = asyncio.get_event_loop()

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
    bot_application.add_handler(CommandHandler("data", data_command, has_args=False))

    # Set webhook
    webhook_path = f"{WEBHOOK_URL}/tg"
    await bot_application.bot.set_webhook(url=webhook_path)
    logger.info(f"Webhook set to: {webhook_path}")

    # Initialize bot
    await bot_application.initialize()
    logger.info("Bot initialized successfully")

def run_flask():
    """Run Flask app in a separate thread"""
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

def run_bot_async():
    """Run bot initialization in async loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(initialize_bot())
        logger.info("Bot setup complete, Flask server is handling updates")
        # Keep loop alive
        loop.run_forever()
    except Exception as e:
        logger.error(f"Error in bot async loop: {e}")

if __name__ == '__main__':
    # Start bot initialization in separate thread
    bot_thread = Thread(target=run_bot_async, daemon=False)
    bot_thread.start()
    logger.info("Bot thread started")

    # Give bot time to initialize
    import time
    time.sleep(2)

    # Start Flask in main thread
    logger.info(f"Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

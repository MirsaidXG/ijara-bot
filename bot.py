import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from collections import defaultdict
import time

# Настройки
ADMIN_ID = 123456789  # Замените на свой Telegram user ID
SAME_MESSAGE_LIMIT = 2  # Лимит одинаковых сообщений в сутки

# Хранилище сообщений
user_messages = defaultdict(lambda: defaultdict(list))  # {user_id: {message_text: [timestamp1, timestamp2, ...]}}

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "supergroup":
        return

    user_id = update.effective_user.id
    message_text = update.message.text.strip() if update.message.text else ""
    chat_id = update.effective_chat.id
    now = time.time()

    if not message_text:
        return

    # Очистка старых записей
    for msg, timestamps in list(user_messages[user_id].items()):
        user_messages[user_id][msg] = [ts for ts in timestamps if now - ts < 86400]

    # Добавление текущего сообщения
    user_messages[user_id][message_text].append(now)

    count = len(user_messages[user_id][message_text])
    if count > SAME_MESSAGE_LIMIT:
        await update.message.delete()
        await update.message.reply_text("⚠️ Вы превысили лимит одинаковых сообщений. Это сообщение было удалено.")
        await context.bot.send_message(chat_id=ADMIN_ID, text=(
            f"🚨 Нарушение в группе:
"
            f"👤 Пользователь: @{update.effective_user.username or user_id}
"
            f"💬 Сообщение: "{message_text}"
"
            f"🔢 Кол-во за сутки: {count}
"
            f"⏰ Время: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
        ))

async def set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SAME_MESSAGE_LIMIT
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        new_limit = int(context.args[0])
        SAME_MESSAGE_LIMIT = new_limit
        await update.message.reply_text(f"✅ Лимит установлен: {new_limit} одинаковых сообщений в сутки.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Неверный формат. Пример: /setlimit 2")

async def get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"ℹ️ Текущий лимит: {SAME_MESSAGE_LIMIT} одинаковых сообщений в сутки.")

def main():
    app = ApplicationBuilder().token("YOUR_BOT_TOKEN").build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CommandHandler("setlimit", set_limit))
    app.add_handler(CommandHandler("getlimit", get_limit))
    app.run_polling()

if __name__ == "__main__":
    main()
import logging
import os
import json
from datetime import datetime, time, timedelta
from cachetools import TTLCache
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import aiohttp
from aiohttp import web
import asyncio

# === Настройка ===
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SELF_URL = os.getenv("SELF_URL")
LIMITS_FILE = "limits.json"
MESSAGES_FILE = "messages.json"
DELETED_COUNTS_FILE = "deleted_counts.json"
DEFAULT_LIMIT = 2

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не установлен!")

# === Логирование ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger()

# === Хранилища ===
user_messages = {}  # {(chat_id:user_id:text): count}
group_limits = {}   # {chat_id: limit}
deleted_messages_count = 0
user_deleted_counts = {}  # {(chat_id:user_id): deleted_count}
filter_enabled = True

# === Загрузка и сохранение лимитов ===
def load_limits():
    global group_limits
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            group_limits = {int(k): int(v) for k, v in json.load(f).items()}
        logger.info("✅ Лимиты загружены.")
    except FileNotFoundError:
        group_limits = {}
        logger.info("📂 limits.json не найден, создан новый.")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки лимитов: {e}")

def save_limits():
    try:
        with open(LIMITS_FILE, "w", encoding="utf-8") as f:
            json.dump(group_limits, f, indent=2, ensure_ascii=False)
        logger.info("💾 Лимиты сохранены.")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении лимитов: {e}")

# === Загрузка и сохранение счетчиков сообщений ===
def load_user_messages():
    global user_messages
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            user_messages = json.load(f)
        logger.info("✅ Счётчики сообщений загружены.")
    except FileNotFoundError:
        user_messages = {}
        logger.info("📂 messages.json не найден. Создан новый.")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки счётчиков сообщений: {e}")

def save_user_messages():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(user_messages, f, indent=2, ensure_ascii=False)
        logger.info("📥 Счётчики сообщений сохранены.")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении счётчиков сообщений: {e}")

# === Загрузка и сохранение счетчиков удалённых сообщений ===
def load_user_deleted_counts():
    global user_deleted_counts
    try:
        with open(DELETED_COUNTS_FILE, "r", encoding="utf-8") as f:
            user_deleted_counts = json.load(f)
        logger.info("✅ Счётчики удалённых сообщений загружены.")
    except FileNotFoundError:
        user_deleted_counts = {}
        logger.info("📂 deleted_counts.json не найден. Создан новый.")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки счётчиков удалённых сообщений: {e}")

def save_user_deleted_counts():
    try:
        with open(DELETED_COUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_deleted_counts, f, indent=2, ensure_ascii=False)
        logger.info("📥 Счётчики удалённых сообщений сохранены.")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении счётчиков удалённых сообщений: {e}")

def get_group_limit(chat_id: int) -> int:
    return group_limits.get(str(chat_id), DEFAULT_LIMIT)

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я антиспам-бот.\n"
        "Команды:\n"
        "/togglefilter\n"
        "/setduplicates <число>\n"
        "/resetlimit\n"
        "/showlimits\n"
        "/status\n"
        "/testadmin"
    )

async def test_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=ADMIN_ID, text="✅ Тест админу.")
    await update.message.reply_text("📨 Отправлено админу")

async def toggle_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global filter_enabled
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Только админ.")
    filter_enabled = not filter_enabled
    status = "🔛 включён" if filter_enabled else "🔴 выключен"
    await update.message.reply_text(f"⚙️ Фильтр {status}")

async def set_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Только админ.")
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("⚠️ Использование: /setduplicates <число>")
    group_limits[str(update.effective_chat.id)] = int(context.args[0])
    save_limits()
    await update.message.reply_text(f"✅ Лимит обновлён: {context.args[0]}")

async def reset_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Только админ.")
    gid = str(update.effective_chat.id)
    group_limits.pop(gid, None)
    save_limits()
    await update.message.reply_text("🔄 Лимит сброшен.")

async def show_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Только админ.")
    if not group_limits:
        return await update.message.reply_text("📭 Нет лимитов.")
    lines = [f"{gid}: {lim}" for gid, lim in group_limits.items()]
    await update.message.reply_text("📋 Лимиты:\n" + "\n".join(lines))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stat = "🟢 активен" if filter_enabled else "🔴 неактивен"
    await update.message.reply_text(f"⚙️ Фильтр {stat}")

# === Обработка сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global deleted_messages_count
    if not filter_enabled or update.effective_chat.type not in ("group", "supergroup"):
        return
    chat_id = str(update.effective_chat.id)
    user = update.message.from_user
    text = (update.message.text or "").strip().lower()
    key = f"{chat_id}:{user.id}:{text[:50]}"
    cnt = user_messages.get(key, 0) + 1
    user_messages[key] = cnt
    save_user_messages()  # сохраняем сразу после обновления счётчика

    if cnt > get_group_limit(update.effective_chat.id):
        try:
            await update.message.delete()
            deleted_messages_count += 1
            user_key = f"{chat_id}:{user.id}"
            user_deleted_counts[user_key] = user_deleted_counts.get(user_key, 0) + 1
            save_user_deleted_counts()  # сохраняем после удаления

            uname = f"@{user.username}" if user.username else f"ID:{user.id}"
            message_text = (
                f"🚨 Повтор в группе\n"
                f"👤 {uname}\n"
                f"🧾 {text[:100]}\n"
                f"⛔️ Сообщение удалено"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=message_text)
            logger.info(f"Удалено от {uname}")
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения: {e}", exc_info=True)

# === Self-ping и отчёт ===
async def cleanup_and_report(context: ContextTypes.DEFAULT_TYPE):
    global deleted_messages_count, user_messages
    report = (
        f"🧹 Очистка\n"
        f"Удалено сообщений: {deleted_messages_count}\n"
        f"Время: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=report)

    user_messages.clear()
    save_user_messages()
    deleted_messages_count = 0
    logger.info("📥 Очистка и отчёт выполнены.")

async def ping_self(context: ContextTypes.DEFAULT_TYPE):
    if not SELF_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(SELF_URL)
            logger.info(f"🛰 Self-ping status: {resp.status}")
    except Exception as e:
        logger.warning(f"⚠️ Self-ping error: {e}")

# === Health check сервер ===
async def health_handler(request):
    return web.Response(text="OK", content_type='text/plain')

async def run_health_server_async(app: ContextTypes.DEFAULT_TYPE):
    server = web.Application()
    server.router.add_get("/", health_handler)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("🚦 Health check сервер запущен на порту 8080")

# === Главная функция ===
def main():
    load_limits()
    load_user_messages()
    load_user_deleted_counts()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(run_health_server_async)
        .build()
    )

    # Команды
    for cmd, fn in [
        ("start", start), ("help", start),
        ("testadmin", test_admin),
        ("togglefilter", toggle_filter),
        ("setduplicates", set_duplicates),
        ("resetlimit", reset_limit),
        ("showlimits", show_limits),
        ("status", status),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Периодические задачи
    # Отчёт и очистка - 24 часа (86400 секунд)
    app.job_queue.run_repeating(cleanup_and_report, interval=86400, first=60)

    # Self-ping - 9 минут (540 секунд)
    app.job_queue.run_repeating(ping_self, interval=540, first=120)

    logger.info("🚀 Бот запускается")
    app.run_polling()

if __name__ == "__main__":
    main()

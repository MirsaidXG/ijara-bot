import logging
import os
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# === Настройка ===
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
if not TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
LIMITS_FILE = "limits.json"
DEFAULT_LIMIT = 2

# === Логирование ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger()

from cachetools import TTLCache
user_messages = TTLCache(maxsize=10000, ttl=3600)
group_limits = {}
deleted_messages_count = 0
filter_enabled = True

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

def get_group_limit(chat_id: int) -> int:
    return group_limits.get(chat_id, DEFAULT_LIMIT)

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
    group_limits[update.effective_chat.id] = int(context.args[0])
    save_limits()
    await update.message.reply_text(f"✅ Лимит обновлён: {context.args[0]}")

async def reset_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Только админ.")
    gid = update.effective_chat.id
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global deleted_messages_count
    if not filter_enabled or update.effective_chat.type not in ("group", "supergroup"):
        return
    chat_id = update.effective_chat.id
    user = update.message.from_user
    text = (update.message.text or "").strip().lower()
    key = f"{chat_id}:{user.id}:{text[:50]}"
    cnt = user_messages.get(key, 0) + 1
    user_messages[key] = cnt
    if cnt > get_group_limit(chat_id):
        try:
            await update.message.delete()
            deleted_messages_count += 1
            uname = f"@{user.username}" if user.username else f"ID:{user.id}"
            message_text = (
                f"🚨 Повтор в группе\n"
                f"👤 {uname}\n"
                f"🧾 {text[:100]}\n"
                f"⛔️ Сообщение удалено"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=message_text)
            logger.info(f"Удалено от {uname}")
        except Exception:
            logger.error("Ошибка удаления", exc_info=True)

async def cleanup_and_report(context: ContextTypes.DEFAULT_TYPE):
    global deleted_messages_count
    report = (
        f"🧹 Очистка\n"
        f"Удалено: {deleted_messages_count}\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=report)
    user_messages.clear()
    deleted_messages_count = 0

async def ping_self(context: ContextTypes.DEFAULT_TYPE):
    url = os.getenv("SELF_URL")
    if not url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(url)
            logger.info(f"🛰 Self-ping status: {resp.status}")
    except Exception as e:
        logger.warning(f"⚠️ Self-ping error: {e}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    server = HTTPServer(("", 8080), HealthHandler)
    server.serve_forever()

def main():
    load_limits()
    app = ApplicationBuilder().token(TOKEN).build()

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

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(cleanup_and_report, interval=36000, first=60)
    app.job_queue.run_repeating(ping_self, interval=540, first=120)

    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info("Сервер self-ping запущен на порту 8080")

    app.run_polling()

if __name__ == "__main__":
    main()

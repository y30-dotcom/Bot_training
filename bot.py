import os
import sqlite3
import logging
import asyncio
import threading
from datetime import datetime
from flask import Flask, request

import openai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

# Загружаем переменные из .env (для локальной разработки)
load_dotenv()
print("Текущая папка:", os.getcwd())
print("Файлы в папке:", os.listdir('.'))
print("BOT_TOKEN из env:", os.getenv("BOT_TOKEN"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ваш Telegram ID
EXCEL_PATH = os.getenv("EXCEL_PATH", "teen_portfolio.xlsx")  # укажите правильное имя
DB_PATH = os.getenv("DB_PATH", "responses.db")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable is required")

if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY environment variable is required")

# Настройка DeepSeek
openai.api_key = OPENAI_API_KEY
openai.api_base = "https://api.deepseek.com"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Вопросы квиза
QUESTIONS = [
    {
        "text": "Какой у вас уровень опыта?",
        "options": ["Начальный", "Продвинутый"],
    },
    {
        "text": "Как часто вы занимаетесь?",
        "options": ["Раз в неделю", "Каждый день"],
    },
]

# Хранилище ответов пользователей
user_answers = {}
allowed_chat = set()

# Глобальная переменная для приложения Telegram (доступна из Flask)
telegram_app = None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            q1 TEXT,
            q2 TEXT
        )
        """
    )
    conn.commit()
    conn.close()


async def save_response_row(row):
    def _insert(r):
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO responses (timestamp, user_id, username, first_name, last_name, q1, q2) VALUES (?,?,?,?,?,?,?)",
            r,
        )
        conn.commit()
        conn.close()

    await asyncio.get_event_loop().run_in_executor(None, _insert, tuple(row))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Мы запустим короткий квиз из 2 вопросов. Нажмите кнопку ниже, чтобы начать.")
    keyboard = [[InlineKeyboardButton("Начать квиз", callback_data="quiz_start")]]
    await update.message.reply_text("Готовы?", reply_markup=InlineKeyboardMarkup(keyboard))


async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "quiz_start":
        user_answers[user_id] = {"user": query.from_user, "answers": [], "step": 0}
        await send_question(query, context, user_id)
        return

    if query.data.startswith("q"):
        parts = query.data.split("_")
        step = int(parts[0][1:])
        choice = int(parts[1])
        ua = user_answers.get(user_id)
        if not ua:
            await query.edit_message_text("Квиз не найден. Отправьте /start чтобы начать снова.")
            return
        ua["answers"].append(QUESTIONS[step]["options"][choice])
        ua["step"] = step + 1
        if ua["step"] < len(QUESTIONS):
            await send_question(query, context, user_id)
        else:
            await finish_quiz(query, context, user_id)


async def send_question(query, context, user_id):
    ua = user_answers[user_id]
    step = ua["step"]
    q = QUESTIONS[step]
    buttons = [InlineKeyboardButton(opt, callback_data=f"q{step}_{i}") for i, opt in enumerate(q["options"])]
    keyboard = [[b] for b in buttons]
    if isinstance(query, Update) and query.callback_query:
        await query.callback_query.edit_message_text(q["text"], reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(chat_id=user_id, text=q["text"], reply_markup=InlineKeyboardMarkup(keyboard))


async def finish_quiz(query, context, user_id):
    ua = user_answers[user_id]
    user = ua["user"]
    answers = ua["answers"]

    row = [datetime.utcnow().isoformat(), user.id, user.username or "", user.first_name or "", user.last_name or "",
           answers[0], answers[1]]
    try:
        await save_response_row(row)
    except Exception:
        logger.exception("Failed to save response to DB")

    admin_text = (
        f"Новый ответ от пользователя:\n"
        f"ID: {user.id}\n"
        f"Username: @{user.username if user.username else ''}\n"
        f"Name: {user.first_name or ''} {user.last_name or ''}\n"
        f"Answers: {answers}"
    )
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=admin_text)
        except Exception as e:
            logger.exception("Failed to send admin message: %s", e)

    if os.path.exists(EXCEL_PATH):
        try:
            with open(EXCEL_PATH, "rb") as f:
                await context.bot.send_document(chat_id=user.id, document=f, filename=os.path.basename(EXCEL_PATH))
        except Exception as e:
            logger.exception("Failed to send Excel to user: %s", e)
            await context.bot.send_message(chat_id=user.id,
                                           text="Не удалось отправить файл. Свяжитесь с администратором.")
    else:
        await context.bot.send_message(chat_id=user.id,
                                       text=f"Подарочный файл не найден на сервере (ожидался: {EXCEL_PATH}).")

    allowed_chat.add(user.id)
    await context.bot.send_message(chat_id=user.id,
                                   text="Спасибо! Вы можете теперь общаться с ИИ — просто напишите сообщение.")


async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in allowed_chat:
        await update.message.reply_text("Сперва пройдите квиз: отправьте /start")
        return

    prompt = update.message.text
    await update.message.chat.send_action("typing")
    try:
        resp = await asyncio.get_event_loop().run_in_executor(None, lambda: openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7,
        ))
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.exception("DeepSeek request failed: %s", e)
        answer = "Ошибка при обращении к ИИ. Попробуйте позже."

    await update.message.reply_text(answer)


async def get_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Ваш chat_id: {user.id}\nОтправьте этот ID администратору для получения личных уведомлений.")


async def get_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет файл базы данных администратору"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("Доступ запрещён.")
        return
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(document=f, filename="responses.db")
    except Exception as e:
        logger.exception("Failed to send DB")
        await update.message.reply_text("Не удалось отправить базу данных.")


# ---------- Flask для webhook ----------
flask_app = Flask(__name__)


@flask_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    """Обработчик входящих обновлений от Telegram"""
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        telegram_app.process_update(update)
    return 'ok', 200


@flask_app.route('/')
def index():
    return 'Bot is running!', 200


def run_flask():
    """Запускает Flask-сервер на указанном порту"""
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port)


# ---------- Основная функция ----------
def main():
    global telegram_app

    init_db()

    # Создаём приложение Telegram
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("get_my_id", get_my_id))
    application.add_handler(CommandHandler("get_db", get_db))  # новая команда
    application.add_handler(CallbackQueryHandler(quiz_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))

    telegram_app = application

    # Устанавливаем webhook
    # Render автоматически задаёт переменную RENDER_EXTERNAL_HOSTNAME с URL вашего сервиса
    render_url = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if render_url:
        webhook_url = f"https://{render_url}/{BOT_TOKEN}"
    else:
        # Для локального тестирования можно задать вручную или использовать ngrok
        webhook_url = f"https://ваш-домен/{BOT_TOKEN}"  # замените при локальном тестировании
        logger.warning("RENDER_EXTERNAL_HOSTNAME не задан, используется заглушка")

    application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook установлен на {webhook_url}")

    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Бесконечный цикл, чтобы программа не завершалась
    while True:
        import time
        time.sleep(60)


if __name__ == "__main__":
    main()

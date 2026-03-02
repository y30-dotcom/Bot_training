import os
import sqlite3
import logging
import asyncio
from datetime import datetime

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

load_dotenv()
print("Текущая папка:", os.getcwd())
print("Файлы в папке:", os.listdir('.'))
print("BOT_TOKEN из env:", os.getenv("BOT_TOKEN"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # numeric id e.g. 123456789
EXCEL_PATH = os.getenv("EXCEL_PATH", "quiz_gift.xlsx")
DB_PATH = os.getenv("DB_PATH", "responses.db")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable is required")

if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY environment variable is required")

openai.api_key = OPENAI_API_KEY
openai.api_base = "https://api.deepseek.com"  # Важно! Меняем адрес API на DeepSeek

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simple quiz: two questions with two choices each
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

# In-memory storage for current quiz answers and chat access
user_answers = {}
allowed_chat = set()


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
    user = update.effective_user
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

    # handle option selection like q0_0, q1_1
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
            # finished
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

    # Save locally (to SQLite)
    row = [datetime.utcnow().isoformat(), user.id, user.username or "", user.first_name or "", user.last_name or "", answers[0], answers[1]]
    try:
        await save_response_row(row)
    except Exception:
        logger.exception("Failed to save response to DB")

    # Notify admin (developer) in personal messages if ADMIN_CHAT_ID is set
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

    # Send Excel gift to user
    if os.path.exists(EXCEL_PATH):
        try:
            with open(EXCEL_PATH, "rb") as f:
                await context.bot.send_document(chat_id=user.id, document=f, filename=os.path.basename(EXCEL_PATH))
        except Exception as e:
            logger.exception("Failed to send Excel to user: %s", e)
            await context.bot.send_message(chat_id=user.id, text="Не удалось отправить файл. Свяжитесь с администратором.")
    else:
        await context.bot.send_message(chat_id=user.id, text=f"Подарочный файл не найден на сервере (ожидался: {EXCEL_PATH}).")

    # Allow AI chat for this user
    allowed_chat.add(user.id)
    await context.bot.send_message(chat_id=user.id, text="Спасибо! Вы можете теперь общаться с ИИ — просто напишите сообщение.")


async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in allowed_chat:
        await update.message.reply_text("Сперва пройдите квиз: отправьте /start")
        return

    prompt = update.message.text
    await update.message.chat.send_action("typing")
    try:
        resp = await asyncio.get_event_loop().run_in_executor(None, lambda: openai.ChatCompletion.create(
            model="deepseek-chat",  # Модель DeepSeek
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


def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("get_my_id", get_my_id))
    app.add_handler(CallbackQueryHandler(quiz_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))

    logger.info("Starting bot...")
    app.run_polling()


if __name__ == "__main__":
    main()

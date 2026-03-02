import os
import sqlite3
import logging
import asyncio
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

# Загружаем переменные окружения из .env (для локальной разработки)
load_dotenv()
print("Текущая папка:", os.getcwd())
print("Файлы в папке:", os.listdir('.'))
print("BOT_TOKEN из env:", os.getenv("BOT_TOKEN"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
EXCEL_PATH = os.getenv("EXCEL_PATH", "teen_portfolio.xlsx")
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

# Вопросы
QUESTIONS = [
    {"type": "options", "text": "Сколько лет вашему ребёнку?",
     "options": ["7–8", "9–10", "11–12", "13–14", "15+"]},
    {"type": "options", "text": "Как часто ребёнок пользуется компьютером или планшетом?",
     "options": ["Почти каждый день", "Пару раз в неделю", "Почти никогда"]},
    {"type": "text", "text": "С какими программами уже знаком? (перечислите через запятую, например: Scratch, Minecraft, Excel. Если не знаком, напишите «не знаком»)"},
    {"type": "options", "text": "Рисует, монтирует видео, создаёт что-то своё?",
     "options": ["Да", "Нет", "Хочет научиться"]},
    {"type": "options", "text": "Как лучше заниматься?",
     "options": ["В группе", "Индивидуально", "Не знаю"]},
    {"type": "text", "text": "Как зовут ребёнка? (напишите имя)"},
    {"type": "text", "text": "Ваш номер телефона для связи (в любом формате)"}
]

# Хранилище данных пользователя
user_answers = {}
allowed_chat = set()

# Глобальная переменная для приложения Telegram
telegram_app = None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            age TEXT,
            frequency TEXT,
            software TEXT,
            creative TEXT,
            format TEXT,
            child_name TEXT,
            phone TEXT
        )
    """)
    conn.commit()
    conn.close()


async def save_response_row(row):
    def _insert(r):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO responses (
                timestamp, user_id, username, first_name, last_name,
                age, frequency, software, creative, format, child_name, phone
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, r)
        conn.commit()
        conn.close()
    await asyncio.get_event_loop().run_in_executor(None, _insert, tuple(row))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Мы проведём небольшой опрос, чтобы лучше узнать вашего ребёнка.\n"
        "Нажмите кнопку ниже, чтобы начать."
    )
    keyboard = [[InlineKeyboardButton("Начать опрос", callback_data="quiz_start")]]
    await update.message.reply_text("Готовы?", reply_markup=InlineKeyboardMarkup(keyboard))


async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "quiz_start":
        user_answers[user_id] = {"user": query.from_user, "answers": [], "step": 0, "waiting_for_text": False}
        await send_question(query, context, user_id)
        return

    if query.data.startswith("q"):
        parts = query.data.split("_")
        step = int(parts[0][1:])
        choice = int(parts[1])
        ua = user_answers.get(user_id)
        if not ua:
            await query.edit_message_text("Опрос не найден. Отправьте /start чтобы начать снова.")
            return
        ua["answers"].append(QUESTIONS[step]["options"][choice])
        ua["step"] = step + 1
        ua["waiting_for_text"] = False
        if ua["step"] < len(QUESTIONS):
            await send_question(query, context, user_id)
        else:
            await finish_quiz(query, context, user_id)


async def send_question(query, context, user_id):
    ua = user_answers.get(user_id)
    if not ua:
        return
    step = ua["step"]
    q = QUESTIONS[step]

    if q["type"] == "options":
        buttons = [InlineKeyboardButton(opt, callback_data=f"q{step}_{i}") for i, opt in enumerate(q["options"])]
        keyboard = [[b] for b in buttons]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = q["text"]
        if isinstance(query, Update) and query.callback_query:
            await query.callback_query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    else:
        text = q["text"]
        if isinstance(query, Update) and query.callback_query:
            await query.callback_query.edit_message_text(text)
        else:
            await context.bot.send_message(chat_id=user_id, text=text)
        ua["waiting_for_text"] = True


async def finish_quiz(query, context, user_id):
    ua = user_answers[user_id]
    user = ua["user"]
    answers = ua["answers"]

    row = [
        datetime.utcnow().isoformat(),
        user.id,
        user.username or "",
        user.first_name or "",
        user.last_name or "",
        answers[0] if len(answers) > 0 else "",
        answers[1] if len(answers) > 1 else "",
        answers[2] if len(answers) > 2 else "",
        answers[3] if len(answers) > 3 else "",
        answers[4] if len(answers) > 4 else "",
        answers[5] if len(answers) > 5 else "",
        answers[6] if len(answers) > 6 else ""
    ]
    try:
        await save_response_row(row)
    except Exception:
        logger.exception("Failed to save response to DB")

    admin_text = (
        f"✅ Новый ответ от пользователя:\n"
        f"ID: {user.id}\nUsername: @{user.username or ''}\nName: {user.first_name or ''} {user.last_name or ''}\n"
        f"Ответы:\n1. Возраст: {answers[0]}\n2. Частота: {answers[1]}\n3. Программы: {answers[2]}\n"
        f"4. Творчество: {answers[3]}\n5. Формат: {answers[4]}\n6. Имя ребёнка: {answers[5]}\n7. Телефон: {answers[6]}"
    )
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=admin_text)
        except Exception as e:
            logger.exception("Failed to send admin message: %s", e)

    if os.path.exists(EXCEL_PATH):
        with open(EXCEL_PATH, "rb") as f:
            await context.bot.send_document(chat_id=user.id, document=f, filename=os.path.basename(EXCEL_PATH))
    else:
        await context.bot.send_message(chat_id=user.id, text=f"Подарочный файл не найден на сервере (ожидался: {EXCEL_PATH}).")

    allowed_chat.add(user.id)
    await context.bot.send_message(chat_id=user.id, text="Спасибо! Теперь вы можете общаться с ИИ — просто напишите сообщение.")
    del user_answers[user_id]


async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if user_id in user_answers and user_answers[user_id].get("waiting_for_text"):
        ua = user_answers[user_id]
        ua["answers"].append(update.message.text)
        ua["step"] += 1
        ua["waiting_for_text"] = False
        if ua["step"] < len(QUESTIONS):
            await send_question(update, context, user_id)
        else:
            await finish_quiz(update, context, user_id)
        return

    if user_id not in allowed_chat:
        await update.message.reply_text("Сперва пройдите опрос: отправьте /start")
        return

    await update.message.chat.send_action("typing")
    try:
        resp = await asyncio.get_event_loop().run_in_executor(None, lambda: openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": update.message.text}],
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
    await update.message.reply_text(f"Ваш chat_id: {user.id}")


async def get_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("Доступ запрещён.")
        return
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(document=f, filename="responses.db")
    except Exception as e:
        logger.exception("Failed to send DB")
        await update.message.reply_text("Не удалось отправить базу данных.")


# ---------- Flask-приложение (для Gunicorn) ----------
app = Flask(__name__)


@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        telegram_app.process_update(update)
    return 'ok', 200


@app.route('/')
def index():
    return 'Bot is running!', 200


# ---------- Инициализация Telegram приложения ----------
def setup_telegram():
    global telegram_app
    init_db()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("get_my_id", get_my_id))
    application.add_handler(CommandHandler("get_db", get_db))
    application.add_handler(CallbackQueryHandler(quiz_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))
    telegram_app = application

    # Определяем URL для webhook
    render_url = os.environ.get('RENDER_EXTERNAL_URL')
    if not render_url:
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        if hostname:
            render_url = f"https://{hostname}"
    if render_url:
        webhook_url = f"{render_url}/{BOT_TOKEN}"
    else:
        webhook_url = f"https://ваш-домен/{BOT_TOKEN}"  # для локальной отладки
        logger.warning("RENDER_EXTERNAL_URL и RENDER_EXTERNAL_HOSTNAME не заданы, используется заглушка")

    try:
        # Создаём новый event loop или используем существующий
        loop = asyncio.get_event_loop()
        loop.run_until_complete(application.bot.set_webhook(url=webhook_url))
        logger.info(f"Webhook установлен на {webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка установки webhook: {e}")


# Запускаем инициализацию при старте приложения (один раз)
setup_telegram()

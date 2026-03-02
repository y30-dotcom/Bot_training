<<<<<<< HEAD
# Telegram quiz bot

This repository contains a simple Telegram bot that:

- Runs a short 2-question quiz (multiple choice)
- Sends your answers and user identification to the admin's personal Telegram (requires `ADMIN_CHAT_ID`)
- Sends an Excel file to the user after completing the quiz
- Allows the user to chat with an AI (OpenAI) after the quiz

Files:
- `bot.py` — main bot implementation
- `requirements.txt` — Python dependencies
- `.env.example` — example environment variables

Setup (Windows)

1. Copy `.env.example` to `.env` in the same folder and fill `ADMIN_CHAT_ID` (leave API keys if you want them prefilled).
2. Place the Excel file you want to send as a gift at the path specified by `EXCEL_PATH` (default `quiz_gift.xlsx`).
3. Create and activate a virtual environment, then install dependencies:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Run the bot:

```
set BOT_TOKEN=8530480480:AAFqXZJNbuk9Wk8oh4tKNM1xFV_fu0MSiKY
set OPENAI_API_KEY=sk-55e34c56caa34534a379bb97375866b1
set ADMIN_CHAT_ID=YOUR_NUMERIC_CHAT_ID
python bot.py
```

How to get your numeric `ADMIN_CHAT_ID`:

1. Start the bot (with `BOT_TOKEN` and `OPENAI_API_KEY` set).
2. From your Telegram account, send `/get_my_id` to the bot.
3. The bot will reply with your numeric chat id — paste it into `.env` as `ADMIN_CHAT_ID`.

Notes

- The bot saves a local `responses.csv` with submitted answers.
- The bot saves responses in a local SQLite database (default `responses.db`). You can change the path using the `DB_PATH` environment variable.
- For security, you may prefer to store API keys as environment variables and not commit them.

If you want, I can:

- Wire the provided Excel file into the repo path you specified.
- Add a persistent database instead of a CSV.
- Deploy the bot to a server or a cloud function.
=======
# Bot_training
>>>>>>> 6621c230dc9078557739c834c8083e3e68d4f4a4

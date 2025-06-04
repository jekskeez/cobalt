
import asyncio
import logging
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from aiohttp import ClientSession, CookieJar
from flask import Flask, request, redirect, Response, session as flask_session
import threading
import requests

# Telegram bot token
TOKEN = os.getenv("BOT_TOKEN", "your_token_here")

# Allowed users
ALLOWED_USER_IDS = [1811568463, 630965641]

# File paths
USERS_FILE = "users.txt"

# Global state
user_sessions = {}
user_tasks = {}
flask_sessions = {}

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Telegram bot setup
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Привет! Зайди в мини-приложение и авторизуйся через mpets.mobi. Потом вернись и введи /confirm чтобы сохранить куки.")

async def confirm(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id in flask_sessions:
        cookies = flask_sessions[user_id]
        user_sessions[user_id] = {"mpets": {"owner": update.message.from_user.username, "cookies": cookies, "active": False}}
        await update.message.reply_text("Куки сохранены и сессия добавлена.")
    else:
        await update.message.reply_text("Куки не найдены. Зайдите в миниапп и авторизуйтесь.")

# Авто-действия
async def auto_actions(session_data, session_name):
    actions = [
        "https://mpets.mobi/?action=food",
        "https://mpets.mobi/?action=play",
        "https://mpets.mobi/show",
        "https://mpets.mobi/glade_dig",
        "https://mpets.mobi/show_coin_get",
        "https://mpets.mobi/task_reward?id=46",
        "https://mpets.mobi/task_reward?id=49",
        "https://mpets.mobi/task_reward?id=52"
    ]

    cookies = session_data["cookies"] if isinstance(session_data, dict) else {}
    jar = CookieJar()
    jar.update_cookies(cookies)
    async with ClientSession(cookie_jar=jar) as session:
        while True:
            if asyncio.current_task().cancelled():
                return
            for action in actions[:4]:
                for _ in range(6):
                    await session.get(action)
                    await asyncio.sleep(1)
            for action in actions[4:]:
                await session.get(action)
                await asyncio.sleep(1)
            for i in range(10, 0, -1):
                await session.get(f"https://mpets.mobi/go_travel?id={i}")
                await asyncio.sleep(1)
            await asyncio.sleep(60)

# Flask web server for cookie interception
app = Flask(__name__)
app.secret_key = "secret"

@app.route('/')
def index():
    return redirect("https://mpets.mobi/welcome")

@app.route('/proxy', methods=["GET", "POST"])
def proxy():
    target_url = "https://mpets.mobi" + request.full_path.replace("/proxy", "")
    headers = {key: value for key, value in request.headers if key != 'Host'}
    cookies = request.cookies
    if request.method == "POST":
        resp = requests.post(target_url, data=request.form, headers=headers, cookies=cookies, allow_redirects=False)
    else:
        resp = requests.get(target_url, headers=headers, cookies=cookies, allow_redirects=False)

    # Если авторизация прошла, сохраняем куки
    if resp.status_code in [301, 302] and "mpets.mobi/" in resp.headers.get("Location", ""):
        user_id = request.args.get("tgid")
        if user_id:
            flask_sessions[int(user_id)] = resp.cookies.get_dict()

    response = Response(resp.content, status=resp.status_code)
    for key, value in resp.headers.items():
        if key.lower() not in ['content-encoding', 'transfer-encoding', 'content-length']:
            response.headers[key] = value
    return response

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# Main runner
async def main():
    threading.Thread(target=run_flask).start()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm))
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())

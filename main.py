import asyncio
import logging
import json
import os
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackContext
from aiohttp import ClientSession, CookieJar
from bs4 import BeautifulSoup
from flask import Flask, request, Response, redirect, session as flask_session
import requests

# Токен Telegram-бота
TOKEN = "7775307986:AAGJphxAEAma6ELYf2Xc_2ayozoVVALBRCY"

# Путь к файлу для хранения сессий
USERS_FILE = "users.txt"

# Разрешённые пользователи (ID Telegram) для специальных команд
ALLOWED_USER_IDS = [1811568463, 630965641]

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Глобальные структуры данных для сессий
user_sessions = {}        # сохранённые сессии пользователей {user_id: {session_name: {...}}}
user_tasks = {}           # запущенные фоновые задачи { (user_id, session_name): task }
pending_cookies = {}      # куки, ожидающие подтверждения {(user_id, session_name): cookies_dict}

# Инициализация Flask приложения для WebApp
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev_secret")  # секрет для сессии Flask

# Чтение сохранённых сессий из файла
def read_from_file():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as file:
        lines = file.readlines()
    sessions = []
    for line in lines:
        session_data = line.strip().split(" | ")
        if len(session_data) != 4:
            logging.warning(f"Некорректная строка в файле: {line.strip()}")
            continue
        try:
            cookies = json.loads(session_data[3])
        except json.JSONDecodeError:
            logging.error(f"Ошибка при парсинге JSON для сессии: {session_data[0]}")
            continue
        sessions.append({
            "session_name": session_data[0],
            "owner": session_data[1],
            "user_id": int(session_data[2]),
            "cookies": cookies
        })
    return sessions

# Запись новой сессии в файл (добавление в конец)
def write_to_file(session_name, owner, user_id, cookies):
    with open(USERS_FILE, "a") as file:
        cookies_json = json.dumps(cookies)
        file.write(f"{session_name} | {owner} | {user_id} | {cookies_json}\n")
    logging.info(f"Сессия {session_name} добавлена в файл.")

# Загрузка сессий из файла в память при старте бота
def load_sessions():
    global user_sessions
    sessions = read_from_file()
    for session in sessions:
        # Если куки хранились как список, конвертируем в словарь
        cookies = {cookie['name']: cookie['value'] for cookie in session["cookies"]} if isinstance(session["cookies"], list) else session["cookies"]
        user_sessions.setdefault(session["user_id"], {})[session["session_name"]] = {
            "owner": session["owner"],
            "cookies": cookies,
            "active": False
        }

# Команда /start – приветственное сообщение и список команд
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "Привет! Меня зовут Cobalt, я создан для игры Удивительные Питомцы на сайте mpets.mobi. Благодаря мне ты можешь поставить своего питомца (или нескольких) на 'прокачку', чтобы не заходить в игру каждый день.\n"
        "Все делается автоматически: Кормление, Игра, Выставка, Прогулка, Поиск семян.\n"
        "Обрати внимание: для авторизации питомца в боте требуются cookie. Как получить их, ты можешь узнать в /guide.\n\n"
        "Мои команды:\n"
        "/info – Контактная информация\n"
        "/guide – инструкция по получению cookie\n"
        "/add – добавить новую сессию\n"
        "/del – удалить сессию\n"
        "/list – посмотреть все сессии\n"
        "/on – активировать сессию\n"
        "/off – деактивировать сессию\n"
        "/stats <имя_сессии> – проверить статистику питомца"
    )

# Команда /info – информация о разработчике и канале
async def info(update: Update, context: CallbackContext):
    message = (
        "Связь с разработчиком: [t.me/bakhusse](https://t.me/bakhusse)\n"
        "Телеграм-канал: [t.me/cobalt_mpets](https://t.me/cobalt_mpets)"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

# Команда /guide – инструкция по получению cookie (обновлена для WebApp)
async def guide(update: Update, context: CallbackContext):
    message = (
        "Теперь получить cookie можно через встроенное мини-приложение Telegram!\n"
        "Просто используй команду /add, укажи название новой сессии и следуй инструкции для авторизации в мини-приложении.\n"
        "После успешной авторизации вернись в чат и введи /confirm для сохранения сессии."
    )
    await update.message.reply_text(message)

# Команда /add – добавить новую сессию (с поддержкой WebApp авторизации)
async def add_session(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    # Проверяем аргументы: требуется имя сессии
    if len(context.args) < 1:
        await update.message.reply_text("Использование: /add <имя_сессии>")
        return
    session_name = context.args[0]
    # Проверяем, что сессия с таким именем еще не существует у этого пользователя
    if user_id in user_sessions and session_name in user_sessions[user_id]:
        await update.message.reply_text(f"Сессия с именем `{session_name}` уже существует.", parse_mode='Markdown')
        return
    # Формируем URL для открытия WebApp (мини-приложения)
    tgid = user_id
    webapp_url = f"https://cobalt-t7qb.onrender.com/?tgid={tgid}&name={session_name}"
    # Кнопка для открытия мини-приложения
    web_app_info = WebAppInfo(url=webapp_url)
    button = InlineKeyboardButton("🔑 Авторизоваться через MPets", web_app=web_app_info)
    keyboard = InlineKeyboardMarkup([[button]])
    await update.message.reply_text(
        f"Для сессии *{session_name}* нажмите кнопку ниже и войдите в MPets:",
        parse_mode='Markdown',
        reply_markup=keyboard
    )
    logging.info(f"Пользователь {user_id} инициировал добавление сессии '{session_name}' через WebApp.")

# Команда /confirm – подтвердить и сохранить сессию после авторизации через WebApp
async def confirm_session(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    session_name = None
    cookies = None
    # Если указано имя сессии в аргументах, пытаемся получить куки для неё
    if context.args:
        session_name = context.args[0]
        key = (user_id, session_name)
        cookies = pending_cookies.get(key)
        if not cookies:
            await update.message.reply_text(f"Не найдены авторизационные данные для сессии `{session_name}`. Сначала используйте /add для этой сессии.", parse_mode='Markdown')
            return
    else:
        # Если имя не указано, но у пользователя есть ровно одна ожидающая сессия – используем её
        pending_for_user = [name for (uid, name) in pending_cookies.keys() if uid == user_id]
        if not pending_for_user:
            await update.message.reply_text("Нет незавершённых авторизаций. Сначала воспользуйтесь командой /add.")
            return
        if len(pending_for_user) > 1:
            await update.message.reply_text(
                "У вас несколько сессий ожидают подтверждения. Введите /confirm <имя_сессии> для каждой из них."
            )
            return
        # Единственная ожидающая сессия
        session_name = pending_for_user[0]
        cookies = pending_cookies.get((user_id, session_name))
        if not cookies:
            await update.message.reply_text("Куки не найдены. Попробуйте пройти авторизацию заново через /add.")
            return
    # Добавляем новую сессию в user_sessions
    user_sessions.setdefault(user_id, {})
    if session_name in user_sessions[user_id]:
        # Если по каким-то причинам сессия уже существует (напр., повторное подтверждение)
        await update.message.reply_text(f"Сессия `{session_name}` уже сохранена.", parse_mode='Markdown')
        # Удаляем отложенные куки, если они ещё есть
        pending_cookies.pop((user_id, session_name), None)
        return
    user_sessions[user_id][session_name] = {
        "owner": update.message.from_user.username or "",
        "cookies": cookies,
        "active": False
    }
    # Записываем сессию в файл
    write_to_file(session_name, update.message.from_user.username or "", user_id, cookies)
    # Очищаем временное хранилище куки
    pending_cookies.pop((user_id, session_name), None)
    await update.message.reply_text(f"Сессия *{session_name}* успешно сохранена! Теперь вы можете активировать её командой /on.", parse_mode='Markdown')
    logging.info(f"Пользователь {user_id} подтвердил и сохранил сессию '{session_name}'.")

# Команда /del – удалить сессию
async def remove_session(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if len(context.args) < 1:
        await update.message.reply_text("Использование: /del <имя_сессии>")
        return
    session_name = context.args[0]
    # Проверяем, существует ли такая сессия у пользователя
    if user_id in user_sessions and session_name in user_sessions[user_id]:
        session = user_sessions[user_id][session_name]
        if session["active"]:
            await update.message.reply_text(f"Сессия {session_name} активна. Сначала деактивируйте её командой /off.")
            return
        # Удаляем сессию из памяти
        user_sessions[user_id].pop(session_name)
        # Обновляем файл, исключая удалённую сессию
        sessions = read_from_file()
        new_sessions = [s for s in sessions if not (s['user_id'] == user_id and s['session_name'] == session_name)]
        with open(USERS_FILE, "w") as file:
            for s in new_sessions:
                cookies_json = json.dumps(s['cookies'])
                file.write(f"{s['session_name']} | {s['owner']} | {s['user_id']} | {cookies_json}\n")
        await update.message.reply_text(f"Сессия {session_name} удалена.")
        logging.info(f"Сессия {session_name} удалена для пользователя {user_id}.")
    else:
        await update.message.reply_text(f"Сессия с именем {session_name} не найдена.")

# Команда /list – показать все сохранённые сессии пользователя
async def list_sessions(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id in user_sessions and user_sessions[user_id]:
        sessions_list = "\n".join([f"• {name} ({'активна' if data['active'] else 'выключена'})" for name, data in user_sessions[user_id].items()])
        await update.message.reply_text(f"Ваши сессии:\n{sessions_list}")
    else:
        await update.message.reply_text("У вас нет сохранённых сессий.")

# Команда /on – активировать одну или все сессии
async def activate_session(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if len(context.args) < 1:
        await update.message.reply_text("Использование: /on <имя_сессии> или /on all")
        return
    session_name = context.args[0]
    # Если указано "all", активируем все сессии пользователя
    if session_name == "all":
        if user_id in user_sessions and user_sessions[user_id]:
            for name, session in user_sessions[user_id].items():
                if not session["active"]:
                    session["active"] = True
                    logging.info(f"Сессия {name} активирована для пользователя {user_id}.")
                    # Запускаем фоновые действия для этой сессии
                    task = asyncio.create_task(auto_actions(session["cookies"], name))
                    user_tasks[(user_id, name)] = task
            await update.message.reply_text("Все ваши сессии активированы и запущены!")
        else:
            await update.message.reply_text("У вас нет сохранённых сессий.")
    else:
        # Активируем указанную сессию
        if user_id in user_sessions and session_name in user_sessions[user_id]:
            if user_sessions[user_id][session_name]["active"]:
                await update.message.reply_text(f"Сессия {session_name} уже активна.")
            else:
                user_sessions[user_id][session_name]["active"] = True
                task = asyncio.create_task(auto_actions(user_sessions[user_id][session_name]["cookies"], session_name))
                user_tasks[(user_id, session_name)] = task
                await update.message.reply_text(f"Сессия {session_name} активирована!")
                logging.info(f"Сессия {session_name} активирована для пользователя {user_id}.")
        else:
            await update.message.reply_text(f"Сессия с именем {session_name} не найдена.")

# Команда /off – деактивировать одну или все сессии
async def deactivate_session(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if len(context.args) < 1:
        await update.message.reply_text("Использование: /off <имя_сессии> или /off all")
        return
    session_name = context.args[0]
    if session_name == "all":
        if user_id in user_sessions and user_sessions[user_id]:
            for name, session in user_sessions[user_id].items():
                if session["active"]:
                    session["active"] = False
                    task = user_tasks.get((user_id, name))
                    if task:
                        task.cancel()
                        user_tasks.pop((user_id, name), None)
                    logging.info(f"Сессия {name} деактивирована для пользователя {user_id}.")
            await update.message.reply_text("Все сессии деактивированы.")
        else:
            await update.message.reply_text("У вас нет активных сессий.")
    else:
        if user_id in user_sessions and session_name in user_sessions[user_id]:
            if user_sessions[user_id][session_name]["active"]:
                user_sessions[user_id][session_name]["active"] = False
                task = user_tasks.get((user_id, session_name))
                if task:
                    task.cancel()
                    user_tasks.pop((user_id, session_name), None)
                await update.message.reply_text(f"Сессия {session_name} деактивирована.")
                logging.info(f"Сессия {session_name} деактивирована для пользователя {user_id}.")
            else:
                await update.message.reply_text(f"Сессия {session_name} уже выключена.")
        else:
            await update.message.reply_text(f"Сессия с именем {session_name} не найдена.")

# Команда /stats – получить статистику питомца по указанной сессии
async def stats(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        await update.message.reply_text("Использование: /stats <имя_сессии>")
        return
    session_name = context.args[0]
    user_id = update.message.from_user.id
    if user_id not in user_sessions or session_name not in user_sessions[user_id]:
        await update.message.reply_text(f"Сессия с именем {session_name} не найдена.")
        return
    # Получаем куки для выбранной сессии
    cookies = user_sessions[user_id][session_name]["cookies"]
    cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies} if isinstance(cookies, list) else cookies
    # Создаём клиентскую сессию aiohttp и подставляем куки
    async with ClientSession(cookie_jar=CookieJar()) as session:
        session.cookie_jar.update_cookies(cookies_dict)
        stats_text = await fetch_pet_stats(session)
    if stats_text:
        await update.message.reply_text(stats_text)
    else:
        await update.message.reply_text(f"Не удалось получить статистику для сессии {session_name}.")

# Вспомогательная функция для получения статистики питомца с сайта
async def fetch_pet_stats(session: ClientSession):
    url = "https://mpets.mobi/profile"
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return f"Ошибка при загрузке страницы профиля: {response.status}"
            page = await response.text()
    except Exception as e:
        logging.error(f"Ошибка при запросе статистики: {e}")
        return None
    soup = BeautifulSoup(page, 'html.parser')
    stat_items = soup.find_all('div', class_='stat_item')
    if not stat_items:
        return "Не удалось найти элементы статистики."
    # Извлекаем ключевую информацию о питомце
    pet_name_tag = stat_items[0].find('a', class_='darkgreen_link')
    if not pet_name_tag:
        return "Не удалось определить имя питомца."
    pet_name = pet_name_tag.text.strip()
    # Предполагается, что уровень – предпоследнее слово в первом элементе стат
    pet_level = stat_items[0].text.split()[-2] if stat_items[0].text else "N/A"
    experience = "Не найдено"
    beauty = "Не найдено"
    coins = "Не найдено"
    hearts = "Не найдено"
    for item in stat_items:
        text = item.text.strip()
        if 'Опыт:' in text:
            experience = text.split('Опыт:')[-1].strip()
        if 'Красота:' in text:
            beauty = text.split('Красота:')[-1].strip()
        if 'Монеты:' in text:
            coins = text.split('Монеты:')[-1].strip()
        if 'Сердечки:' in text:
            hearts = text.split('Сердечки:')[-1].strip()
    # Формируем текст статистики
    stats_text = (f"*{pet_name}* — уровень {pet_level}\n"
                  f"Опыт: {experience}\n"
                  f"Красота: {beauty}\n"
                  f"Монеты: {coins}\n"
                  f"Сердечки: {hearts}")
    return stats_text

# Команда для получения информации о владельце сессии
async def get_user(update: Update, context: CallbackContext):
    # Проверка, что пользователь имеет разрешение
    user_id = update.message.from_user.id
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("У вас нет прав на использование этой команды.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Использование: /get_user <имя_сессии>")
        return

    session_name = context.args[0]

    session_info = read_from_file()
    for session in session_info:
        if session["session_name"] == session_name:
            response = f"Сессия: {session_name}\n"
            response += f"Владелец: {session['owner']}\n"

            # Форматируем куки как скрытый блок
            cookies = json.dumps(session['cookies'], indent=4)  # Форматируем куки с отступами для читаемости
            hidden_cookies = f"```json\n{cookies}```"  # Скрываем куки в блоке, доступном для раскрытия

            response += f"Куки:\n {hidden_cookies}"  # Добавляем цитату с куками

            await send_message(update, response)
            return

    await update.message.reply_text(f"Сессия с именем {session_name} не найдена.")

# Вспомогательная функция автоматических действий (периодические запросы для прокачки питомца)
async def auto_actions(session_cookies, session_name):
    # URL-адреса для автоматических действий
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
    # Формируем словарь cookies (если хранится список объектов)
    cookies_dict = {c['name']: c['value'] for c in session_cookies} if isinstance(session_cookies, list) else (session_cookies.get("cookies", {}) if "cookies" in session_cookies else session_cookies)
    # Создаём aiohttp-сессию с заданными cookie
    cookie_jar = CookieJar()
    for name, value in cookies_dict.items():
        cookie_jar.update_cookies({name: value})
    async with ClientSession(cookie_jar=cookie_jar) as web_session:
        while True:
            if asyncio.current_task().cancelled():
                logging.info(f"Автозадача для сессии {session_name} отменена.")
                return
            # Первые 4 действия повторяем 6 раз с паузой 1 сек
            for action_url in actions[:4]:
                for _ in range(6):
                    await visit_url(web_session, action_url, session_name)
                    await asyncio.sleep(1)
            # Оставшиеся действия выполняем по 1 разу
            for action_url in actions[4:]:
                await visit_url(web_session, action_url, session_name)
                await asyncio.sleep(1)
            # Дополнительные переходы с параметром id от 10 до 1
            for i in range(10, 0, -1):
                url = f"https://mpets.mobi/go_travel?id={i}"
                await visit_url(web_session, url, session_name)
                await asyncio.sleep(1)
            # Пауза между циклами (60 секунд)
            await asyncio.sleep(60)

# Вспомогательная функция для выполнения GET-запроса и логирования результата
async def visit_url(web_session, url, session_name):
    try:
        async with web_session.get(url) as response:
            if response.status == 200:
                logging.info(f"[{session_name}] Переход по {url} выполнен успешно.")
            else:
                logging.error(f"[{session_name}] Ошибка {response.status} при переходе по {url}.")
    except Exception as e:
        logging.error(f"[{session_name}] Ошибка при запросе {url}: {e}")

# Flask маршрут: корневой – перенаправление на страницу авторизации MPets
@app.route('/')
def webapp_root():
    tgid = request.args.get("tgid")
    session_name = request.args.get("name")
    if not tgid or not session_name:
        return "Ошибка: отсутствуют параметры tgid или name в URL.", 400
    try:
        flask_session['tgid'] = int(tgid)
    except ValueError:
        return "Некорректный идентификатор Telegram.", 400
    flask_session['session_name'] = session_name
    # Перенаправляем на прокси-страницу авторизации MPets
    return redirect("/welcome")

# Flask маршрут: прокси для запросов к mpets.mobi (логин через WebApp)
@app.route('/', defaults={'url_path': ''}, methods=['GET', 'POST'])
@app.route('/<path:url_path>', methods=['GET', 'POST'])
def proxy_mpets(url_path):
    target_url = f"https://mpets.mobi/{url_path}"
    method = request.method
    headers = {key: value for key, value in request.headers if key.lower() != 'host'}
    cookies = request.cookies
    try:
        if method == 'POST':
            resp = requests.post(target_url, data=request.form, headers=headers, cookies=cookies, allow_redirects=False)
        else:
            resp = requests.get(target_url, headers=headers, cookies=cookies, allow_redirects=False)
    except Exception as e:
        logging.error(f"Ошибка прокси-запроса к {target_url}: {e}")
        return "Ошибка соединения с MPets.", 502

    if url_path.lower() == "login" and resp.status_code in (301, 302):
        tgid = flask_session.get("tgid")
        session_name = flask_session.get("session_name")
        if tgid and session_name:
            pending_cookies[(tgid, session_name)] = resp.cookies.get_dict()
            logging.info(f"Получены куки для user_id={tgid}, session='{session_name}'. Ожидается подтверждение /confirm.")
        return (
            "<html><body style='text-align:center; font-family:Arial,sans-serif;'>"
            "<h2>✅ Авторизация успешна!</h2>"
            "<p>Теперь вы можете закрыть это окно и вернуться в бот.<br>"
            "Отправьте команду <b>/confirm</b> в чате, чтобы сохранить сессию.</p>"
            "<button onclick=\"window.close()\" style='padding:10px 20px; font-size:16px; cursor:pointer;'>Закрыть</button>"
            "</body></html>"
        )

    excluded_headers = ['content-encoding', 'transfer-encoding', 'content-length', 'connection']
    response = Response(resp.content, status=resp.status_code)
    for header, value in resp.headers.items():
        if header.lower() not in (h.lower() for h in excluded_headers):
            response.headers[header] = value
    return response

# Основная функция запуска Telegram-бота
async def main_bot():
    app_tg = Application.builder().token(TOKEN).build()
    load_sessions()
    # Регистрация обработчиков команд
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("info", info))
    app_tg.add_handler(CommandHandler("guide", guide))
    app_tg.add_handler(CommandHandler("add", add_session))
    app_tg.add_handler(CommandHandler("confirm", confirm_session))
    app_tg.add_handler(CommandHandler("del", remove_session))
    app_tg.add_handler(CommandHandler("list", list_sessions))
    app_tg.add_handler(CommandHandler("on", activate_session))
    app_tg.add_handler(CommandHandler("off", deactivate_session))
    app_tg.add_handler(CommandHandler("stats", fetch_pet_stats))
    app_tg.add_handler(CommandHandler("get_user", get_user))
    # Специальные команды для разрешённых пользователей (если нужны)
    app_tg.add_handler(CommandHandler("aon", activate_session))   # возможно, объединяется с /on
    app_tg.add_handler(CommandHandler("aoff", deactivate_session))  # возможно, объединяется с /off
    # Запуск бота (довольно продолжительный, пока бот не остановлен)
    await app_tg.run_polling()

# Запуск Flask и Telegram бота в одном процессе
if __name__ == "__main__":
    # Запускаем веб-сервер Flask в отдельном потоке
    port = int(os.environ.get('PORT', 5000))
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    # Запускаем Telegram-бота в основном потоке
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(main_bot())

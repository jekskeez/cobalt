import asyncio
import logging
import os
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from aiohttp import web, ClientSession, CookieJar
from bs4 import BeautifulSoup

from main3 import *  # импортируем всю логику Telegram-бота

PORT = int(os.environ.get("PORT", 8080))

# HTML форма логина
LOGIN_PAGE = """
<html>
  <head><title>Авторизация Cobalt</title></head>
  <body>
    <h2>Вход в mpets.mobi</h2>
    <form action="/login" method="post">
      <label>Имя: <input name="name" type="text" /></label><br />
      <label>Пароль: <input name="password" type="password" /></label><br />
      <label>Captcha: <input name="captcha" type="text" /></label><br />
      <input type="submit" value="Войти" />
    </form>
  </body>
</html>
"""

# Хендлер формы логина
async def handle_welcome(request):
    return web.Response(text=LOGIN_PAGE, content_type='text/html')

# Авторизация и экспорт куки
async def handle_login(request):
    data = await request.post()
    payload = {
        "name": data.get("name"),
        "password": data.get("password"),
        "captcha": data.get("captcha")
    }

    jar = CookieJar()
    async with ClientSession(cookie_jar=jar) as session:
        async with session.post("https://mpets.mobi/login", data=payload) as resp:
            if str(resp.url) == "https://mpets.mobi/":
                cookies = [
                    {'name': c.key, 'value': c.value}
                    for c in jar.filter_cookies("https://mpets.mobi/").values()
                ]
                return web.Response(text=f"<h3>Авторизация успешна!</h3><pre>{json.dumps(cookies, indent=2, ensure_ascii=False)}</pre>", content_type="text/html")
            else:
                return web.Response(text="Ошибка авторизации. Проверь данные и капчу.", content_type="text/html")

# Запуск aiohttp-сервера и Telegram-бота
async def start_all():
    load_sessions()

    app = Application.builder().token(TOKEN).build()

    # Хендлеры бота
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_session))
    app.add_handler(CommandHandler("del", remove_session))
    app.add_handler(CommandHandler("list", list_sessions))
    app.add_handler(CommandHandler("on", activate_session))
    app.add_handler(CommandHandler("off", deactivate_session))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("get_user", get_user))
    app.add_handler(CommandHandler("aon", activate_other_session))
    app.add_handler(CommandHandler("aoff", deactivate_other_session))
    app.add_handler(CommandHandler("get_list", get_user_sessions))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("guide", guide))

    async def run_bot():
        await app.run_polling()

    async def run_web():
        server = web.Application()
        server.add_routes([web.get('/', handle_welcome), web.post('/login', handle_login)])
        runner = web.AppRunner(server)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()

    await asyncio.gather(run_bot(), run_web())

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(start_all())

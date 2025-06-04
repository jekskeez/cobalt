
from flask import Flask, request, Response, redirect, session as flask_session
import requests
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev_secret")

user_sessions = {}

@app.route('/')
def root():
    tgid = request.args.get("tgid")
    if not tgid:
        return "Добавь ?tgid=ТВОЙ_TELEGRAM_ID в URL."
    flask_session['tgid'] = int(tgid)
    return redirect("/proxy/welcome")

@app.route('/proxy/<path:url_path>', methods=['GET', 'POST'])
def proxy(url_path):
    target_url = f"https://mpets.mobi/{url_path}"
    method = request.method
    headers = {key: value for key, value in request.headers if key.lower() != 'host'}
    cookies = request.cookies

    if method == 'POST':
        resp = requests.post(target_url, data=request.form, headers=headers, cookies=cookies, allow_redirects=False)
    else:
        resp = requests.get(target_url, headers=headers, cookies=cookies, allow_redirects=False)

    if url_path == "login" and resp.status_code in [301, 302]:
        if flask_session.get("tgid") is not None:
            user_sessions[flask_session["tgid"]] = resp.cookies.get_dict()

    excluded_headers = ['content-encoding', 'transfer-encoding', 'content-length']
    response = Response(resp.content, status=resp.status_code)
    for key, value in resp.headers.items():
        if key.lower() not in excluded_headers:
            response.headers[key] = value
    return response

@app.route('/confirm')
def confirm():
    tgid = flask_session.get("tgid")
    if not tgid:
        return "Сначала перейди по ссылке с tgid."
    cookies = user_sessions.get(tgid)
    if not cookies:
        return "Куки не найдены. Залогинься в игре."
    return f"<h3>Куки сохранены:</h3><pre>{cookies}</pre>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)

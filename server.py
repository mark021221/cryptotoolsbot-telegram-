"""Сервер для Telegram Mini App.

Делает три вещи:
1. Отдаёт страницы из webapp/.
2. Проксирует запросы к P2P-API бирж и к курсу USD/KZT (без этого браузер
   Telegram их не пропустит — на биржах нет CORS-заголовков для сторонних сайтов).
3. Хранит немного общих данных для фич: историю спреда (для графика) и список
   подписок на уведомления (которые реально рассылает bot.py — см. README).

Хранилище — простые JSON-файлы в data/. На бесплатных хостингах диск может
очищаться при пересборке — это не страшно, история накопится заново.

Запуск локально:   python server.py
На хостинге процесс сам слушает порт из переменной окружения PORT.
"""
import json
import mimetypes
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ALLOWED = {'p2p.binance.com', 'api.binance.com', 'api2.bybit.com', 'api2.bybit.kz',
           'www.okx.com', 'open.er-api.com'}
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webapp')
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PORT = int(os.environ.get('PORT', 8000))
BOT_SECRET = os.environ.get('BOT_SECRET', '')  # внутренний ключ для bot.py, см. .env.example

HIST_FILE = os.path.join(DATA_DIR, 'history.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subs.json')
_lock = threading.Lock()


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)


class H(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b'{}', ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text') or 'json' in ctype else ''))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    def _fwd(self):
        url = parse_qs(urlparse(self.path).query).get('u', [''])[0]
        if urlparse(url).hostname not in ALLOWED:
            return self._send(403)
        n = int(self.headers.get('Content-Length') or 0)
        data = self.rfile.read(n) if n else None
        rq = urllib.request.Request(url, data=data, method=self.command, headers={
            'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(rq, timeout=15) as r:
                self._send(200, r.read())
        except Exception as e:
            self._send(502, json.dumps({'error': str(e)}).encode())

    def _static(self):
        path = urlparse(self.path).path
        if path == '/':
            path = '/index.html'
        full = os.path.normpath(os.path.join(BASE, path.lstrip('/')))
        if not full.startswith(BASE) or not os.path.isfile(full):
            return self._send(404, b'not found', 'text/plain')
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        with open(full, 'rb') as f:
            self._send(200, f.read(), ctype)

    # --- история спреда (для графика на странице) ---
    def _history_get(self):
        q = parse_qs(urlparse(self.path).query)
        asset = (q.get('asset') or ['USDT'])[0]
        amount = int(float((q.get('amount') or ['100000'])[0]))
        limit = min(500, int((q.get('limit') or ['200'])[0]))
        with _lock:
            rows = _load(HIST_FILE, [])
        rows = [r for r in rows if r.get('asset') == asset and r.get('amount') == amount][-limit:]
        self._send(200, json.dumps(rows).encode())

    def _track_post(self):
        b = self._json()
        if not BOT_SECRET or b.get('key') != BOT_SECRET:
            return self._send(403)
        with _lock:
            rows = _load(HIST_FILE, [])
            rows.append({'t': int(time.time()), 'asset': b.get('asset'), 'amount': b.get('amount'),
                         'pct': b.get('pct'), 'profit': b.get('profit')})
            rows = rows[-5000:]
            _save(HIST_FILE, rows)
        self._send(200, b'{"ok":true}')

    # --- подписки на уведомления ---
    def _subscribe_post(self):
        b = self._json()
        uid = str(b.get('uid') or '').strip()
        if not uid:
            return self._send(400)
        with _lock:
            subs = _load(SUBS_FILE, {})
            if b.get('on'):
                subs[uid] = {'asset': b.get('asset', 'USDT'), 'amount': float(b.get('amount', 100000)),
                             'threshold': float(b.get('threshold', 0.5)), 'fee': float(b.get('fee', 1)),
                             'ts': int(time.time())}
            else:
                subs.pop(uid, None)
            _save(SUBS_FILE, subs)
        self._send(200, b'{"ok":true}')

    def _subscriptions_get(self):
        q = parse_qs(urlparse(self.path).query)
        if not BOT_SECRET or (q.get('key') or [''])[0] != BOT_SECRET:
            return self._send(403)
        with _lock:
            subs = _load(SUBS_FILE, {})
        self._send(200, json.dumps(subs).encode())

    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/fwd':
            return self._fwd()
        if p == '/history':
            return self._history_get()
        if p == '/subscriptions':
            return self._subscriptions_get()
        self._static()

    def do_POST(self):
        p = urlparse(self.path).path
        if p == '/fwd':
            return self._fwd()
        if p == '/track':
            return self._track_post()
        if p == '/subscribe':
            return self._subscribe_post()
        self._send(404)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f'Сервер запущен: http://localhost:{PORT}')
    if not BOT_SECRET:
        print('BOT_SECRET не задан — история и уведомления не будут работать (см. .env.example).')
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()

"""Сервер для Telegram Mini App: отдаёт страницы из webapp/ и проксирует
запросы к P2P-API бирж (без этого браузер Telegram их не пропустит, так как
на биржах нет CORS-заголовков для сторонних сайтов).

Запуск локально:   python server.py
На хостинге (Render/Railway и т.п.) процесс сам слушает порт из переменной
окружения PORT — отдельно ничего настраивать не нужно.
"""
import json
import mimetypes
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ALLOWED = {'p2p.binance.com', 'api2.bybit.com', 'api2.bybit.kz', 'www.okx.com'}
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webapp')
PORT = int(os.environ.get('PORT', 8000))


class H(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b'{}', ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text') or 'json' in ctype else ''))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

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

    def do_GET(self):
        if urlparse(self.path).path == '/fwd':
            return self._fwd()
        self._static()

    def do_POST(self):
        if urlparse(self.path).path == '/fwd':
            return self._fwd()
        self._send(404)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    print(f'Сервер запущен: http://localhost:{PORT}')
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()

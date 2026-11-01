"""Telegram-бот: кнопка для открытия Mini App + фоновый мониторинг P2P.

Что делает фоновая часть (работает, пока запущен этот процесс):
  1. Каждые POLL_SECONDS секунд берёт у сервера список подписок на уведомления.
  2. Загружает P2P-объявления Binance/Bybit/OKX и ищет лучшую связку.
  3. Отправляет снимки спреда на сервер — по ним на странице строится график истории.
  4. Если связка выгоднее порога пользователя — присылает ему сообщение в Telegram.

Переменные окружения (см. .env.example):
  BOT_TOKEN     — токен от @BotFather
  WEBAPP_URL    — публичный https-адрес сервера (server.py)
  BOT_SECRET    — любой длинный секрет, ОДИНАКОВЫЙ у server.py и bot.py
  POLL_SECONDS  — как часто опрашивать биржи (по умолчанию 180)
"""
import asyncio
import json
import logging
import os
import time
import urllib.request
from urllib.parse import urljoin

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import p2p

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')
BOT_SECRET = os.environ.get('BOT_SECRET', '')
POLL_SECONDS = max(60, int(os.environ.get('POLL_SECONDS', '180')))

# суммы, для которых всегда пишем историю (совпадают с быстрыми кнопками на странице)
TRACK_AMOUNTS = [50000, 100000, 300000, 500000, 1000000]
DEFAULT_FEE = {'USDT': 1.0, 'USDC': 1.0, 'BTC': 0.00005, 'ETH': 0.002}
COOLDOWN = 30 * 60      # не чаще одного уведомления в 30 минут...
BETTER_BY = 0.3         # ...если только связка не стала выгоднее хотя бы на 0.3 п.п.

log = logging.getLogger('cryptotools')
dp = Dispatcher()
_last_sent = {}         # uid -> (время, процент последнего уведомления)


@dp.message(CommandStart())
async def start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='📊 Открыть CryptoTools', web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await message.answer(
        'Привет! Это CryptoTools: курсы покупки и продажи на нескольких биржах '
        'в реальном времени и поиск выгодных P2P-связок в тенге.\n\n'
        'Уведомления о выгодных связках включаются в приложении: ⚙ → «Уведомления».\n\n'
        'Нажми кнопку ниже, чтобы открыть приложение.',
        reply_markup=kb,
    )


# ---------- связь с сервером ----------
def _server(path, body=None):
    url = urljoin(WEBAPP_URL, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method='POST' if body is not None else 'GET',
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'cryptotools-bot'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b'{}')


def _fmt_money(n):
    return f'{round(n):,}'.replace(',', ' ')


def _message(asset, amount, r):
    sign = '+' if r['profit'] >= 0 else ''
    return (
        f"🔔 Выгодная связка по {asset}: {sign}{r['pct']:.2f}%\n"
        f"На {_fmt_money(amount)} ₸ это около {sign}{_fmt_money(r['profit'])} ₸\n\n"
        f"Купить на {r['i'].upper()} по {r['buy_price']:.2f} ₸\n"
        f"Продать на {r['j'].upper()} по {r['sell_price']:.2f} ₸\n\n"
        "Цены быстро меняются — перед сделкой проверь лимиты и условия объявлений."
    )


async def poll_once(bot: Bot):
    subs = {}
    try:
        subs = await asyncio.to_thread(_server, f'subscriptions?key={BOT_SECRET}')
    except Exception as e:
        log.warning('Не удалось получить подписки: %s', e)

    # история: USDT всегда + монеты, которые просят подписчики; суммы — как быстрые кнопки на странице
    hist_assets = {'USDT'} | {s.get('asset', 'USDT') for s in subs.values()}
    assets = set(hist_assets)
    books = {}
    for asset in assets:
        books[asset] = await asyncio.to_thread(p2p.load_books, asset)

    for asset in hist_assets:
        if not books.get(asset):
            continue
        for amount in TRACK_AMOUNTS:
            r = p2p.best_route(books[asset], amount, DEFAULT_FEE.get(asset, 1.0))
            if not r:
                continue
            try:
                await asyncio.to_thread(_server, 'track', {
                    'key': BOT_SECRET, 'asset': asset, 'amount': amount,
                    'pct': round(r['pct'], 4), 'profit': round(r['profit'], 2)})
            except Exception as e:
                log.warning('track: %s', e)

    now = time.time()
    for uid, s in subs.items():
        asset, amount = s.get('asset', 'USDT'), float(s.get('amount', 100000))
        fee = float(s.get('fee', DEFAULT_FEE.get(asset, 1.0)))
        if not books.get(asset):
            continue
        r = p2p.best_route(books[asset], amount, fee)
        if not r or r['pct'] < float(s.get('threshold', 0.5)):
            continue
        prev = _last_sent.get(uid)
        if prev and now - prev[0] < COOLDOWN and r['pct'] < prev[1] + BETTER_BY:
            continue
        try:
            await bot.send_message(int(uid), _message(asset, amount, r))
            _last_sent[uid] = (now, r['pct'])
        except Exception as e:  # пользователь мог не запускать бота или заблокировать его
            log.warning('Не удалось отправить %s: %s', uid, e)


async def poll_loop(bot: Bot):
    if not BOT_SECRET:
        log.warning('BOT_SECRET не задан — уведомления и история отключены.')
        return
    while True:
        try:
            await poll_once(bot)
        except Exception:
            log.exception('Ошибка фонового опроса')
        await asyncio.sleep(POLL_SECONDS)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit('Не задан BOT_TOKEN. Заполни файл .env (см. .env.example).')
    if not WEBAPP_URL.startswith('https://'):
        raise SystemExit('WEBAPP_URL должен быть публичным https-адресом (локальный сервер не подойдёт).')
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(poll_loop(bot))
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())

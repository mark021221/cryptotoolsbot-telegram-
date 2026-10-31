"""Telegram-бот: присылает кнопку, которая открывает Mini App.

Перед запуском заполни .env (см. .env.example):
  BOT_TOKEN  — токен от @BotFather
  WEBAPP_URL — публичный https-адрес твоего сервера (server.py), например
               https://your-app.onrender.com/
"""
import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='📊 Открыть CryptoTools', web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await message.answer(
        'Привет! Это CryptoTools: курсы покупки и продажи на нескольких биржах '
        'в реальном времени и поиск выгодных P2P-связок в тенге.\n\n'
        'Нажми кнопку ниже, чтобы открыть приложение.',
        reply_markup=kb,
    )


async def main():
    if not BOT_TOKEN:
        raise SystemExit('Не задан BOT_TOKEN. Заполни файл .env (см. .env.example).')
    if not WEBAPP_URL.startswith('https://'):
        raise SystemExit('WEBAPP_URL должен быть публичным https-адресом (локальный сервер не подойдёт).')
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())

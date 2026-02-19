import aiohttp
import asyncio
import time
import logging
import os
from aiogram import Router, Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from collections import defaultdict
from env import TOKEN
from aiohttp import ClientTimeout

API_TOKEN = TOKEN
CHAT_ID_FILE = "chat_id.txt"
PRICE_CHANGE_THRESHOLD = 3
CHECK_INTERVAL = 1
CHAT_ID = None
TIMEFRAME = 15  # в минутах

router = Router()


# ── CHAT_ID ───────────────────────────────────────────────────────────────
def load_chat_id():
    global CHAT_ID
    if os.path.exists(CHAT_ID_FILE):
        with open(CHAT_ID_FILE, "r") as f:
            saved_id = f.read().strip()
            if saved_id.isdigit():
                CHAT_ID = int(saved_id)
                print(f"[INIT] Загружен CHAT_ID: {CHAT_ID}")

load_chat_id()


def get_chat_id():
    return CHAT_ID


def save_chat_id(chat_id: int):
    global CHAT_ID
    CHAT_ID = chat_id
    with open(CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))


# ── Бот ─────────────────────────────────────────────────────────────────────
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.include_router(router)


def create_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Start")],
            [KeyboardButton(text="Donate")],
            [KeyboardButton(text="Settings")]
        ],
        resize_keyboard=True
    )


# ── Хендлеры ────────────────────────────────────────────────────────────────
# @dp.message()
# async def auto_register(message: types.Message):
#     if CHAT_ID is None:
#         save_chat_id(message.chat.id)
#       await start_tracking()


@router.message(Command("start"))
@router.message(lambda message: message.text == "Start")  # ← добавляем обработку кнопки
async def cmd_start(message: Message):
    print(f"🔥 Получено: {message.text} от {message.from_user.id}")

    saved = False
    if get_chat_id() is None:
        save_chat_id(message.chat.id)
        saved = True
        print(f"💾 Сохранён новый CHAT_ID: {message.chat.id}")
        asyncio.create_task(track_price_changes())

    # Сообщение пользователю
    text = (
        f"👋 Привет, {message.from_user.first_name}!\n"
        "Я отслеживаю цены фьючерсов на **Binance**.\n"
        "При изменении ≥ 3% пришлю уведомление.\n"
        "Поддержать проект: /donate\n"
    )
    if saved:
        text += "\n✅ Чат успешно зарегистрирован!"

    await message.answer(text, reply_markup=create_reply_keyboard())

# @router.message(Command("donate"))
# @router.message(lambda m: m.text == "Donate")
# async def donate_handler(message: Message):
#     await message.answer(
#         "💸 DONATE 💸\nUSDT (BEP20):\n0x164e3739f35de2d391515012373e5c3e8c9ba5fa",
#         reply_markup=create_reply_keyboard()
#     )


# @router.message(lambda m: m.text == "Settings")
# async def settings_handler(message: Message):
#     await message.answer("⚙️ Настройки пока в разработке")


async def start_tracking():
    print("Запускаем трекинг...")
    asyncio.create_task(track_price_changes())


# ── Binance API (USDT-M Perpetual) ──────────────────────────────────────────
async def get_futures_prices():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    timeout = ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json()

    prices = {}
    for item in data:
        symbol = item.get("symbol")
        if symbol and symbol.endswith("USDT") and not symbol.startswith("USDT_"):
            try:
                price = float(item["lastPrice"])
                if price > 0:
                    prices[symbol] = price
            except:
                pass
    return prices


# ── Основной трекер (логика осталась прежней) ───────────────────────────────
async def track_price_changes():
    global CHAT_ID
    previous_prices = {}
    price_changes = defaultdict(lambda: 0)
    tracking_start_time = defaultdict(lambda: time.time())

    while True:
        try:
            current_prices = await get_futures_prices()
            current_time = time.time()

            # Сброс каждые TIMEFRAME минут
            for symbol in list(price_changes.keys()):
                if current_time - tracking_start_time[symbol] >= TIMEFRAME * 60:
                    price_changes[symbol] = 0
                    tracking_start_time[symbol] = current_time

            for symbol, current_price in current_prices.items():
                if symbol not in previous_prices:
                    previous_prices[symbol] = current_price
                    continue

                last_price = previous_prices[symbol]
                if last_price != current_price:
                    change_percent = ((current_price - last_price) / last_price) * 100
                    price_changes[symbol] += change_percent
                    time_diff = current_time - tracking_start_time[symbol]

                    print(f"{symbol}: {price_changes[symbol]:+.2f}% ({time_diff:.0f}s)")

                    if abs(price_changes[symbol]) >= PRICE_CHANGE_THRESHOLD:
                        emoji = "🟢" if price_changes[symbol] > 0 else "🔴"
                        speed = "⚡ FAST" if time_diff < 20 else "🐢 SLOW"
                        time_str = f"{time_diff:.0f}s" if time_diff < 60 else f"{time_diff/60:.0f}min"

                        msg = (
                            f"{emoji} {symbol} {price_changes[symbol]:+.2f}% Binance\n\n"
                            f"{speed} {time_str} \n"
                            f"v1.0 Binance"
                        )

                        if CHAT_ID:
                            try:
                                await bot.send_message(chat_id=CHAT_ID, text=msg)
                                print(f"✅ Отправлено: {symbol}")
                                price_changes[symbol] = 0
                                tracking_start_time[symbol] = current_time
                            except Exception as e:
                                print(f"❌ Ошибка отправки: {e}")

                previous_prices[symbol] = current_price

        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(CHECK_INTERVAL)
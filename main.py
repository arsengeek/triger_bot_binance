import aiohttp
import asyncio
import time
import os
import json
from aiogram import Router, Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from collections import defaultdict
from env import TOKEN
from aiohttp import ClientTimeout
from datetime import datetime

API_TOKEN = TOKEN

# ==================== НАСТРОЙКИ ====================
PRICE_CHANGE_THRESHOLD = 10     # % изменения цены (накопленное)
INSTANT_PRICE_THRESHOLD = 3     # % для мгновенных алертов цены
OI_CHANGE_THRESHOLD = 5         # % роста Open Interest (мгновенно)
CHECK_INTERVAL = 0.5             # секунды между проверками
TIMEFRAME = 15                  # минут — сброс накопления (только для цены)
CHAT_IDS_FILE = "chat_ids.json"
# ===================================================

router = Router()
CHAT_IDS = set()


# ── Загрузка / сохранение списка chat_id ───────────────────────────────────
def load_chat_ids():
    global CHAT_IDS
    if os.path.exists(CHAT_IDS_FILE):
        try:
            with open(CHAT_IDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                CHAT_IDS = set(data)
            print(f"[INIT] Загружено {len(CHAT_IDS)} пользователей")
        except Exception as e:
            print(f"Ошибка загрузки chat_ids.json: {e}")
            CHAT_IDS = set()


def save_chat_ids():
    try:
        with open(CHAT_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(CHAT_IDS), f, ensure_ascii=False, indent=2)
        print(f"Сохранено {len(CHAT_IDS)} пользователей")
    except Exception as e:
        print(f"Ошибка сохранения chat_ids.json: {e}")


def register_chat_id(chat_id: int):
    if chat_id not in CHAT_IDS:
        CHAT_IDS.add(chat_id)
        save_chat_ids()
        print(f"💾 Новый пользователь зарегистрирован: {chat_id}")


load_chat_ids()


# ── Инициализация бота ─────────────────────────────────────────────────────
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


# ── Хендлеры ───────────────────────────────────────────────────────────────
@router.message(Command("start"))
@router.message(lambda message: message.text == "Start")
async def cmd_start(message: Message):
    print(f"🔥 Получено: {message.text} от {message.from_user.id} (chat_id={message.chat.id})")

    new_user = False
    if message.chat.id not in CHAT_IDS:
        register_chat_id(message.chat.id)
        new_user = True

    text = (
        f"👋 Привет, {message.from_user.first_name}!\n"
        "Я отслеживаю фьючерсы на **Binance**.\n"
        f"📊 Мгновенные алерты цены ≥ {INSTANT_PRICE_THRESHOLD}%\n"
        f"📊 Накопленные алерты цены за {TIMEFRAME} мин ≥ {PRICE_CHANGE_THRESHOLD}%\n"
        f"📈 Алерты OI при росте ≥ {OI_CHANGE_THRESHOLD}% (мгновенно)\n"
        "💰 Также показываю Funding Rate\n"
        "Поддержать проект: /donate"
    )
    if new_user:
        text += "\n\n✅ Ты успешно зарегистрирован! Теперь будешь получать уведомления."

    await message.answer(text, reply_markup=create_reply_keyboard())


@router.message(lambda message: message.text == "Donate")
async def donate_handler(message: Message):
    await message.answer(
        "💸 DONATE 💸\nUSDT (TRC20):\nTQ94zz11YZsuFTXLZZn9vEYEfWDxWoQavx",
        reply_markup=create_reply_keyboard()
    )


@router.message(lambda message: message.text == "Settings")
async def settings_handler(message: Message):
    await message.answer("⚙️ Настройки пока в разработке", reply_markup=create_reply_keyboard())


# ── Binance API ────────────────────────────────────────────────────────────
async def get_futures_prices():
    """Получение цен фьючерсов - все монеты"""
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    timeout = ClientTimeout(total=5)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    print(f"⚠️ Rate limit, ждем {retry_after}с")
                    await asyncio.sleep(retry_after)
                    return {}
                
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
                        except (ValueError, TypeError):
                            continue
                print(f"📊 Получено {len(prices)} монет")
                return prices
                
    except Exception as e:
        print(f"⚠️ Ошибка получения цен: {e}")
        return {}


async def get_open_interest(symbol: str) -> float:
    """Получение Open Interest для символа"""
    url = f"https://fapi.binance.com/fapi/v1/openInterest"
    params = {"symbol": symbol}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get("openInterest", 0))
    except Exception as e:
        print(f"⚠️ Ошибка OI для {symbol}: {e}")
    
    return 0


async def get_funding_rate(symbol: str) -> dict:
    """Получение Funding Rate для символа"""
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {
        "symbol": symbol,
        "limit": 1
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        return {
                            "rate": float(data[0]["fundingRate"]) * 100,
                            "time": data[0]["fundingTime"]
                        }
    except Exception:
        pass
    
    return {"rate": 0, "time": 0}


# ── Функции отправки уведомлений ───────────────────────────────────────────
def format_number(num: float) -> str:
    """Форматирование больших чисел"""
    if num > 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    elif num > 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num > 1_000:
        return f"{num / 1_000:.2f}K"
    else:
        return f"{num:.0f}"


async def send_price_alert(symbol: str, price_change: float, current_time: float, start_time: float, 
                          funding: dict = None, oi: float = None, oi_change: float = None, alert_type: str = "НАКОПЛЕНО"):
    """Отправка уведомления об изменении цены"""
    emoji = "🟢" if price_change > 0 else "🔴"
    
    # Время
    time_diff = max(0.1, current_time - start_time)
    if time_diff < 20:
        speed = "⚡ FAST"
    elif time_diff < 60:
        speed = "🏃 NORMAL"
    else:
        speed = "🐢 SLOW"
    
    time_str = f"{time_diff:.0f}s"
    
    # Funding Rate
    funding_str = ""
    if funding and funding["rate"] != 0:
        funding_emoji = "📈" if funding["rate"] > 0 else "📉"
        funding_str = f"{funding_emoji} Funding: {funding['rate']:.4f}%\n"
    
    # OI с процентом изменения
    oi_str = ""
    if oi and oi > 0:
        oi_formatted = format_number(oi)
        if oi_change is not None and abs(oi_change) >= 0.01:
            oi_emoji = "📈" if oi_change > 0 else "📉"
            oi_str = f"{oi_emoji} OI: {oi_change:+.2f}% ({oi_formatted})\n"
        else:
            oi_str = f"📊 OI: {oi_formatted}\n"
    
    # Тип алерта
    type_icon = "⚡" if alert_type == "МГНОВЕННО" else "📈"
    
    # Сообщение
    msg = (
        f"🚨 {emoji} {symbol} {type_icon} {alert_type}\n"
        f"{'─' * 20}\n"
        f"📊 Цена: {price_change:+.2f}%\n"
        f"{oi_str}"
        f"{funding_str}"
        f"⚡ {speed} • {time_str} • ⌚ {TIMEFRAME} мин"
    )
    
    await send_message_to_all(msg)
    print(f"✅ {alert_type} Цена: {symbol} {price_change:+.2f}%")


async def send_oi_alert(symbol: str, oi_change: float, current_time: float, 
                       current_oi: float, funding: dict = None, price_change: float = None):
    """Отправка уведомления о росте Open Interest (упрощенная версия)"""
    # Время (просто для информации)
    time_str = f"{CHECK_INTERVAL:.1f}с"
    
    # OI
    oi_formatted = format_number(current_oi)
    oi_emoji = "📈" if oi_change > 0 else "📉"
    
    # Funding Rate
    funding_str = ""
    if funding and funding["rate"] != 0:
        funding_emoji = "📈" if funding["rate"] > 0 else "📉"
        funding_str = f"{funding_emoji} Funding: {funding['rate']:.4f}%\n"
    
    # Цена с процентом
    price_str = ""
    if price_change and abs(price_change) >= 0.01:
        price_emoji = "🟢" if price_change > 0 else "🔴"
        price_str = f"{price_emoji} Цена: {price_change:+.2f}%\n"
    
    # Сообщение
    msg = (
        f"🚨 {symbol} {oi_emoji} OI РОСТ {oi_change:+.2f}%\n"
        f"{'─' * 20}\n"
        f"{oi_emoji} OI: {oi_formatted}\n"
        f"{price_str}"
        f"{funding_str}"
        f"⚡ FAST • {time_str}"
    )
    
    await send_message_to_all(msg)
    print(f"✅ OI РОСТ: {symbol} +{oi_change:+.2f}%")


async def send_message_to_all(msg: str):
    """Отправка сообщения всем зарегистрированным пользователям"""
    for chat_id in list(CHAT_IDS):
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
            print(f"✅ Отправлено в {chat_id}")
        except Exception as e:
            print(f"❌ Ошибка отправки в {chat_id}: {e}")
            if "chat not found" in str(e).lower() or "blocked" in str(e).lower():
                CHAT_IDS.discard(chat_id)
                save_chat_ids()


async def track_changes():
    """Фоновая задача для отслеживания изменений"""
    print("✅ Трекинг цен, OI и Funding запущен")
    
    # Хранилища данных
    prices = {}           # Текущие цены
    oi_values = {}        # Текущие OI
    funding_rates = {}     # Текущие funding rates
    
    # Накопленные изменения (только для цены)
    price_acc = defaultdict(float)      # Накопленное изменение цены
    price_start = defaultdict(float)    # Время начала отслеживания цены
    
    # ПРОСТАЯ ЛОГИКА ДЛЯ OI
    last_oi_values = {}    # Предыдущие значения OI
    oi_alert_cooldown = {} # Время последнего алерта (чтобы не спамить)
    
    # Время последнего обновления
    last_oi_update = defaultdict(float)
    last_funding_update = defaultdict(float)
    
    # Для отслеживания предыдущих цен
    last_prices = {}
    
    last_report = time.time()
    request_count = 0
    
    while True:
        try:
            current_time = time.time()
            
            # Получаем цены
            new_prices = await get_futures_prices()
            request_count += 1
            
            if not new_prices:
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # Сброс устаревших накоплений цены
            for symbol in list(price_start.keys()):
                if current_time - price_start[symbol] >= TIMEFRAME * 60:
                    price_acc[symbol] = 0
                    price_start[symbol] = current_time
            
            # Анализируем каждый символ
            for symbol, current_price in new_prices.items():
                # Инициализация для новых символов
                if symbol not in price_start:
                    price_start[symbol] = current_time
                    price_acc[symbol] = 0
                
                # ---- МГНОВЕННЫЙ АЛЕРТ ЦЕНЫ ----
                if symbol in last_prices:
                    last_price = last_prices[symbol]
                    if last_price > 0:
                        instant_change = ((current_price - last_price) / last_price) * 100
                        if abs(instant_change) >= INSTANT_PRICE_THRESHOLD:
                            await send_price_alert(
                                symbol, 
                                instant_change, 
                                current_time, 
                                current_time - CHECK_INTERVAL,
                                funding_rates.get(symbol, {"rate": 0}),
                                oi_values.get(symbol, 0),
                                0,
                                "МГНОВЕННО"
                            )
                
                # ---- НАКОПЛЕННЫЙ АЛЕРТ ЦЕНЫ ----
                if symbol in prices:
                    last_price = prices[symbol]
                    if last_price > 0:
                        change = ((current_price - last_price) / last_price) * 100
                        if abs(change) >= 0.01:
                            price_acc[symbol] += change
                            
                            if abs(price_acc[symbol]) >= PRICE_CHANGE_THRESHOLD:
                                await send_price_alert(
                                    symbol, 
                                    price_acc[symbol], 
                                    current_time, 
                                    price_start[symbol],
                                    funding_rates.get(symbol, {"rate": 0}),
                                    oi_values.get(symbol, 0),
                                    0,
                                    "НАКОПЛЕНО"
                                )
                                price_acc[symbol] = 0
                                price_start[symbol] = current_time
                
                # ---- УПРОЩЕННАЯ ПРОВЕРКА OI (каждые 10 секунд) ----
                if current_time - last_oi_update[symbol] >= 10:
                    current_oi = await get_open_interest(symbol)
                    last_oi_update[symbol] = current_time
                    
                    if current_oi > 0:
                        # Сохраняем текущее OI
                        oi_values[symbol] = current_oi
                        
                        # Проверяем рост от предыдущего значения
                        if symbol in last_oi_values:
                            last_oi = last_oi_values[symbol]
                            if last_oi > 0:
                                oi_growth = ((current_oi - last_oi) / last_oi) * 100
                                
                                # Если рост достиг порога и не спамим (кулдаун 5 минут)
                                if (oi_growth >= OI_CHANGE_THRESHOLD and 
                                    symbol not in oi_alert_cooldown or 
                                    current_time - oi_alert_cooldown.get(symbol, 0) > 300):
                                    
                                    print(f"🔔 OI РОСТ {symbol}: {oi_growth:.2f}%")
                                    await send_oi_alert(
                                        symbol, 
                                        oi_growth, 
                                        current_time,
                                        current_oi,
                                        funding_rates.get(symbol, {"rate": 0}),
                                        price_acc.get(symbol, 0)
                                    )
                                    oi_alert_cooldown[symbol] = current_time
                        
                        # Обновляем предыдущее значение
                        last_oi_values[symbol] = current_oi
                
                # ---- Funding Rate ----
                if current_time - last_funding_update[symbol] >= 60:
                    funding_rates[symbol] = await get_funding_rate(symbol)
                    last_funding_update[symbol] = current_time
            
            # Сохраняем текущие цены для следующего сравнения
            last_prices = prices.copy()
            
            # Обновляем цены
            prices = new_prices.copy()
            
            # Отчет
            if current_time - last_report >= 30:
                print(f"[{time.strftime('%H:%M:%S')}] Запросов: {request_count}, монет: {len(prices)}")
                last_report = current_time
                request_count = 0
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
        
        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup():
    """Действия при запуске бота"""
    print("🚀 Бот запускается...")
    if CHAT_IDS:
        print(f"✅ Найдено {len(CHAT_IDS)} пользователей → запускаем мониторинг")
        asyncio.create_task(track_changes())
    else:
        print("❌ Нет пользователей. Ждём /start")


async def on_shutdown():
    """Действия при остановке бота"""
    print("👋 Бот останавливается...")
    await bot.session.close()


# ── Запуск бота ────────────────────────────────────────────────────────────
async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    print("🚀 Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
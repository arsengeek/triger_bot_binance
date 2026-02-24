import asyncio
import logging
from main import dp, bot, CHAT_IDS, track_changes

logging.basicConfig(level=logging.INFO)

async def main():
    print("🚀 Запуск start.py...")
    
    if CHAT_IDS:
        print(f"✅ Найдено {len(CHAT_IDS)} пользователей")
        asyncio.create_task(track_changes())
    else:
        print("⚠️ Нет пользователей")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
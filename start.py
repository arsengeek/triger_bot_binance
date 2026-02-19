import asyncio
import sys
from main import dp, bot, track_price_changes, get_chat_id, load_chat_id, CHAT_ID

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    load_chat_id()

    if get_chat_id():
        asyncio.create_task(track_price_changes())

    # Тест отправки при запуске
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="✅ Бот запущен на Binance!\nМониторинг цен фьючерсов запущен."
        )
        print("Тестовое сообщение отправлено")
    except Exception as e:
        print("Не удалось отправить тест:", e)

    print("🚀 Запускаем polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from database import init_db
from handlers import router

# Токен передаётся через переменную окружения TELEGRAM_BOT_TOKEN,
# чтобы он не лежал в коде и не попадал в историю git.
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

async def main():
    if not API_TOKEN:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN. Задайте переменную окружения и перезапустите бота.")

    await init_db()

    # Расширяем сетевой таймаут до 300 секунд (5 минут) для тяжелых аудиофайлов
    session = AiohttpSession(timeout=300)
    bot = Bot(token=API_TOKEN, session=session)

    dp = Dispatcher()
    dp.include_router(router)

    logger.info(">>> DAEMON STARTED WITH EXTENDED TIMEOUT")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
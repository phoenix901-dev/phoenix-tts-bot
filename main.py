import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from database import init_db
from handlers import router

# Токен вставь сюда
API_TOKEN = ""

logging.basicConfig(level=logging.INFO)

async def main():
    await init_db()
    
    # Расширяем сетевой таймаут до 300 секунд (5 минут) для тяжелых аудиофайлов
    session = AiohttpSession(timeout=300)
    bot = Bot(token=API_TOKEN, session=session)
    
    dp = Dispatcher()
    dp.include_router(router)
    
    print(">>> DAEMON STARTED WITH EXTENDED TIMEOUT")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import logging
import logging.handlers
import os
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from database import init_db
from handlers import router
from keyboards import VOICES
from core import validate_voices

# Токен передаётся через переменную окружения TELEGRAM_BOT_TOKEN,
# чтобы он не лежал в коде и не попадал в историю git.
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Путь к лог-файлу. Без него логи демона теряются при рестарте.
LOG_FILE = os.getenv("TTS_LOG_FILE", "")

_handlers: list[logging.Handler] = [logging.StreamHandler()]
if LOG_FILE:
    _handlers.append(logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    ))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

async def main():
    if not API_TOKEN:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN. Задайте переменную окружения и перезапустите бота.")

    await init_db()

    # Заранее сообщаем о несуществующих голосах: иначе опечатка в списке
    # вылезала только у пользователя во время озвучки.
    invalid = await validate_voices([code for _, code in VOICES])
    if invalid:
        logger.warning("Нерабочие коды голосов в keyboards.VOICES: %s", ", ".join(invalid))

    # Расширяем сетевой таймаут до 300 секунд (5 минут) для тяжелых аудиофайлов
    session = AiohttpSession(timeout=300)
    bot = Bot(token=API_TOKEN, session=session)

    dp = Dispatcher()
    dp.include_router(router)

    # Меню команд в клиенте Telegram.
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запустить бота и открыть меню"),
        ])
    except Exception as exc:
        logger.warning("Не удалось установить меню команд: %s", exc)

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
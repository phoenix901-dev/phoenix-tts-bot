import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from database import init_db
from handlers import router
from keyboards import VOICES
from core import validate_voices
from engines import SILERO_WARMUP, resolve_model_id, warmup

# Секреты и настройки берём из .env рядом с проектом. Реальные переменные
# окружения (например, EnvironmentFile у systemd) приоритетнее файла, поэтому
# существующие установки продолжают работать как раньше.
_ENV_FILE = Path(__file__).with_name(".env")


def _load_env_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_ENV_FILE)

# Токен лежит в .env рядом с проектом (или в переменной окружения) — в коде
# и в истории git его нет.
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

    # Локальный движок грузит модель в память при первом запросе (пара секунд).
    # SILERO_WARMUP=1 переносит эту задержку на старт бота.
    if SILERO_WARMUP:
        try:
            await warmup()
            logger.info("Модель Silero прогрета: %s", resolve_model_id())
        except Exception as exc:  # noqa: BLE001 - бот должен стартовать в любом случае
            logger.warning("Silero не прогрелась (%s) — загрузится при первом запросе", exc)

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
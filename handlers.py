import os
import shutil
import asyncio
import logging
import re
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
import edge_tts

from database import get_user, update_user
from keyboards import main_menu, settings_menu, voices_menu, rates_menu
from core import parse_file, process_book, clean_text, get_semaphore, SUPPORTED_EXTS

logger = logging.getLogger(__name__)

router = Router()
TEMP_BASE = Path(os.getenv("TTS_TMP_DIR", "/root/telegram/bbot/tmp"))
# parents=True обязателен: без него бот падал с FileNotFoundError прямо на
# импорте, если каталога /root/telegram/bbot ещё не существовало.
TEMP_BASE.mkdir(parents=True, exist_ok=True)

# Telegram не даёт скачивать файлы больше 20 МБ через getFile.
MAX_FILE_BYTES = 20 * 1024 * 1024

# Одна книга на пользователя: иначе можно было забить очередь десятком томов.
_user_book_locks: dict[int, asyncio.Lock] = {}

_UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')

def safe_filename(name: str | None, fallback: str = "file") -> str:
    """Убирает из имени путь и спецсимволы, сохраняя юникод.

    Раньше имя файла подставлялось в путь напрямую, поэтому документ с именем
    "../../../etc/cron.d/x" писал за пределы рабочего каталога.
    """
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_NAME_RE.sub("_", name).strip().replace(" ", "_")
    return name[:120] or fallback

def _user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_book_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_book_locks[user_id] = lock
    return lock

async def _safe_delete(message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        pass

async def _safe_edit(call: CallbackQuery, text: str, **kwargs) -> None:
    """edit_text падает, если текст не изменился (TelegramBadRequest)."""
    try:
        await call.message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("Не удалось обновить сообщение: %s", exc)

async def _probe_duration(path: Path) -> int:
    """Длительность ogg в секундах — без неё Telegram показывает голосовое на 0 сек."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return max(1, int(float(out.decode().strip())))
    except (ValueError, OSError):
        return 1


@router.message(Command("start"))
async def start_cmd(message: Message):
    await get_user(message.from_user.id)
    text = (
        "Привет! 👋 Я бот-чтец. Умею быстро озвучивать текст и создавать аудиокниги.\n\n"
        "💬 **Что я могу:**\n"
        "• Отправь мне любой текст — я прочитаю его и пришлю голосовое сообщение.\n"
        "• Отправь файл (PDF, DOCX, FB2, EPUB, TXT) — я аккуратно нарежу его, озвучу и пришлю готовую аудиокнигу в MP3. Длинные книги я сам разобью на части по главам (до 2-3 часов).\n\n"
        "Настрой голос и скорость под себя в меню 👇"
    )
    await message.answer(text, reply_markup=main_menu())

@router.message(F.text == "⚙️ Настройки")
async def settings_cmd(message: Message):
    user = await get_user(message.from_user.id)
    text = (
        "⚙️ **Твои настройки**\n\n"
        f"🗣 Голос (Текст): `{user.text_voice}`\n"
        f"📚 Голос (Книги): `{user.book_voice}`\n"
        f"⚡ Скорость: `{user.rate}`\n\n"
        "Выбери, что хочешь изменить:"
    )
    await message.answer(text, reply_markup=settings_menu())

# --- БЛОК НАСТРОЕК ---

@router.callback_query(F.data.startswith("set_voice_"))
async def voice_select(call: CallbackQuery):
    await call.answer()
    mode = call.data.rsplit("_", 1)[-1]
    if mode not in {"text", "book"}:
        await call.answer("Неизвестный режим", show_alert=True)
        return
    mode_ru = "ТЕКСТА" if mode == "text" else "КНИГ"
    await _safe_edit(
        call,
        f"Выбери голос для **{mode_ru}**:",
        reply_markup=voices_menu(mode)
    )

@router.callback_query(F.data.startswith("voice_"))
async def voice_apply(call: CallbackQuery):
    await call.answer()
    _, mode, voice_code = call.data.split("_", 2)
    if mode == "text":
        await update_user(call.from_user.id, text_voice=voice_code)
    else:
        await update_user(call.from_user.id, book_voice=voice_code)

    await _safe_edit(
        call,
        f"✅ Отлично! Новый голос сохранен.\n\n"
        f"Текущий выбор: `{voice_code}`",
        reply_markup=settings_menu()
    )

@router.callback_query(F.data == "set_rate")
async def rate_select(call: CallbackQuery):
    await call.answer()
    await _safe_edit(
        call,
        "⚡ Выбери скорость воспроизведения (применяется для всех режимов):",
        reply_markup=rates_menu()
    )

@router.callback_query(F.data.startswith("rate_"))
async def rate_apply(call: CallbackQuery):
    await call.answer()
    rate_code = call.data.split("_", 1)[1]
    await update_user(call.from_user.id, rate=rate_code)

    await _safe_edit(
        call,
        f"✅ Отлично! Скорость сохранена.\n\n"
        f"Текущий выбор: `{rate_code}`",
        reply_markup=settings_menu()
    )

@router.callback_query(F.data == "back_to_settings")
async def back_to_settings(call: CallbackQuery):
    await call.answer()
    user = await get_user(call.from_user.id)
    text = (
        "⚙️ **Твои настройки**\n\n"
        f"🗣 Голос (Текст): `{user.text_voice}`\n"
        f"📚 Голос (Книги): `{user.book_voice}`\n"
        f"⚡ Скорость: `{user.rate}`\n\n"
        "Выбери, что хочешь изменить:"
    )
    await _safe_edit(call, text, reply_markup=settings_menu())

# --- БЛОК ОБРАБОТКИ ---

@router.message(F.text & ~F.text.startswith("/"))
async def process_short_text(message: Message):
    if message.text == "⚙️ Настройки": return
    
    status_msg = await message.answer("🎙 Записываю аудио...")
    user = await get_user(message.from_user.id)

    # message_id уникален только ВНУТРИ чата, поэтому два пользователя с
    # одинаковым message_id раньше писали в один и тот же файл и портили
    # друг другу аудио.
    stem = f"msg_{message.from_user.id}_{message.chat.id}_{message.message_id}"
    tmp_mp3 = TEMP_BASE / f"{stem}.mp3"
    tmp_ogg = TEMP_BASE / f"{stem}.ogg"

    try:
        async with get_semaphore():
            cleaned_text = clean_text(message.text)
            communicate = edge_tts.Communicate(cleaned_text, user.text_voice, rate=user.rate)
            await communicate.save(str(tmp_mp3))

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(tmp_mp3), "-c:a", "libopus", "-b:a", "32k", str(tmp_ogg), "-y"]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()

        if not tmp_ogg.exists() or tmp_ogg.stat().st_size == 0:
            raise RuntimeError("ffmpeg не смог сконвертировать аудио в ogg")

        # Без duration Telegram показывает голосовое сообщение как 0 секунд.
        duration = await _probe_duration(tmp_ogg)
        try:
            await message.answer_voice(FSInputFile(tmp_ogg), duration=duration)
        except TelegramBadRequest as e:
            if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                await message.answer_audio(
                    FSInputFile(tmp_mp3, filename="voice.mp3"),
                    caption="🔒 Твои настройки приватности блокируют голосовые сообщения. Отправляю обычным аудиофайлом."
                )
            else:
                raise e

    except Exception as e:
        logger.exception("Ошибка озвучивания текста")
        await message.answer(f"❌ Ошибка при обработке: {str(e)}")
    finally:
        await _safe_delete(status_msg)
        tmp_mp3.unlink(missing_ok=True)
        tmp_ogg.unlink(missing_ok=True)

@router.message(F.document)
async def process_document(message: Message, bot: Bot):
    user = await get_user(message.from_user.id)
    raw_name = message.document.file_name or "file"
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""

    if ext not in SUPPORTED_EXTS:
        await message.answer("😔 Извини, но этот формат пока не поддерживается. Попробуй отправить FB2, TXT, EPUB, MOBI, DOCX или PDF.")
        return

    if (message.document.file_size or 0) > MAX_FILE_BYTES:
        await message.answer("📦 Файл слишком большой — Telegram не даёт скачать больше 20 МБ. Разбей книгу на части и пришли по очереди.")
        return

    # Не даём одному пользовачеку занять всю очередь десятком томов.
    lock = _user_lock(message.from_user.id)
    if lock.locked():
        await message.answer("⏳ Ты уже ждёшь одну книгу. Дождись её, а потом присылай следующую.")
        return

    async with lock:
        workdir = TEMP_BASE / f"job_{message.from_user.id}_{message.message_id}"
        status_msg = None

        try:
            workdir.mkdir(parents=True, exist_ok=True)
            status_msg = await message.answer("⏳ Скачиваю файл...")

            # Раньше имя файла от юзера подставлялось в путь как есть:
            # документ с именем "../../../etc/cron.d/x" писал наружу workdir.
            input_path = workdir / safe_filename(raw_name)
            file = await bot.get_file(message.document.file_id)
            await bot.download_file(file.file_path, input_path)

            await status_msg.edit_text("📖 Читаю файл и подготавливаю текст...")
            raw_text = await parse_file(input_path, ext)

            if raw_text is None:
                await status_msg.edit_text("❌ Ошибка: превышено время ожидания при обработке файла (таймаут 60 секунд). Возможно, файл слишком сложный или содержит много медиаданных.")
                return

            if not raw_text.strip():
                await status_msg.edit_text("❌ Не удалось извлечь текст. Возможно, файл пуст или это картинка в PDF без текстового слоя.")
                return

            last_percent = 0
            async def progress(completed, total):
                nonlocal last_percent
                if total <= 0:  # защита от ZeroDivisionError
                    return
                percent = min(100, int(completed / total * 100))
                if percent - last_percent < 5 and percent != 100:
                    return
                bar = "🟩" * (percent // 10) + "⬜️" * (10 - (percent // 10))
                try:
                    await status_msg.edit_text(f"🎧 Озвучиваю книгу: {percent}%\n\n{bar}\n\nПожалуйста, подожди, это займет немного времени.")
                except TelegramAPIError:
                    pass  # "message is not modified" — это норма
                last_percent = percent

            clean_filename = os.path.splitext(safe_filename(raw_name))[0][:80]

            # ТУТ ВАЖНОЕ ИЗМЕНЕНИЕ: мы получаем список томов
            volumes = await process_book(raw_text, workdir, user.book_voice, user.rate, progress, clean_filename)

            if not volumes:
                await status_msg.edit_text("❌ Ошибка при нарезке текста.")
                return

            total_volumes = len(volumes)
            await status_msg.edit_text(f"📦 Собираю аудиокнигу. Получилось частей: {total_volumes}. Отправляю...")

            try:
                # ТУТ ВАЖНОЕ ИЗМЕНЕНИЕ: цикл для отправки каждого тома отдельно
                for i, vol_path in enumerate(volumes, 1):
                    caption = f"✨ Твоя аудиокнига готова! (Часть {i} из {total_volumes})" if i == 1 else f"Часть {i} из {total_volumes}"

                    await message.answer_audio(
                        FSInputFile(vol_path, filename=f"{clean_filename}_Часть_{i}.mp3"),
                        caption=caption
                    )
                    # Защита от флуда
                    await asyncio.sleep(2)

            except Exception as e:
                await message.answer(f"❌ Произошла ошибка сети при отправке: {str(e)}")

        except Exception as e:
            # Раньше process_book стоял ВНЕ try, поэтому любой сбой рендера
            # оставлял сообщение "Скачиваю файл..." навсегда, не отвечал юзеру
            # и бесконечно мусорил в tmp.
            logger.exception("Ошибка обработки книги %s", raw_name)
            notice = f"❌ Не получилось сделать аудиокнигу: {e}"
            if status_msg is not None:
                try:
                    await status_msg.edit_text(notice)
                except TelegramAPIError:
                    await message.answer(notice)
            else:
                await message.answer(notice)
        finally:
            if status_msg is not None:
                await _safe_delete(status_msg)
            shutil.rmtree(workdir, ignore_errors=True)
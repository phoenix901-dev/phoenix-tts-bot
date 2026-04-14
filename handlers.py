import os
import shutil
import asyncio
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
import edge_tts

from database import get_user, update_user
from keyboards import main_menu, settings_menu, voices_menu, rates_menu
from core import parse_file, process_book

router = Router()
TEMP_BASE = Path("/root/telegram/bbot/tmp")
TEMP_BASE.mkdir(exist_ok=True)

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
    mode = call.data.split("_")[2]
    mode_ru = "ТЕКСТА" if mode == "text" else "КНИГ"
    await call.message.edit_text(
        f"Выбери голос для **{mode_ru}**:", 
        reply_markup=voices_menu(mode)
    )

@router.callback_query(F.data.startswith("voice_"))
async def voice_apply(call: CallbackQuery):
    _, mode, voice_code = call.data.split("_", 2)
    if mode == "text":
        await update_user(call.from_user.id, text_voice=voice_code)
    else:
        await update_user(call.from_user.id, book_voice=voice_code)
    
    await call.message.edit_text(
        f"✅ Отлично! Новый голос сохранен.\n\n"
        f"Текущий выбор: `{voice_code}`", 
        reply_markup=settings_menu()
    )

@router.callback_query(F.data == "set_rate")
async def rate_select(call: CallbackQuery):
    await call.message.edit_text(
        "⚡ Выбери скорость воспроизведения (применяется для всех режимов):", 
        reply_markup=rates_menu()
    )

@router.callback_query(F.data.startswith("rate_"))
async def rate_apply(call: CallbackQuery):
    rate_code = call.data.split("_", 1)[1]
    await update_user(call.from_user.id, rate=rate_code)
    
    await call.message.edit_text(
        f"✅ Отлично! Скорость сохранена.\n\n"
        f"Текущий выбор: `{rate_code}`", 
        reply_markup=settings_menu()
    )

@router.callback_query(F.data == "back_to_settings")
async def back_to_settings(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    text = (
        "⚙️ **Твои настройки**\n\n"
        f"🗣 Голос (Текст): `{user.text_voice}`\n"
        f"📚 Голос (Книги): `{user.book_voice}`\n"
        f"⚡ Скорость: `{user.rate}`\n\n"
        "Выбери, что хочешь изменить:"
    )
    await call.message.edit_text(text, reply_markup=settings_menu())

# --- БЛОК ОБРАБОТКИ ---

@router.message(F.text & ~F.text.startswith("/"))
async def process_short_text(message: Message):
    if message.text == "⚙️ Настройки": return
    
    status_msg = await message.answer("🎙 Записываю аудио...")
    user = await get_user(message.from_user.id)
    
    tmp_mp3 = TEMP_BASE / f"msg_{message.message_id}.mp3"
    tmp_ogg = TEMP_BASE / f"msg_{message.message_id}.ogg"
    
    try:
        communicate = edge_tts.Communicate(message.text, user.text_voice, rate=user.rate)
        await communicate.save(str(tmp_mp3))
        
        cmd = f"ffmpeg -i '{tmp_mp3}' -c:a libopus -b:a 32k '{tmp_ogg}' -y"
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        
        try:
            await message.answer_voice(FSInputFile(tmp_ogg))
        except TelegramBadRequest as e:
            if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                await message.answer_audio(
                    FSInputFile(tmp_mp3, filename="voice.mp3"),
                    caption="🔒 Твои настройки приватности блокируют голосовые сообщения. Отправляю обычным аудиофайлом."
                )
            else:
                raise e

    except Exception as e:
        await message.answer(f"❌ Ошибка при обработке: {str(e)}")
    finally:
        await status_msg.delete()
        if tmp_mp3.exists(): os.remove(tmp_mp3)
        if tmp_ogg.exists(): os.remove(tmp_ogg)

@router.message(F.document)
async def process_document(message: Message, bot: Bot):
    user = await get_user(message.from_user.id)
    ext = message.document.file_name.split('.')[-1].lower()
    
    if ext not in ['pdf', 'doc', 'docx', 'fb2', 'txt', 'epub', 'mobi']:
        await message.answer("😔 Извини, но этот формат пока не поддерживается. Попробуй отправить FB2, TXT, EPUB или PDF.")
        return

    status_msg = await message.answer("⏳ Скачиваю файл...")
    workdir = TEMP_BASE / f"job_{message.message_id}"
    workdir.mkdir(exist_ok=True)
    
    input_path = workdir / message.document.file_name
    file = await bot.get_file(message.document.file_id)
    await bot.download_file(file.file_path, input_path)

    await status_msg.edit_text("📖 Читаю файл и подготавливаю текст...")
    raw_text = await parse_file(input_path, ext)
    
    if not raw_text:
        await status_msg.edit_text("❌ Не удалось извлечь текст. Возможно, файл пуст или это картинка в PDF без текстового слоя.")
        shutil.rmtree(workdir)
        return

    last_percent = 0
    async def progress(completed, total):
        nonlocal last_percent
        percent = int(completed / total * 100)
        if percent - last_percent >= 5 or percent == 100:
            bar = "🟩" * (percent // 10) + "⬜️" * (10 - (percent // 10))
            try:
                await status_msg.edit_text(f"🎧 Озвучиваю книгу: {percent}%\n\n{bar}\n\nПожалуйста, подожди, это займет немного времени.")
                last_percent = percent
            except: pass 

    # ТУТ ВАЖНОЕ ИЗМЕНЕНИЕ: мы получаем список томов
    volumes = await process_book(raw_text, workdir, user.book_voice, user.rate, progress)
    
    if not volumes:
        await status_msg.edit_text("❌ Ошибка при нарезке текста.")
        shutil.rmtree(workdir)
        return

    total_volumes = len(volumes)
    await status_msg.edit_text(f"📦 Собираю аудиокнигу. Получилось частей: {total_volumes}. Отправляю...")
    
    clean_filename = os.path.splitext(message.document.file_name)[0]
    
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
    finally:
        await status_msg.delete()
        shutil.rmtree(workdir)
import asyncio
import shutil
import re
import unicodedata
import aiofiles
from pathlib import Path
import edge_tts

THREADS = 12
# Безопасный предел для Telegram: Bot API режет загрузку на 50 МБ.
# edge-tts отдаёт MP3 48 кбит/с (6 КБ/с), русская речь ~850 символов/мин,
# поэтому 200 000 символов давали ~80 МБ на том и ЛЮБАЯ большая книга падала
# на отправке с "file is too big". 75 000 символов ~= 88 минут ~= 32 МБ.
MAX_VOL_CHARS = 75000
# Сверх этого размера том автоматически пережимается перед отправкой.
MAX_VOL_BYTES = 45 * 1024 * 1024
# Размер микро-чанка. edge-tts заметно лучше звучит на коротких фрагментах.
TTS_CHUNK_SIZE = 1800

# Сетевые повторы: Microsoft периодически рвёт соединение, а одна ошибка
# раньше убивала всю книгу целиком вместе с уже готовыми частями.
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0

SUPPORTED_EXTS = {"pdf", "doc", "docx", "fb2", "epub", "mobi", "txt"}

_global_semaphore: asyncio.Semaphore | None = None

def get_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(THREADS)
    return _global_semaphore

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_INLINE_WS_RE = re.compile(r"[^\S\n]+")

def clean_text(text: str) -> str:
    """Очистка текста от разметки, HTML-тегов и ссылок перед синтезом."""
    text = text.replace("\ufeff", "").replace("\xa0", " ")
    text = unicodedata.normalize("NFKC", text)
    # Картинки и markdown-ссылки: ![alt](url) -> alt, [text](url) -> text
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    # HTML-мусор от pandoc/pdftotext
    text = _TAG_RE.sub(" ", text)
    # Удаление HTTP/HTTPS ссылок
    text = _URL_RE.sub("", text)
    # Удаление Markdown символов (#, *, _, `, ~)
    text = re.sub(r"[#\*_`~>]", "", text)
    # Замена множественных пробелов на один
    text = _INLINE_WS_RE.sub(" ", text)
    return text.strip()

# Таймаут на извлечение текста: FB2/PDF с большим количеством медиа
# подвешивали pandoc/pdftotext навсегда.
PARSE_TIMEOUT = 60.0

async def parse_file(input_path: Path, ext: str) -> str | None:
    """Извлечение текста с сохранением семантической структуры (Markdown).

    Возвращает None при таймауте, "" — если текст извлечь не удалось.
    """
    md_path = input_path.with_suffix('.md')

    if ext == 'txt':
        try:
            return input_path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return ""

    if ext == 'pdf':
        cmd = ["pdftotext", "-q", str(input_path), str(md_path)]
    elif ext in ['doc', 'docx', 'fb2', 'epub', 'mobi']:
        # Конвертация в markdown сохраняет структуру глав (# Заголовок)
        # Добавляем --quiet для подавления вывода
        cmd = ["pandoc", "--quiet", "-t", "markdown", str(input_path), "-o", str(md_path)]
    else:
        return ""

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        # pandoc/pdftotext не установлены на сервере.
        return ""

    try:
        await asyncio.wait_for(proc.communicate(), timeout=PARSE_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        try:
            proc.kill()
            await proc.communicate()
        except Exception:
            pass
        md_path.unlink(missing_ok=True)
        return None

    # Проверяем код возврата: при ошибке pandoc/pdftotext оставлял частично
    # записанный файл, и этот мусор уходил в озвучку.
    if proc.returncode != 0:
        md_path.unlink(missing_ok=True)
        return ""

    if md_path.exists():
        try:
            return md_path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return ""
    return ""

async def validate_voices(codes) -> list[str]:
    """Проверяет коды голосов против живого списка edge-tts.

    Возвращает список несуществующих кодов. Если список получить не удалось
    (нет сети), возвращает пустой список — не мешаем запуску.
    """
    try:
        voices = await edge_tts.list_voices()
    except Exception:
        return []

    known = {v.get("ShortName") for v in voices}
    return sorted(code for code in set(codes) if code not in known)


async def generate_chunk(text: str, path: Path, voice: str, rate: str):
    """Озвучивает один чанк с повторами при сетевых сбоях."""
    async with get_semaphore():
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                communicate = edge_tts.Communicate(clean_text(text), voice, rate=rate)
                await communicate.save(str(path))
                if path.exists() and path.stat().st_size > 0:
                    return
                last_error = RuntimeError("edge-tts вернул пустой файл")
            except Exception as exc:  # noqa: BLE001 - edge_tts бросает разное
                last_error = exc
                path.unlink(missing_ok=True)

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))

        raise RuntimeError(
            f"edge-tts: не удалось озвучить чанк за {MAX_RETRIES} попыток ({last_error})"
        ) from last_error

# Границы фраз для нарезки чанков.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…:;])\s+")

def _hard_split(text: str, limit: int) -> tuple[str, str]:
    """Режет строку по последнему пробелу в пределах лимита."""
    if len(text) <= limit:
        return text, ""
    cut = text.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].strip(), text[cut:].strip()

def split_chunks(text: str, limit: int = TTS_CHUNK_SIZE) -> list[str]:
    """Делит текст на чанки, не разрывая слова и по возможности — по фразам.

    textwrap.wrap склеивал абзацы в одну длинную строку и мог разрезать слово
    пополам: на стыке чанков ломались слова и синтезатор читал обрывки.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buf = ""

    for para in re.split(r"\n{2,}", text):
        for sent in _SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if not sent:
                continue
            # Аномально длинный «абзац» без пробелов режем принудительно.
            while len(sent) > limit:
                head, tail = _hard_split(sent, limit)
                if buf:
                    chunks.append(buf)
                    buf = ""
                if head:
                    chunks.append(head)
                sent = tail
            # continue, а не break: иначе терялись все оставшиеся фразы абзаца.
            if not sent:
                continue
            if not buf:
                buf = sent
            elif len(buf) + 1 + len(sent) <= limit:
                buf = f"{buf} {sent}"
            else:
                chunks.append(buf)
                buf = sent

    if buf:
        chunks.append(buf)
    return chunks

def _concat_entry(path: Path) -> str:
    """Строка для ffmpeg concat. Экранируем одинарную кавычку в пути."""
    posix = path.resolve().as_posix()
    return "file '{}'".format(posix.replace("'", r"'\''"))

async def _squeeze_to_telegram_limit(final_file: Path) -> Path:
    """Сжимает том, если он не влезает в 50 МБ Telegram Bot API."""
    if not final_file.exists() or final_file.stat().st_size <= MAX_VOL_BYTES:
        return final_file

    small_file = final_file.with_name(f"{final_file.stem}_small.mp3")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(final_file),
        "-vn", "-ac", "1", "-ar", "22050", "-b:a", "24k",
        "-y", str(small_file),
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()

    # Без проверки returncode можно подменить рабочий том битым огрызком.
    if proc.returncode == 0 and small_file.exists() and small_file.stat().st_size > 0:
        final_file.unlink(missing_ok=True)
        small_file.replace(final_file)
    else:
        small_file.unlink(missing_ok=True)
    return final_file

async def process_book(text: str, workdir: Path, voice: str, rate: str, progress_callback, filename: str = "Unknown"):
    """Сборка томов по границам глав с контролем переполнения."""

    # Очистка мусора от pandoc (картинки, html-теги).
    # Раньше здесь стоял re.sub(r'', '', text) с ПУСТЫМ паттерном — он был
    # no-op: комментарий обещал чистку html-тегов, но чистились только картинки.
    text = _IMAGE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("\ufeff", "").replace("\xa0", " ")

    # Разрез по Markdown-заголовкам. Если их нет (txt) - по двойным переносам
    if re.search(r'(?m)^#{1,6}\s+', text):
        blocks = re.split(r'(?m)^#{1,6}\s+', text)
    else:
        blocks = re.split(r'\n\s*\n', text)

    blocks = [b.strip() for b in blocks if b.strip()]
    if not blocks:
        return []

    volumes_text = []
    current_vol = []
    current_len = 0
    
    # 1. Упаковка глав в Тома
    for block in blocks:
        block = block.strip()
        if not block: continue
        
        # Если одна глава монструозная (больше лимита), рубим ее принудительно
        if len(block) > MAX_VOL_CHARS:
            sub_blocks = split_chunks(block, MAX_VOL_CHARS)
            for sb in sub_blocks:
                if current_len + len(sb) > MAX_VOL_CHARS and current_len > 0:
                    volumes_text.append(" ".join(current_vol))
                    current_vol = [sb]
                    current_len = len(sb)
                else:
                    current_vol.append(sb)
                    current_len += len(sb)
        else:
            # Штатная укладка глав в Том
            if current_len + len(block) > MAX_VOL_CHARS and current_len > 0:
                volumes_text.append(" ".join(current_vol))
                current_vol = [block]
                current_len = len(block)
            else:
                current_vol.append(block)
                current_len += len(block)
                
    if current_vol:
        volumes_text.append(" ".join(current_vol))

    final_volumes = []

    # Чанки считаем заранее и теми же правилами, по которым реально рендерим.
    # Раньше счёт шёл через textwrap.wrap с break_long_words=True, а рендер —
    # с False, поэтому прогресс-бар уезжал за 100% и делился на ноль.
    volume_chunks = [split_chunks(vol) for vol in volumes_text]
    volume_chunks = [chunks for chunks in volume_chunks if chunks]
    if not volume_chunks:
        return []

    total_chunks_overall = sum(len(chunks) for chunks in volume_chunks)
    completed_chunks = 0
    progress_step = max(1, total_chunks_overall // 20)

    # 2. Изолированный рендер каждого Тома (снижает нагрузку на FS)
    for vol_idx, chunks in enumerate(volume_chunks, 1):
        tasks = []

        vol_dir = workdir / f"vol_{vol_idx}"
        vol_dir.mkdir(parents=True, exist_ok=True)

        try:
            for chunk_idx, chunk in enumerate(chunks):
                part_path = vol_dir / f"part_{chunk_idx:04d}.mp3"
                tasks.append(generate_chunk(chunk, part_path, voice, rate))

            # Асинхронное ожидание микро-чанков текущего Тома
            for future in asyncio.as_completed(tasks):
                await future
                completed_chunks += 1

                # Апдейт интерфейса каждые 5% или по завершению
                if completed_chunks % progress_step == 0 or completed_chunks == total_chunks_overall:
                    await progress_callback(completed_chunks, total_chunks_overall)

            # Склейка готового Тома
            list_file = vol_dir / "list.txt"
            final_file = workdir / f"volume_{vol_idx}.mp3"

            mp3_files = sorted(vol_dir.glob("part_*.mp3"))
            if not mp3_files:
                continue
            async with aiofiles.open(list_file, "w", encoding="utf-8") as f:
                for mp3 in mp3_files:
                    await f.write(_concat_entry(mp3) + "\n")

            # Имя файла приходит от юзера: убираем переводы строк и кавычки,
            # иначе ffmpeg спотыкается на ID3-теге.
            safe_title = re.sub(r'[\r\n"\'\\]', " ", str(filename))[:80].strip()

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                "-metadata", f"title={safe_title} - Часть {vol_idx}",
                "-metadata", "artist=Phoenix TTS Bot",
                "-metadata", "album=Аудиокнига",
                "-y",
                str(final_file)
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.communicate()

            if not final_file.exists() or final_file.stat().st_size == 0:
                continue

            # Страховка на случай, если том всё же не влезает в 50 МБ.
            await _squeeze_to_telegram_limit(final_file)

            if final_file.stat().st_size > MAX_VOL_BYTES:
                raise RuntimeError(
                    f"Том {vol_idx} после сжатия всё ещё больше 45 МБ — его не отправить в Telegram"
                )

            final_volumes.append(final_file)
        finally:
            shutil.rmtree(vol_dir, ignore_errors=True)
            
    return final_volumes
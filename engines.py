"""Движки синтеза речи бота.

edge   — облачный Microsoft Edge TTS: быстро, много языков, нужен интернет;
silero — локальная нейросеть Silero: полностью офлайн, считает на CPU сервера.
         Голосов меньше, скорость упирается в ядра.

Код голоса сам говорит, какой движок нужен: silero-голоса идут с префиксом
"silero:" (например "silero:aidar"), всё остальное — edge-tts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)

EDGE = "edge"
SILERO = "silero"
SILERO_PREFIX = "silero:"

# Микро-чанк для edge-tts: он лучше звучит на коротких фрагментах.
EDGE_CHUNK_SIZE = int(os.getenv("TTS_CHUNK_SIZE", "1800"))
# Silero ругается на строку длиннее 1000 символов и падает с
# "Model couldn't generate your text, probably it's too long".
# Проверено на сервере: 600 символов стабильно, 950 уже падает.
SILERO_CHUNK_SIZE = int(os.getenv("SILERO_CHUNK_SIZE", "600"))

# 24 кГц хватает для речи: файл вдвое легче, и mp3 потом кодируется вдвое быстрее.
SILERO_SAMPLE_RATE = int(os.getenv("SILERO_SAMPLE_RATE", "24000"))
# Потоки torch и число воркеров. Замер на i7-6700 (8 потоков): 1 воркер с 4
# потоками ~550 симв/с, 2 воркера по 2 потока ~690 симв/с, 8 потоков хуже 4.
SILERO_THREADS = int(os.getenv("SILERO_THREADS", "4"))
SILERO_WORKERS = int(os.getenv("SILERO_WORKERS", "1"))
# Прогревать модель при старте бота (занимает пару секунд и ~0.5 ГБ RAM).
SILERO_WARMUP = os.getenv("SILERO_WARMUP", "0") == "1"

SILERO_MAX_RETRIES = int(os.getenv("SILERO_MAX_RETRIES", "3"))
EDGE_MAX_RETRIES = int(os.getenv("EDGE_MAX_RETRIES", "4"))
RETRY_BASE_DELAY = 2.0

# Список моделей Silero (models.yml из их репозитория). Его обновляет
# scripts/update_silero.sh, чтобы движок брал самую свежую модель.
SILERO_MODELS_YML = Path(
    os.getenv("SILERO_MODELS_YML", str(Path(__file__).with_name("silero_models.yml")))
)
SILERO_MODEL_ID = os.getenv("SILERO_MODEL_ID", "")

# Запасной вариант, если список моделей недоступен.
SILERO_FALLBACK_MODEL = "v5_ru"

_edge_semaphore: asyncio.Semaphore | None = None
_silero_model = None
_silero_model_id: str | None = None
_silero_model_lock = threading.Lock()
_silero_pool: ThreadPoolExecutor | None = None
_silero_semaphore: asyncio.Semaphore | None = None


# --- общие помощники -------------------------------------------------------

def engine_of(voice_code: str) -> str:
    """Какой движок нужен для этого кода голоса."""
    return SILERO if (voice_code or "").startswith(SILERO_PREFIX) else EDGE


def silero_speaker(voice_code: str) -> str:
    """'silero:aidar' -> 'aidar'."""
    code = voice_code or ""
    return code[len(SILERO_PREFIX):] if code.startswith(SILERO_PREFIX) else code


def silero_code(speaker: str) -> str:
    return f"{SILERO_PREFIX}{speaker}"


def edge_semaphore() -> asyncio.Semaphore:
    """Общая очередь edge-tts: сеть можно грузить параллельно."""
    global _edge_semaphore
    if _edge_semaphore is None:
        _edge_semaphore = asyncio.Semaphore(int(os.getenv("TTS_THREADS", "12")))
    return _edge_semaphore


def rate_factor(rate: str) -> float:
    """'+15%' -> 1.15. Мусор в настройке не должен ломать озвучку."""
    try:
        return 1.0 + float(str(rate).strip().rstrip("%")) / 100.0
    except (TypeError, ValueError):
        return 1.0


def atempo_args(rate: str) -> list[str]:
    """Скорость для ffmpeg. Нужна там, где движок не умеет её сам (Silero)."""
    factor = rate_factor(rate)
    if abs(factor - 1.0) < 0.005:
        return []
    factor = min(max(factor, 0.5), 2.0)
    return ["-filter:a", f"atempo={factor:.3f}"]


def resolve_model_id() -> str:
    """Самая свежая русская модель Silero.

    Порядок: явная настройка SILERO_MODEL_ID -> список моделей из
    silero_models.yml -> встроенный запасной вариант.
    """
    if SILERO_MODEL_ID:
        return SILERO_MODEL_ID
    try:
        text = SILERO_MODELS_YML.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return SILERO_FALLBACK_MODEL
    found: list[tuple[tuple[int, int], str]] = []
    for name in re.findall(r"^\s{2,6}(v\d+(?:_\d+)?_ru):", text, flags=re.M):
        m = re.match(r"v(\d+)(?:_(\d+))?_ru", name)
        if m:
            # v5_ru — это v5.0, v5_6_ru — v5.6, поэтому младший номер 0.
            found.append(((int(m.group(1)), int(m.group(2) or 0)), name))
    return max(found)[1] if found else SILERO_FALLBACK_MODEL


# --- edge-tts --------------------------------------------------------------

async def _edge_synth(text: str, voice: str, rate: str, out_path: Path) -> None:
    """Озвучивает фрагмент через облако, повторяя попытку при сетевых сбоях."""
    async with edge_semaphore():
        last_error: Exception | None = None
        for attempt in range(EDGE_MAX_RETRIES):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(str(out_path))
                if out_path.exists() and out_path.stat().st_size > 0:
                    return
                last_error = RuntimeError("edge-tts вернул пустой файл")
            except Exception as exc:  # noqa: BLE001 - edge_tts бросает разное
                last_error = exc
                out_path.unlink(missing_ok=True)
            if attempt < EDGE_MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        raise RuntimeError(
            f"edge-tts: не удалось озвучить фрагмент за {EDGE_MAX_RETRIES} попыток ({last_error})"
        ) from last_error


# --- silero ----------------------------------------------------------------

def _silero_pool_get() -> ThreadPoolExecutor:
    global _silero_pool
    if _silero_pool is None:
        _silero_pool = ThreadPoolExecutor(max_workers=max(1, SILERO_WORKERS),
                                          thread_name_prefix="silero")
    return _silero_pool


def _silero_semaphore_get() -> asyncio.Semaphore:
    global _silero_semaphore
    if _silero_semaphore is None:
        _silero_semaphore = asyncio.Semaphore(max(1, SILERO_WORKERS))
    return _silero_semaphore


def _load_model(model_id: str):
    """Загружает модель Silero в память (в потоке, а не в event loop)."""
    import torch
    from silero import silero_tts

    torch.set_num_threads(max(1, SILERO_THREADS))
    model, _ = silero_tts(language="ru", speaker=model_id)
    model.to(torch.device("cpu"))
    return model


def _ensure_model(model_id: str):
    global _silero_model, _silero_model_id
    if _silero_model is not None and _silero_model_id == model_id:
        return _silero_model
    with _silero_model_lock:
        if _silero_model is None or _silero_model_id != model_id:
            logger.info("Загружаю модель Silero %s (потоков torch: %s)", model_id, SILERO_THREADS)
            _silero_model = _load_model(model_id)
            _silero_model_id = model_id
    return _silero_model


def _reset_model() -> None:
    global _silero_model, _silero_model_id
    with _silero_model_lock:
        _silero_model = None
        _silero_model_id = None


def _silero_synth_sync(model_id: str, text: str, speaker: str, out_path: Path) -> None:
    import soundfile as sf

    model = _ensure_model(model_id)
    audio = model.apply_tts(
        text=text,
        speaker=speaker,
        sample_rate=SILERO_SAMPLE_RATE,
        put_accent=True,   # сама расставляет ударения
        put_yo=True,       # и букву ё
    )
    sf.write(str(out_path), audio.numpy(), SILERO_SAMPLE_RATE)


async def _silero_synth(text: str, voice: str, rate: str, out_path: Path) -> None:
    speaker = silero_speaker(voice)
    model_id = resolve_model_id()
    loop = asyncio.get_running_loop()
    last_error: Exception | None = None

    async with _silero_semaphore_get():
        for attempt in range(SILERO_MAX_RETRIES):
            try:
                await loop.run_in_executor(
                    _silero_pool_get(), _silero_synth_sync, model_id, text, speaker, out_path
                )
                if out_path.exists() and out_path.stat().st_size > 0:
                    return
                last_error = RuntimeError("Silero вернул пустой файл")
            except Exception as exc:  # noqa: BLE001 - torch бросает разное
                last_error = exc
                out_path.unlink(missing_ok=True)
                # Модель могла не загрузиться или быть сломанной обновлением:
                # в следующий раз попробуем поднять её заново.
                _reset_model()
            if attempt < SILERO_MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BASE_DELAY * (attempt + 1))

    raise RuntimeError(
        f"Silero: не удалось озвучить фрагмент за {SILERO_MAX_RETRIES} попыток ({last_error})"
    ) from last_error


async def silero_voices() -> list[str]:
    """Список голосов текущей модели (нужен для проверки и для --voices в CLI)."""
    loop = asyncio.get_running_loop()
    model_id = resolve_model_id()

    def _speakers() -> list[str]:
        return list(getattr(_ensure_model(model_id), "speakers", []))

    return await loop.run_in_executor(_silero_pool_get(), _speakers)


async def warmup() -> None:
    """Заранее поднять модель, чтобы первое сообщение не ждало загрузки."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_silero_pool_get(), _ensure_model, resolve_model_id())


# --- единая точка входа ----------------------------------------------------

def part_suffix(voice_code: str) -> str:
    """Чем движок отдаёт фрагмент: edge — mp3, silero — wav."""
    return ".wav" if engine_of(voice_code) == SILERO else ".mp3"


def chunk_size_for(voice_code: str) -> int:
    return SILERO_CHUNK_SIZE if engine_of(voice_code) == SILERO else EDGE_CHUNK_SIZE


async def synthesize(text: str, voice_code: str, rate: str, out_path: Path) -> None:
    """Озвучить фрагмент текста выбранным движком в файл out_path."""
    if engine_of(voice_code) == SILERO:
        await _silero_synth(text, voice_code, rate, out_path)
    else:
        await _edge_synth(text, voice_code, rate, out_path)

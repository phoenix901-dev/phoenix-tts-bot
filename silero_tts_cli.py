#!/usr/bin/env python3
"""Обёртка над движками озвучки без телеграм-бота.

Умеет то же, что бот, но из командной строки: локальная нейросеть Silero
(офлайн, считает на CPU) и облачный edge-tts.

Примеры:
    python silero_tts_cli.py --voices
    python silero_tts_cli.py --check
    python silero_tts_cli.py --text "Привет, мир" -o out
    python silero_tts_cli.py --file book.fb2 --voice baya --rate +10%
    python silero_tts_cli.py --file book.txt --voice ru-RU-SvetlanaNeural

Голоса: короткие имена (aidar, baya, kseniya, eugene, xenia) — это Silero,
полные коды вида ru-RU-DmitryNeural — облако edge-tts.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import engines
from core import SUPPORTED_EXTS, parse_file, process_book

SAMPLE = (
    "Ночь была тихая и светлая, хотя солнце уже давно село. Тёплый воздух едва "
    "заметно дрожал над рекой, и в этом дрожании угадывалось приближение утра. "
    "Он вышел на крыльцо и долго стоял, слушая, как шумит вода на перекатах."
)


def normalize_voice(value: str) -> str:
    """'aidar' -> 'silero:aidar', 'ru-RU-DmitryNeural' остаётся как есть."""
    if not value:
        return "silero:aidar"
    if value.startswith("silero:") or "-" in value:
        return value
    return engines.silero_code(value)


async def cmd_voices() -> int:
    model_id = engines.resolve_model_id()
    print(f"Модель Silero: {model_id} ({engines.SILERO_SAMPLE_RATE} Гц, потоков {engines.SILERO_THREADS})")
    try:
        speakers = await engines.silero_voices()
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось поднять модель: {exc}")
        return 1
    for speaker in speakers:
        print(f"  {engines.silero_code(speaker)}")
    print("\nОблачные голоса edge-tts: edge-tts --list-voices | grep ru-RU")
    return 0


async def cmd_check() -> int:
    """Проверка на живой модели: загрузилась, синтезирует, с какой скоростью."""
    model_id = engines.resolve_model_id()
    print(f"Модель: {model_id}, потоков torch: {engines.SILERO_THREADS}")
    out = Path("silero_check.wav")
    started = time.time()
    await engines.synthesize(SAMPLE, engines.silero_code("aidar"), "+0%", out)
    elapsed = time.time() - started
    if not out.exists() or out.stat().st_size == 0:
        print("ОШИБКА: пустой файл")
        return 1
    # Загрузка модели попадает в первый замер, поэтому второй прогон честнее.
    started = time.time()
    await engines.synthesize(SAMPLE, engines.silero_code("aidar"), "+0%", out)
    warm = time.time() - started
    size_kb = out.stat().st_size / 1024
    print(f"Первый прогон (с загрузкой модели): {elapsed:.1f} с")
    print(f"Повторный прогон: {warm:.2f} с на {len(SAMPLE)} символов "
          f"(~{len(SAMPLE) / warm:.0f} симв/с), файл {size_kb:.0f} КБ")
    out.unlink(missing_ok=True)
    return 0


async def cmd_synth(args) -> int:
    voice = normalize_voice(args.voice)
    outdir = Path(args.out or ".")
    outdir.mkdir(parents=True, exist_ok=True)

    if args.text:
        ext = engines.part_suffix(voice)
        target = outdir / f"speech{ext}"
        started = time.time()
        await engines.synthesize(args.text, voice, args.rate, target)
        print(f"Готово: {target} ({time.time() - started:.1f} с)")
        return 0

    path = Path(args.file)
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 1
    ext = path.suffix.lstrip(".").lower()
    if ext not in SUPPORTED_EXTS:
        print(f"Формат не поддерживается: {ext}. Умею: {', '.join(sorted(SUPPORTED_EXTS))}")
        return 1

    print(f"Читаю {path.name}...")
    raw = await parse_file(path, ext)
    if raw is None:
        print("Ошибка: истёк таймаут разбора файла")
        return 1
    if not raw.strip():
        print("Ошибка: текст извлечь не удалось")
        return 1
    print(f"Символов: {len(raw)}. Движок: {'Silero (локально)' if engines.engine_of(voice) == engines.SILERO else 'edge-tts (облако)'}")

    started = time.time()
    last = {"percent": -5}

    async def progress(done: int, total: int) -> None:
        if total <= 0:
            return
        percent = int(done / total * 100)
        if percent - last["percent"] < 5 and percent != 100:
            return
        last["percent"] = percent
        print(f"  {percent}% ({done}/{total})", flush=True)

    volumes = await process_book(raw, outdir, voice, args.rate, progress, path.stem)
    if not volumes:
        print("Не получилось собрать ни одного тома")
        return 1
    total_mb = sum(v.stat().st_size for v in volumes) / 1048576
    print(f"Готово за {(time.time() - started) / 60:.1f} мин: {len(volumes)} файл(ов), {total_mb:.1f} МБ")
    for volume in volumes:
        print(f"  {volume}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Озвучка текста и книг: локальная Silero или облачный edge-tts",
    )
    parser.add_argument("--text", help="короткий текст для озвучки")
    parser.add_argument("--file", help="книга: pdf, doc, docx, fb2, epub, mobi, txt")
    parser.add_argument("--voice", default="aidar", help="голос: aidar/baya/kseniya/eugene/xenia или код edge-tts")
    parser.add_argument("--rate", default="+0%", help="скорость речи, например +15%%")
    parser.add_argument("-o", "--out", help="куда положить результат (по умолчанию текущая папка)")
    parser.add_argument("--voices", action="store_true", help="показать голоса локальной модели")
    parser.add_argument("--check", action="store_true", help="проверить модель и замерить скорость")
    args = parser.parse_args()

    if args.voices:
        return asyncio.run(cmd_voices())
    if args.check:
        return asyncio.run(cmd_check())
    if not args.text and not args.file:
        parser.print_help()
        return 1
    return asyncio.run(cmd_synth(args))


if __name__ == "__main__":
    sys.exit(main())

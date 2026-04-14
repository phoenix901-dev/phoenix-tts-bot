import os
import asyncio
import textwrap
import re
import aiofiles
from pathlib import Path
import edge_tts

THREADS = 12
# Безопасный предел для Telegram: ~200 000 символов = ~2.5 часа речи = ~20 МБ MP3
MAX_VOL_CHARS = 200000 
TTS_CHUNK_SIZE = 2000

async def parse_file(input_path: Path, ext: str) -> str:
    """Извлечение текста с сохранением семантической структуры (Markdown)."""
    md_path = input_path.with_suffix('.md')
    
    if ext == 'pdf':
        cmd = f"pdftotext '{input_path}' '{md_path}'"
    elif ext in ['doc', 'docx', 'fb2', 'epub', 'mobi']:
        # Конвертация в markdown сохраняет структуру глав (# Заголовок)
        cmd = f"pandoc -t markdown '{input_path}' -o '{md_path}'"
    else: # txt
        return input_path.read_text(encoding='utf-8', errors='ignore')
        
    proc = await asyncio.create_subprocess_shell(cmd)
    await proc.communicate()
    
    if md_path.exists():
        return md_path.read_text(encoding='utf-8', errors='ignore')
    return ""

async def generate_chunk(text: str, path: Path, voice: str, rate: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(path))

async def process_book(text: str, workdir: Path, voice: str, rate: str, progress_callback):
    """Сборка томов по границам глав с контролем переполнения."""
    
    # Очистка мусора от pandoc (картинки, html-теги)
    text = re.sub(r'', '', text, flags=re.DOTALL)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    
    # Разрез по Markdown-заголовкам. Если их нет (txt) - по двойным переносам
    if re.search(r'(?m)^#{1,6}\s+', text):
        blocks = re.split(r'(?m)^#{1,6}\s+', text)
    else:
        blocks = re.split(r'\n\s*\n', text)
        
    volumes_text = []
    current_vol = []
    current_len = 0
    
    # 1. Упаковка глав в Тома
    for block in blocks:
        block = block.strip()
        if not block: continue
        
        # Если одна глава монструозная (больше лимита), рубим ее принудительно
        if len(block) > MAX_VOL_CHARS:
            sub_blocks = textwrap.wrap(block, MAX_VOL_CHARS, break_long_words=False)
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
    
    # Подсчет общего количества микро-чанков для корректного прогресс-бара
    total_chunks_overall = sum(len(textwrap.wrap(v, TTS_CHUNK_SIZE)) for v in volumes_text)
    completed_chunks = 0
    
    semaphore = asyncio.Semaphore(THREADS)

    # 2. Изолированный рендер каждого Тома (снижает нагрузку на FS)
    for vol_idx, vol_text in enumerate(volumes_text, 1):
        chunks = textwrap.wrap(vol_text, TTS_CHUNK_SIZE, break_long_words=False)
        tasks = []
        
        vol_dir = workdir / f"vol_{vol_idx}"
        vol_dir.mkdir(exist_ok=True)
        
        for chunk_idx, chunk in enumerate(chunks):
            part_path = vol_dir / f"part_{chunk_idx:04d}.mp3"
            tasks.append(generate_chunk(chunk, part_path, voice, rate, semaphore))
            
        # Асинхронное ожидание микро-чанков текущего Тома
        for future in asyncio.as_completed(tasks):
            await future
            completed_chunks += 1
            
            # Апдейт интерфейса каждые 5% или по завершению
            if completed_chunks % max(1, total_chunks_overall // 20) == 0 or completed_chunks == total_chunks_overall:
                await progress_callback(completed_chunks, total_chunks_overall)
                
        # Склейка готового Тома
        list_file = vol_dir / "list.txt"
        final_file = workdir / f"volume_{vol_idx}.mp3"
        
        mp3_files = sorted(vol_dir.glob("part_*.mp3"))
        async with aiofiles.open(list_file, "w") as f:
            for mp3 in mp3_files:
                await f.write(f"file '{mp3.absolute()}'\n")
                
        cmd = f"ffmpeg -f concat -safe 0 -i {list_file} -c copy -y {final_file}"
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        
        if final_file.exists():
            final_volumes.append(final_file)
            
    return final_volumes
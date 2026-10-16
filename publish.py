"""Публикация готовой аудиокниги: файлы в общий каталог плюс короткая ссылка.

Телеграм не даёт ботам отправлять файлы больше 50 МБ, поэтому книга целиком
уходит не вложением, а ссылкой на нашем сервере: скачивание не ограничено,
можно докачивать и вернуться к книге позже.

Ссылка одноразовая по смыслу: имя каталога — случайный токен, а сам каталог
удаляется через BOOK_TTL_DAYS дней (scripts/cleanup_books.sh в кроне).
"""
from __future__ import annotations

import html
import logging
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Куда складывать книги. Этот каталог отдаёт Apache на отдельном порту,
# наружу его публикует прокси-фасад book.2aim.ru.
PUBLIC_BOOKS_DIR = Path(os.getenv("PUBLIC_BOOKS_DIR", "/var/www/books"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://book.2aim.ru").rstrip("/")
BOOK_TTL_DAYS = int(os.getenv("BOOK_TTL_DAYS", "7"))
# Одну часть можно и вложением прислать: лимит Телеграма она не рвёт.
ATTACH_SINGLE_PART = os.getenv("ATTACH_SINGLE_PART", "1") == "1"

_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_name(name: str, fallback: str = "book") -> str:
    """Имя файла для публичного каталога: без путей и спецсимволов."""
    name = _UNSAFE_RE.sub("_", (name or "").strip()).strip(". ")
    name = re.sub(r"\s+", " ", name)
    return name[:80] or fallback


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.1f} МБ"
    return f"{max(1, num_bytes // 1024)} КБ"


def publish(volumes: list[Path], title: str) -> tuple[str, Path, list[Path]]:
    """Переносит готовые тома в публичный каталог.

    Возвращает (ссылка на страницу книги, каталог, список файлов томов).
    """
    safe_title = _safe_name(title)
    target = PUBLIC_BOOKS_DIR / secrets.token_urlsafe(9)
    target.mkdir(parents=True, exist_ok=False)

    parts: list[Path] = []
    for idx, volume in enumerate(volumes, 1):
        name = f"{safe_title}_Часть_{idx}.mp3" if len(volumes) > 1 else f"{safe_title}.mp3"
        dst = target / name
        # move, а не copy: тома по 30 МБ, копировать их незачем.
        shutil.move(str(volume), str(dst))
        dst.chmod(0o644)
        parts.append(dst)

    archive = None
    if len(parts) > 1:
        archive = target / f"{safe_title}_все_части.zip"
        # ZIP_STORED: mp3 уже сжат, упаковка только склеивает файлы.
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
            for part in parts:
                zf.write(part, arcname=part.name)
        archive.chmod(0o644)

    _write_index(target, safe_title, parts, archive)
    target.chmod(0o755)
    url = f"{PUBLIC_BASE_URL}/{target.name}/"
    logger.info("Книга опубликована: %s (%s файлов)", url, len(parts))
    return url, target, parts


def _write_index(target: Path, title: str, parts: list[Path], archive: Path | None) -> None:
    total = sum(p.stat().st_size for p in parts)
    rows = []
    if archive is not None:
        rows.append(
            f'<li><a href="{html.escape(archive.name)}">Вся книга одним архивом</a> '
            f'<span>{_human_size(archive.stat().st_size)}</span></li>'
        )
    for part in parts:
        rows.append(
            f'<li><a href="{html.escape(part.name)}">{html.escape(part.name)}</a> '
            f'<span>{_human_size(part.stat().st_size)}</span></li>'
        )

    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style>
 body {{ background:#12141a; color:#e8e8ee; font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px; }}
 main {{ max-width:640px; margin:0 auto; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 p.meta {{ color:#9aa0ae; margin:0 0 18px; }}
 ul {{ list-style:none; padding:0; margin:0; }}
 li {{ display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-bottom:1px solid #232734; }}
 a {{ color:#7ab8ff; text-decoration:none; }}
 a:hover {{ text-decoration:underline; }}
 span {{ color:#9aa0ae; white-space:nowrap; }}
</style>
</head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="meta">{len(parts)} файл(ов), всего {_human_size(total)}. Ссылка живёт {BOOK_TTL_DAYS} дней.</p>
<ul>
{chr(10).join(rows)}
</ul>
</main></body>
</html>
"""
    (target / "index.html").write_text(page, encoding="utf-8")


def public_url_for(part: Path) -> str:
    """Ссылка на конкретный файл книги."""
    return f"{PUBLIC_BASE_URL}/{part.parent.name}/{part.name}"

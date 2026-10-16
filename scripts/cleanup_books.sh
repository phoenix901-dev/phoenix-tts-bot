#!/usr/bin/env bash
# Чистит опубликованные книги старше BOOK_TTL_DAYS дней.
#
# Телеграм-сообщение со ссылкой живёт вечно, а файлы на сервере — нет.
# Ежедневный cron: 15 4 * * *
set -uo pipefail

BOOKS_DIR="${PUBLIC_BOOKS_DIR:-/var/www/books}"
TTL_DAYS="${BOOK_TTL_DAYS:-7}"
LOG="${BOOKS_CLEANUP_LOG:-/var/log/tts-books-cleanup.log}"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

if [[ ! -d "$BOOKS_DIR" ]]; then
    log "Каталога $BOOKS_DIR нет, чистить нечего"
    exit 0
fi

# Считаем заранее: сколько каталогов и сколько места освободили.
count=$(find "$BOOKS_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$TTL_DAYS" | wc -l)
before=$(du -sm "$BOOKS_DIR" 2>/dev/null | awk '{print $1}')

# -mindepth/-maxdepth и только type d: удаляем ровно каталоги книг, не задевая
# сам каталог раздачи и случайные файлы в нём.
find "$BOOKS_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$TTL_DAYS" -exec rm -rf -- {} +

after=$(du -sm "$BOOKS_DIR" 2>/dev/null | awk '{print $1}')
log "Удалено книг: $count (старше $TTL_DAYS дней). Занято: ${before:-?} МБ -> ${after:-?} МБ"
exit 0

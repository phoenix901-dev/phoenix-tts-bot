#!/usr/bin/env bash
# Держит локальный движок Silero в актуальном состоянии.
#
# Что делает:
#   1) обновляет список моделей (silero_models.yml) — движок берёт из него
#      самый свежий русский вариант;
#   2) обновляет пакет silero в venv бота (без зависимостей: torch не трогаем);
#   3) если файл модели на стороне Silero изменился — перекачивает его;
#   4) прогоняет живой тест синтеза и перезапускает бота;
#   5) при провале теста откатывает пакет и модель из архива.
#
# Запуск: scripts/update_silero.sh   (в cron — раз в неделю, модель не вызывает)
set -uo pipefail

BOT_DIR="${BOT_DIR:-/root/telegram/phoenix-tts-bot}"
VENV="$BOT_DIR/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
SERVICE="${TTS_SERVICE:-bbot}"
BACKUP_DIR="${BACKUP_DIR:-/root/telegram/backups}"
LOG="${SILERO_UPDATE_LOG:-/var/log/tts-silero-update.log}"
MODELS_URL="https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml"
MODELS_YML="$BOT_DIR/silero_models.yml"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

site_packages=$(ls -d "$VENV"/lib/python3.*/site-packages 2>/dev/null | head -1)
if [[ -z "$site_packages" ]]; then
    log "ОШИБКА: не нашёл site-packages в $VENV"
    exit 1
fi
SILERO_DIR="$site_packages/silero"
MODEL_DIR="$SILERO_DIR/model"

installed_version() { "$PIP" show silero 2>/dev/null | awk '/^Version:/{print $2}'; }

latest_version() {
    "$PIP" index versions silero 2>/dev/null \
        | sed -n 's/^silero (\(.*\))$/\1/p' | head -1
}

# Список моделей: он же источник для engines.resolve_model_id.
if curl -fsSL --max-time 60 "$MODELS_URL" -o "$MODELS_YML.new"; then
    if ! cmp -s "$MODELS_YML.new" "$MODELS_YML"; then
        mv "$MODELS_YML.new" "$MODELS_YML"
        changed_yml=1
    else
        rm -f "$MODELS_YML.new"
        changed_yml=0
    fi
else
    rm -f "$MODELS_YML.new"
    changed_yml=0
    log "ПРЕДУПРЕЖДЕНИЕ: список моделей не скачался, работаю с локальным"
fi

# Новейшая русская модель: имя спрашиваем у самого движка, чтобы правило
# выбора версии жило в одном месте (engines.resolve_model_id).
newest_model=$(cd "$BOT_DIR" && "$PY" -c "import engines; print(engines.resolve_model_id())" 2>/dev/null)
newest_model="${newest_model:-v5_ru}"
model_url=$(grep -A3 -E "^ {2,6}${newest_model}:" "$MODELS_YML" 2>/dev/null \
    | sed -n "s/.*package: *'\([^']*\)'.*/\1/p" | head -1)
model_file="$MODEL_DIR/$(basename "${model_url:-$newest_model.pt}")"
remote_size=$(curl -fsSI --max-time 60 "${model_url:-}" 2>/dev/null \
    | tr -d '\r' | awk 'tolower($1)=="content-length:"{print $2}' | tail -1)
local_size=$(stat -c%s "$model_file" 2>/dev/null || echo 0)

old_ver=$(installed_version)
new_ver=$(latest_version)
[[ -z "$new_ver" ]] && new_ver="$old_ver"

model_changed=0
if [[ -n "${remote_size:-}" && "$remote_size" != "$local_size" ]]; then
    model_changed=1
fi

if [[ "$old_ver" == "$new_ver" && "$changed_yml" == 0 && "$model_changed" == 0 ]]; then
    log "Актуально: silero $old_ver, модель $newest_model ($local_size Б). Ничего не делаю."
    exit 0
fi

log "Обновление: пакет $old_ver -> $new_ver, модель $newest_model (${local_size} -> ${remote_size:-?} Б)"

stamp=$(date '+%Y%m%d-%H%M%S')
backup="$BACKUP_DIR/silero-$stamp.tgz"
mkdir -p "$BACKUP_DIR"
if ! tar czf "$backup" -C "$site_packages" silero 2>>"$LOG"; then
    log "ОШИБКА: не смог забэкапить $SILERO_DIR, обновление отменено"
    exit 1
fi
log "Бэкап: $backup"

# --no-deps: обновляем только сам пакет, torch и прочее не трогаем.
if ! "$PIP" install --quiet --upgrade --no-deps silero >>"$LOG" 2>&1; then
    log "ОШИБКА: pip не смог обновить silero, откат не нужен (ничего не изменилось)"
    exit 1
fi

if [[ "$model_changed" == 1 ]]; then
    # Скачается заново при первом синтезе — уже актуальный.
    rm -f "$MODEL_DIR"/*.pt
    log "Файл модели удалён, будет скачан заново"
fi

smoke=$("$PY" "$BOT_DIR/silero_tts_cli.py" --check 2>&1)
if [[ $? -ne 0 ]]; then
    log "ОШИБКА теста синтеза, откатываю: $smoke"
    rm -rf "$SILERO_DIR"
    tar xzf "$backup" -C "$site_packages" >>"$LOG" 2>&1
    systemctl restart "$SERVICE" >>"$LOG" 2>&1
    log "Откат выполнен, бот перезапущен на прежней версии"
    exit 1
fi

systemctl restart "$SERVICE" >>"$LOG" 2>&1
# Старые модели на диске больше не нужны: движок грузит только актуальную.
find "$MODEL_DIR" -maxdepth 1 -name '*.pt' ! -name "$(basename "$model_file")" -delete 2>/dev/null
log "OK: silero $new_ver, модель $newest_model. Тест синтеза: $(echo "$smoke" | tail -1)"
exit 0

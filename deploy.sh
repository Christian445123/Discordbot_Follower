#!/usr/bin/env bash
# Holt neue Commits vom Repo und startet den Bot nur neu, wenn es tatsaechlich
# Aenderungen gab. Gedacht fuer einen periodischen Cron-Aufruf (siehe README).
#
# Nutzt --ff-only statt --hard: schlaegt sauber fehl (und loggt/meldet das),
# statt lokale Aenderungen auf dem Server stillschweigend zu ueberschreiben.
set -euo pipefail

cd "$(dirname "$0")"

LOG_FILE="./logs/deploy.log"
mkdir -p ./logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Liest eine einzelne Variable direkt aus .env, ohne die ganze Datei zu sourcen
# (vermeidet Ueberraschungen durch andere Zeilen/Sonderzeichen in .env).
env_get() {
    local key="$1"
    [ -f .env ] || return 0
    grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | sed -E 's/[[:space:]]*(#.*)?$//'
}

notify_discord() {
    local message="$1"
    local webhook_url
    webhook_url="$(env_get DISCORD_LOG_WEBHOOK_URL)"
    [ -z "$webhook_url" ] && return 0
    local escaped="${message//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    escaped="${escaped//$'\n'/\\n}"
    curl -fsS -X POST -H "Content-Type: application/json" \
        -d "{\"content\":\"${escaped}\"}" \
        "$webhook_url" >/dev/null 2>&1 || true
}

before="$(git rev-parse HEAD)"

if ! git fetch --quiet origin main; then
    log "git fetch fehlgeschlagen, breche ab."
    notify_discord "⚠️ Deploy: git fetch fehlgeschlagen."
    exit 1
fi

if ! git merge --ff-only --quiet origin/main; then
    log "Fast-Forward nicht moeglich (lokale Aenderungen auf dem Server?) - manueller Eingriff noetig."
    notify_discord "⚠️ Deploy: Fast-Forward nicht moeglich - manueller Eingriff auf dem Server noetig."
    exit 1
fi

after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
    exit 0  # keine Aenderungen, nichts zu tun
fi

log "Update gefunden: $before -> $after"
notify_discord "🔄 Deploy: Update gefunden ($(echo "$before" | cut -c1-7) -> $(echo "$after" | cut -c1-7)), starte Bot neu..."

if git diff --name-only "$before" "$after" | grep -qx "requirements.txt"; then
    log "requirements.txt geaendert, installiere Abhaengigkeiten..."
    ./venv/bin/pip install -r requirements.txt >> "$LOG_FILE" 2>&1
fi

log "Starte Bot neu (pm2 restart follower-bot)..."
if pm2 restart follower-bot >> "$LOG_FILE" 2>&1; then
    log "Fertig."
    notify_discord "✅ Deploy erfolgreich abgeschlossen."
else
    log "pm2 restart fehlgeschlagen."
    notify_discord "⚠️ Deploy: pm2 restart fehlgeschlagen, bitte pruefen."
    exit 1
fi

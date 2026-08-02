#!/usr/bin/env bash
# Holt neue Commits vom Repo und startet den Bot nur neu, wenn es tatsaechlich
# Aenderungen gab. Gedacht fuer einen periodischen Cron-Aufruf (siehe README).
#
# Nutzt --ff-only statt --hard: schlaegt sauber fehl (und loggt das), statt
# lokale Aenderungen auf dem Server stillschweigend zu ueberschreiben.
set -euo pipefail

cd "$(dirname "$0")"

LOG_FILE="./logs/deploy.log"
mkdir -p ./logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

before="$(git rev-parse HEAD)"

if ! git fetch --quiet origin main; then
    log "git fetch fehlgeschlagen, breche ab."
    exit 1
fi

if ! git merge --ff-only --quiet origin/main; then
    log "Fast-Forward nicht moeglich (lokale Aenderungen auf dem Server?) - manueller Eingriff noetig."
    exit 1
fi

after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
    exit 0  # keine Aenderungen, nichts zu tun
fi

log "Update gefunden: $before -> $after"

if git diff --name-only "$before" "$after" | grep -qx "requirements.txt"; then
    log "requirements.txt geaendert, installiere Abhaengigkeiten..."
    ./venv/bin/pip install -r requirements.txt >> "$LOG_FILE" 2>&1
fi

log "Starte Bot neu (pm2 restart follower-bot)..."
pm2 restart follower-bot >> "$LOG_FILE" 2>&1
log "Fertig."

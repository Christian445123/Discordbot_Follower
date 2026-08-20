#!/usr/bin/env bash
# Prueft periodisch (per Cron), ob der Bot wirklich funktioniert - nicht nur,
# ob der Prozess laeuft, sondern ob die Slash-Commands tatsaechlich bei
# Discord fuer die Guild registriert sind. Startet per PM2 neu, falls nicht.
#
# Ergaenzt deploy.sh (das nur bei neuen Commits neu startet) und PM2's
# autorestart (das nur einen abgestuerzten Prozess erkennt, nicht einen
# Prozess, der laeuft aber dessen Commands verschwunden/nie synchronisiert
# sind, z.B. weil der Sync beim Start dauerhaft fehlgeschlagen ist).
set -uo pipefail

cd "$(dirname "$0")"

LOG_FILE="./logs/watchdog.log"
mkdir -p ./logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Liest eine einzelne Variable direkt aus .env (siehe deploy.sh fuer denselben Ansatz)
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

restart_bot() {
    local reason="$1"
    log "Neustart ausgeloest: $reason"
    notify_discord "🔁 Watchdog: Bot wird neugestartet ($reason)."
    if pm2 restart follower-bot >> "$LOG_FILE" 2>&1; then
        log "Neustart erfolgreich."
    else
        log "pm2 restart fehlgeschlagen!"
        notify_discord "⚠️ Watchdog: pm2 restart ist fehlgeschlagen, bitte manuell pruefen."
    fi
}

TOKEN="$(env_get DISCORD_TOKEN)"
GUILD_ID="$(env_get GUILD_ID)"

if [ -z "$TOKEN" ] || [ -z "$GUILD_ID" ]; then
    log "DISCORD_TOKEN oder GUILD_ID fehlt in .env, ueberspringe Check."
    exit 0
fi

# 1) Laeuft der PM2-Prozess ueberhaupt? (JSON-Parsing per Python statt
# fragilem grep/sed auf pm2's Ausgabe - ./venv/bin/python3 existiert immer,
# da der Bot selbst darueber laeuft.)
status="$(pm2 jlist 2>/dev/null | ./venv/bin/python3 -c '
import json, sys
try:
    procs = json.load(sys.stdin)
except Exception:
    print("unknown")
    sys.exit()
for p in procs:
    if p.get("name") == "follower-bot":
        print(p.get("pm2_env", {}).get("status", "unknown"))
        sys.exit()
print("missing")
' 2>/dev/null)"

if [ "$status" != "online" ]; then
    restart_bot "PM2-Status ist '${status:-unbekannt}' statt 'online'"
    exit 0
fi

# 2) Sind bei Discord ueberhaupt Slash-Commands fuer die Guild registriert?
# Direkter Check gegen die Discord-API statt nur zu vermuten, dass ein
# laufender Prozess auch funktionierende Commands hat.
check_result="$(curl -fsS --max-time 10 -H "Authorization: Bot ${TOKEN}" \
    https://discord.com/api/v10/oauth2/applications/@me 2>/dev/null \
    | ./venv/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data["id"])
except Exception:
    print("")
')"

if [ -z "$check_result" ]; then
    log "Konnte Application-ID nicht von der Discord-API abrufen (Netzwerk/Token-Problem?), ueberspringe Command-Check."
    exit 0
fi
app_id="$check_result"

command_count="$(curl -fsS --max-time 10 -H "Authorization: Bot ${TOKEN}" \
    "https://discord.com/api/v10/applications/${app_id}/guilds/${GUILD_ID}/commands" 2>/dev/null \
    | ./venv/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(len(data) if isinstance(data, list) else -1)
except Exception:
    print(-1)
')"

if [ "$command_count" = "-1" ] || [ -z "$command_count" ]; then
    log "Konnte registrierte Commands nicht abrufen (API-Fehler/Rate-Limit), ueberspringe diesmal."
    exit 0
fi

if [ "$command_count" -eq 0 ]; then
    restart_bot "0 Slash-Commands bei Discord fuer die Guild registriert"
else
    log "OK: PM2 online, ${command_count} Slash-Command(s) bei Discord registriert."
fi

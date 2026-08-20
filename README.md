# Follower-Stats Discord-Bot

Zeigt Instagram- und TikTok-Follower live auf Discord-Channels an. Jede
Plattform bekommt einen eigenen Channel, dessen Name periodisch aktualisiert
wird (z. B. `📸 Instagram: 12.345`).

Wichtig: Die konfigurierten Channels muessen **Voice- oder Stage-Channels**
sein. Discord erzwingt bei Text-Channel-Namen Kleinschreibung und ersetzt
Leerzeichen durch Bindestriche - fuer eine lesbare Anzeige mit Emoji,
Doppelpunkt und Tausenderpunkt funktioniert das nur bei Voice/Stage-Channels.

Jeder erfolgreiche Abruf wird zusaetzlich in MySQL dokumentiert - pro Plattform
eine eigene Tabelle (`instagram_history`, `tiktok_history`), wird beim ersten
Start automatisch angelegt. Ueber den
Slash-Command `/statistik social` zeigt der Bot die aktuellen Zahlen sowie die
Entwicklung der letzten 24 Stunden und 7 Tage an. `/syncfollower` stoesst
einen sofortigen Abruf aller Plattformen an, statt auf das naechste
`UPDATE_INTERVAL` zu warten (Berechtigung "Manage Channels" noetig, max. 1x
pro 5 Minuten).

## Dateien

| Datei | Zweck |
|---|---|
| `bot.py` | Hauptprogramm: startet den Bot, aktualisiert die Channels, stellt `/statistik social` bereit |
| `config.py` | Liest alle Einstellungen aus `.env` |
| `platforms.py` | Abruf der Follower-/Abonnentenzahlen je Plattform |
| `db.py` | Speichert jeden Abruf in MySQL und liest den Verlauf fuer `/statistik social` |
| `schema.sql` | Optionales manuelles SQL-Setup (identisch zu dem, was `db.py` automatisch anlegt) |
| `ecosystem.config.js` | PM2-Konfiguration fuer den Dauerbetrieb auf dem Server (inkl. taeglichem Neustart) |
| `deploy.sh` | Holt per Cron periodisch neue Commits und startet den Bot bei Aenderungen neu |
| `watchdog.sh` | Prueft per Cron, ob die Slash-Commands bei Discord noch registriert sind, und startet bei Ausfall neu |

Jede Plattform ist unabhaengig: Ist ihre Channel-ID (oder ein anderer
Pflichtwert) nicht gesetzt, wird sie automatisch uebersprungen.

## Setup (Linux-Server)

Der Bot ist fuer den Dauerbetrieb auf einem Linux-Server mit PM2 gedacht.

### 1. Projekt + Abhaengigkeiten

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

`ecosystem.config.js` startet den Bot ueber `./venv/bin/python3` - Systemweite
Python-Pakete werden also nicht angefasst.

### 2. Discord-Bot anlegen

1. Anwendung + Bot in der [Discord Developer Console](https://discord.com/developers/applications) erstellen.
2. Berechtigungen **Manage Channels** (Channel-Umbenennung) und **applications.commands**
   (Slash-Commands) geben und den Bot per Invite-Link auf den Server einladen.
3. Discord-Entwicklermodus aktivieren (Einstellungen → Erweitert), um Server-/Channel-IDs zu kopieren.
4. Fuer jede gewuenschte Plattform einen Voice- oder Stage-Channel anlegen und dessen ID notieren.

### 3. `.env` anlegen

`.env.example` nach `.env` kopieren und ausfuellen:

```
DISCORD_TOKEN=...
GUILD_ID=...
```

### 4. Plattformen konfigurieren

Weder Instagram noch TikTok brauchen einen API-Key, Developer-Account oder
OAuth-Setup - beide lesen die oeffentliche Profilseite aus. Einfach Channel-ID
+ Benutzername in der `.env` eintragen. Das macht die Einrichtung einfach,
heisst aber auch: beide Quellen koennen brechen, wenn die jeweilige Plattform
ihr Seitenformat aendert oder den Zugriff blockiert. In dem Fall bitte melden,
damit `platforms.py` angepasst wird.

**Instagram-Login-Wand bei Server-/Cloud-IPs:** Instagram stuft viele
Rechenzentrums-IP-Bereiche als verdaechtig ein und leitet anonyme Anfragen auf
die Login-Seite um (im Bot-Log sichtbar als "auf Login-Seite umgeleitet").
Falls das passiert, `INSTAGRAM_COOKIE` setzen:
1. Im Browser normal bei instagram.com einloggen.
2. DevTools oeffnen (F12) → Tab "Netzwerk"/"Network" → Seite neu laden.
3. Eine Anfrage an `www.instagram.com` anklicken → bei den Request-Headers
   den kompletten Wert von `Cookie:` kopieren (die ganze Zeile, nicht nur
   `sessionid`).
4. Als `INSTAGRAM_COOKIE` in die `.env` einfuegen.

Der Cookie laeuft nach einiger Zeit ab (typischerweise mehrere Wochen) und
muss dann erneuert werden - man merkt es daran, dass die Login-Umleitung
wieder auftaucht.

### 5. Starten

```bash
pm2 start ecosystem.config.js
pm2 save
```

### 6. Automatische Updates per Cron (deploy.sh)

`deploy.sh` holt neue Commits vom `main`-Branch und startet den Bot per PM2
nur dann neu, wenn es tatsaechlich Aenderungen gab (fast-forward-only, damit
ein Server mit lokalen Handaenderungen nicht stillschweigend ueberschrieben
wird - der Lauf bricht in dem Fall einfach ab und loggt das nach `logs/deploy.log`).

```bash
chmod +x deploy.sh   # nur beim allerersten Mal noetig
crontab -e
```

Zeile eintragen (prueft alle 5 Minuten auf Updates, Pfad anpassen):

```
*/5 * * * * /pfad/zu/Discordbot_Follower/deploy.sh
```

Intervall nach Bedarf aendern (z. B. `*/15` fuer alle 15 Minuten). Logs dazu:

```bash
tail -f logs/deploy.log
```

### 7. Automatischer Neustart bei Ausfall (watchdog.sh)

Zusaetzlich zu `deploy.sh` (startet nur bei neuen Commits neu) sorgen zwei
weitere Sicherheitsnetze dafuer, dass der Bot nicht dauerhaft offline bleibt:

- **Taeglicher Neustart um Mitternacht**: `ecosystem.config.js` setzt
  `cron_restart: '0 0 * * *'` - PM2 startet den Bot dafuer automatisch selbst
  neu, ohne zusaetzlichen Cronjob (Serverzeitzone).
- **`watchdog.sh`**: fragt periodisch direkt bei der Discord-API nach, ob die
  Slash-Commands fuer die Guild ueberhaupt noch registriert sind (nicht nur,
  ob der Prozess laeuft) und startet per `pm2 restart` neu, falls nicht.
  Deckt damit auch Faelle ab, in denen der Prozess zwar laeuft, der
  Command-Sync beim Start aber dauerhaft fehlgeschlagen ist.

```bash
chmod +x watchdog.sh   # nur beim allerersten Mal noetig
crontab -e
```

Zeile eintragen (Pfad anpassen, alle 5 Minuten reicht):

```
*/5 * * * * /pfad/zu/Discordbot_Follower/watchdog.sh
```

Meldet - wie `deploy.sh` - ausgeloeste Neustarts zusaetzlich in
`DISCORD_LOG_WEBHOOK_URL`, falls konfiguriert. Logs dazu:

```bash
tail -f logs/watchdog.log
```

Ausserdem gibt PM2 nach wiederholten Abstuerzen nicht mehr dauerhaft auf:
`max_restarts` ist grosszuegig hoch gesetzt und `exp_backoff_restart_delay`
sorgt dafuer, dass PM2 bei einem kurzen Crash-Loop (z. B. DB beim Start kurz
nicht erreichbar) die Wartezeit zwischen Versuchen automatisch erhoeht statt
sofort aufzugeben.

Optional meldet `deploy.sh` gefundene Updates zusaetzlich in den Discord-Log-Channel
(siehe unten) - dafuer `DISCORD_LOG_WEBHOOK_URL` in der `.env` setzen:
1. Im Ziel-Text-Channel (derselbe wie `CHANNEL_ID_LOG`): Kanal-Einstellungen
   → Integrationen → Webhooks → Neuer Webhook.
2. Die "Webhook-URL kopieren" und als `DISCORD_LOG_WEBHOOK_URL` in die `.env` eintragen.

Das ist noetig, weil `deploy.sh` als eigenstaendiges Cron-Skript laeuft, ohne
Zugriff auf den Bot-Prozess/dessen Discord-Verbindung - ein Webhook ist der
einfachste Weg, trotzdem in denselben Channel zu posten.

## Konfigurationsuebersicht

| Variable | Beschreibung |
|---|---|
| `DISCORD_TOKEN` | Bot-Token (sensibel) |
| `GUILD_ID` | Server-ID |
| `UPDATE_INTERVAL` | Sekunden zwischen Updates (Default 14400 = 4 Std.) |
| `LOG_LEVEL` | z. B. `INFO` oder `DEBUG` |
| `CHANNEL_ID_LOG` | Text-Channel fuer allgemeine Bot-Logs (Start, Sync, Warnungen/Fehler - 0 = deaktiviert) |
| `DISCORD_LOG_WEBHOOK_URL` | Webhook desselben Channels, genutzt von `deploy.sh` |
| `CHANNEL_ID_LOG_FOLLOWER` | Optionaler separater Channel nur fuer Follower-Update-Logs (0 = `CHANNEL_ID_LOG` mitbenutzen) |
| `CHANNEL_ID_INSTAGRAM`, `INSTAGRAM_USERNAME`, `INSTAGRAM_COOKIE` | Instagram-Channel + Benutzername + optionaler Login-Cookie (siehe oben) |
| `CHANNEL_ID_TIKTOK`, `TIKTOK_USERNAME` | TikTok-Channel + Benutzername |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Zugangsdaten der MySQL-Datenbank fuer die Statistik-Historie |

## Discord-Log-Channel

Der Bot spiegelt seine Logs live nach Discord - aufgeteilt in zwei Kategorien:

- **Allgemein** (`CHANNEL_ID_LOG`): Start-Meldung nach jedem (Neu-)Start,
  Slash-Command-Sync, sowie alles andere, was nicht zu einem Follower-Update
  gehoert.
- **Follower-Updates** (`CHANNEL_ID_LOG_FOLLOWER`, faellt auf `CHANNEL_ID_LOG`
  zurueck wenn nicht gesetzt): Start/Ende jedes Update-Zyklus, Verbindungsaufbau
  und Ergebnis je Plattform ("Instagram: verbinde zu ...", "Instagram: 15
  Follower ermittelt"), jede Channel-Umbenennung (oder "keine Aenderung
  noetig") sowie fehlgeschlagene Abrufe. Zwei Channels konfigurieren, wenn man
  diese Meldungen getrennt von den allgemeinen Bot-Logs haben moechte.

Beide folgen `LOG_LEVEL`: `INFO` zeigt die genannten Meilensteine
(Verbindungsaufbau, Ergebnis, Zyklus-Grenzen), `DEBUG` zusaetzlich technische
Details wie einzelne Fallback-Versuche oder HTTP-Statuscodes - sowohl in
Discord als auch in `pm2 logs follower-bot`.

Eintraege, die innerhalb derselben Sekunde anfallen (z. B. der Burst aus
Start-Logs + erstem Update-Zyklus), werden zu einer Nachricht gebuendelt statt
als viele Einzel-Sends verschickt - sonst wuerde Discords Rate-Limit
(~5 Nachrichten/5s pro Channel) einen Teil der Meldungen verzoegert
nachreichen und das Log wirkt "schleppend".

`deploy.sh` (`DISCORD_LOG_WEBHOOK_URL`, siehe oben) meldet zusaetzlich in den
allgemeinen Channel, wenn ein automatisches Update eingespielt wurde - oder
wenn das fehlschlaegt.

## Datenbank: Tabellen entstehen automatisch

Es ist keine manuelle SQL-Ausfuehrung noetig. `db.py` legt beim allerersten
Verbindungsaufbau selbst eine Tabelle pro Plattform an (`CREATE TABLE IF NOT
EXISTS`): `instagram_history`, `tiktok_history` - jeweils mit den Spalten
`id`, `count`, `recorded_at`. Vorausgesetzt ist nur, dass die Datenbank selbst
(`DB_NAME`, hier `followerDB`) bereits existiert und der konfigurierte
Benutzer Schreib-/Create-Table-Rechte darauf hat.

Wer die Tabellen trotzdem vorab manuell anlegen moechte (z. B. um Rechte
unabhaengig vom Bot zu testen), findet die identischen `CREATE TABLE`-
Statements in `schema.sql`:

```bash
mysql -h 127.0.0.1 -P 3306 -u viennastaterpfollower -p followerDB < schema.sql
```

Verbindungsfehler wie `(2059, "Authentication plugin '...' not configured")`
sind kein Tabellen-Problem, sondern ein Auth-Plugin-Mismatch zwischen dem
MySQL-User und dem Python-Client - siehe naechster Abschnitt.

### Troubleshooting: Auth-Plugin-Fehler (Error 2059)

`aiomysql`/PyMySQL unterstuetzt u. a. `mysql_native_password` und
`caching_sha2_password`, aber keine exotischeren Plugins wie `auth_gssapi_client`.
Pruefen, welches Plugin der konfigurierte User tatsaechlich nutzt:

```sql
SELECT user, host, plugin FROM mysql.user WHERE user='<DB_USER>';
```

Falls dort etwas anderes als `mysql_native_password`/`caching_sha2_password`
steht, per `ALTER USER` korrigieren (Host-Teil an das Ergebnis oben anpassen):

```sql
ALTER USER '<DB_USER>'@'127.0.0.1' IDENTIFIED WITH mysql_native_password BY '<DB_PASSWORD>';
FLUSH PRIVILEGES;
```

## Hinweise

- Instagram und TikTok werden beide ueber inoffizielle/oeffentliche Wege
  ausgelesen (kein API-Key/OAuth noetig) - das macht das Setup einfach, kann
  aber jederzeit brechen, wenn eine Plattform ihr Format aendert oder den
  Zugriff blockiert.
- Discord limitiert Namensaenderungen pro Channel auf 2 pro 10 Minuten - bei
  einem `UPDATE_INTERVAL` von mehreren Stunden (Default) ist das kein Thema.
- Tokens (`DISCORD_TOKEN`, `DB_PASSWORD`, `DISCORD_LOG_WEBHOOK_URL`) niemals
  oeffentlich teilen oder committen.

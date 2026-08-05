# Follower-Stats Discord-Bot

Zeigt Instagram-, TikTok-, YouTube- und Twitch-Follower/Abonnenten live auf
Discord-Channels an. Jede Plattform bekommt einen eigenen Channel, dessen
Name periodisch aktualisiert wird (z. B. `📸 Instagram: 12.345`).

Wichtig: Die konfigurierten Channels muessen **Voice- oder Stage-Channels**
sein. Discord erzwingt bei Text-Channel-Namen Kleinschreibung und ersetzt
Leerzeichen durch Bindestriche - fuer eine lesbare Anzeige mit Emoji,
Doppelpunkt und Tausenderpunkt funktioniert das nur bei Voice/Stage-Channels.

Jeder erfolgreiche Abruf wird zusaetzlich in MySQL dokumentiert - pro Plattform
eine eigene Tabelle (`instagram_history`, `tiktok_history`, `youtube_history`,
`twitch_history`), wird beim ersten Start automatisch angelegt. Ueber den
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
| `ecosystem.config.js` | PM2-Konfiguration fuer den Dauerbetrieb auf dem Server |
| `deploy.sh` | Holt per Cron periodisch neue Commits und startet den Bot bei Aenderungen neu |

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

Keine der vier Plattformen braucht einen API-Key, Developer-Account oder
OAuth-Setup - ueberall genuegt Channel-ID + Benutzername/Login in der `.env`.
Das macht die Einrichtung einfach, heisst aber auch: alle vier Quellen lesen
oeffentliche Seiten/inoffizielle Endpunkte aus und koennen brechen, wenn die
jeweilige Plattform ihr Format aendert oder den Zugriff blockiert. In dem
Fall bitte melden, damit `platforms.py` angepasst wird.

**Instagram / TikTok** — liest die oeffentliche Profilseite aus.

**YouTube** — liest ueber YouTubes eigenes internes "InnerTube"-Browse-API
(dieselbe Schnittstelle, die die Web-Oberflaeche selbst nutzt), kein
API-Key noetig. `YOUTUBE_CHANNEL_ID` (beginnt meist mit `UC...`, zu finden
ueber die Kanal-URL oder https://commentpicker.com/youtube-channel-id.php)
in die `.env` eintragen. Hat der Kanal unter seinen Einstellungen die Option
"Abonnentenzahl nicht anzeigen" aktiviert, liefert weder dieser Weg noch die
offizielle Data API einen Wert - das ist eine Kanal-Privatsphaere-Einstellung,
keine Einschraenkung des Bots.

**Twitch** — liest ueber [decapi.me](https://decapi.me/) (ein oeffentlicher,
inoffizieller Wrapper um die Twitch-API), kein Developer-App/OAuth-Setup
noetig. Einfach `TWITCH_BROADCASTER_LOGIN` (Twitch-Login-Name, ohne @) in
die `.env` eintragen.

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
| `CHANNEL_ID_INSTAGRAM`, `INSTAGRAM_USERNAME` | Instagram-Channel + Benutzername |
| `CHANNEL_ID_TIKTOK`, `TIKTOK_USERNAME` | TikTok-Channel + Benutzername |
| `CHANNEL_ID_YOUTUBE`, `YOUTUBE_CHANNEL_ID` | YouTube-Channel + Kanal-ID |
| `CHANNEL_ID_TWITCH`, `TWITCH_BROADCASTER_LOGIN` | Twitch-Channel + Login-Name |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Zugangsdaten der MySQL-Datenbank fuer die Statistik-Historie |

## Discord-Log-Channel

Der Bot spiegelt seine Logs live nach Discord - aufgeteilt in zwei Kategorien:

- **Allgemein** (`CHANNEL_ID_LOG`): Start-Meldung nach jedem (Neu-)Start,
  Slash-Command-Sync, sowie alles andere, was nicht zu einem Follower-Update
  gehoert.
- **Follower-Updates** (`CHANNEL_ID_LOG_FOLLOWER`, faellt auf `CHANNEL_ID_LOG`
  zurueck wenn nicht gesetzt): jede erfolgreiche Channel-Umbenennung sowie
  fehlgeschlagene Follower-Abrufe pro Plattform. Zwei Channels konfigurieren,
  wenn man diese Meldungen getrennt von den allgemeinen Bot-Logs haben moechte.

Beide folgen `LOG_LEVEL` - bei `LOG_LEVEL=DEBUG` landen z. B. auch Details zu
einzelnen Abrufversuchen (etwa "Instagram API Status 429, nutze Fallback") in
Discord, nicht nur in `pm2 logs follower-bot`. Fuer den Normalbetrieb reicht
`LOG_LEVEL=INFO`.

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
EXISTS`): `instagram_history`, `tiktok_history`, `youtube_history`,
`twitch_history` - jeweils mit den Spalten `id`, `count`, `recorded_at`.
Vorausgesetzt ist nur, dass die Datenbank selbst (`DB_NAME`, hier
`followerDB`) bereits existiert und der konfigurierte Benutzer Schreib-/
Create-Table-Rechte darauf hat.

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

- Instagram, TikTok, YouTube und Twitch werden alle ueber inoffizielle/
  oeffentliche Wege ausgelesen (kein API-Key/OAuth noetig) - das macht das
  Setup einfach, kann aber jederzeit brechen, wenn eine Plattform ihr Format
  aendert oder den Zugriff blockiert.
- Discord limitiert Namensaenderungen pro Channel auf 2 pro 10 Minuten - bei
  einem `UPDATE_INTERVAL` von mehreren Stunden (Default) ist das kein Thema.
- Tokens (`DISCORD_TOKEN`, `DB_PASSWORD`, `DISCORD_LOG_WEBHOOK_URL`) niemals
  oeffentlich teilen oder committen.

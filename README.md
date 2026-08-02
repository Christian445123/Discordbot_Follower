# Follower-Stats Discord-Bot

Zeigt Instagram-, TikTok-, YouTube- und Twitch-Follower/Abonnenten live auf
Discord-Channels an. Jede Plattform bekommt einen eigenen Channel, dessen
Name periodisch aktualisiert wird (z. B. `📸 Instagram: 12.345`).

Wichtig: Die konfigurierten Channels muessen **Voice- oder Stage-Channels**
sein. Discord erzwingt bei Text-Channel-Namen Kleinschreibung und ersetzt
Leerzeichen durch Bindestriche - fuer eine lesbare Anzeige mit Emoji,
Doppelpunkt und Tausenderpunkt funktioniert das nur bei Voice/Stage-Channels.

Jeder erfolgreiche Abruf wird zusaetzlich in einer lokalen SQLite-Datenbank
(`follower_stats.db`) dokumentiert. Ueber den Slash-Command `/statistik social`
zeigt der Bot die aktuellen Zahlen sowie die Entwicklung der letzten 24 Stunden
und 7 Tage an.

## Dateien

| Datei | Zweck |
|---|---|
| `bot.py` | Hauptprogramm: startet den Bot, aktualisiert die Channels, stellt `/statistik social` bereit |
| `config.py` | Liest alle Einstellungen aus `.env` |
| `platforms.py` | Abruf der Follower-/Abonnentenzahlen je Plattform |
| `db.py` | Speichert jeden Abruf in `follower_stats.db` (SQLite) und liest den Verlauf fuer `/statistik social` |
| `twitch_auth.py` | Einmaliges Setup-Skript fuer den Twitch-Login |
| `ecosystem.config.js` | PM2-Konfiguration fuer den Dauerbetrieb auf dem Server |

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

**Instagram / TikTok** — kein offizielles kostenloses API vorhanden, daher
liest der Bot die oeffentliche Profilseite aus (Scraping). Einfach
Channel-ID + Benutzername in der `.env` eintragen. Kann bei Layout-Aenderungen
der Plattformen brechen; in dem Fall bitte melden, damit `platforms.py`
angepasst wird.

**YouTube** — offizielle YouTube Data API v3:
1. Projekt in der [Google Cloud Console](https://console.cloud.google.com/) anlegen.
2. "YouTube Data API v3" aktivieren (APIs & Services → Library).
3. Unter "Credentials" einen API-Key erstellen.
4. `YOUTUBE_API_KEY` und `YOUTUBE_CHANNEL_ID` (die Channel-ID beginnt meist mit `UC...`, zu finden ueber die Kanal-URL oder https://commentpicker.com/youtube-channel-id.php) in die `.env` eintragen.

**Twitch** — Followerzahlen sind seit 2023 nur mit einem User-Access-Token
des Broadcasters abrufbar (Scope `moderator:read:followers`), ein einfacher
App-Token reicht nicht mehr. Einmaliges Setup:
1. App in der [Twitch Developer Console](https://dev.twitch.tv/console/apps) anlegen.
2. Als OAuth Redirect URL exakt `http://localhost:17563/callback` eintragen.
3. `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` und `TWITCH_BROADCASTER_LOGIN` in die `.env` eintragen.
4. `python twitch_auth.py` ausfuehren, im Browser mit dem Streamer-Account einloggen.
5. Den ausgegebenen `TWITCH_REFRESH_TOKEN` in die `.env` eintragen.

Der Bot erneuert den Access-Token danach automatisch - dieser Schritt ist nur einmalig noetig.

### 5. Starten

```
python bot.py
```

## Konfigurationsuebersicht

| Variable | Beschreibung |
|---|---|
| `DISCORD_TOKEN` | Bot-Token (sensibel) |
| `GUILD_ID` | Server-ID |
| `UPDATE_INTERVAL` | Sekunden zwischen Updates (Default 14400 = 4 Std.) |
| `LOG_LEVEL` | z. B. `INFO` oder `DEBUG` |
| `CHANNEL_ID_INSTAGRAM`, `INSTAGRAM_USERNAME` | Instagram-Channel + Benutzername |
| `CHANNEL_ID_TIKTOK`, `TIKTOK_USERNAME` | TikTok-Channel + Benutzername |
| `CHANNEL_ID_YOUTUBE`, `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID` | YouTube-Channel + API-Key + Kanal-ID |
| `CHANNEL_ID_TWITCH`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BROADCASTER_LOGIN`, `TWITCH_REFRESH_TOKEN` | Twitch-Channel + App-Zugangsdaten |

## Hinweise

- Scraping-basierte Quellen (Instagram, TikTok) sind inoffiziell und koennen
  jederzeit durch Layout-Aenderungen der Plattform brechen.
- Discord limitiert Namensaenderungen pro Channel auf 2 pro 10 Minuten - bei
  einem `UPDATE_INTERVAL` von mehreren Stunden (Default) ist das kein Thema.
- Tokens (`DISCORD_TOKEN`, `TWITCH_CLIENT_SECRET`, `TWITCH_REFRESH_TOKEN`,
  `YOUTUBE_API_KEY`) niemals oeffentlich teilen oder committen.

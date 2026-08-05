#!/usr/bin/env python3
"""Konfiguration: liest alle Einstellungen aus Umgebungsvariablen (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = _int("GUILD_ID")
UPDATE_INTERVAL = _int("UPDATE_INTERVAL", 14400)  # Sekunden, Default 4 Stunden
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Text-Channel fuer allgemeine Bot-Logs (Start, Sync, Warnungen/Fehler - 0 = deaktiviert)
CHANNEL_ID_LOG = _int("CHANNEL_ID_LOG")
# Text-Channel speziell fuer Follower-Update-Logs (0 = CHANNEL_ID_LOG mitbenutzen)
CHANNEL_ID_LOG_FOLLOWER = _int("CHANNEL_ID_LOG_FOLLOWER")


@dataclass
class InstagramConfig:
    channel_id: int
    username: str
    cookie: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.channel_id and self.username)


@dataclass
class TikTokConfig:
    channel_id: int
    username: str

    @property
    def enabled(self) -> bool:
        return bool(self.channel_id and self.username)


INSTAGRAM = InstagramConfig(
    channel_id=_int("CHANNEL_ID_INSTAGRAM"),
    username=os.getenv("INSTAGRAM_USERNAME", "").lstrip("@").strip(),
    cookie=os.getenv("INSTAGRAM_COOKIE", "").strip(),
)

TIKTOK = TikTokConfig(
    channel_id=_int("CHANNEL_ID_TIKTOK"),
    username=os.getenv("TIKTOK_USERNAME", "").lstrip("@").strip(),
)

# ---------------- Datenbank (MySQL) ----------------
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

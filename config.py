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


@dataclass
class InstagramConfig:
    channel_id: int
    username: str

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


@dataclass
class YouTubeConfig:
    channel_id: int
    api_key: str
    youtube_channel_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.channel_id and self.api_key and self.youtube_channel_id)


@dataclass
class TwitchConfig:
    channel_id: int
    client_id: str
    client_secret: str
    refresh_token: str
    broadcaster_login: str

    @property
    def enabled(self) -> bool:
        return bool(
            self.channel_id
            and self.client_id
            and self.client_secret
            and self.refresh_token
            and self.broadcaster_login
        )


INSTAGRAM = InstagramConfig(
    channel_id=_int("CHANNEL_ID_INSTAGRAM"),
    username=os.getenv("INSTAGRAM_USERNAME", "").lstrip("@").strip(),
)

TIKTOK = TikTokConfig(
    channel_id=_int("CHANNEL_ID_TIKTOK"),
    username=os.getenv("TIKTOK_USERNAME", "").lstrip("@").strip(),
)

YOUTUBE = YouTubeConfig(
    channel_id=_int("CHANNEL_ID_YOUTUBE"),
    api_key=os.getenv("YOUTUBE_API_KEY", "").strip(),
    youtube_channel_id=os.getenv("YOUTUBE_CHANNEL_ID", "").strip(),
)

TWITCH = TwitchConfig(
    channel_id=_int("CHANNEL_ID_TWITCH"),
    client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
    client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
    refresh_token=os.getenv("TWITCH_REFRESH_TOKEN", "").strip(),
    broadcaster_login=os.getenv("TWITCH_BROADCASTER_LOGIN", "").lstrip("@").strip(),
)

TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:17563/callback")

# ---------------- Datenbank (MySQL) ----------------
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

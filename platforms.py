#!/usr/bin/env python3
"""Abruf der Followerzahlen/Abonnenten je Plattform.

Jede fetch_*-Funktion gibt die aktuelle Zahl als int zurueck oder wirft eine
Exception, wenn der Abruf fehlgeschlagen ist. Die Aufrufer (bot.py) fangen
Fehler pro Plattform ab und ueberspringen den jeweiligen Update-Zyklus.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger("follower-bot.platforms")

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _parse_abbreviated_count(raw: str) -> int:
    """Parst Zahlen wie '12,345', '861K' oder '1.2M' zu einem int.
    Ab ca. 1000 zeigt Instagram im Meta-Tag nur noch gerundete K/M/B-Werte an -
    das ist der bestmoegliche Wert, den wir ohne eingeloggte Session bekommen."""
    raw = raw.strip().replace(",", "")
    match = re.match(r"^([\d.]+)\s*([KMB]?)$", raw, re.IGNORECASE)
    if not match:
        raise ValueError(f"Unbekanntes Zahlenformat: {raw!r}")
    number = float(match.group(1))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
    return int(number * multiplier)


# ---------------- Instagram ----------------
async def fetch_instagram_followers(session: aiohttp.ClientSession, username: str) -> int:
    """Instagram hat keine oeffentliche Follower-API. Erster Versuch liefert eine
    exakte Zahl ueber die interne Web-Profil-API; wird der Request geblockt
    (z. B. HTTP 429), lesen wir ersatzweise das og:description-Meta-Tag der
    Profilseite - dort zeigt Instagram ab ca. 1000 Followern nur noch gerundete
    Werte (z. B. '12K')."""
    api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {
        "User-Agent": MOBILE_UA,
        "X-IG-App-ID": "936619743392459",
        "Accept": "application/json",
    }
    try:
        async with session.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                return int(data["data"]["user"]["edge_followed_by"]["count"])
            logger.debug("Instagram web_profile_info Status %s, nutze HTML-Fallback", resp.status)
    except Exception as e:
        logger.debug("Instagram web_profile_info fehlgeschlagen: %s", e)

    # Fallback: Followerzahl aus dem og:description Meta-Tag der Profilseite lesen
    profile_url = f"https://www.instagram.com/{username}/"
    async with session.get(profile_url, headers={"User-Agent": MOBILE_UA}, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()
    match = re.search(r'content="([\d.,]+\s?[KMB]?) Followers', html, re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Instagram: Followerzahl fuer '{username}' nicht gefunden")
    return _parse_abbreviated_count(match.group(1))


# ---------------- TikTok ----------------
async def fetch_tiktok_followers(session: aiohttp.ClientSession, username: str) -> int:
    """TikTok hat keine oeffentliche Follower-API, daher Auslesen der Profilseite."""
    url = f"https://www.tiktok.com/@{username}"
    async with session.get(url, headers={"User-Agent": DESKTOP_UA}, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()

    match = re.search(r'"followerCount":(\d+)', html)
    if not match:
        raise RuntimeError(f"TikTok: Followerzahl fuer '{username}' nicht gefunden")
    return int(match.group(1))


# ---------------- YouTube ----------------
async def fetch_youtube_subscribers(session: aiohttp.ClientSession, api_key: str, channel_id: str) -> int:
    """Offizielle YouTube Data API v3 (kostenloser API-Key genuegt)."""
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {"part": "statistics", "id": channel_id, "key": api_key}
    async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        data = await resp.json()

    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"YouTube: Channel '{channel_id}' nicht gefunden")
    return int(items[0]["statistics"]["subscriberCount"])


# ---------------- Twitch ----------------
class TwitchClient:
    """Haelt den Twitch-Access-Token aktuell (per Refresh-Token) und fragt die
    offizielle Helix-API nach der Follower-Zahl.

    Twitch verlangt seit 2023 fuer Followerzahlen zwingend einen User-Access-Token
    des Broadcasters (Scope moderator:read:followers) - ein einfacher App-Token
    reicht nicht mehr. Der Refresh-Token wird einmalig mit twitch_auth.py erzeugt.
    """

    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    API_BASE = "https://api.twitch.tv/helix"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: Optional[str] = None
        self._broadcaster_id: Optional[str] = None

    async def _refresh_access_token(self, session: aiohttp.ClientSession) -> None:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with session.post(self.TOKEN_URL, data=data, timeout=REQUEST_TIMEOUT) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        self._access_token = payload["access_token"]
        # Twitch rotiert den Refresh-Token bei jedem Refresh
        self.refresh_token = payload.get("refresh_token", self.refresh_token)
        logger.debug("Twitch Access-Token erneuert")

    def _headers(self) -> dict:
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._access_token}"}

    async def _get_broadcaster_id(self, session: aiohttp.ClientSession, login: str) -> str:
        if self._broadcaster_id:
            return self._broadcaster_id
        async with session.get(
            f"{self.API_BASE}/users", params={"login": login}, headers=self._headers(), timeout=REQUEST_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        entries = payload.get("data") or []
        if not entries:
            raise RuntimeError(f"Twitch: Benutzer '{login}' nicht gefunden")
        self._broadcaster_id = entries[0]["id"]
        return self._broadcaster_id

    async def fetch_followers(self, session: aiohttp.ClientSession, broadcaster_login: str) -> int:
        if not self._access_token:
            await self._refresh_access_token(session)

        broadcaster_id = await self._get_broadcaster_id(session, broadcaster_login)
        url = f"{self.API_BASE}/channels/followers"
        params = {"broadcaster_id": broadcaster_id}

        async with session.get(url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 401:
                # Access-Token abgelaufen -> einmal erneuern und erneut versuchen
                await self._refresh_access_token(session)
                async with session.get(
                    url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT
                ) as retry_resp:
                    retry_resp.raise_for_status()
                    payload = await retry_resp.json()
            else:
                resp.raise_for_status()
                payload = await resp.json()

        return int(payload["total"])

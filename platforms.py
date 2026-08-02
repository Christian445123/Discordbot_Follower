#!/usr/bin/env python3
"""Abruf der Followerzahlen/Abonnenten je Plattform.

Jede fetch_*-Funktion gibt die aktuelle Zahl als int zurueck oder wirft eine
Exception, wenn der Abruf fehlgeschlagen ist. Die Aufrufer (bot.py) fangen
Fehler pro Plattform ab und ueberspringen den jeweiligen Update-Zyklus.
"""
from __future__ import annotations

import logging
import re

import aiohttp

logger = logging.getLogger("follower-bot.updates.platforms")

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
# Oeffentlicher Web-Client-Schluessel, der in jeder YouTube-Seite eingebettet ist -
# kein Google-Account/API-Key noetig, jeder Browser nutzt denselben.
_YOUTUBE_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_YOUTUBE_BROWSE_URL = f"https://www.youtube.com/youtubei/v1/browse?key={_YOUTUBE_INNERTUBE_KEY}"


async def fetch_youtube_subscribers(session: aiohttp.ClientSession, channel_id: str) -> int:
    """Fragt YouTubes internes 'InnerTube'-Browse-API direkt ab (dieselbe API, die
    die Web-Oberflaeche selbst nutzt) - liefert JSON ohne Cookie-Consent-Wall und
    ohne API-Key-Setup.

    Hat der Kanal die Option 'Abonnentenzahl nicht anzeigen' aktiviert, enthaelt
    die Antwort keine Zahl - das betrifft dann auch die offizielle Data API."""
    payload = {
        "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00", "hl": "en", "gl": "US"}},
        "browseId": channel_id,
    }
    async with session.post(
        _YOUTUBE_BROWSE_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=REQUEST_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        text = await resp.text()

    match = re.search(r'"content":\s*"([\d.,]+\s?[KMB]?)\s*subscribers?"', text, re.IGNORECASE)
    if not match:
        raise RuntimeError(
            f"YouTube: Abonnentenzahl fuer Channel '{channel_id}' nicht gefunden "
            "(evtl. hat der Kanal die Abonnentenzahl ausgeblendet)"
        )
    return _parse_abbreviated_count(match.group(1))


# ---------------- Twitch ----------------
async def fetch_twitch_followers(session: aiohttp.ClientSession, broadcaster_login: str) -> int:
    """Nutzt decapi.me (inoffizieller, oeffentlicher Wrapper um die Twitch-API) -
    kein Developer-App/OAuth-Setup noetig. Kann wie jeder Drittanbieter-Dienst
    ausfallen oder rate-limiten."""
    url = f"https://decapi.me/twitch/followcount/{broadcaster_login}"
    async with session.get(url, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        text = (await resp.text()).strip()

    if not text.isdigit():
        raise RuntimeError(f"Twitch: unerwartete Antwort fuer '{broadcaster_login}': {text!r}")
    return int(text)

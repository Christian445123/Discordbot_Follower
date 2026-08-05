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
async def fetch_instagram_followers(session: aiohttp.ClientSession, username: str, cookie: str = "") -> int:
    """Instagram hat keine oeffentliche Follower-API. Erster Versuch liefert eine
    exakte Zahl ueber die interne Web-Profil-API; wird der Request geblockt
    (z. B. HTTP 429), lesen wir ersatzweise das og:description-Meta-Tag der
    Profilseite - dort zeigt Instagram ab ca. 1000 Followern nur noch gerundete
    Werte (z. B. '12K').

    Ohne angemeldete Session blockt Instagram anonyme Anfragen von manchen
    IPs (v. a. Rechenzentrums-/Cloud-IPs) komplett und leitet auf die
    Login-Seite um. Ist ein 'cookie' (kompletter Cookie-Header eines
    eingeloggten Browsers, siehe README) gesetzt, wird er mitgeschickt, um
    das zu vermeiden."""
    headers = {
        "User-Agent": MOBILE_UA,
        "X-IG-App-ID": "936619743392459",
        "Accept": "application/json",
    }
    if cookie:
        headers["Cookie"] = cookie

    logger.info("Instagram: verbinde zu i.instagram.com fuer '%s' ...", username)
    api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    try:
        async with session.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                count = int(data["data"]["user"]["edge_followed_by"]["count"])
                logger.info("Instagram: %s Follower ermittelt (API)", count)
                return count
            logger.debug("Instagram: API-Status %s, nutze HTML-Fallback", resp.status)
    except Exception as e:
        logger.debug("Instagram: API-Versuch fehlgeschlagen (%s), nutze HTML-Fallback", e)

    # Fallback: Followerzahl aus dem og:description Meta-Tag der Profilseite lesen
    logger.info("Instagram: verbinde zu www.instagram.com/%s/ (Fallback) ...", username)
    profile_url = f"https://www.instagram.com/{username}/"
    html_headers = {"User-Agent": MOBILE_UA}
    if cookie:
        html_headers["Cookie"] = cookie
    async with session.get(profile_url, headers=html_headers, timeout=REQUEST_TIMEOUT) as resp:
        if resp.url.path.startswith("/accounts/login"):
            raise RuntimeError(
                "Instagram: auf Login-Seite umgeleitet (IP geblockt) - INSTAGRAM_COOKIE in .env setzen"
            )
        resp.raise_for_status()
        html = await resp.text()
    logger.debug("Instagram: Profilseite geladen (%s Zeichen), lese Followerzahl aus", len(html))
    match = re.search(r'content="([\d.,]+\s?[KMB]?) Followers', html, re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Instagram: Followerzahl fuer '{username}' nicht gefunden")
    count = _parse_abbreviated_count(match.group(1))
    logger.info("Instagram: %s Follower ermittelt (HTML-Fallback)", count)
    return count


# ---------------- TikTok ----------------
async def fetch_tiktok_followers(session: aiohttp.ClientSession, username: str) -> int:
    """TikTok hat keine oeffentliche Follower-API, daher Auslesen der Profilseite."""
    logger.info("TikTok: verbinde zu www.tiktok.com/@%s ...", username)
    url = f"https://www.tiktok.com/@{username}"
    async with session.get(url, headers={"User-Agent": DESKTOP_UA}, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()

    logger.debug("TikTok: Profilseite geladen (%s Zeichen), lese Followerzahl aus", len(html))
    match = re.search(r'"followerCount":(\d+)', html)
    if not match:
        raise RuntimeError(f"TikTok: Followerzahl fuer '{username}' nicht gefunden")
    count = int(match.group(1))
    logger.info("TikTok: %s Follower ermittelt", count)
    return count

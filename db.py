#!/usr/bin/env python3
"""Speichert jeden erfolgreichen Follower-Abruf in MySQL, damit sich per
/statistik social ein Verlauf anzeigen laesst.

Pro Plattform gibt es eine eigene Tabelle (instagram_history, tiktok_history,
youtube_history, twitch_history), jeweils mit denselben Spalten (id, count,
recorded_at). Alle Tabellen werden beim ersten Verbindungsaufbau automatisch
angelegt - keine manuelle SQL-Ausfuehrung noetig.

Der Connection-Pool wird beim ersten Aufruf lazy aufgebaut (kein separater
Init-Schritt in bot.py noetig) und danach wiederverwendet.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiomysql

import config

# Plattform-Schluessel -> Tabellenname. Nur diese vier Werte werden je in
# SQL-Statements eingesetzt (kein User-Input), daher unproblematisch trotz
# String-Formatierung des Tabellennamens (Platzhalter erlauben in SQL nur
# Werte, keine Tabellen-/Spaltennamen).
_TABLES = {
    "instagram": "instagram_history",
    "tiktok": "tiktok_history",
    "youtube": "youtube_history",
    "twitch": "twitch_history",
}

_pool: Optional[aiomysql.Pool] = None
_pool_lock = asyncio.Lock()


def _table_for(platform: str) -> str:
    try:
        return _TABLES[platform]
    except KeyError:
        raise ValueError(f"Unbekannte Plattform: {platform!r}") from None


async def _get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            pool = await aiomysql.create_pool(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                db=config.DB_NAME,
                autocommit=True,
                minsize=1,
                maxsize=5,
            )
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for table in _TABLES.values():
                        await cur.execute(
                            f"""
                            CREATE TABLE IF NOT EXISTS {table} (
                                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                                count BIGINT NOT NULL,
                                recorded_at BIGINT NOT NULL,
                                KEY idx_recorded_at (recorded_at)
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                            """
                        )
            _pool = pool
    return _pool


async def record(platform: str, count: int) -> None:
    """Haengt einen neuen Messpunkt an den Verlauf einer Plattform an."""
    table = _table_for(platform)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {table} (count, recorded_at) VALUES (%s, %s)",
                (count, int(time.time())),
            )


async def latest(platform: str) -> Optional[int]:
    """Letzter bekannter Wert einer Plattform, oder None falls noch keine Daten."""
    table = _table_for(platform)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT count FROM {table} ORDER BY recorded_at DESC LIMIT 1")
            row = await cur.fetchone()
            return row[0] if row else None


async def count_at_or_before(platform: str, timestamp: int) -> Optional[int]:
    """Letzter bekannter Wert einer Plattform zu einem bestimmten Zeitpunkt
    (oder davor) - fuer die Berechnung von Aenderungen ueber Zeitraeume."""
    table = _table_for(platform)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT count FROM {table} WHERE recorded_at <= %s ORDER BY recorded_at DESC LIMIT 1",
                (timestamp,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

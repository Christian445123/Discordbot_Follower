#!/usr/bin/env python3
"""Speichert jeden erfolgreichen Follower-Abruf in MySQL, damit sich per
/statistik social ein Verlauf anzeigen laesst.

Der Connection-Pool wird beim ersten Aufruf lazy aufgebaut (kein separater
Init-Schritt in bot.py noetig) und danach wiederverwendet.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiomysql

import config

_pool: Optional[aiomysql.Pool] = None
_pool_lock = asyncio.Lock()


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
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS follower_history (
                            id BIGINT AUTO_INCREMENT PRIMARY KEY,
                            platform VARCHAR(32) NOT NULL,
                            count BIGINT NOT NULL,
                            recorded_at BIGINT NOT NULL,
                            KEY idx_platform_time (platform, recorded_at)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
            _pool = pool
    return _pool


async def record(platform: str, count: int) -> None:
    """Haengt einen neuen Messpunkt an den Verlauf einer Plattform an."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO follower_history (platform, count, recorded_at) VALUES (%s, %s, %s)",
                (platform, count, int(time.time())),
            )


async def latest(platform: str) -> Optional[int]:
    """Letzter bekannter Wert einer Plattform, oder None falls noch keine Daten."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count FROM follower_history WHERE platform = %s ORDER BY recorded_at DESC LIMIT 1",
                (platform,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def count_at_or_before(platform: str, timestamp: int) -> Optional[int]:
    """Letzter bekannter Wert einer Plattform zu einem bestimmten Zeitpunkt
    (oder davor) - fuer die Berechnung von Aenderungen ueber Zeitraeume."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count FROM follower_history WHERE platform = %s AND recorded_at <= %s "
                "ORDER BY recorded_at DESC LIMIT 1",
                (platform, timestamp),
            )
            row = await cur.fetchone()
            return row[0] if row else None

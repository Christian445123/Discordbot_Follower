#!/usr/bin/env python3
"""Discord-Bot: zeigt Instagram-, TikTok-, YouTube- und Twitch-Follower auf
Discord-Channels an, indem er deren Namen periodisch aktualisiert.

Jede Plattform ist unabhaengig konfigurierbar (eigene Channel-ID in .env) und
wird uebersprungen, wenn sie nicht vollstaendig konfiguriert ist. Discord.py
kuemmert sich intern bereits um Rate-Limit-Handling bei Channel-Edits, daher
braucht es dafuer keine eigene Retry-Logik.

Wichtig: Discord "saeubert" Namen von Text-Channels (nur Kleinbuchstaben,
Leerzeichen werden zu Bindestrichen). Fuer eine lesbare Anzeige wie
"Instagram: 12.345" muessen die konfigurierten Channels daher Voice- oder
Stage-Channels sein.
"""
from __future__ import annotations

import logging
import time

import aiohttp
import discord
from discord.ext import tasks, commands

import config
import db
import platforms

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("follower-bot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

twitch_client = (
    platforms.TwitchClient(config.TWITCH.client_id, config.TWITCH.client_secret, config.TWITCH.refresh_token)
    if config.TWITCH.enabled
    else None
)

# (db_key, Emoji, Anzeigename, Config) - eine Stelle fuer alle Plattform-Metadaten
PLATFORM_INFO = (
    ("instagram", "📸", "Instagram", config.INSTAGRAM),
    ("tiktok", "🎵", "TikTok", config.TIKTOK),
    ("youtube", "▶️", "YouTube", config.YOUTUBE),
    ("twitch", "🟣", "Twitch", config.TWITCH),
)


def format_channel_name(emoji: str, label: str, count: int) -> str:
    return f"{emoji} {label}: {count:,}".replace(",", ".")


async def rename_channel(guild: discord.Guild, channel_id: int, new_name: str) -> None:
    channel = guild.get_channel(channel_id)
    if channel is None:
        logger.warning("Channel %s nicht gefunden (falsche ID oder Bot nicht auf dem Server?)", channel_id)
        return
    if channel.name == new_name:
        return  # keine Aenderung noetig -> kein API-Call
    try:
        await channel.edit(name=new_name)
        logger.info("Channel %s aktualisiert -> %s", channel_id, new_name)
    except discord.Forbidden:
        logger.error("Fehlende 'Manage Channels'-Berechtigung fuer Channel %s", channel_id)
    except discord.HTTPException as e:
        logger.warning("Konnte Channel %s nicht umbenennen: %s", channel_id, e)


@tasks.loop(seconds=config.UPDATE_INTERVAL)
async def update_followers() -> None:
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        logger.warning("Guild %s nicht gefunden. Ist GUILD_ID korrekt und der Bot auf dem Server?", config.GUILD_ID)
        return

    async with aiohttp.ClientSession() as session:
        if config.INSTAGRAM.enabled:
            try:
                count = await platforms.fetch_instagram_followers(session, config.INSTAGRAM.username)
                await db.record("instagram", count)
                await rename_channel(guild, config.INSTAGRAM.channel_id, format_channel_name("📸", "Instagram", count))
            except Exception as e:
                logger.warning("Instagram-Update fehlgeschlagen: %s", e)

        if config.TIKTOK.enabled:
            try:
                count = await platforms.fetch_tiktok_followers(session, config.TIKTOK.username)
                await db.record("tiktok", count)
                await rename_channel(guild, config.TIKTOK.channel_id, format_channel_name("🎵", "TikTok", count))
            except Exception as e:
                logger.warning("TikTok-Update fehlgeschlagen: %s", e)

        if config.YOUTUBE.enabled:
            try:
                count = await platforms.fetch_youtube_subscribers(
                    session, config.YOUTUBE.api_key, config.YOUTUBE.youtube_channel_id
                )
                await db.record("youtube", count)
                await rename_channel(guild, config.YOUTUBE.channel_id, format_channel_name("▶️", "YouTube", count))
            except Exception as e:
                logger.warning("YouTube-Update fehlgeschlagen: %s", e)

        if twitch_client is not None:
            try:
                count = await twitch_client.fetch_followers(session, config.TWITCH.broadcaster_login)
                await db.record("twitch", count)
                await rename_channel(guild, config.TWITCH.channel_id, format_channel_name("🟣", "Twitch", count))
            except Exception as e:
                logger.warning("Twitch-Update fehlgeschlagen: %s", e)


@update_followers.before_loop
async def before_update_followers() -> None:
    await bot.wait_until_ready()


# ---------------- Slash-Command: /statistik social ----------------
statistik_group = discord.app_commands.Group(name="statistik", description="Statistiken des Servers")


@statistik_group.command(name="social", description="Zeigt die aktuellen Social-Media-Zahlen und ihre Entwicklung")
async def statistik_social(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    embed = discord.Embed(title="📊 Social Media Statistik", color=discord.Color.blurple())
    now = int(time.time())
    has_data = False

    for key, emoji, label, cfg in PLATFORM_INFO:
        if not cfg.enabled:
            continue

        current = await db.latest(key)
        if current is None:
            embed.add_field(name=f"{emoji} {label}", value="Noch keine Daten erfasst", inline=False)
            continue

        has_data = True
        lines = [f"Aktuell: **{current:,}**".replace(",", ".")]

        day_ago = await db.count_at_or_before(key, now - 24 * 3600)
        if day_ago is not None:
            lines.append(f"24h: {current - day_ago:+,}".replace(",", "."))

        week_ago = await db.count_at_or_before(key, now - 7 * 24 * 3600)
        if week_ago is not None:
            lines.append(f"7 Tage: {current - week_ago:+,}".replace(",", "."))

        embed.add_field(name=f"{emoji} {label}", value="\n".join(lines), inline=True)

    if not has_data:
        embed.description = "Noch keine Statistikdaten vorhanden - der erste Update-Zyklus muss zuerst durchlaufen."

    await interaction.followup.send(embed=embed)


bot.tree.add_command(statistik_group)

_commands_synced = False


@bot.event
async def on_ready() -> None:
    global _commands_synced
    logger.info("Eingeloggt als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    enabled = [label for _, _, label, cfg in PLATFORM_INFO if cfg.enabled]
    logger.info("Aktive Plattformen: %s", ", ".join(enabled) if enabled else "keine (siehe .env)")

    if not _commands_synced:
        try:
            guild = discord.Object(id=config.GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info("%s Slash-Command(s) fuer Guild %s synchronisiert", len(synced), config.GUILD_ID)
            _commands_synced = True
        except discord.HTTPException as e:
            logger.warning("Slash-Command-Sync fehlgeschlagen: %s", e)

    if not update_followers.is_running():
        update_followers.start()


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in der .env-Datei.")
    if not config.GUILD_ID:
        raise SystemExit("GUILD_ID fehlt in der .env-Datei.")
    bot.run(config.DISCORD_TOKEN)

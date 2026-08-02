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

import asyncio
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
# Eigener Namespace fuer alles rund um Follower-Abrufe/Channel-Updates, damit
# sich das per Filter in einen separaten Discord-Channel leiten laesst (siehe
# DiscordLogHandler/on_ready). platforms.py haengt sich als Kind hier ein.
update_logger = logging.getLogger("follower-bot.updates")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# (db_key, Emoji, Anzeigename, Config) - eine Stelle fuer alle Plattform-Metadaten
PLATFORM_INFO = (
    ("instagram", "📸", "Instagram", config.INSTAGRAM),
    ("tiktok", "🎵", "TikTok", config.TIKTOK),
    ("youtube", "▶️", "YouTube", config.YOUTUBE),
    ("twitch", "🟣", "Twitch", config.TWITCH),
)


def format_channel_name(emoji: str, label: str, count: int) -> str:
    return f"{emoji} {label}: {count:,}".replace(",", ".")


class _NamespaceFilter(logging.Filter):
    """Laesst nur (bzw. explizit nicht) Log-Eintraege eines Logger-Namespaces
    durch - damit ein Handler, der am 'follower-bot'-Logger haengt, trotzdem
    nur fuer einen Teilbereich (z. B. 'follower-bot.updates') zustaendig ist."""

    def __init__(self, prefix: str, exclude: bool = False):
        super().__init__()
        self.prefix = prefix
        self.exclude = exclude

    def filter(self, record: logging.LogRecord) -> bool:
        matches = record.name == self.prefix or record.name.startswith(self.prefix + ".")
        return not matches if self.exclude else matches


class DiscordLogHandler(logging.Handler):
    """Spiegelt Log-Eintraege zusaetzlich in einen Discord-Channel.

    Wird nur an den 'follower-bot'-Logger gehaengt (siehe on_ready), daher
    landen hier ausschliesslich unsere eigenen Meldungen, nie discord.py-
    interne Logs - das vermeidet Spam und eine moegliche Endlosschleife,
    falls das Senden selbst fehlschlaegt.
    """

    def __init__(self, bot: commands.Bot, channel_id: int, level: int):
        super().__init__(level=level)
        self.bot = bot
        self.channel_id = channel_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        asyncio.create_task(self._send(message))

    async def _send(self, message: str) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            return
        if len(message) > 1900:
            message = message[:1900] + "…"
        try:
            await channel.send(f"```{message}```")
        except discord.HTTPException:
            pass


async def rename_channel(guild: discord.Guild, channel_id: int, new_name: str) -> None:
    channel = guild.get_channel(channel_id)
    if channel is None:
        update_logger.warning("Channel %s nicht gefunden (falsche ID oder Bot nicht auf dem Server?)", channel_id)
        return
    if channel.name == new_name:
        return  # keine Aenderung noetig -> kein API-Call
    try:
        await channel.edit(name=new_name)
        update_logger.info("Channel %s aktualisiert -> %s", channel_id, new_name)
    except discord.Forbidden:
        update_logger.error("Fehlende 'Manage Channels'-Berechtigung fuer Channel %s", channel_id)
    except discord.HTTPException as e:
        update_logger.warning("Konnte Channel %s nicht umbenennen: %s", channel_id, e)


@tasks.loop(seconds=config.UPDATE_INTERVAL)
async def update_followers() -> None:
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        update_logger.warning(
            "Guild %s nicht gefunden. Ist GUILD_ID korrekt und der Bot auf dem Server?", config.GUILD_ID
        )
        return

    async with aiohttp.ClientSession() as session:
        if config.INSTAGRAM.enabled:
            try:
                count = await platforms.fetch_instagram_followers(session, config.INSTAGRAM.username)
                await db.record("instagram", count)
                await rename_channel(guild, config.INSTAGRAM.channel_id, format_channel_name("📸", "Instagram", count))
            except Exception as e:
                update_logger.warning("Instagram-Update fehlgeschlagen: %s", e)

        if config.TIKTOK.enabled:
            try:
                count = await platforms.fetch_tiktok_followers(session, config.TIKTOK.username)
                await db.record("tiktok", count)
                await rename_channel(guild, config.TIKTOK.channel_id, format_channel_name("🎵", "TikTok", count))
            except Exception as e:
                update_logger.warning("TikTok-Update fehlgeschlagen: %s", e)

        if config.YOUTUBE.enabled:
            try:
                count = await platforms.fetch_youtube_subscribers(session, config.YOUTUBE.youtube_channel_id)
                await db.record("youtube", count)
                await rename_channel(guild, config.YOUTUBE.channel_id, format_channel_name("▶️", "YouTube", count))
            except Exception as e:
                update_logger.warning("YouTube-Update fehlgeschlagen: %s", e)

        if config.TWITCH.enabled:
            try:
                count = await platforms.fetch_twitch_followers(session, config.TWITCH.broadcaster_login)
                await db.record("twitch", count)
                await rename_channel(guild, config.TWITCH.channel_id, format_channel_name("🟣", "Twitch", count))
            except Exception as e:
                update_logger.warning("Twitch-Update fehlgeschlagen: %s", e)


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
_discord_log_attached = False


@bot.event
async def on_ready() -> None:
    global _commands_synced, _discord_log_attached
    logger.info("Eingeloggt als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    enabled = [label for _, _, label, cfg in PLATFORM_INFO if cfg.enabled]
    logger.info("Aktive Plattformen: %s", ", ".join(enabled) if enabled else "keine (siehe .env)")

    if config.CHANNEL_ID_LOG and not _discord_log_attached:
        discord_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
        formatter = logging.Formatter("%(levelname)s [%(name)s]: %(message)s")
        follower_channel_id = config.CHANNEL_ID_LOG_FOLLOWER or config.CHANNEL_ID_LOG

        general_handler = DiscordLogHandler(bot, config.CHANNEL_ID_LOG, level=discord_level)
        general_handler.setFormatter(formatter)
        general_handler.addFilter(_NamespaceFilter("follower-bot.updates", exclude=True))
        logger.addHandler(general_handler)

        updates_handler = DiscordLogHandler(bot, follower_channel_id, level=discord_level)
        updates_handler.setFormatter(formatter)
        updates_handler.addFilter(_NamespaceFilter("follower-bot.updates"))
        logger.addHandler(updates_handler)

        _discord_log_attached = True
        channel = bot.get_channel(config.CHANNEL_ID_LOG)
        if channel is not None:
            await channel.send(f"🟢 Bot gestartet (aktive Plattformen: {', '.join(enabled) or 'keine'}).")

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

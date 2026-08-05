#!/usr/bin/env python3
"""Discord-Bot: zeigt Instagram- und TikTok-Follower auf Discord-Channels an,
indem er deren Namen periodisch aktualisiert.

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
from typing import Optional

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


def _chunk_lines(lines: list[str], limit: int = 1900) -> list[str]:
    """Fasst Zeilen zu moeglichst wenigen Bloecken bis 'limit' Zeichen zusammen."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class DiscordLogHandler(logging.Handler):
    """Spiegelt Log-Eintraege zusaetzlich in einen Discord-Channel.

    Buendelt alle Eintraege, die innerhalb von FLUSH_INTERVAL Sekunden
    reinkommen, zu EINER Nachricht statt vieler Einzel-Sends. Ohne das wuerde
    ein Burst (z. B. Start + erster Update-Zyklus mit mehreren Plattformen)
    zig einzelne channel.send()-Aufrufe gleichzeitig ausloesen - Discord
    erlaubt aber nur ~5 Nachrichten/5s pro Channel, der Rest wird von
    discord.py intern verzoegert nachgereicht ('schleppendes' Log).

    Wird nur an den 'follower-bot'-Logger gehaengt (siehe on_ready), daher
    landen hier ausschliesslich unsere eigenen Meldungen, nie discord.py-
    interne Logs - das vermeidet Spam und eine moegliche Endlosschleife,
    falls das Senden selbst fehlschlaegt.
    """

    FLUSH_INTERVAL = 1.0

    def __init__(self, bot: commands.Bot, channel_id: int, level: int):
        super().__init__(level=level)
        self.bot = bot
        self.channel_id = channel_id
        self._buffer: list[str] = []
        self._flush_task: Optional[asyncio.Task] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        self._buffer.append(message)
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_after_delay())

    async def _flush_after_delay(self) -> None:
        await asyncio.sleep(self.FLUSH_INTERVAL)
        if not self._buffer:
            return
        lines, self._buffer = self._buffer, []

        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            return
        for chunk in _chunk_lines(lines):
            try:
                await channel.send(f"```{chunk}```")
            except discord.HTTPException:
                pass


async def rename_channel(guild: discord.Guild, channel_id: int, new_name: str) -> None:
    channel = guild.get_channel(channel_id)
    if channel is None:
        update_logger.warning("Channel %s nicht gefunden (falsche ID oder Bot nicht auf dem Server?)", channel_id)
        return
    if channel.name == new_name:
        update_logger.info("Channel %s bereits aktuell (%s), keine Aenderung noetig", channel_id, new_name)
        return  # keine Aenderung noetig -> kein API-Call
    try:
        await channel.edit(name=new_name)
        update_logger.info("Channel %s aktualisiert -> %s", channel_id, new_name)
    except discord.Forbidden:
        update_logger.error("Fehlende 'Manage Channels'-Berechtigung fuer Channel %s", channel_id)
    except discord.HTTPException as e:
        update_logger.warning("Konnte Channel %s nicht umbenennen: %s", channel_id, e)


async def sync_followers() -> None:
    """Fragt alle aktivierten Plattformen ab und aktualisiert Channels + DB.

    Wird sowohl vom periodischen Task (update_followers) als auch vom
    manuellen Slash-Command /syncfollower aufgerufen.
    """
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        update_logger.warning(
            "Guild %s nicht gefunden. Ist GUILD_ID korrekt und der Bot auf dem Server?", config.GUILD_ID
        )
        return

    update_logger.info("Update-Zyklus gestartet")

    async with aiohttp.ClientSession() as session:
        if config.INSTAGRAM.enabled:
            try:
                count = await platforms.fetch_instagram_followers(
                    session, config.INSTAGRAM.username, cookie=config.INSTAGRAM.cookie
                )
                await db.record("instagram", count)
                update_logger.debug("Instagram: %s in DB gespeichert, aktualisiere Channel", count)
                await rename_channel(guild, config.INSTAGRAM.channel_id, format_channel_name("📸", "Instagram", count))
            except Exception as e:
                update_logger.warning("Instagram-Update fehlgeschlagen: %s", e)

        if config.TIKTOK.enabled:
            try:
                count = await platforms.fetch_tiktok_followers(session, config.TIKTOK.username)
                await db.record("tiktok", count)
                update_logger.debug("TikTok: %s in DB gespeichert, aktualisiere Channel", count)
                await rename_channel(guild, config.TIKTOK.channel_id, format_channel_name("🎵", "TikTok", count))
            except Exception as e:
                update_logger.warning("TikTok-Update fehlgeschlagen: %s", e)

    update_logger.info("Update-Zyklus abgeschlossen")


@tasks.loop(seconds=config.UPDATE_INTERVAL)
async def update_followers() -> None:
    await sync_followers()


@update_followers.before_loop
async def before_update_followers() -> None:
    await bot.wait_until_ready()


async def build_statistik_embed(title: str = "📊 Social Media Statistik") -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple())
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

    return embed


# ---------------- Slash-Command: /statistik social ----------------
statistik_group = discord.app_commands.Group(name="statistik", description="Statistiken des Servers")


@statistik_group.command(name="social", description="Zeigt die aktuellen Social-Media-Zahlen und ihre Entwicklung")
async def statistik_social(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    embed = await build_statistik_embed()
    await interaction.followup.send(embed=embed)


bot.tree.add_command(statistik_group)


# ---------------- Slash-Command: /syncfollower ----------------
@bot.tree.command(name="syncfollower", description="Aktualisiert alle Follower-Zahlen sofort, statt auf das naechste Intervall zu warten")
@discord.app_commands.default_permissions(manage_channels=True)
@discord.app_commands.checks.has_permissions(manage_channels=True)
@discord.app_commands.checks.cooldown(1, 300, key=lambda i: i.guild_id)
async def syncfollower(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    await sync_followers()
    embed = await build_statistik_embed(title="🔄 Follower-Sync abgeschlossen")
    await interaction.followup.send(embed=embed)


@syncfollower.error
async def syncfollower_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳ Bitte warte noch {error.retry_after:.0f}s, bevor du erneut manuell synchronisierst.",
            ephemeral=True,
        )
    elif isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Dafuer fehlt dir die Berechtigung 'Manage Channels'.", ephemeral=True
        )
    else:
        update_logger.error("Unerwarteter Fehler bei /syncfollower: %s", error)
        raise error

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

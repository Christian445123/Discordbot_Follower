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
import webpanel

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


@update_followers.error
async def update_followers_error(error: BaseException) -> None:
    # tasks.loop() beendet die Schleife bei einer unbehandelten Exception
    # dauerhaft (kein automatischer Neustart). sync_followers() faengt bereits
    # alle Fehler pro Plattform ab, ein Fehler hier ist also unerwartet -
    # trotzdem neu starten, statt dass Follower-Updates fuer immer stillstehen.
    update_logger.error("Unerwarteter Fehler im Update-Loop, starte neu: %s", error, exc_info=error)
    update_followers.restart()


async def gather_follower_stats() -> list[dict]:
    """Sammelt aktuelle Follower-Zahl + 24h-/7-Tage-Veraenderung je aktivierter
    Plattform aus der DB. Gemeinsame Datengrundlage fuer /statistik social
    (Discord-Embed) und die Follower-Anzeige im Webpanel-Dashboard."""
    now = int(time.time())
    stats: list[dict] = []
    for key, emoji, label, cfg in PLATFORM_INFO:
        if not cfg.enabled:
            continue
        entry = {"key": key, "emoji": emoji, "label": label, "current": None, "day_delta": None, "week_delta": None, "error": False}
        try:
            current = await db.latest(key)
            entry["current"] = current
            if current is not None:
                day_ago = await db.count_at_or_before(key, now - 24 * 3600)
                if day_ago is not None:
                    entry["day_delta"] = current - day_ago
                week_ago = await db.count_at_or_before(key, now - 7 * 24 * 3600)
                if week_ago is not None:
                    entry["week_delta"] = current - week_ago
        except Exception as e:
            # Ein DB-Fehler (z.B. MySQL kurzzeitig nicht erreichbar) soll nicht
            # den ganzen Abruf scheitern lassen - stattdessen pro Plattform
            # markieren, dass gerade keine Daten verfuegbar sind.
            entry["error"] = True
            update_logger.error("DB-Fehler beim Abruf der Follower-Statistik (%s): %s", key, e)
        stats.append(entry)
    return stats


async def build_statistik_embed(title: str = "📊 Social Media Statistik") -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple())
    stats = await gather_follower_stats()
    has_data = any(s["current"] is not None for s in stats)
    db_error = any(s["error"] for s in stats)

    for s in stats:
        if s["error"]:
            embed.add_field(name=f"{s['emoji']} {s['label']}", value="⚠️ Datenbank aktuell nicht erreichbar", inline=True)
        elif s["current"] is None:
            embed.add_field(name=f"{s['emoji']} {s['label']}", value="Noch keine Daten erfasst", inline=False)
        else:
            lines = [f"Aktuell: **{s['current']:,}**".replace(",", ".")]
            if s["day_delta"] is not None:
                lines.append(f"24h: {s['day_delta']:+,}".replace(",", "."))
            if s["week_delta"] is not None:
                lines.append(f"7 Tage: {s['week_delta']:+,}".replace(",", "."))
            embed.add_field(name=f"{s['emoji']} {s['label']}", value="\n".join(lines), inline=True)

    if not has_data and not db_error:
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


async def _respond_with_error(interaction: discord.Interaction, message: str) -> None:
    """Antwortet auf eine fehlgeschlagene Interaktion, egal ob response.send_message
    schon benutzt wurde (z.B. per defer()) oder nicht - sonst zeigt Discord dem
    Nutzer nur "Diese Interaktion ist fehlgeschlagen" ohne jede Erklaerung."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


@syncfollower.error
async def syncfollower_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        await _respond_with_error(
            interaction, f"⏳ Bitte warte noch {error.retry_after:.0f}s, bevor du erneut manuell synchronisierst."
        )
    elif isinstance(error, discord.app_commands.MissingPermissions):
        await _respond_with_error(interaction, "❌ Dafuer fehlt dir die Berechtigung 'Manage Channels'.")
    else:
        update_logger.error("Unerwarteter Fehler bei /syncfollower: %s", error, exc_info=error)
        await _respond_with_error(interaction, "⚠️ Unerwarteter Fehler beim Sync. Details siehe Bot-Log.")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    """Auffangnetz fuer alle Slash-Commands ohne eigenen Error-Handler (z.B.
    /statistik social). Ohne das bleibt eine fehlgeschlagene Interaktion fuer
    den Nutzer als 'Diese Interaktion ist fehlgeschlagen' ohne Erklaerung stehen,
    waehrend der eigentliche Fehler nur in den Server-Logs sichtbar ist."""
    command_name = interaction.command.qualified_name if interaction.command else "unbekannt"
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        await _respond_with_error(interaction, f"⏳ Bitte warte noch {error.retry_after:.0f}s.")
    elif isinstance(error, discord.app_commands.MissingPermissions):
        await _respond_with_error(interaction, "❌ Dafuer fehlt dir die noetige Berechtigung.")
    else:
        update_logger.error("Unerwarteter Fehler bei /%s: %s", command_name, error, exc_info=error)
        await _respond_with_error(interaction, "⚠️ Unerwarteter Fehler bei der Ausfuehrung. Details siehe Bot-Log.")


_commands_synced = False
_discord_log_attached = False
_webpanel_started = False


async def ensure_commands_synced() -> None:
    """Synchronisiert die Slash-Commands fuer die Guild, falls das noch nicht
    gelungen ist. Wird sowohl von on_ready als auch periodisch von
    resync_commands_watchdog aufgerufen: on_ready feuert bei einem Gateway-
    RESUME (kurzer Netz-Hänger, der die Session behaelt) nicht erneut - ohne
    den periodischen Task wuerde ein einmal fehlgeschlagener Sync dann fuer
    den Rest der Prozesslaufzeit nie wieder versucht."""
    global _commands_synced
    if _commands_synced:
        return
    try:
        guild = discord.Object(id=config.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info("%s Slash-Command(s) fuer Guild %s synchronisiert", len(synced), config.GUILD_ID)
        _commands_synced = True
    except Exception as e:
        logger.error("Slash-Command-Sync fehlgeschlagen, naechster Versuch in wenigen Minuten: %s", e)


async def force_resync_commands() -> int:
    """Erzwingt eine vollstaendige Neu-Registrierung der Slash-Commands bei
    Discord, unabhaengig vom _commands_synced-Status. Anders als
    ensure_commands_synced() faengt diese Funktion Fehler NICHT selbst ab,
    sondern gibt sie weiter - genutzt vom Webpanel-Button 'Slash-Commands
    wiederherstellen', wo ein echter Fehler dem Nutzer angezeigt werden soll,
    statt still im Log zu verschwinden."""
    global _commands_synced
    guild = discord.Object(id=config.GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    _commands_synced = True
    return len(synced)


@tasks.loop(minutes=10)
async def resync_commands_watchdog() -> None:
    if not _commands_synced:
        await ensure_commands_synced()


@resync_commands_watchdog.before_loop
async def before_resync_commands_watchdog() -> None:
    await bot.wait_until_ready()


@resync_commands_watchdog.error
async def resync_commands_watchdog_error(error: BaseException) -> None:
    # Gleiches Prinzip wie update_followers_error: tasks.loop() beendet die
    # Schleife bei einer unbehandelten Exception sonst dauerhaft und
    # stillschweigend - dann wuerde ein einmal fehlgeschlagener Sync nie wieder
    # automatisch neu versucht.
    logger.error("Unerwarteter Fehler im Command-Sync-Watchdog, starte neu: %s", error, exc_info=error)
    resync_commands_watchdog.restart()


@bot.event
async def on_ready() -> None:
    global _discord_log_attached, _webpanel_started
    logger.info("Eingeloggt als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    enabled = [label for _, _, label, cfg in PLATFORM_INFO if cfg.enabled]
    logger.info("Aktive Plattformen: %s", ", ".join(enabled) if enabled else "keine (siehe .env)")

    # Kritischer Teil zuerst und unabhaengig vom optionalen Discord-Log weiter
    # unten: on_ready feuert pro Verbindung nur einmal (kein RESUME-Trigger).
    # Wuerde eine Exception hier durchschlagen (z.B. weil das Senden der
    # Start-Nachricht in CHANNEL_ID_LOG fehlschlaegt), blieben Slash-Commands
    # und der Update-Loop fuer den Rest der Prozesslaufzeit unsynchronisiert.
    await ensure_commands_synced()

    if not update_followers.is_running():
        update_followers.start()
    if not resync_commands_watchdog.is_running():
        resync_commands_watchdog.start()

    if config.CHANNEL_ID_LOG and not _discord_log_attached:
        try:
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
        except Exception as e:
            logger.error("Konnte Discord-Log-Channel nicht anbinden: %s", e, exc_info=e)

    if config.WEB_ENABLED and not _webpanel_started:
        try:
            await webpanel.start_webpanel(
                bot,
                force_resync_commands=force_resync_commands,
                sync_followers=sync_followers,
                gather_follower_stats=gather_follower_stats,
            )
            _webpanel_started = True
        except Exception as e:
            logger.error("Webpanel konnte nicht gestartet werden: %s", e, exc_info=e)


@bot.event
async def on_error(event_method: str, *args, **kwargs) -> None:
    # Ersetzt Discord.py's Default-Handler (druckt nur nach stderr) durch
    # unseren Logger, damit unerwartete Fehler in Event-Handlern (z.B.
    # on_ready) auch im Discord-Log-Channel sichtbar werden statt nur in
    # 'pm2 logs'.
    logger.exception("Unerwarteter Fehler in Event-Handler '%s'", event_method)


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in der .env-Datei.")
    if not config.GUILD_ID:
        raise SystemExit("GUILD_ID fehlt in der .env-Datei.")
    bot.run(config.DISCORD_TOKEN)

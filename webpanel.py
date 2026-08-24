#!/usr/bin/env python3
"""Kleines Admin-Webpanel mit Discord-OAuth2-Login (analog zum Webpanel in
Discord_Time): nur Mitglieder mit der Rolle config.ROLE_ADMIN_ID koennen den
Bot neustarten, Slash-Commands neu registrieren, per Git deployen oder einen
Follower-Sync anstossen - ohne SSH-Zugriff auf den Server.

Laeuft als aiohttp.web-Server im selben Prozess/Event-Loop wie der Bot (siehe
start_webpanel(), aufgerufen aus bot.py). Sessions werden als signierte
Cookies gespeichert (HMAC-SHA256 ueber config.SESSION_SECRET) statt ueber eine
zusaetzliche Abhaengigkeit wie aiohttp-session - dafuer reicht hier die
Standardbibliothek.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands

import config

logger = logging.getLogger("follower-bot.web")

DISCORD_API = "https://discord.com/api/v10"
REPO_ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "session"


# ---------------- Signierte Session-Cookies ----------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: bytes) -> bytes:
    return hmac.new(config.SESSION_SECRET.encode(), payload, hashlib.sha256).digest()


def _encode_session(data: dict, max_age: int) -> str:
    payload = json.dumps({"d": data, "exp": int(time.time()) + max_age}, separators=(",", ":")).encode()
    return f"{_b64url_encode(payload)}.{_b64url_encode(_sign(payload))}"


def _decode_session(token: str) -> dict:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64url_decode(raw_b64)
        if not hmac.compare_digest(_b64url_decode(sig_b64), _sign(raw)):
            return {}
        payload = json.loads(raw)
        if payload.get("exp", 0) < time.time():
            return {}
        return payload.get("d", {})
    except Exception:
        return {}


def _read_session(request: web.Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    return _decode_session(token) if token else {}


def _set_session_cookie(response: web.StreamResponse, data: dict, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _encode_session(data, max_age),
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=config.WEB_BASE_URL.startswith("https://"),
        path="/",
    )


def _clear_session_cookie(response: web.StreamResponse) -> None:
    response.del_cookie(SESSION_COOKIE, path="/")


# ---------------- Discord OAuth2 ----------------
def _redirect_uri() -> str:
    return f"{config.WEB_BASE_URL}/auth/callback"


def _authorize_url(state: str) -> str:
    params = {
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state,
    }
    return f"{DISCORD_API}/oauth2/authorize?{urlencode(params)}"


async def _exchange_code(http: aiohttp.ClientSession, code: str) -> dict:
    data = {
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }
    async with http.post(f"{DISCORD_API}/oauth2/token", data=data) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Token-Austausch fehlgeschlagen ({resp.status})")
        return await resp.json()


async def _fetch_user(http: aiohttp.ClientSession, access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with http.get(f"{DISCORD_API}/users/@me", headers=headers) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Nutzerabfrage fehlgeschlagen ({resp.status})")
        return await resp.json()


async def _fetch_guild_member(http: aiohttp.ClientSession, access_token: str) -> Optional[dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{DISCORD_API}/users/@me/guilds/{config.GUILD_ID}/member"
    async with http.get(url, headers=headers) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


def _avatar_url(uid: str, avatar_hash: Optional[str]) -> str:
    if avatar_hash:
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.png?size=128"
    return "https://cdn.discordapp.com/embed/avatars/0.png"


async def _notify(bot: commands.Bot, title: str, description: str, color: int, actor: str) -> None:
    if not config.CHANNEL_ID_LOG:
        return
    channel = bot.get_channel(config.CHANNEL_ID_LOG)
    if channel is None:
        return
    embed = discord.Embed(title=title, description=description or None, color=color)
    embed.add_field(name="Von", value=f"{actor} (Webpanel)")
    embed.timestamp = discord.utils.utcnow()
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# ---------------- Git-Deploy (Button "Deployen") ----------------
async def _run_cmd(*args: str, cwd: Path = REPO_ROOT, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Befehl '{' '.join(args)}' hat das Zeitlimit ({timeout:.0f}s) ueberschritten")
    return proc.returncode, stdout.decode(errors="replace").strip()


async def _run_git_deploy() -> dict:
    """Holt den neuesten Stand vom Git-Remote (nur Fast-Forward) und zieht bei
    Aenderungen an requirements.txt automatisch 'pip install' nach - analog zu
    deploy.sh, nur manuell per Webpanel-Button statt per Cron ausgeloest."""
    rc, before = await _run_cmd("git", "rev-parse", "HEAD")
    if rc != 0:
        raise RuntimeError(before or "git rev-parse fehlgeschlagen")

    rc, pull_output = await _run_cmd("git", "pull", "--ff-only", timeout=120)
    if rc != 0:
        raise RuntimeError(pull_output or "git pull fehlgeschlagen")

    rc, after = await _run_cmd("git", "rev-parse", "HEAD")
    if rc != 0:
        raise RuntimeError(after or "git rev-parse fehlgeschlagen")

    changed = before != after
    pip_install_ran = False
    pip_install_error: Optional[str] = None

    if changed:
        rc, diff_output = await _run_cmd("git", "diff", "--name-only", before, after)
        if rc == 0 and any(line.strip() == "requirements.txt" for line in diff_output.splitlines()):
            rc, pip_output = await _run_cmd(
                sys.executable, "-m", "pip", "install", "-r", "requirements.txt", timeout=300
            )
            if rc != 0:
                pip_install_error = pip_output[:1500]
            else:
                pip_install_ran = True

    return {
        "changed": changed,
        "output": pull_output,
        "pip_install_ran": pip_install_ran,
        "pip_install_error": pip_install_error,
    }


# ---------------- HTML ----------------
def _page_shell(title: str, body: str, wide: bool = False) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: radial-gradient(circle at top, #1e2130 0%, #0f1117 60%);
    color: #e6e6ea; padding: 24px;
  }}
  .card {{ width: 100%; max-width: 480px; background: #171a24; border: 1px solid #2a2e3d; border-radius: 16px; padding: 32px; box-shadow: 0 20px 60px rgba(0,0,0,0.4); }}
  .card.wide {{ max-width: 640px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: #9098ab; font-size: 0.9rem; margin-bottom: 24px; }}
  .btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding: 12px 20px; border-radius: 10px; background: #5865f2; color: #fff; text-decoration: none; font-weight: 600; border: none; cursor: pointer; font-size: 0.95rem; transition: background 0.15s ease; }}
  .btn:hover {{ background: #4752c4; }}
  .btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .profile {{ display: flex; align-items: center; gap: 14px; margin-bottom: 24px; }}
  .profile img {{ width: 56px; height: 56px; border-radius: 50%; }}
  .profile .name {{ font-weight: 600; font-size: 1.05rem; }}
  .hint {{ color: #9098ab; font-size: 0.85rem; line-height: 1.5; }}
  .logout {{ display: block; margin-top: 20px; color: #9098ab; font-size: 0.85rem; text-decoration: none; }}
  .logout:hover {{ color: #e6e6ea; }}
  .actions {{ display: flex; flex-direction: column; gap: 10px; margin-top: 20px; }}
  .btn.resync {{ background: #2f3d4d; color: #7ec4f1; }}
  .btn.resync:hover {{ background: #3a4d61; }}
  .btn.deploy {{ background: #3d2f4d; color: #c4a3f1; }}
  .btn.deploy:hover {{ background: #4d3a61; }}
  .btn.sync {{ background: #2f4d3a; color: #6fe39b; }}
  .btn.sync:hover {{ background: #3a6349; }}
  .btn.restart {{ background: #4d1f24; color: #f28b8b; }}
  .btn.restart:hover {{ background: #63272e; }}
  .result {{ margin-top: 4px; font-size: 0.82rem; white-space: pre-wrap; font-family: ui-monospace, monospace; min-height: 1em; }}
  .forbidden {{ text-align: center; padding: 20px 0; }}
</style>
</head>
<body>
  <div class="card{' wide' if wide else ''}">{body}</div>
</body>
</html>"""


def _render_login_page(error: Optional[str] = None) -> str:
    error_html = f'<p class="hint" style="color:#e74c3c;margin-bottom:16px;">{html.escape(error)}</p>' if error else ""
    return _page_shell(
        "Follower-Bot Webpanel - Login",
        f"""
        <h1>Follower-Bot Webpanel</h1>
        <p class="sub">Melde dich mit Discord an, um Zugriff auf das Admin-Panel zu erhalten.</p>
        {error_html}
        <a class="btn" href="/login">Mit Discord anmelden</a>
        """,
    )


def _render_home_page(session: dict) -> str:
    return _page_shell(
        "Follower-Bot Webpanel",
        f"""
        <div class="profile">
          <img src="{_avatar_url(session['uid'], session.get('avatar'))}" alt="Avatar">
          <div class="name">{html.escape(session.get('username', '?'))}</div>
        </div>
        <p class="hint">Du bist angemeldet, hast aber keinen Zugriff auf das Admin-Panel (fehlende Rolle).</p>
        <a class="logout" href="/logout">Abmelden</a>
        """,
    )


def _render_forbidden_page() -> str:
    return _page_shell(
        "Kein Zugriff",
        """
        <div class="forbidden">
          <h1>Kein Zugriff</h1>
          <p class="hint">Dieser Bereich ist einer bestimmten Rolle vorbehalten.</p>
          <a class="logout" href="/logout">Abmelden</a>
        </div>
        """,
    )


_STAFF_ACTIONS_AND_SCRIPT = """
<div class="actions">
  <button id="syncBtn" class="btn sync" onclick="runSimple('sync-followers', 'syncBtn', 'syncResult', '🔄 Follower jetzt synchronisieren')">🔄 Follower jetzt synchronisieren</button>
  <div id="syncResult" class="result"></div>

  <button id="resyncBtn" class="btn resync" onclick="runResync()">🔁 Slash-Commands wiederherstellen</button>
  <div id="resyncResult" class="result"></div>

  <button id="deployBtn" class="btn deploy" onclick="runDeploy()">🚀 Deployen (git pull)</button>
  <div id="deployResult" class="result"></div>

  <button id="restartBtn" class="btn restart" onclick="runRestart()">♻️ Bot neustarten</button>
  <div id="restartResult" class="result"></div>
</div>
<a class="logout" href="/logout">Abmelden</a>
<script>
  async function postJson(path) {
    const res = await fetch('/staff/' + path, { method: 'POST' });
    return res.json();
  }

  async function runSimple(path, btnId, resultId, doneLabel) {
    const btn = document.getElementById(btnId);
    const result = document.getElementById(resultId);
    btn.disabled = true;
    result.style.color = '';
    result.textContent = '⏳ ...';
    try {
      const data = await postJson(path);
      if (data.ok) {
        result.style.color = '#6fe39b';
        result.textContent = '✅ Fertig.';
      } else {
        result.style.color = '#e74c3c';
        result.textContent = '❌ ' + (data.error || 'Unbekannter Fehler');
      }
    } catch (e) {
      result.style.color = '#e74c3c';
      result.textContent = '❌ Verbindungsfehler';
    }
    btn.disabled = false;
  }

  async function runResync() {
    const btn = document.getElementById('resyncBtn');
    const result = document.getElementById('resyncResult');
    btn.disabled = true;
    result.style.color = '';
    result.textContent = '⏳ Registriere ...';
    try {
      const data = await postJson('resync-commands');
      if (data.ok) {
        result.style.color = '#6fe39b';
        result.textContent = '✅ ' + data.count + ' Slash-Commands neu registriert.';
      } else {
        result.style.color = '#e74c3c';
        result.textContent = '❌ ' + (data.error || 'Unbekannter Fehler');
      }
    } catch (e) {
      result.style.color = '#e74c3c';
      result.textContent = '❌ Verbindungsfehler';
    }
    btn.disabled = false;
  }

  async function runDeploy() {
    if (!confirm('Neuesten Stand aus dem Git-Repository laden (git pull)? Bei Aenderungen startet der Bot danach automatisch neu.')) return;
    const btn = document.getElementById('deployBtn');
    const result = document.getElementById('deployResult');
    btn.disabled = true;
    result.style.color = '';
    result.textContent = '⏳ Deploye ...';
    try {
      const data = await postJson('deploy');
      if (data.ok) {
        result.style.color = data.pip_install_error ? '#e74c3c' : '#6fe39b';
        let text = data.output || '(keine Ausgabe)';
        if (data.pip_install_ran) text += '\\n\\npip install erfolgreich ausgefuehrt.';
        if (data.pip_install_error) text += '\\n\\npip install fehlgeschlagen:\\n' + data.pip_install_error + '\\n\\nBot wurde NICHT neugestartet.';
        if (data.restarting) text += '\\n\\nBot startet neu, Seite laedt in Kuerze neu ...';
        result.textContent = text;
        if (data.restarting) { setTimeout(() => location.reload(), 8000); return; }
      } else {
        result.style.color = '#e74c3c';
        result.textContent = '❌ ' + (data.error || 'Unbekannter Fehler');
      }
    } catch (e) {
      result.style.color = '#e74c3c';
      result.textContent = '❌ Verbindungsfehler';
    }
    btn.disabled = false;
  }

  async function runRestart() {
    if (!confirm('Bot wirklich neustarten? Er ist danach fuer wenige Sekunden nicht erreichbar.')) return;
    const btn = document.getElementById('restartBtn');
    const result = document.getElementById('restartResult');
    btn.disabled = true;
    result.style.color = '';
    result.textContent = '⏳ Neustart wird ausgeloest ...';
    try {
      const data = await postJson('restart');
      if (data.ok) {
        result.style.color = '#6fe39b';
        result.textContent = '✅ Neustart ausgeloest. Seite laedt in Kuerze neu ...';
        setTimeout(() => location.reload(), 8000);
        return;
      } else {
        result.style.color = '#e74c3c';
        result.textContent = '❌ ' + (data.error || 'Unbekannter Fehler');
      }
    } catch (e) {
      result.style.color = '#e74c3c';
      result.textContent = '❌ Verbindungsfehler';
    }
    btn.disabled = false;
  }
</script>
"""


def _render_staff_page(username: str) -> str:
    header = f"""
    <h1>📊 Admin Dashboard</h1>
    <p class="sub">Angemeldet als {html.escape(username)}</p>
    """
    return _page_shell("Admin Dashboard", header + _STAFF_ACTIONS_AND_SCRIPT, wide=True)


# ---------------- Server ----------------
def _build_app(bot: commands.Bot, force_resync_commands, sync_followers) -> web.Application:
    app = web.Application()

    async def index(request: web.Request) -> web.StreamResponse:
        session = _read_session(request)
        if not session.get("uid"):
            return web.Response(text=_render_login_page(), content_type="text/html")
        if session.get("is_admin"):
            raise web.HTTPFound("/staff")
        return web.Response(text=_render_home_page(session), content_type="text/html")

    async def login(request: web.Request) -> web.StreamResponse:
        state = secrets.token_urlsafe(24)
        resp = web.HTTPFound(_authorize_url(state))
        _set_session_cookie(resp, {"oauth_state": state}, max_age=600)
        return resp

    async def auth_callback(request: web.Request) -> web.StreamResponse:
        session = _read_session(request)
        code = request.query.get("code")
        state = request.query.get("state")
        if not code or not state or state != session.get("oauth_state"):
            return web.Response(
                text=_render_login_page("Anmeldung ungueltig oder abgelaufen. Bitte erneut versuchen."),
                content_type="text/html",
                status=400,
            )
        try:
            async with aiohttp.ClientSession() as http:
                token_data = await _exchange_code(http, code)
                access_token = token_data["access_token"]
                user = await _fetch_user(http, access_token)
                member = await _fetch_guild_member(http, access_token)
        except Exception as e:
            logger.error("Web-OAuth2-Fehler: %s", e, exc_info=e)
            return web.Response(
                text=_render_login_page("Anmeldung fehlgeschlagen. Bitte erneut versuchen."),
                content_type="text/html",
                status=500,
            )

        role_ids = member.get("roles", []) if member else []
        is_admin = bool(config.ROLE_ADMIN_ID) and str(config.ROLE_ADMIN_ID) in role_ids
        username = user.get("global_name") or user.get("username", "Unbekannt")
        session_data = {"uid": user["id"], "username": username, "avatar": user.get("avatar"), "is_admin": is_admin}

        resp = web.HTTPFound("/staff" if is_admin else "/")
        _set_session_cookie(resp, session_data, max_age=7 * 24 * 3600)
        logger.info("Web-Login: %s (%s) - Admin-Zugriff: %s", username, user["id"], is_admin)
        return resp

    async def logout(request: web.Request) -> web.StreamResponse:
        resp = web.HTTPFound("/")
        _clear_session_cookie(resp)
        return resp

    async def staff_page(request: web.Request) -> web.StreamResponse:
        session = _read_session(request)
        if not session.get("uid"):
            raise web.HTTPFound("/login")
        if not session.get("is_admin"):
            return web.Response(text=_render_forbidden_page(), content_type="text/html", status=403)
        return web.Response(text=_render_staff_page(session.get("username", "?")), content_type="text/html")

    def _require_admin(request: web.Request) -> Optional[dict]:
        session = _read_session(request)
        if not session.get("uid") or not session.get("is_admin"):
            return None
        return session

    async def api_resync_commands(request: web.Request) -> web.Response:
        session = _require_admin(request)
        if not session:
            return web.json_response({"ok": False, "error": "Kein Zugriff."}, status=403)
        try:
            count = await force_resync_commands()
        except Exception as e:
            logger.error("Webpanel: Command-Resync fehlgeschlagen: %s", e, exc_info=e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        logger.warning("Webpanel: %s Slash-Commands neu registriert von %s.", count, session["username"])
        await _notify(bot, "🔁 Slash-Commands neu registriert", f"{count} Command(s) registriert.", 0x3498DB, session["username"])
        return web.json_response({"ok": True, "count": count})

    async def api_sync_followers(request: web.Request) -> web.Response:
        session = _require_admin(request)
        if not session:
            return web.json_response({"ok": False, "error": "Kein Zugriff."}, status=403)
        try:
            await sync_followers()
        except Exception as e:
            logger.error("Webpanel: Follower-Sync fehlgeschlagen: %s", e, exc_info=e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return web.json_response({"ok": True})

    async def api_deploy(request: web.Request) -> web.Response:
        session = _require_admin(request)
        if not session:
            return web.json_response({"ok": False, "error": "Kein Zugriff."}, status=403)
        try:
            result = await _run_git_deploy()
        except Exception as e:
            logger.error("Webpanel: Deploy fehlgeschlagen: %s", e, exc_info=e)
            await _notify(bot, "🚀 Deploy fehlgeschlagen", f"```{str(e)[:1500]}```", 0xE74C3C, session["username"])
            return web.json_response({"ok": False, "error": str(e)}, status=500)

        logger.warning("Webpanel: Deploy ausgeloest von %s. Aenderungen: %s.", session["username"], result["changed"])
        restarting = result["changed"] and not result["pip_install_error"]
        outcome = (
            "Bereits aktuell"
            if not result["changed"]
            else "Aenderungen geladen, pip install fehlgeschlagen - KEIN Neustart"
            if result["pip_install_error"]
            else "Neue Aenderungen geladen, Bot startet neu ..."
        )
        color = 0xE74C3C if result["pip_install_error"] else (0xF1C40F if result["changed"] else 0x3498DB)
        await _notify(bot, "🚀 Deploy (git pull) ausgefuehrt", f"**Ergebnis:** {outcome}\n```{result['output'][:1500]}```", color, session["username"])

        response = web.json_response({"ok": True, **result, "restarting": restarting})
        if restarting:
            asyncio.get_running_loop().call_later(0.5, lambda: os._exit(0))
        return response

    async def api_restart(request: web.Request) -> web.Response:
        session = _require_admin(request)
        if not session:
            return web.json_response({"ok": False, "error": "Kein Zugriff."}, status=403)
        logger.warning("Webpanel: Neustart angefordert von %s.", session["username"])
        await _notify(bot, "♻️ Bot-Neustart angefordert", "", 0xF1C40F, session["username"])
        response = web.json_response({"ok": True})
        # Antwort zuerst rausschicken, dann sauber beenden - pm2 (autorestart) startet den Prozess neu.
        asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
        return response

    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/auth/callback", auth_callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/staff", staff_page)
    app.router.add_post("/staff/resync-commands", api_resync_commands)
    app.router.add_post("/staff/sync-followers", api_sync_followers)
    app.router.add_post("/staff/deploy", api_deploy)
    app.router.add_post("/staff/restart", api_restart)
    return app


async def start_webpanel(bot: commands.Bot, *, force_resync_commands, sync_followers) -> None:
    """Startet das Webpanel als aiohttp-Server im selben Event-Loop wie der
    Bot. Prueft vorher, ob alle noetigen .env-Variablen gesetzt sind - fehlt
    etwas, bleibt das Panel deaktiviert statt den ganzen Bot am Start zu
    hindern (WEB_ENABLED ist eine optionale Zusatzfunktion)."""
    if not config.WEB_ENABLED:
        return

    missing = [
        name
        for name, value in (
            ("DISCORD_CLIENT_ID", config.DISCORD_CLIENT_ID),
            ("DISCORD_CLIENT_SECRET", config.DISCORD_CLIENT_SECRET),
            ("SESSION_SECRET", config.SESSION_SECRET),
        )
        if not value
    ]
    if not config.ROLE_ADMIN_ID:
        missing.append("ROLE_ADMIN_ID")
    if missing:
        logger.error("WEB_ENABLED=true, aber es fehlen: %s. Webpanel bleibt deaktiviert.", ", ".join(missing))
        return

    app = _build_app(bot, force_resync_commands, sync_followers)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.WEB_PORT)
    await site.start()
    logger.info("Webpanel laeuft auf %s (Port %s)", config.WEB_BASE_URL, config.WEB_PORT)

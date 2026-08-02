#!/usr/bin/env python3
"""Einmaliges Setup-Skript fuer die Twitch-Anmeldung.

Twitch verlangt fuer Followerzahlen einen User-Access-Token des Broadcasters
mit dem Scope 'moderator:read:followers' - ein einfacher App-Token reicht
nicht. Dieses Skript fuehrt einmalig den Login im Browser durch und erzeugt
einen Refresh-Token, den der Bot danach dauerhaft selbststaendig erneuert.

Ablauf:
1. TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET in die .env eintragen
   (siehe README fuer die Twitch-Developer-Console).
2. In der Twitch-App-Konfiguration muss als OAuth Redirect URL exakt
   http://localhost:17563/callback hinterlegt sein (oder TWITCH_REDIRECT_URI
   in der .env entsprechend anpassen).
3. python twitch_auth.py ausfuehren, im Browser mit dem Streamer-Account
   einloggen und bestaetigen.
4. Den ausgegebenen TWITCH_REFRESH_TOKEN in die .env eintragen.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import config

SCOPE = "moderator:read:followers"
AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_received_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # Server-Log unterdruecken
        pass

    def do_GET(self):
        global _received_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error_description", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if code:
            _received_code = code
            self.wfile.write("<h1>Erfolgreich! Du kannst dieses Fenster jetzt schliessen.</h1>".encode("utf-8"))
        else:
            self.wfile.write(f"<h1>Fehler: {error or 'unbekannt'}</h1>".encode("utf-8"))


def main() -> None:
    if not config.TWITCH.client_id or not config.TWITCH.client_secret:
        raise SystemExit("TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET muessen in der .env gesetzt sein.")

    redirect_uri = config.TWITCH_REDIRECT_URI
    parsed_redirect = urllib.parse.urlparse(redirect_uri)
    port = parsed_redirect.port or 17563

    auth_params = {
        "client_id": config.TWITCH.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(auth_params)}"

    print("Oeffne folgende URL im Browser und logge dich mit dem Streamer-Account ein:")
    print(auth_url)
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", port), _CallbackHandler)
    print(f"\nWarte auf Login (lausche auf {redirect_uri}) ...")
    while _received_code is None:
        server.handle_request()

    print("Code erhalten, tausche gegen Token ein ...")
    token_data = urllib.parse.urlencode(
        {
            "client_id": config.TWITCH.client_id,
            "client_secret": config.TWITCH.client_secret,
            "code": _received_code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")

    req = urllib.request.Request(TOKEN_URL, data=token_data, method="POST")
    with urllib.request.urlopen(req) as resp:
        payload = json.load(resp)

    refresh_token = payload["refresh_token"]
    print("\nFertig! Trage folgende Zeile in deine .env ein:\n")
    print(f"TWITCH_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()

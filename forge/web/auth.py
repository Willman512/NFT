"""Keeping strangers out once the office is reachable from more than this machine.

While the office is bound to loopback the only thing that can reach it is
you, and asking for a password would be theatre. The moment it binds to an
address other people can route to, that stops being true — and this office
can be talked to, spend from a budget and see a wallet. So the rule is
simple and not configurable: off localhost, a token is required.

The token goes in the link, because the realistic way you open this on a
phone is by tapping something. The first request exchanges it for an
HttpOnly cookie and redirects to a clean URL, so it stops appearing in the
address bar, in screenshots, and in the browser history of whoever you hand
the phone to.
"""

from __future__ import annotations

import ipaddress
import secrets
from functools import wraps

from flask import Response, current_app, redirect, request

from ..core.log import get_logger

log = get_logger("auth")

COOKIE = "forge_session"
HEADER = "X-Forge-Token"
QUERY = "t"
#: A month. Long enough that a phone stays signed in, short enough to expire.
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
TOKEN_STATE_KEY = "office:token"
#: Paths that must answer before a token can possibly be known.
OPEN_PATHS = {"/healthz", "/manifest.webmanifest", "/sw.js"}


def is_loopback(host: str) -> bool:
    """Is this address only reachable from this machine?"""
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname we cannot resolve to an address is not assumed to be safe.
        return False


def new_token() -> str:
    return secrets.token_urlsafe(24)


def resolve_token(cfg, ledger) -> str | None:
    """The token this office should accept, minting one if it needs to.

    A token set in the config or the environment wins. Otherwise, once the
    office is exposed, one is generated and kept in the database so the
    link stays the same across restarts.
    """
    configured = (cfg.office.token or "").strip()
    if configured:
        return configured
    if is_loopback(cfg.office.host):
        return None
    stored = ledger.get_state(TOKEN_STATE_KEY)
    if not stored:
        stored = new_token()
        ledger.set_state(TOKEN_STATE_KEY, stored)
        log.info("generated an access token for this office")
    return stored


def matches(supplied: str | None, expected: str) -> bool:
    """Compare without leaking how much of the token was right."""
    if not supplied:
        return False
    return secrets.compare_digest(str(supplied), expected)


def install(app, token: str | None) -> None:
    """Put the door on. With no token, the office stays open as before."""
    app.config["FORGE_TOKEN"] = token
    if not token:
        return

    @app.before_request
    def _check():  # type: ignore[unused-ignore]
        if request.path in OPEN_PATHS:
            return None
        expected = current_app.config["FORGE_TOKEN"]

        # A link with the token in it: swap it for a cookie and clean the URL.
        supplied = request.args.get(QUERY)
        if matches(supplied, expected):
            target = request.path
            other = {k: v for k, v in request.args.items(multi=True) if k != QUERY}
            if other:
                from urllib.parse import urlencode

                target += "?" + urlencode(other)
            response = redirect(target, code=302)
            response.set_cookie(
                COOKIE, expected,
                max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax", path="/",
            )
            return response

        if matches(request.cookies.get(COOKIE), expected):
            return None
        if matches(request.headers.get(HEADER), expected):
            return None

        log.warning("refused a request to %s from %s", request.path, request.remote_addr)
        if request.path.startswith("/api/"):
            return {"error": "not signed in"}, 401
        return Response(_locked_page(), status=401, mimetype="text/html")


def lan_address() -> str | None:
    """This machine's address on its own network, for the link you send yourself.

    Opens a UDP socket toward a routable address to find out which local
    interface would be used. Nothing is sent and nothing is connected to.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1: reserved, never routed
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def link(host: str, port: int, token: str | None) -> str:
    """The URL to actually open, token and all."""
    if host in {"0.0.0.0", "::"}:
        shown = lan_address() or "localhost"
    elif is_loopback(host):
        shown = "localhost"
    else:
        shown = host
    base = f"http://{shown}:{port}/"
    return f"{base}?{QUERY}={token}" if token else base


def _locked_page() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forge — locked</title>
<style>
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:#0e1014; color:#e8ecf3; text-align:center; padding:24px;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .card { max-width:30rem; }
  h1 { font-size:1.15rem; font-weight:600; margin:0 0 .6rem; }
  p { color:#98a2b3; font-size:.9rem; line-height:1.6; margin:0 0 .5rem; }
  code { background:#161a21; border:1px solid #232833; border-radius:6px;
         padding:.15rem .4rem; font-size:.82rem; }
</style></head>
<body><div class="card">
  <h1>This office is locked</h1>
  <p>Open it with the link the office printed when it started — the one with
     <code>?t=</code> on the end.</p>
  <p>Lost it? Run <code>forge office --show-link</code> on the machine it is
     running on.</p>
</div></body></html>"""

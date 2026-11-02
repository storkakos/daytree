"""Access control: the internal and the public side, the exposure modes, CSRF and the ICS token."""
import hmac
import os
from urllib.parse import urlparse

from flask import abort, request

from daytree.core import settings

PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8081"))   # port the Cloudflare tunnel points to


def is_public():
    """True if the request arrived on the public port (tunnel) or carries Cloudflare headers."""
    try:
        port = int(request.environ.get("SERVER_PORT", 0))
    except ValueError:
        port = 0
    return port == PUBLIC_PORT or "Cf-Ray" in request.headers or "Cf-Connecting-Ip" in request.headers


def guard():
    """Runs before every request."""
    # CSRF protection: state-changing requests are only accepted from this site itself
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if not origin or urlparse(origin).netloc != request.host:
            abort(403)

    if not is_public() or request.path == "/ping":
        return                                   # /ping stays reachable in every mode (uptime monitors)
    mode = settings["public_mode"]
    if mode == "off":
        abort(404)
    if request.path == "/calendar.ics":
        return                                   # token is checked in the route
    if mode == "ics":
        abort(404)                               # only the ICS file is public
    # mode "dashboard": read-only - no settings, no tasks, no triggering syncs
    if request.method != "GET" or request.path.startswith(("/settings", "/api/tasks")):
        abort(404)


def token_ok(val):
    return hmac.compare_digest(val or "", settings["token"])

"""Access control: the internal and the public side, the exposure modes, CSRF, API and ICS tokens, the settings padlock."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import date
from urllib.parse import urlparse

from flask import Response, abort, render_template, request

from daytree.core import needs_setup, save_settings, settings

PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8081"))   # port the Cloudflare tunnel points to
SETTINGS_LOCK = os.environ.get("SETTINGS_LOCK", "false").strip().lower() in ("1", "true", "yes", "on")
LOCKED_PATHS = ("/settings", "/sync", "/api/background/refresh")
SETUP_PATHS = ("/settings/login", "/settings/save")         # the first-time setup on the homepage
LOCK_COOKIE = "daytree_unlock"
LOCK_IDLE = 30 * 60                 # seconds without a request until the padlock closes by itself
MAX_FAILURES, LOCKOUT_SECONDS = 5, 60
SCRYPT = (2 ** 14, 8, 1)            # n, r, p

_sessions = {}                      # unlock cookie -> time of its last request; lost on restart, which locks
_failures = {"count": 0, "until": 0.0}
_lock = threading.Lock()


def is_public():
    """True if the request arrived on the public port (tunnel) or carries Cloudflare headers."""
    try:
        port = int(request.environ.get("SERVER_PORT", 0))
    except ValueError:
        port = 0
    return port == PUBLIC_PORT or "Cf-Ray" in request.headers or "Cf-Connecting-Ip" in request.headers


def guard():
    """Runs before every request."""
    public = is_public() and request.path != "/ping"   # /ping stays reachable in every mode (uptime monitors)
    if public:
        _public_rules()
    token = api_token()
    if token is False:
        abort(Response("Invalid API token\n", 401, {"WWW-Authenticate": "Bearer"}))
    if request.method not in ("GET", "HEAD"):
        if token and not token["write"]:
            abort(403)                           # a read-only token
        if not token:
            _same_site()
    if not public:
        return padlock()


def _public_rules():
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


def _same_site():
    """CSRF protection: state-changing requests without an API token are only accepted from this site itself."""
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin or urlparse(origin).netloc != request.host:
        abort(403)


def token_ok(val):
    return hmac.compare_digest(val or "", settings["token"])


# ---------------------------------------------------------------- settings padlock
def padlock():
    """With SETTINGS_LOCK on, the settings need the password. The settings page shows the padlock instead."""
    if not SETTINGS_LOCK or not request.path.startswith(LOCKED_PATHS) or unlocked():
        return None
    if request.path == "/settings/unlock" or (needs_setup() and request.path in SETUP_PATHS):
        return None
    if request.method in ("GET", "HEAD") and request.path == "/settings":
        return render_template("locked.html", first=not settings["lock_hash"])
    abort(403)


def unlocked():
    sid = request.cookies.get(LOCK_COOKIE, "")
    now = time.time()
    with _lock:
        seen = _sessions.get(sid)
        if seen is None or now - seen > LOCK_IDLE:
            _sessions.pop(sid, None)
            return False
        _sessions[sid] = now
        return True


def open_session(response):
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for old in [s for s, seen in _sessions.items() if now - seen > LOCK_IDLE]:
            del _sessions[old]
        _sessions[sid] = now
    response.set_cookie(LOCK_COOKIE, sid, httponly=True, samesite="Strict", secure=request.is_secure)


def close_session(response):
    with _lock:
        _sessions.pop(request.cookies.get(LOCK_COOKIE, ""), None)
    response.delete_cookie(LOCK_COOKIE, httponly=True, samesite="Strict")


def close_other_sessions():
    """After a password change only the browser that changed it stays unlocked."""
    with _lock:
        mine = request.cookies.get(LOCK_COOKIE, "")
        for sid in [s for s in _sessions if s != mine]:
            del _sessions[sid]


def hash_password(password):
    n, r, p = SCRYPT
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def password_ok(password):
    try:
        _, n, r, p, salt, digest = settings["lock_hash"].split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


def try_unlock(password):
    """"ok", "wrong" or "blocked". After MAX_FAILURES wrong passwords every try fails for LOCKOUT_SECONDS."""
    with _lock:
        if time.time() < _failures["until"]:
            return "blocked"
    if password_ok(password):
        with _lock:
            _failures["count"] = 0
        return "ok"
    with _lock:
        _failures["count"] += 1
        if _failures["count"] >= MAX_FAILURES:
            _failures.update(count=0, until=time.time() + LOCKOUT_SECONDS)
    return "wrong"


# ---------------------------------------------------------------- API tokens
def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def api_token():
    """The API token of the request: None without one, False if it is not valid. Only the Authorization header of an
    /api/ request counts, never the URL, so tokens do not end up in logs or browser history."""
    header = request.headers.get("Authorization", "")
    if not header or not request.path.startswith("/api/"):
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return False
    digest = token_hash(value.strip())
    token = next((t for t in settings["api_tokens"] if hmac.compare_digest(t["hash"], digest)), None)
    if token is None:
        return False
    today = date.today().isoformat()
    if token["last_used"] != today:              # saved at most once a day per token
        token["last_used"] = today
        save_settings(settings)
    return token

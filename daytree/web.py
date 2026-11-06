"""The Flask app: puts the areas together and starts the background threads.

Run with exactly ONE gunicorn worker: sync state, scheduler and tunnel supervisor live in process memory.
"""
import secrets
import threading
from pathlib import Path

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from daytree import admin, api, auth, display, sync, tunnel
from daytree.core import settings
from daytree.i18n import _, language

ROOT = Path(__file__).resolve().parent.parent      # templates/ and static/ live next to the package

app = Flask(__name__, root_path=str(ROOT))
app.secret_key = secrets.token_hex(16)
# A reverse proxy (HTTPS_URL) or the Cloudflare tunnel says in X-Forwarded-Proto whether the browser used HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
app.config["SESSION_COOKIE_SECURE"] = bool(auth.HTTPS_URL)
for blueprint in (display.bp, api.bp, admin.bp):
    app.register_blueprint(blueprint)
app.before_request(auth.guard)


@app.context_processor
def _inject():
    # "light" or "dark" fixes the look; an empty class follows the device
    theme = settings["appearance"] if settings["appearance"] in ("light", "dark") else ""
    # Tells browsers with their own dark mode which schemes the page has, so they do not recolor it
    scheme = f"only {theme}" if theme else "light dark"
    return {"public": auth.is_public(), "_": _, "lang": language(), "theme": theme, "color_scheme": scheme}


@app.after_request
def no_stale_pages(response):
    # Pages depend on the language and carry the app version, so a stored copy must never be shown
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.after_request
def log_ics_fetch(response):
    # Shows whether calendar services reach the app. The path only: the query string holds the token.
    if request.path in ("/ics", "/calendar.ics") or request.path.startswith("/ics/"):
        agent = request.headers.get("User-Agent", "-")[:120]
        print(f"ICS fetch: {request.method} {response.status_code} public={auth.is_public()} agent={agent}", flush=True)
    return response


def start():
    """Load the last sync state and start the scheduler and the tunnel supervisor."""
    sync.migrate_legacy()
    sync.load_state()
    threading.Thread(target=sync.scheduler, daemon=True).start()
    threading.Thread(target=tunnel.supervisor, daemon=True).start()

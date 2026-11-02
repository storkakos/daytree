"""The Flask app: puts the areas together and starts the background threads.

Run with exactly ONE gunicorn worker: sync state, scheduler and tunnel supervisor live in process memory.
"""
import secrets
import threading
from pathlib import Path

from flask import Flask, request

from daytree import admin, api, auth, display, sync, tunnel
from daytree.i18n import _, language

ROOT = Path(__file__).resolve().parent.parent      # templates/ and static/ live next to the package

app = Flask(__name__, root_path=str(ROOT))
app.secret_key = secrets.token_hex(16)
for blueprint in (display.bp, api.bp, admin.bp):
    app.register_blueprint(blueprint)
app.before_request(auth.guard)


@app.context_processor
def _inject():
    return {"public": auth.is_public(), "_": _, "lang": language()}


@app.after_request
def no_stale_pages(response):
    # Pages depend on the language and carry the app version, so a stored copy must never be shown
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.after_request
def log_ics_fetch(response):
    # Shows whether calendar services reach the app. The path only: the query string holds the token.
    if request.path == "/calendar.ics":
        agent = request.headers.get("User-Agent", "-")[:120]
        print(f"ICS fetch: {request.method} {response.status_code} public={auth.is_public()} agent={agent}", flush=True)
    return response


def start():
    """Load the last sync state and start the scheduler and the tunnel supervisor."""
    sync.load_state()
    threading.Thread(target=sync.scheduler, daemon=True).start()
    threading.Thread(target=tunnel.supervisor, daemon=True).start()

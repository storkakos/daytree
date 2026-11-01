"""TimeTree -> ICS bridge: periodic export, web dashboard, encrypted settings, optional Cloudflare tunnel.

Single-process Flask app (run with exactly ONE gunicorn worker: state, scheduler and tunnel
supervisor live in memory and in background threads).
"""
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import recurring_ical_events
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from icalendar import Calendar

DATA = Path(os.environ.get("DATA_DIR", "/data"))
KEY_FILE = Path(os.environ.get("SECRET_KEY_FILE", "/secrets/secret.key"))
ICS_FILE = DATA / "timetree.ics"
STATE_FILE = DATA / "state.json"
SETTINGS_FILE = DATA / "settings.enc"      # fully encrypted (Fernet)
TASKS_FILE = DATA / "tasks.enc"            # fully encrypted (Fernet)
LEGACY_FILE = DATA / "settings.json"       # old plaintext format -> migrated, then deleted

MIN_MINUTES, MAX_MINUTES, STEP_MINUTES = 30, 1440, 30
PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8081"))   # port the Cloudflare tunnel points to
PUBLIC_MODES = ("off", "ics", "dashboard")
SECRET_KEYS = ("password", "tunnel_token")                 # never passed to templates

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

_lock = threading.Lock()          # one sync at a time
_settings_lock = threading.Lock()
_tasks_lock = threading.Lock()
_next_due = 0.0
_state = {"last_run": None, "last_success": None, "ok": None, "message": "No sync yet", "events": 0}


# ---------------------------------------------------------------- encryption
def _load_key() -> bytes:
    """Key from SECRET_KEY (environment) or the key file; generated on first start.
    The key file deliberately lives outside the data directory (separate volume)."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.strip().encode()
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


FERNET = Fernet(_load_key())


# ---------------------------------------------------------------- settings
def _clamp(v):
    """Limit an interval to the allowed range and snap it to the 30-minute grid."""
    return max(MIN_MINUTES, min(MAX_MINUTES, round(int(v) / STEP_MINUTES) * STEP_MINUTES))


def _hours(minutes):
    return f"{minutes / 60:g} h"


DEFAULTS = {
    "email": "",
    "password": "",
    "calendar_code": "",
    "calendar_name": "",
    "calendars": [],            # [{"name": ..., "code": ...}] from the last login
    "interval_minutes": 180,
    "token": "",                # token embedded in the ICS URL
    "public_mode": "off",       # off | ics | dashboard (applies to traffic via the tunnel / port 8081)
    "public_hostname": "",
    "tunnel_enabled": False,
    "tunnel_token": "",
}


def _write_encrypted(path, value):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(FERNET.encrypt(json.dumps(value, ensure_ascii=False).encode()))
    tmp.replace(path)


def save_settings(s):
    with _settings_lock:
        _write_encrypted(SETTINGS_FILE, s)


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    dirty = False
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(FERNET.decrypt(SETTINGS_FILE.read_bytes())))
        except InvalidToken:
            raise SystemExit(
                "settings.enc cannot be decrypted with the available key. "
                "Restore the matching key, or delete settings.enc and set everything up again."
            )
    elif LEGACY_FILE.exists():
        try:
            s.update(json.loads(LEGACY_FILE.read_text()))
            dirty = True
        except Exception:
            pass
    s["interval_minutes"] = _clamp(s["interval_minutes"])
    if s["public_mode"] not in PUBLIC_MODES:
        s["public_mode"] = "off"
    if not s["token"]:
        s["token"] = secrets.token_urlsafe(24)
        dirty = True
    if dirty:
        save_settings(s)
        LEGACY_FILE.unlink(missing_ok=True)
    return s


settings = load_settings()


def needs_setup():
    return not (settings["email"] and settings["password"] and settings["calendar_code"])


def view():
    """Settings for templates - without secrets (password, tunnel token)."""
    v = {k: val for k, val in settings.items() if k not in SECRET_KEYS}
    v["has_password"] = bool(settings["password"])
    v["has_tunnel_token"] = bool(settings["tunnel_token"])
    v["interval_label"] = _hours(settings["interval_minutes"])
    return v


# ---------------------------------------------------------------- sync state
def _save_state():
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(_state))


def _load_state():
    if STATE_FILE.exists():
        try:
            _state.update(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def count_events():
    try:
        cal = Calendar.from_ical(ICS_FILE.read_bytes())
        return sum(1 for c in cal.walk() if c.name == "VEVENT")
    except Exception:
        return 0


# ---------------------------------------------------------------- TimeTree
def fetch_calendars(email, password):
    """Log in to TimeTree and list the active calendars (same API calls as the exporter uses)."""
    from timetree_exporter.api.auth import login
    from timetree_exporter.api.calendar import TimeTreeCalendar

    session_id = login(email, password)
    if not session_id:
        raise RuntimeError("Login returned no session")
    metas = TimeTreeCalendar(session_id).get_metadata()
    return [
        {"name": m.get("name") or "Unnamed", "code": m["alias_code"]}
        for m in metas
        if m.get("deactivated_at") is None and m.get("alias_code")
    ]


_ALLDAY_RE = re.compile(
    rb"^(DTSTART|DTEND|EXDATE|RDATE|RECURRENCE-ID)(?![^:\r\n]*VALUE=)([^:\r\n]*):(\d{8}(?:,\d{8})*)(?=\r?$)", re.M
)


def fix_ics(raw):
    """Repair defects in the exporter's output: all-day dates lack VALUE=DATE (invalid per RFC 5545), and
    VTIMEZONE only covers the stored start dates, so recurrences miss daylight saving time. Also adds the
    headers calendar subscriptions expect and drops COLOR, which strict RFC 5545 parsers do not know."""
    raw = _ALLDAY_RE.sub(rb"\1\2;VALUE=DATE:\3", raw)
    try:
        cal = Calendar.from_ical(raw)
        cal.subcomponents = [c for c in cal.subcomponents if c.name != "VTIMEZONE"]
        starts = [e.decoded("DTSTART") for e in cal.walk("VEVENT")]
        dates = [s.date() if isinstance(s, datetime) else s for s in starts]
        last = date.today() + timedelta(days=3650)
        cal.add_missing_timezones(min(dates, default=date.today()) - timedelta(days=1), last)
        for tz in cal.walk("VTIMEZONE"):
            tz.pop("COMMENT", None)
        for event in cal.walk("VEVENT"):
            event.pop("COLOR", None)
        cal["CALSCALE"] = "GREGORIAN"
        cal["METHOD"] = "PUBLISH"
        if settings["calendar_name"]:
            cal["X-WR-CALNAME"] = settings["calendar_name"]
        return cal.to_ical()
    except Exception:  # noqa: BLE001
        return raw


def run_sync():
    """Run timetree-exporter; replace the ICS file only on success."""
    if not _lock.acquire(blocking=False):
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = DATA / "timetree.tmp.ics"
        tmp.unlink(missing_ok=True)
        _state["last_run"] = _now()
        s = settings
        if not s["email"] or not s["password"] or not s["calendar_code"]:
            _state.update(ok=False, message="Please configure credentials and calendar under Settings")
            return
        # Credentials go to the child process via its environment only - never argv, never disk.
        env = {**os.environ, "TIMETREE_EMAIL": s["email"], "TIMETREE_PASSWORD": s["password"]}
        env.pop("SECRET_KEY", None)
        try:
            res = subprocess.run(
                ["timetree-exporter", "-c", s["calendar_code"], "-o", str(tmp)],
                env=env, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
            )
            if res.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                tmp.write_bytes(fix_ics(tmp.read_bytes()))
                tmp.replace(ICS_FILE)
                _state.update(ok=True, last_success=_now(), message="OK", events=count_events())
            else:
                err = (res.stderr or res.stdout or "unknown error").strip().splitlines()[-3:]
                _state.update(ok=False, message=" | ".join(err)[:300])
        except subprocess.TimeoutExpired:
            _state.update(ok=False, message="Timed out after 10 minutes")
        except Exception as e:  # noqa: BLE001
            _state.update(ok=False, message=str(e)[:300])
    finally:
        _save_state()
        _lock.release()


def scheduler():
    """Every 10 s check whether the next sync is due; interval changes take effect immediately."""
    global _next_due
    time.sleep(3)
    _next_due = time.time()
    while True:
        if time.time() >= _next_due:
            _next_due = time.time() + settings["interval_minutes"] * 60
            run_sync()
        time.sleep(10)


def _reset_timer():
    global _next_due
    _next_due = time.time() + settings["interval_minutes"] * 60


# ---------------------------------------------------------------- Cloudflare tunnel
_tunnel = {"proc": None, "status": "off", "error": ""}
_tunnel_restart = threading.Event()
_TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9+/_=\-]+")


def _redact(line):
    return _TOKEN_RE.sub("***", line)


def _tunnel_reader(proc):
    for line in proc.stdout:
        if "Registered tunnel connection" in line:
            _tunnel.update(status="connected", error="")
        elif " ERR " in line:
            _tunnel["error"] = _redact(line.strip())[-200:]
    proc.wait()
    if _tunnel["proc"] is proc:
        _tunnel["status"] = "disconnected"


def _stop_tunnel(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


def tunnel_supervisor():
    """Start/stop cloudflared according to the settings. The token is passed via environment only."""
    retry_at = 0.0
    while True:
        try:
            want = bool(settings["tunnel_enabled"] and settings["tunnel_token"])
            proc = _tunnel["proc"]
            running = proc is not None and proc.poll() is None
            if _tunnel_restart.is_set():
                _tunnel_restart.clear()
                if running:
                    _stop_tunnel(proc)
                    running = False
                retry_at = 0.0
            if running and not want:
                _stop_tunnel(proc)
                _tunnel.update(proc=None, status="off", error="")
            elif want and not running and time.time() >= retry_at:
                retry_at = time.time() + 15
                _tunnel.update(status="connecting", error="")
                env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
                env["TUNNEL_TOKEN"] = settings["tunnel_token"]
                try:
                    proc = subprocess.Popen(
                        ["cloudflared", "tunnel", "--no-autoupdate", "run"],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, text=True, bufsize=1,
                    )
                    _tunnel["proc"] = proc
                    threading.Thread(target=_tunnel_reader, args=(proc,), daemon=True).start()
                except FileNotFoundError:
                    _tunnel.update(proc=None, status="error", error="cloudflared is not present in the image")
            elif not want and not running and _tunnel["status"] != "off":
                _tunnel.update(proc=None, status="off", error="")
        except Exception as e:  # noqa: BLE001
            _tunnel.update(status="error", error=str(e)[:200])
        time.sleep(3)


# ---------------------------------------------------------------- access control (internal vs. public)
def is_public():
    """True if the request arrived on the public port (tunnel) or carries Cloudflare headers."""
    try:
        port = int(request.environ.get("SERVER_PORT", 0))
    except ValueError:
        port = 0
    return port == PUBLIC_PORT or "Cf-Ray" in request.headers or "Cf-Connecting-Ip" in request.headers


@app.context_processor
def _inject():
    return {"public": is_public()}


@app.before_request
def guard():
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


@app.after_request
def log_ics_fetch(response):
    # Shows whether calendar services reach the app. The path only: the query string holds the token.
    if request.path == "/calendar.ics":
        agent = request.headers.get("User-Agent", "-")[:120]
        print(f"ICS fetch: {request.method} {response.status_code} public={is_public()} agent={agent}", flush=True)
    return response


def _token_ok(val):
    return hmac.compare_digest(val or "", settings["token"])


def ics_urls():
    path = "/calendar.ics?token=" + settings["token"]
    local = request.host_url.rstrip("/") + path
    pub = f"https://{settings['public_hostname']}{path}" if settings["public_hostname"] else None
    return local, pub


def _local(value):
    """Event time as naive local time of the container; all-day dates are returned unchanged."""
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone().replace(tzinfo=None)
    return value


def occurrences(first, last):
    """Events from first up to last, sorted, with every occurrence of recurring events."""
    if not ICS_FILE.exists():
        return []
    try:
        cal = Calendar.from_ical(ICS_FILE.read_bytes())
        # Expands RRULE/EXDATE, so every occurrence of a recurring event is listed
        events = recurring_ical_events.of(cal).between(first, last)
    except Exception:
        return []
    out = []
    for ev in events:
        start = _local(ev.decoded("DTSTART"))
        end = _local(ev.decoded("DTEND")) if "DTEND" in ev else start
        timed = isinstance(start, datetime)
        out.append({
            "title": str(ev.get("SUMMARY", "(untitled)")),
            "date": start.date() if timed else start,
            "end_date": end.date() if timed else max(start, end - timedelta(days=1)),  # all-day DTEND is exclusive
            "time": start.strftime("%H:%M") if timed else "",
            "end_time": end.strftime("%H:%M") if timed else "",
            "all_day": not timed,
            "sort": start if timed else datetime.combine(start, datetime.min.time()),
        })
    out.sort(key=lambda e: e["sort"])
    return out


# ---------------------------------------------------------------- routes
@app.route("/")
def dashboard():
    return render_template(
        "kiosk.html", s=view(), setup=needs_setup() and not is_public(), default_minutes=DEFAULTS["interval_minutes"],
    )


@app.post("/sync")
def sync_now():
    _reset_timer()
    threading.Thread(target=run_sync, daemon=True).start()
    return redirect(url_for("settings_page"))


@app.get("/settings")
def settings_page():
    local, pub = ics_urls()
    return render_template(
        "settings.html", s=view(), state=_state, running=_lock.locked(),
        next_run=datetime.fromtimestamp(_next_due).strftime("%H:%M") if _next_due else None,
        intervals=[(m, _hours(m)) for m in range(MIN_MINUTES, MAX_MINUTES + 1, STEP_MINUTES)],
        ics_local=local, ics_pub=pub, tunnel=_tunnel, public_port=PUBLIC_PORT,
    )


def _back():
    """Forms of the first-time setup return to the homepage, all others to the settings page."""
    return redirect(url_for("dashboard" if request.form.get("setup") else "settings_page"))


@app.post("/settings/login")
def settings_login():
    """Store credentials and load the calendar list from TimeTree."""
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "") or settings["password"]  # empty = keep stored password
    if not email or not password:
        flash("Enter e-mail and password.", "err")
        return _back()
    try:
        cals = fetch_calendars(email, password)
    except Exception as e:  # noqa: BLE001
        flash(f"Login failed: {str(e)[:200]}", "err")
        return _back()
    settings.update(email=email, password=password, calendars=cals)
    if request.form.get("setup"):
        settings.update(calendar_code="", calendar_name="")              # the setup asks for the calendar next
    elif not any(c["code"] == settings["calendar_code"] for c in cals):
        # previous selection no longer valid -> preselect the first calendar
        settings.update(calendar_code=cals[0]["code"] if cals else "", calendar_name=cals[0]["name"] if cals else "")
    save_settings(settings)
    flash(f"Signed in. {len(cals)} calendars found.", "ok")
    return _back()


@app.post("/settings/save")
def settings_save():
    """Save calendar selection and sync interval."""
    try:
        settings["interval_minutes"] = _clamp(request.form["interval"])
    except (KeyError, ValueError):
        abort(400)
    code = request.form.get("calendar_code", "")
    match = next((c for c in settings["calendars"] if c["code"] == code), None)
    if match:
        settings.update(calendar_code=match["code"], calendar_name=match["name"])
    if request.form.get("new_token"):
        settings["token"] = secrets.token_urlsafe(24)
        flash("New token generated - update the ICS URL in Outlook.", "ok")
    save_settings(settings)
    _reset_timer()
    flash("Settings saved.", "ok")
    if request.form.get("sync_now"):
        threading.Thread(target=run_sync, daemon=True).start()
    return _back()


@app.post("/settings/tunnel")
def settings_tunnel():
    """Cloudflare tunnel and exposure mode."""
    raw = request.form.get("tunnel_token", "").strip()
    if raw:
        m = _TOKEN_RE.search(raw)   # also accepts a pasted "cloudflared ... --token XYZ" command
        if not m:
            flash("Tunnel token not recognized (it normally starts with “eyJ”).", "err")
            return redirect(url_for("settings_page"))
        settings["tunnel_token"] = m.group(0)
    if request.form.get("clear_token"):
        settings["tunnel_token"] = ""

    host = request.form.get("public_hostname", "").strip().lower()
    if host and not re.fullmatch(r"[a-z0-9]([a-z0-9.\-]*[a-z0-9])?", host):
        flash("Invalid hostname.", "err")
        return redirect(url_for("settings_page"))
    settings["public_hostname"] = host

    mode = request.form.get("public_mode", "off")
    settings["public_mode"] = mode if mode in PUBLIC_MODES else "off"

    want = bool(request.form.get("tunnel_enabled"))
    if want and not settings["tunnel_token"]:
        flash("Enter the tunnel token first to enable the tunnel.", "err")
        want = False
    settings["tunnel_enabled"] = want
    save_settings(settings)
    _tunnel_restart.set()
    flash("Sharing settings saved.", "ok")
    return redirect(url_for("settings_page"))


@app.get("/api/events")
def api_events():
    today = date.today()
    events = occurrences(today, today + timedelta(days=8))
    return jsonify(events=[
        {**e, "date": e["date"].isoformat(), "end_date": e["end_date"].isoformat(), "sort": None} for e in events
    ])


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return json.loads(FERNET.decrypt(TASKS_FILE.read_bytes()))


def _task_title():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title or len(title) > 200:
        abort(400)
    return title


@app.get("/api/tasks")
def api_tasks():
    return jsonify(tasks=load_tasks())


@app.post("/api/tasks")
def api_task_add():
    task = {"id": secrets.token_hex(8), "title": _task_title(), "done": False}
    with _tasks_lock:
        _write_encrypted(TASKS_FILE, load_tasks() + [task])
    return jsonify(task), 201


@app.patch("/api/tasks/<task_id>")
def api_task_update(task_id):
    done = bool((request.get_json(silent=True) or {}).get("done"))
    with _tasks_lock:
        tasks = load_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            abort(404)
        task["done"] = done
        _write_encrypted(TASKS_FILE, tasks)
    return jsonify(task)


@app.delete("/api/tasks/<task_id>")
def api_task_delete(task_id):
    with _tasks_lock:
        tasks = load_tasks()
        remaining = [t for t in tasks if t["id"] != task_id]
        if len(remaining) == len(tasks):
            abort(404)
        _write_encrypted(TASKS_FILE, remaining)
    return "", 204


@app.route("/api/status")
def status():
    return jsonify({**_state, "running": _lock.locked(), "tunnel": _tunnel["status"]})


@app.get("/ping")
def ping():
    """Liveness check for uptime monitors. Public in every mode, so it must not reveal anything."""
    return Response("pong\n", mimetype="text/plain", headers={"Cache-Control": "no-store"})


@app.route("/calendar.ics")
def calendar_ics():
    if not _token_ok(request.args.get("token")):
        abort(403)
    if not ICS_FILE.exists():
        abort(404)
    return Response(ICS_FILE.read_bytes(), mimetype="text/calendar",
                    headers={"Content-Disposition": "inline; filename=timetree.ics"})


_load_state()
threading.Thread(target=scheduler, daemon=True).start()
threading.Thread(target=tunnel_supervisor, daemon=True).start()

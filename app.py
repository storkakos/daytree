"""TimeTree -> ICS bridge: periodic export, web dashboard, encrypted settings, optional Cloudflare tunnel.

Single-process Flask app (run with exactly ONE gunicorn worker: state, scheduler and tunnel
supervisor live in memory and in background threads).
"""
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, available_timezones

import recurring_ical_events
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from icalendar import Calendar

from i18n import translate

VERSION = "0.12.0"
DATA = Path(os.environ.get("DATA_DIR", "/data"))
KEY_FILE = Path(os.environ.get("SECRET_KEY_FILE", "/secrets/secret.key"))
ICS_FILE = DATA / "timetree.ics"
STATE_FILE = DATA / "state.json"
COLORS_FILE = DATA / "event_colors.json"   # event UID -> TimeTree color, for the homepage only
SETTINGS_FILE = DATA / "settings.enc"      # fully encrypted (Fernet)
BACKGROUND_FILE = DATA / "background.json"   # current photos, so a restart does not change them
WASTE_FILE = DATA / "waste.enc"            # waste collection calendar, fully encrypted (Fernet)
TASKS_FILE = DATA / "tasks.enc"            # fully encrypted (Fernet)
LEGACY_FILE = DATA / "settings.json"       # old plaintext format -> migrated, then deleted

MIN_MINUTES, MAX_MINUTES, STEP_MINUTES = 30, 1440, 30
PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8081"))   # port the Cloudflare tunnel points to
PUBLIC_MODES = ("off", "ics", "dashboard")
SECRET_KEYS = ("password", "tunnel_token")                 # never passed to templates
LANGUAGES = ("en", "de")
TIMEZONE_REGIONS = ("Africa/", "America/", "Antarctica/", "Asia/", "Atlantic/", "Australia/", "Europe/", "Indian/", "Pacific/", "UTC")
DEFAULT_TZ = os.environ.get("TZ", "")                      # from docker-compose / .env

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
    hours = f"{minutes / 60:g}"
    return f"{hours.replace('.', ',')} Std." if language() == "de" else f"{hours} h"


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
    "language": "auto",         # auto | en | de
    "timezone": "",             # empty = DEFAULT_TZ
    "time_format": "24",        # 24 | 12
    "date_style": "long",       # long | numeric
    "tasks_enabled": True,      # shows the tasks panel on the homepage
    "show_seconds": False,
    "clock_style": "digital",   # digital | analog
    "clock_size": 100,          # percent
    "greeting_enabled": True,
    "greeting_name": "",
    "weather_enabled": False,
    "weather_city": "",
    "weather_lat": None,
    "weather_lon": None,
    "weather_unit": "celsius",  # celsius | fahrenheit
    "weather_forecast": "off",  # off | hours | days
    "waste_enabled": False,
    "waste_colors": {},         # collection name -> #rrggbb
    "waste_day_before": False,  # show a bin the day before the collection instead of on it
    "background_type": "photo",  # photo | color
    "background_color": "#1c2733",
    "background_every": "hour",  # hour | day | fixed | custom
    "background_minutes": 30,   # used by "custom"
    "background_blur": 0,       # pixels
    "background_brightness": 60,  # percent
    "font_family": "system",
    "font_size": 100,           # percent
    "font_weight": 300,
}

SYSTEM_FONT = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif'
FONTS = {"system": None, "inter": "Inter", "nunito": "Nunito", "poppins": "Poppins", "lora": "Lora", "playfair": "Playfair Display"}
WASTE_MAX_BYTES = 2 * 1024 * 1024
# First match in the lowercase collection name; the user can change every color in the settings
WASTE_COLORS = (("bio", "#8b5a2b"), ("papier", "#2f6fde"), ("paper", "#2f6fde"), ("gelb", "#f2c40f"), ("plast", "#f2c40f"),
                ("rest", "#6b7280"), ("glas", "#2e9e5b"), ("glass", "#2e9e5b"))
BACKGROUND_SECONDS = {"hour": 3600, "day": 86400, "fixed": float("inf")}
WEATHER_TEXT = {
    "Clear sky": (0,),
    "Partly cloudy": (1, 2),
    "Overcast": (3,),
    "Fog": (45, 48),
    "Drizzle": (51, 53, 55, 56, 57),
    "Rain": (61, 63, 65, 66, 67, 80, 81, 82),
    "Snow": (71, 73, 75, 77, 85, 86),
    "Thunderstorm": (95, 96, 99),
}
WEATHER_ICONS = {       # names in static/weather-sprites.svg
    "clearsky": (0,), "fewclouds": (1,), "brokenclouds": (2,), "overcastclouds": (3,), "mist": (45, 48),
    "lightrain": (51, 53, 55, 56, 57), "rain": (61, 63, 65, 66, 67), "sunnyrain": (80, 81, 82),
    "snow": (71, 73, 75, 77, 85, 86), "thunderstorm": (95, 96, 99),
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


def effective_timezone():
    name = settings["timezone"] or DEFAULT_TZ or "UTC"
    try:
        ZoneInfo(name)
        return name
    except (KeyError, ValueError, OSError):
        return "UTC"


def apply_timezone():
    """Local time of this process and its child processes follows the configured time zone."""
    os.environ["TZ"] = effective_timezone()
    if hasattr(time, "tzset"):
        time.tzset()


apply_timezone()


def language():
    if settings["language"] in LANGUAGES:
        return settings["language"]
    if request.cookies.get("lang") in LANGUAGES:     # set by the homepage from the browser language
        return request.cookies["lang"]
    return request.accept_languages.best_match(LANGUAGES) or LANGUAGES[0]


def _(text):
    return translate(text, language())


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


def event_colors(raw):
    """TimeTree colors by event UID. The published feed has no COLOR, so the homepage keeps them separately."""
    colors = {}
    for block in raw.replace(b"\r\n ", b"").decode("utf-8", "replace").split("BEGIN:VEVENT")[1:]:
        uid = re.search(r"^UID:(.+?)\r?$", block, re.M)
        color = re.search(r"^COLOR:(#[0-9a-fA-F]{6})\r?$", block, re.M)
        if uid and color:
            colors[uid.group(1)] = color.group(1)
    return colors


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
                raw = tmp.read_bytes()
                COLORS_FILE.write_text(json.dumps(event_colors(raw)))
                tmp.write_bytes(fix_ics(raw))
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
    return {"public": is_public(), "_": _, "lang": language()}


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
    try:
        colors = json.loads(COLORS_FILE.read_text())
    except (OSError, ValueError):
        colors = {}
    out = []
    for ev in events:
        start_raw = ev.decoded("DTSTART")
        end_raw = ev.decoded("DTEND") if "DTEND" in ev else start_raw
        start, end = _local(start_raw), _local(end_raw)
        timed = isinstance(start, datetime)
        out.append({
            "title": str(ev.get("SUMMARY", "(untitled)")),
            "date": start.date() if timed else start,
            "end_date": end.date() if timed else max(start, end - timedelta(days=1)),  # all-day DTEND is exclusive
            "time": start.strftime("%H:%M") if timed else "",
            "end_time": end.strftime("%H:%M") if timed else "",
            "all_day": not timed,
            "color": colors.get(str(ev.get("UID"))),
            "start_ts": int(start_raw.timestamp()) if timed else None,
            "end_ts": int(end_raw.timestamp()) if timed else None,
            "sort": start if timed else datetime.combine(start, datetime.min.time()),
        })
    out.sort(key=lambda e: e["sort"])
    return out


# ---------------------------------------------------------------- routes
def display_config_strings():
    return ("All day", "Nothing planned today", "No tasks", "Delete task", "Starts at {time} · tap to dismiss",
            "Good morning", "Good afternoon", "Good evening", "Good night", "Photo by {name}")


def display_config():
    """Everything the homepage needs to draw itself. Free of secrets: it also reaches the public read-only view."""
    font = FONTS[settings["font_family"]]
    font_url = None
    if font:
        font_url = f"https://fonts.googleapis.com/css2?family={urllib.parse.quote_plus(font)}:wght@300;400;500;600;700&display=swap"
    return {
        "language": language(),
        "languageAuto": settings["language"] == "auto",
        "languages": LANGUAGES,
        "locale": "de-DE" if language() == "de" else "en-GB",
        "hour12": settings["time_format"] == "12",
        "timeZone": effective_timezone(),
        "dateStyle": settings["date_style"],
        "showSeconds": settings["show_seconds"],
        "clockStyle": settings["clock_style"],
        "clockSize": settings["clock_size"],
        "showTasks": settings["tasks_enabled"] and not is_public(),
        "greeting": settings["greeting_enabled"],
        "name": "" if is_public() else settings["greeting_name"],
        "weather": settings["weather_enabled"],
        "forecast": settings["weather_forecast"],
        "waste": settings["waste_enabled"],
        "wasteStamp": waste_stamp(),
        "backgroundType": settings["background_type"],
        "backgroundColor": settings["background_color"],
        "blur": settings["background_blur"],
        "brightness": settings["background_brightness"],
        "fontFamily": f'"{font}",{SYSTEM_FONT}' if font else SYSTEM_FONT,
        "fontUrl": font_url,
        "fontSize": settings["font_size"],
        "fontWeight": settings["font_weight"],
        "text": {t: _(t) for t in display_config_strings()},
        "version": VERSION,
    }


@app.route("/")
def dashboard():
    return render_template(
        "kiosk.html", s=view(), setup=needs_setup() and not is_public(), default_minutes=DEFAULTS["interval_minutes"],
        show_tasks=settings["tasks_enabled"] and not is_public(), config=display_config(), read_only=is_public(),
    )


@app.get("/kiosk")
def kiosk():
    """The homepage for a fixed screen: tasks can be read but not changed, and there is no way into the settings."""
    return render_template(
        "kiosk.html", s=view(), setup=False, show_tasks=settings["tasks_enabled"] and not is_public(),
        config=display_config(), read_only=True,
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
        timezones=sorted(z for z in available_timezones() if z.startswith(TIMEZONE_REGIONS)),
        default_timezone=DEFAULT_TZ or "UTC", fonts=FONTS, version=VERSION,
        waste=[(title, waste_color(title)) for title in waste_types()],
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
        flash(_("Enter e-mail and password."), "err")
        return _back()
    try:
        cals = fetch_calendars(email, password)
    except Exception as e:  # noqa: BLE001
        flash(_("Login failed: {error}").format(error=str(e)[:200]), "err")
        return _back()
    settings.update(email=email, password=password, calendars=cals)
    if request.form.get("setup"):
        settings.update(calendar_code="", calendar_name="")              # the setup asks for the calendar next
    elif not any(c["code"] == settings["calendar_code"] for c in cals):
        # previous selection no longer valid -> preselect the first calendar
        settings.update(calendar_code=cals[0]["code"] if cals else "", calendar_name=cals[0]["name"] if cals else "")
    save_settings(settings)
    flash(_("Signed in. {count} calendars found.").format(count=len(cals)), "ok")
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
        flash(_("New token generated. Update the ICS URL in your calendar apps."), "ok")
    save_settings(settings)
    _reset_timer()
    flash(_("Settings saved."), "ok")
    if request.form.get("sync_now"):
        threading.Thread(target=run_sync, daemon=True).start()
    return _back()


def _number(name, low, high):
    try:
        return max(low, min(high, int(request.form.get(name, settings[name]))))
    except ValueError:
        abort(400)


def _choice(name, allowed):
    value = request.form.get(name, settings[name])
    if value not in allowed:
        abort(400)
    return value


def geocode(city):
    """First match of a place name as (lat, lon, label), or None."""
    query = urllib.parse.urlencode({"name": city, "count": 1, "language": language()})
    results = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}").get("results")
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"], results[0]["name"]


@app.post("/settings/display")
def settings_display():
    """Homepage appearance: language, time and date, greeting, weather, background and font."""
    zone = request.form.get("timezone", settings["timezone"])
    color = request.form.get("background_color", settings["background_color"])
    if (zone and zone not in available_timezones()) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        abort(400)
    new = {
        "timezone": zone, "background_color": color,
        "language": _choice("language", ("auto", *LANGUAGES)),
        "time_format": _choice("time_format", ("24", "12")),
        "date_style": _choice("date_style", ("long", "numeric")),
        "clock_style": _choice("clock_style", ("digital", "analog")),
        "weather_unit": _choice("weather_unit", ("celsius", "fahrenheit")),
        "weather_forecast": _choice("weather_forecast", ("off", "hours", "days")),
        "background_type": _choice("background_type", ("photo", "color")),
        "background_every": _choice("background_every", (*BACKGROUND_SECONDS, "custom")),
        "background_minutes": _number("background_minutes", 1, 1440),
        "font_family": _choice("font_family", tuple(FONTS)),
        "clock_size": _number("clock_size", 50, 200),
        "background_blur": _number("background_blur", 0, 50),
        "background_brightness": _number("background_brightness", 20, 100),
        "font_size": _number("font_size", 70, 150),
        "font_weight": _number("font_weight", 300, 700),
        "tasks_enabled": bool(request.form.get("tasks_enabled")),
        "show_seconds": bool(request.form.get("show_seconds")),
        "greeting_enabled": bool(request.form.get("greeting_enabled")),
        "weather_enabled": bool(request.form.get("weather_enabled")),
        "greeting_name": request.form.get("greeting_name", settings["greeting_name"]).strip()[:40],
    }
    message = _("Settings saved.")
    city = request.form.get("weather_city", settings["weather_city"]).strip()[:80]
    if city != settings["weather_city"]:
        new.update(weather_city=city, weather_lat=None, weather_lon=None)
        _weather.clear()
        if city:
            try:
                place = geocode(city)
            except Exception:  # noqa: BLE001
                place = None
            if place:
                new.update(weather_lat=place[0], weather_lon=place[1], weather_city=place[2])
            else:
                message = _("Place not found.")
    if settings["weather_unit"] != new["weather_unit"]:
        _weather.clear()
    settings.update(new)
    save_settings(settings)
    apply_timezone()
    if request.headers.get("X-Autosave"):
        return jsonify(message=message, weather_city=settings["weather_city"])
    flash(message, "ok")
    return redirect(url_for("settings_page"))


def waste_stamp():
    """Changes with the file, colors and timing, so open homepages know when to redraw the bins. Reveals neither."""
    modified = WASTE_FILE.stat().st_mtime if WASTE_FILE.exists() else 0
    state = f"{modified}{sorted(settings['waste_colors'].items())}{settings['waste_day_before']}"
    return hashlib.sha256(state.encode()).hexdigest()[:12]


def waste_calendar():
    try:
        return Calendar.from_ical(json.loads(FERNET.decrypt(WASTE_FILE.read_bytes()))["ics"])
    except (OSError, InvalidToken, ValueError, KeyError):
        return None


def waste_types(calendar=None):
    calendar = calendar or waste_calendar()
    return sorted({str(event.get("SUMMARY", "")).strip() for event in calendar.walk("VEVENT")} - {""}) if calendar else []


def waste_color(title):
    if title in settings["waste_colors"]:
        return settings["waste_colors"][title]
    return next((color for word, color in WASTE_COLORS if word in title.lower()), "#9ca3af")


def waste_days():
    """The bins to show on today and the 7 days after it, one entry per day."""
    today = date.today()
    early = timedelta(days=1 if settings["waste_day_before"] else 0)
    days = {today + timedelta(days=i): [] for i in range(8)}
    calendar = waste_calendar()
    for event in recurring_ical_events.of(calendar).between(today, today + timedelta(days=8) + early) if calendar else []:
        start = _local(event.decoded("DTSTART"))
        day = (start.date() if isinstance(start, datetime) else start) - early
        title = str(event.get("SUMMARY", "")).strip()
        if day in days and title and title not in [item["title"] for item in days[day]]:
            days[day].append({"title": title, "color": waste_color(title)})
    return [{"date": day.isoformat(), "bins": bins} for day, bins in days.items()]


@app.post("/settings/waste")
def settings_waste():
    """Waste collection calendar: upload, remove, colors and whether the homepage shows it."""
    upload = request.files.get("waste_file")
    enabled = bool(request.form.get("waste_enabled"))
    if request.form.get("remove"):
        WASTE_FILE.unlink(missing_ok=True)
        settings["waste_colors"] = {}
        settings["waste_day_before"] = False
        enabled = False
    elif upload and upload.filename:
        raw = upload.read(WASTE_MAX_BYTES + 1)
        if len(raw) > WASTE_MAX_BYTES:
            flash(_("The file is too large."), "err")
            return redirect(url_for("settings_page"))
        text = raw.decode("utf-8-sig", "replace")
        try:
            valid = bool(waste_types(Calendar.from_ical(text)))
        except ValueError:
            valid = False
        if not valid:
            flash(_("The file is not a usable calendar."), "err")
            return redirect(url_for("settings_page"))
        _write_encrypted(WASTE_FILE, {"ics": text})
        enabled = True
    for index, title in enumerate(waste_types()):
        color = request.form.get(f"waste_color_{index}", "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            settings["waste_colors"][title] = color
    settings["waste_enabled"] = enabled
    if not request.form.get("remove"):
        settings["waste_day_before"] = bool(request.form.get("waste_day_before"))
    save_settings(settings)
    if request.headers.get("X-Autosave"):
        return jsonify(message=_("Settings saved."))
    flash(_("Settings saved."), "ok")
    return redirect(url_for("settings_page", updated=1))


@app.post("/settings/tunnel")
def settings_tunnel():
    """Cloudflare tunnel and exposure mode."""
    raw = request.form.get("tunnel_token", "").strip()
    if raw:
        m = _TOKEN_RE.search(raw)   # also accepts a pasted "cloudflared ... --token XYZ" command
        if not m:
            flash(_("Tunnel token not recognized (it normally starts with “eyJ”)."), "err")
            return redirect(url_for("settings_page"))
        settings["tunnel_token"] = m.group(0)
    if request.form.get("clear_token"):
        settings["tunnel_token"] = ""

    host = request.form.get("public_hostname", "").strip().lower()
    if host and not re.fullmatch(r"[a-z0-9]([a-z0-9.\-]*[a-z0-9])?", host):
        flash(_("Invalid hostname."), "err")
        return redirect(url_for("settings_page"))
    settings["public_hostname"] = host

    mode = request.form.get("public_mode", "off")
    settings["public_mode"] = mode if mode in PUBLIC_MODES else "off"

    want = bool(request.form.get("tunnel_enabled"))
    if want and not settings["tunnel_token"]:
        flash(_("Enter the tunnel token first to enable the tunnel."), "err")
        want = False
    settings["tunnel_enabled"] = want
    save_settings(settings)
    _tunnel_restart.set()
    flash(_("Sharing settings saved."), "ok")
    return redirect(url_for("settings_page"))


@app.get("/api/events")
def api_events():
    today = date.today()
    events = occurrences(today, today + timedelta(days=8))
    return jsonify(events=[
        {**e, "date": e["date"].isoformat(), "end_date": e["end_date"].isoformat(), "sort": None} for e in events
    ])


def fetch_json(url, headers=None):
    """External lookups stay short: the homepage keeps its last values if a service is unreachable."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=8) as response:
        return json.loads(response.read())


def background_seconds():
    every = settings["background_every"]
    return settings["background_minutes"] * 60 if every == "custom" else BACKGROUND_SECONDS[every]


def _load_backgrounds():
    try:
        return json.loads(BACKGROUND_FILE.read_text())
    except (OSError, ValueError):
        return {}


_backgrounds = _load_backgrounds()      # orientation -> {"url", "author", "link", "location", "fetched"}
_weather = {}


def _picsum_photo(orientation):
    photos = fetch_json(f"https://picsum.photos/v2/list?page={random.randint(1, 30)}&limit=30")
    wide = orientation == "landscape"
    matching = [p for p in photos if (p["width"] >= p["height"]) == wide] or photos
    photo = random.choice(matching)
    size = "2560/1600" if wide else "1600/2560"
    return {"url": f"https://picsum.photos/id/{photo['id']}/{size}", "author": photo["author"], "link": photo["url"], "location": ""}


@app.get("/api/background")
def api_background():
    orientation = "portrait" if request.args.get("orientation") == "portrait" else "landscape"
    entry = _backgrounds.get(orientation)
    if not entry or time.time() - entry["fetched"] >= background_seconds():
        try:
            entry = {**_picsum_photo(orientation), "fetched": time.time()}
            _backgrounds[orientation] = entry
            BACKGROUND_FILE.write_text(json.dumps(_backgrounds))
        except Exception:  # noqa: BLE001
            pass
    if not entry:
        return jsonify(url=None)
    return jsonify({k: entry[k] for k in ("url", "author", "link", "location")})


@app.post("/api/background/refresh")
def api_background_refresh():
    _backgrounds.clear()
    BACKGROUND_FILE.unlink(missing_ok=True)
    return "", 204


def weather_icon(code, day=True):
    name = next((icon for icon, codes in WEATHER_ICONS.items() if code in codes), "overcastclouds")
    if name == "sunnyrain" and not day:
        name = "rain"                # the sprite has no night version of this one
    return f"weather-{'day' if day else 'night'}-{name}"


def forecast(data):
    """The next 10 hours and 7 days from an Open-Meteo answer. Times stay local to the place."""
    now = data["current"].get("time", "")[:13]
    hourly, daily = data.get("hourly", {}), data.get("daily", {})
    hours = [
        {"time": t, "temperature": round(value), "icon": weather_icon(code, bool(day))}
        for t, value, code, day in zip(hourly.get("time", []), hourly.get("temperature_2m", []), hourly.get("weather_code", []),
                                       hourly.get("is_day", [1] * len(hourly.get("time", []))))
        if t[:13] >= now and value is not None
    ]
    days = [
        {"date": d, "high": round(high), "low": round(low), "icon": weather_icon(code)}
        for d, high, low, code in zip(daily.get("time", []), daily.get("temperature_2m_max", []),
                                      daily.get("temperature_2m_min", []), daily.get("weather_code", []))
        if high is not None and low is not None
    ]
    return {"hours": hours[:10], "days": days[:7]}


@app.get("/api/weather")
def api_weather():
    lat, lon = settings["weather_lat"], settings["weather_lon"]
    if not settings["weather_enabled"] or lat is None:
        return jsonify(enabled=False)
    if time.time() >= _weather.get("expires", 0):
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,is_day",
            "hourly": "temperature_2m,weather_code,is_day", "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": 8,
            "temperature_unit": settings["weather_unit"], "timezone": "auto",
        })
        try:
            data = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}")
            current = data["current"]
            _weather.update(temperature=round(current["temperature_2m"]), code=current["weather_code"], expires=time.time() + 1800,
                            day=bool(current.get("is_day", 1)), **forecast(data))
        except Exception:  # noqa: BLE001
            if "code" not in _weather:
                return jsonify(enabled=False)
    text = next((t for t, codes in WEATHER_TEXT.items() if _weather["code"] in codes), "Overcast")
    return jsonify(enabled=True, temperature=_weather["temperature"], text=_(text), city=settings["weather_city"],
                   icon=weather_icon(_weather["code"], _weather["day"]),
                   hours=_weather["hours"], days=_weather["days"])


@app.get("/api/waste")
def api_waste():
    return jsonify(enabled=True, days=waste_days()) if settings["waste_enabled"] else jsonify(enabled=False)


@app.get("/api/config")
def api_config():
    return jsonify(display_config())


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

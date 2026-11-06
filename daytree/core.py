"""Storage: data paths, the encryption key, encrypted files, the settings and the time zone. No Flask in here."""
import json
import os
import secrets
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken

DATA = Path(os.environ.get("DATA_DIR", "/data"))
KEY_FILE = Path(os.environ.get("SECRET_KEY_FILE", "/secrets/secret.key"))
CALENDAR_DIR = DATA / "calendars"          # one fully encrypted file per calendar: its ICS and the TimeTree colors
LEGACY_ICS = DATA / "timetree.ics"         # up to 0.26: the one calendar, unencrypted -> migrated, then deleted
LEGACY_COLORS = DATA / "event_colors.json"
STATE_FILE = DATA / "state.json"
SETTINGS_FILE = DATA / "settings.enc"      # fully encrypted (Fernet)
BACKGROUND_FILE = DATA / "background.json"   # current photos, so a restart does not change them
WASTE_FILE = DATA / "waste.enc"            # waste collection calendar, fully encrypted (Fernet)
TASKS_FILE = DATA / "tasks.enc"            # fully encrypted (Fernet)
LEGACY_FILE = DATA / "settings.json"       # old plaintext format -> migrated, then deleted

MIN_MINUTES, MAX_MINUTES, STEP_MINUTES = 30, 1440, 30
PUBLIC_AREAS = ("ics", "homepage", "api")                  # what the tunnel may reach, chosen in the settings
SECRET_KEYS = ("password", "tunnel_token", "lock_hash", "api_tokens", "notify_url")   # never passed to templates as they are
LANGUAGES = ("en", "de")
DEFAULT_TZ = os.environ.get("TZ", "")                      # from docker-compose / .env

_settings_lock = threading.Lock()


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


def write_encrypted(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(FERNET.encrypt(json.dumps(value, ensure_ascii=False).encode()))
    tmp.replace(path)


def read_encrypted(path):
    return json.loads(FERNET.decrypt(path.read_bytes()))


# ---------------------------------------------------------------- settings
def clamp(v):
    """Limit an interval to the allowed range and snap it to the 30-minute grid."""
    return max(MIN_MINUTES, min(MAX_MINUTES, round(int(v) / STEP_MINUTES) * STEP_MINUTES))


DEFAULTS = {
    "email": "",
    "password": "",
    "calendars": [],            # [{"name": ..., "code": ...}] TimeTree offers, from the last login
    "sources": [],              # the calendars Daytree syncs, shows and serves; see new_source()
    "calendar_tags": False,     # shows the calendar name at the end of each event on the homepage
    "interval_minutes": 180,
    "token": "",                # token embedded in the ICS URL
    "public_areas": [],         # subset of PUBLIC_AREAS for requests through the tunnel; empty = only /ping
    "public_hostname": "",
    "tunnel_enabled": False,
    "tunnel_token": "",
    "lock_hash": "",            # scrypt hash of the settings padlock password
    "notify_url": "",           # ntfy topic or webhook for sync failures; the URL itself is the secret
    "notify_format": "ntfy",    # ntfy | json
    "api_tokens": [],           # [{"id", "name", "hash" (SHA-256), "write", "created", "last_used"}]
    "language": "auto",         # auto | en | de
    "appearance": "auto",       # auto | light | dark, for the settings pages and the setup popup
    "timezone": "",             # empty = DEFAULT_TZ
    "time_format": "24",        # 24 | 12
    "date_style": "long",       # long | numeric
    "tasks_enabled": True,      # shows the tasks panel on the homepage
    "upcoming_days": 7,         # days after today in the "Next days" list, 3 to 14
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


def save_settings(s):
    with _settings_lock:
        write_encrypted(SETTINGS_FILE, s)


def load_settings():
    stored, dirty = {}, False
    if SETTINGS_FILE.exists():
        try:
            stored = read_encrypted(SETTINGS_FILE)
        except InvalidToken:
            raise SystemExit(
                "settings.enc cannot be decrypted with the available key. "
                "Restore the matching key, or delete settings.enc and set everything up again."
            )
    elif LEGACY_FILE.exists():
        try:
            stored = json.loads(LEGACY_FILE.read_text())
            dirty = True
        except Exception:
            pass
    s, changed = normalize(stored)
    if dirty or changed:
        save_settings(s)
        LEGACY_FILE.unlink(missing_ok=True)
    return s


def normalize(stored):
    """Stored settings with defaults for missing keys and old formats brought up to date. Also used for backups.
    Returns the settings and whether they differ from what was stored."""
    s = json.loads(json.dumps(DEFAULTS))
    s.update(stored)
    dirty = False
    s["interval_minutes"] = clamp(s["interval_minutes"])
    if "public_mode" in s:      # up to 0.15: one mode instead of areas
        s["public_areas"] = {"ics": ["ics"], "dashboard": ["ics", "homepage"]}.get(s.pop("public_mode"), [])
        dirty = True
    s["public_areas"] = [a for a in PUBLIC_AREAS if a in s["public_areas"]]
    if not s["token"]:
        s["token"] = secrets.token_urlsafe(24)
        dirty = True
    code, name = s.pop("calendar_code", ""), s.pop("calendar_name", "")
    if code and not s["sources"]:   # up to 0.26: one TimeTree calendar
        s["sources"] = [new_source("timetree", name or code, code)]
        dirty = True
    for source in s["sources"]:
        for key, value in SOURCE_DEFAULTS.items():
            source.setdefault(key, value)
        if not source.get("token"):
            source["token"] = secrets.token_urlsafe(24)
            dirty = True
    return s, dirty


SOURCE_DEFAULTS = {"color": "", "feed": True, "homepage": True}


def new_source(kind, name, source_id=None):
    """A calendar Daytree syncs. TimeTree calendars keep their code as id, so API addresses stay the same.
    color: for events that bring none ("" = the homepage default); feed: part of /ics; homepage: shown there.
    token: for its own feed at /ics/<id>, so sharing one calendar reveals no other."""
    return {"id": source_id or secrets.token_hex(4), "kind": kind, "name": name, "token": secrets.token_urlsafe(24),
            **SOURCE_DEFAULTS}


def calendar_file(source_id):
    return CALENDAR_DIR / f"{source_id}.enc"


def timetree_sources():
    return [s for s in settings["sources"] if s["kind"] == "timetree"]


settings = load_settings()      # changed in place only, so every module shares the same dict


def needs_setup():
    return not (settings["email"] and settings["password"] and timetree_sources())


# ---------------------------------------------------------------- time zone
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

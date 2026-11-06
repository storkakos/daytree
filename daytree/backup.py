"""Password-encrypted backup of the settings, tasks, waste calendar and uploaded calendars, and its restore.

The file does not need the key file: it is encrypted with a key derived from its own password, so it can move an
installation. A restore checks the whole file before it replaces anything.
"""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from icalendar import Calendar

from daytree import VERSION
from daytree.core import (DEFAULTS, SOURCE_ID, TASKS_FILE, WASTE_FILE, apply_timezone, calendar_file, normalize,
                          read_encrypted, save_settings, settings, write_encrypted)

FORMAT = "daytree-backup"
FORMAT_VERSION = 1
SCRYPT = (2 ** 15, 8, 1)            # n, r, p for new backups
MAX_BYTES = 5 * 1024 * 1024


class BackupError(ValueError):
    """The file cannot be restored. The message is shown to the user."""


def _fernet(password, salt, n, r, p):
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * 1024 * 1024)
    return Fernet(base64.urlsafe_b64encode(key))


def export(password):
    """The backup file as bytes: a small readable header and the encrypted content."""
    content = {
        "settings": settings,
        "tasks": read_encrypted(TASKS_FILE) if TASKS_FILE.exists() else [],
        "waste": read_encrypted(WASTE_FILE)["ics"] if WASTE_FILE.exists() else None,
        # TimeTree and URL calendars are fetched again; an uploaded file exists only here
        "calendars": {s["id"]: read_encrypted(calendar_file(s["id"])) for s in settings["sources"]
                      if s["kind"] == "file" and calendar_file(s["id"]).exists()},
    }
    n, r, p = SCRYPT
    salt = secrets.token_bytes(16)
    data = _fernet(password, salt, n, r, p).encrypt(json.dumps(content, ensure_ascii=False).encode())
    return json.dumps({
        "format": FORMAT, "version": FORMAT_VERSION, "app": VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kdf": {"name": "scrypt", "n": n, "r": r, "p": p, "salt": salt.hex()},
        "data": data.decode(),
    }, indent=2).encode()


def read(raw, password):
    """The checked content of a backup file. Raises BackupError and changes nothing if anything is wrong."""
    if len(raw) > MAX_BYTES:
        raise BackupError("The file is too large.")
    try:
        envelope = json.loads(raw)
        kdf = envelope["kdf"]
        n, r, p, salt = int(kdf["n"]), int(kdf["r"]), int(kdf["p"]), bytes.fromhex(kdf["salt"])
        ok = envelope["format"] == FORMAT and envelope["version"] == FORMAT_VERSION and kdf["name"] == "scrypt"
    except (ValueError, KeyError, TypeError):
        ok = False
    # Parameters from the file decide the work for the key, so they must stay within what a real backup uses
    if not ok or not (2 ** 10 <= n <= 2 ** 17 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4 and len(salt) >= 16):
        raise BackupError("This is not a Daytree backup.")
    try:
        content = json.loads(_fernet(password, salt, n, r, p).decrypt(envelope["data"].encode()))
    except (InvalidToken, ValueError, AttributeError):
        raise BackupError("Wrong password, or the file is damaged.")
    return _checked(content)


def _checked(content):
    if not isinstance(content, dict) or not isinstance(content.get("settings"), dict):
        raise BackupError("The backup is incomplete.")
    stored = content["settings"]
    for key, default in DEFAULTS.items():
        if key in stored and not _same_type(stored[key], default):
            raise BackupError("The backup is incomplete.")
    tasks = content.get("tasks", [])
    if not isinstance(tasks, list) or not all(
            isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("title"), str)
            and 0 < len(t["title"]) <= 200 and isinstance(t.get("done"), bool) for t in tasks):
        raise BackupError("The backup is incomplete.")
    waste = content.get("waste")
    if waste is not None:
        try:
            if not isinstance(waste, str) or not Calendar.from_ical(waste).walk("VEVENT"):
                raise ValueError
        except ValueError:
            raise BackupError("The backup is incomplete.")
    calendars = content.get("calendars", {})
    try:
        if not isinstance(calendars, dict):
            raise ValueError
        for source_id, data in calendars.items():
            if (not SOURCE_ID.fullmatch(source_id) or not isinstance(data, dict) or not isinstance(data.get("ics"), str)
                    or not isinstance(data.get("colors", {}), dict) or Calendar.from_ical(data["ics"]).name != "VCALENDAR"):
                raise ValueError
    except ValueError:
        raise BackupError("The backup is incomplete.")
    return {"settings": stored, "tasks": [{"id": t["id"], "title": t["title"], "done": t["done"]} for t in tasks],
            "waste": waste, "calendars": {k: {"ics": v["ics"], "colors": {str(a): str(b) for a, b in v.get("colors", {}).items()}}
                                          for k, v in calendars.items()}}


def _same_type(value, default):
    if default is None:                       # weather coordinates
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


def restore(content):
    """Replace settings, tasks and the waste calendar with checked content from read()."""
    new, _ = normalize(content["settings"])
    settings.clear()
    settings.update(new)
    save_settings(settings)
    write_encrypted(TASKS_FILE, content["tasks"])
    if content["waste"] is None:
        WASTE_FILE.unlink(missing_ok=True)
    else:
        write_encrypted(WASTE_FILE, {"ics": content["waste"]})
    for source in settings["sources"]:
        if source["kind"] == "file" and source["id"] in content["calendars"]:
            write_encrypted(calendar_file(source["id"]), content["calendars"][source["id"]])
    apply_timezone()

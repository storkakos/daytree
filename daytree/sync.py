"""TimeTree sync: the exporter run, the ICS repair, the scheduler and reading events back from the calendars."""
import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone

import recurring_ical_events
import requests
from cryptography.fernet import InvalidToken
from icalendar import Calendar

from daytree import VERSION, notify, pages
from daytree.core import (CALENDAR_DIR, DATA, LEGACY_COLORS, LEGACY_ICS, STATE_FILE, calendar_file, effective_timezone,
                          read_encrypted, settings, timetree_sources, write_encrypted)

MAX_SOURCE_BYTES = 5 * 1024 * 1024
sources_lock = threading.Lock()  # one URL fetch at a time

lock = threading.Lock()          # one sync at a time
next_due = 0.0
state = {"last_run": None, "last_success": None, "ok": None, "message": "No sync yet", "events": 0}


# ---------------------------------------------------------------- sync state
def _save_state():
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def load_state():
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- stored calendars
def load_calendar(source_id):
    """The stored data of one calendar: {"ics": text, "colors": {uid: color}}, or None."""
    try:
        return read_encrypted(calendar_file(source_id))
    except (OSError, InvalidToken, ValueError):
        return None


def save_calendar(source_id, ics, colors=None):
    write_encrypted(calendar_file(source_id), {"ics": ics, "colors": colors or {}})


def prune_calendars():
    """Removes the files of calendars that are no longer selected."""
    keep = {f"{s['id']}.enc" for s in settings["sources"]}
    for path in CALENDAR_DIR.glob("*.enc") if CALENDAR_DIR.exists() else []:
        if path.name not in keep:
            path.unlink(missing_ok=True)
    for source_id in set(state.get("sources", {})) - {s["id"] for s in settings["sources"]}:
        state["sources"].pop(source_id, None)


def migrate_legacy():
    """Up to 0.26 the one calendar lay unencrypted in timetree.ics. It moves into its encrypted file."""
    sources = timetree_sources()
    if LEGACY_ICS.exists() and sources and not calendar_file(sources[0]["id"]).exists():
        try:
            colors = json.loads(LEGACY_COLORS.read_text())
        except (OSError, ValueError):
            colors = {}
        save_calendar(sources[0]["id"], LEGACY_ICS.read_bytes().decode("utf-8", "replace"), colors)
    LEGACY_ICS.unlink(missing_ok=True)
    LEGACY_COLORS.unlink(missing_ok=True)


def count_events():
    total = 0
    for source in settings["sources"]:
        data = load_calendar(source["id"])
        try:
            total += sum(1 for c in Calendar.from_ical(data["ics"]).walk("VEVENT")) if data else 0
        except ValueError:
            pass
    return total


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


def fix_ics(raw, name=""):
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
        if name:
            cal["X-WR-CALNAME"] = name
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


def _export(source, env):
    """Run timetree-exporter for one calendar and store it only on success. Returns None or the error."""
    tmp = DATA / "timetree.tmp.ics"
    tmp.unlink(missing_ok=True)
    try:
        res = subprocess.run(
            ["timetree-exporter", "-c", source["id"], "-o", str(tmp)],
            env=env, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
        )
        if res.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            raw = tmp.read_bytes()
            save_calendar(source["id"], fix_ics(raw, source["name"]).decode("utf-8", "replace"), event_colors(raw))
            return None
        err = (res.stderr or res.stdout or "unknown error").strip().splitlines()[-3:]
        return " | ".join(err)
    except subprocess.TimeoutExpired:
        return "Timed out after 10 minutes"
    except Exception as e:  # noqa: BLE001
        return str(e)
    finally:
        tmp.unlink(missing_ok=True)


def run_sync():
    """Export the selected TimeTree calendars one after the other. A calendar that fails keeps its last data."""
    if not lock.acquire(blocking=False):
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        state["last_run"] = _now()
        s, sources = settings, timetree_sources()
        if not s["email"] or not s["password"] or not sources:
            state.update(ok=False, message="Please configure credentials and calendar under Settings")
            return
        # Credentials go to the child process via its environment only - never argv, never disk.
        env = {**os.environ, "TIMETREE_EMAIL": s["email"], "TIMETREE_PASSWORD": s["password"]}
        env.pop("SECRET_KEY", None)
        errors = []
        for source in sources:
            error = _export(source, env)
            if error:
                errors.append(f"{source['name']}: {error}" if len(sources) > 1 else error)
        prune_calendars()
        if errors:
            state.update(ok=False, message=" | ".join(errors)[:300], events=count_events())
        else:
            state.update(ok=True, last_success=_now(), message="OK", events=count_events())
    finally:
        notify.after_sync(state)
        _save_state()
        lock.release()


# ---------------------------------------------------------------- calendars from files and URLs
def store_source(source, raw):
    """Stores an uploaded or fetched calendar. Raises ValueError if it is not one."""
    try:
        if Calendar.from_ical(raw.decode("utf-8-sig", "replace")).name != "VCALENDAR":
            raise ValueError
    except ValueError:
        raise ValueError("The file is not a usable calendar.")
    save_calendar(source["id"], fix_ics(raw, source["name"]).decode("utf-8", "replace"), event_colors(raw))


def fetch_url(source):
    """Downloads a URL calendar and stores it. Recognizes an ICS file or an events web page (see pages.py) by its
    content and returns which one it was: "ics" or "page"."""
    url = source["url"]
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    raw = download(url)
    if raw.decode("utf-8-sig", "replace").lstrip()[:15].upper() == "BEGIN:VCALENDAR":
        store_source(source, raw)
        return "ics"
    store_source(source, pages.to_ics(pages.read(url, raw, download), effective_timezone()))
    return "page"


def download(url):
    """The body at url, at most MAX_SOURCE_BYTES. Errors never contain the URL, which can hold a private token."""
    try:
        with requests.get(url, timeout=30, stream=True, headers={"User-Agent": f"Daytree/{VERSION}"}) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            raw = response.raw.read(MAX_SOURCE_BYTES + 1, decode_content=True)
    except ValueError:
        raise
    except requests.Timeout:
        raise ValueError("The server did not answer in time.") from None
    except Exception:  # noqa: BLE001 - also errors while reading the body; the message must not carry the URL
        raise ValueError("The server could not be reached.") from None
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("The file is too large.")
    return raw


def refresh(source):
    """Fetches one URL calendar now and notes the result for the settings. Returns the error or None."""
    with sources_lock:
        status = state.setdefault("sources", {}).setdefault(source["id"], {})
        status["last_run"] = time.time()
        try:
            status.update(ok=True, message="OK", last_success=_now(), format=fetch_url(source))
        except ValueError as e:
            status.update(ok=False, message=str(e))
        _save_state()
        return None if status["ok"] else status["message"]


def refresh_due(now=None):
    """Fetches every URL calendar whose interval has passed since its last attempt."""
    now = now or time.time()
    for source in [s for s in settings["sources"] if s["kind"] == "url"]:
        last = state.get("sources", {}).get(source["id"], {}).get("last_run", 0)
        if now >= last + source["interval_minutes"] * 60:
            refresh(source)


def scheduler():
    """Every 10 s check whether the next sync is due; interval changes take effect immediately."""
    global next_due
    time.sleep(3)
    next_due = time.time()
    while True:
        if time.time() >= next_due:
            next_due = time.time() + settings["interval_minutes"] * 60
            run_sync()
        try:
            refresh_due()
        except Exception:  # noqa: BLE001 - one broken calendar must not stop the scheduler
            pass
        time.sleep(10)


def reset_timer():
    global next_due
    next_due = time.time() + settings["interval_minutes"] * 60


def start_in_background():
    """A sync requested from the settings, without waiting for it."""
    threading.Thread(target=run_sync, daemon=True).start()


# ---------------------------------------------------------------- events for the homepage
def local_time(value):
    """Event time as naive local time of the container; all-day dates are returned unchanged."""
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone().replace(tzinfo=None)
    return value


def occurrences(first, last, source_ids=None):
    """Events from first up to last, sorted, with every occurrence of recurring events. Without source_ids from
    the calendars shown on the homepage."""
    if source_ids is None:
        sources = [s for s in settings["sources"] if s["homepage"]]
    else:
        sources = [s for s in settings["sources"] if s["id"] in source_ids]
    out = []
    for source in sources:
        out += _occurrences(source, first, last)
    out.sort(key=lambda e: e["sort"])
    return out


def _occurrences(source, first, last):
    data = load_calendar(source["id"])
    if not data:
        return []
    try:
        cal = Calendar.from_ical(data["ics"])
        # Expands RRULE/EXDATE, so every occurrence of a recurring event is listed
        events = recurring_ical_events.of(cal).between(first, last)
    except Exception:
        return []
    colors = data.get("colors") or {}
    out = []
    for ev in events:
        start_raw = ev.decoded("DTSTART")
        end_raw = ev.decoded("DTEND") if "DTEND" in ev else start_raw
        start, end = local_time(start_raw), local_time(end_raw)
        timed = isinstance(start, datetime)
        out.append({
            "title": str(ev.get("SUMMARY", "(untitled)")),
            "date": start.date() if timed else start,
            "end_date": end.date() if timed else max(start, end - timedelta(days=1)),  # all-day DTEND is exclusive
            "time": start.strftime("%H:%M") if timed else "",
            "end_time": end.strftime("%H:%M") if timed else "",
            "all_day": not timed,
            "color": colors.get(str(ev.get("UID"))) or source["color"] or None,
            "calendar": source["name"],
            "tag": source["tag"],
            "calendar_id": source["id"],
            "start_ts": int(start_raw.timestamp()) if timed else None,
            "end_ts": int(end_raw.timestamp()) if timed else None,
            "sort": start if timed else datetime.combine(start, datetime.min.time()),
        })
    return out


def as_json(events):
    """Events from occurrences() with ISO dates, ready for jsonify."""
    return [{**e, "date": e["date"].isoformat(), "end_date": e["end_date"].isoformat(), "sort": None} for e in events]

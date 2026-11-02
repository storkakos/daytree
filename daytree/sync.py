"""TimeTree sync: the exporter run, the ICS repair, the scheduler and reading events back from the ICS file."""
import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone

import recurring_ical_events
from icalendar import Calendar

from daytree.core import COLORS_FILE, DATA, ICS_FILE, STATE_FILE, settings

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
    if not lock.acquire(blocking=False):
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = DATA / "timetree.tmp.ics"
        tmp.unlink(missing_ok=True)
        state["last_run"] = _now()
        s = settings
        if not s["email"] or not s["password"] or not s["calendar_code"]:
            state.update(ok=False, message="Please configure credentials and calendar under Settings")
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
                state.update(ok=True, last_success=_now(), message="OK", events=count_events())
            else:
                err = (res.stderr or res.stdout or "unknown error").strip().splitlines()[-3:]
                state.update(ok=False, message=" | ".join(err)[:300])
        except subprocess.TimeoutExpired:
            state.update(ok=False, message="Timed out after 10 minutes")
        except Exception as e:  # noqa: BLE001
            state.update(ok=False, message=str(e)[:300])
    finally:
        _save_state()
        lock.release()


def scheduler():
    """Every 10 s check whether the next sync is due; interval changes take effect immediately."""
    global next_due
    time.sleep(3)
    next_due = time.time()
    while True:
        if time.time() >= next_due:
            next_due = time.time() + settings["interval_minutes"] * 60
            run_sync()
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
        start, end = local_time(start_raw), local_time(end_raw)
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

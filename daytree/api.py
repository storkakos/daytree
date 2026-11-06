"""The JSON API for calendars, tasks and status, the health check and the ICS feed."""
import hmac
import secrets
import threading
from datetime import date, timedelta

from flask import Blueprint, Response, abort, jsonify, redirect, request
from icalendar import Calendar

from daytree import sync, tunnel
from daytree.auth import token_ok
from daytree.core import TASKS_FILE, read_encrypted, settings, write_encrypted

bp = Blueprint("api", __name__)

_tasks_lock = threading.Lock()
MAX_DAYS = 366                      # longest range /api/calendars/<id>/events answers in one go


# ---------------------------------------------------------------- calendars
def calendars():
    """The calendars Daytree syncs. TimeTree calendars keep their TimeTree code as id."""
    return [{"id": s["id"], "name": s["name"], "color": s["color"] or None} for s in settings["sources"]]


@bp.get("/api/calendars")
def api_calendars():
    return jsonify(calendars=calendars())


def _day(name, default):
    value = request.args.get(name)
    if value is None:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        abort(400)


@bp.get("/api/calendars/<calendar_id>/events")
def api_calendar_events(calendar_id):
    """Events from ?from= up to, not including, ?to= (ISO dates). Default: today and the next 7 days."""
    if calendar_id not in [c["id"] for c in calendars()]:
        abort(404)
    first = _day("from", date.today())
    last = _day("to", first + timedelta(days=8))
    if not first < last <= first + timedelta(days=MAX_DAYS):
        abort(400)
    return jsonify(events=sync.as_json(sync.occurrences(first, last, [calendar_id])))


# ---------------------------------------------------------------- tasks
def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return read_encrypted(TASKS_FILE)


def _title(data):
    title = str(data.get("title", "")).strip()
    if not title or len(title) > 200:
        abort(400)
    return title


def _done(data):
    if not isinstance(data.get("done", False), bool):
        abort(400)
    return data.get("done", False)


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _change(task_id, update):
    """Apply update(task) to one stored task and return it."""
    with _tasks_lock:
        tasks = load_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            abort(404)
        update(task)
        write_encrypted(TASKS_FILE, tasks)
    return jsonify(task)


@bp.get("/api/tasks")
def api_tasks():
    return jsonify(tasks=load_tasks())


@bp.post("/api/tasks")
def api_task_add():
    data = _body()
    task = {"id": secrets.token_hex(8), "title": _title(data), "done": _done(data)}
    with _tasks_lock:
        write_encrypted(TASKS_FILE, load_tasks() + [task])
    return jsonify(task), 201


@bp.get("/api/tasks/<task_id>")
def api_task(task_id):
    task = next((t for t in load_tasks() if t["id"] == task_id), None)
    if task is None:
        abort(404)
    return jsonify(task)


@bp.put("/api/tasks/<task_id>")
def api_task_replace(task_id):
    """Replace title and done. A missing done means not done."""
    data = _body()
    title, done = _title(data), _done(data)
    return _change(task_id, lambda task: task.update(title=title, done=done))


@bp.patch("/api/tasks/<task_id>")
def api_task_update(task_id):
    """Change title, done or both."""
    data = _body()
    new = {}
    if "title" in data:
        new["title"] = _title(data)
    if "done" in data:
        new["done"] = _done(data)
    if not new:
        abort(400)
    return _change(task_id, lambda task: task.update(new))


@bp.delete("/api/tasks/<task_id>")
def api_task_delete(task_id):
    with _tasks_lock:
        tasks = load_tasks()
        remaining = [t for t in tasks if t["id"] != task_id]
        if len(remaining) == len(tasks):
            abort(404)
        write_encrypted(TASKS_FILE, remaining)
    return "", 204


# ---------------------------------------------------------------- sync
@bp.post("/api/sync")
def api_sync():
    """Start a sync without waiting for it. Always needs a read-write token (see auth.TOKEN_ONLY_PATHS)."""
    if sync.lock.locked():
        return jsonify(running=True, started=False), 409
    sync.reset_timer()
    sync.start_in_background()
    return jsonify(running=True, started=True), 202


# ---------------------------------------------------------------- status, health check, ICS feed
@bp.route("/api/status")
def status():
    return jsonify({**sync.state, "running": sync.lock.locked(), "tunnel": tunnel.status["status"]})


@bp.get("/ping")
def ping():
    """Liveness check for uptime monitors. Public in every mode, so it must not reveal anything."""
    return Response("pong\n", mimetype="text/plain", headers={"Cache-Control": "no-store"})


def combine(sources, name):
    """One ICS file from several calendars: each time zone once, and event IDs that stay unique."""
    stored = [data for data in (sync.load_calendar(s["id"]) for s in sources) if data]
    if len(stored) == 1:
        return stored[0]["ics"].encode()                 # one calendar is served exactly as it was synced
    if not stored:
        return None
    combined = Calendar()
    for key, value in (("VERSION", "2.0"), ("PRODID", "-//Daytree//EN"), ("CALSCALE", "GREGORIAN"),
                       ("METHOD", "PUBLISH"), ("X-WR-CALNAME", name)):
        combined[key] = value
    zones, events, uids = {}, [], set()
    for source, data in ((s, sync.load_calendar(s["id"])) for s in sources):
        try:
            cal = Calendar.from_ical(data["ics"]) if data else None
        except ValueError:
            cal = None
        for component in cal.subcomponents if cal else []:
            if component.name == "VTIMEZONE":
                zones.setdefault(str(component.get("TZID")), component)
            elif component.name == "VEVENT":
                uid = str(component.get("UID", ""))
                if uid in uids:
                    component["UID"] = uid = f"{uid}-{source['id']}"
                uids.add(uid)
                events.append(component)
    for component in [*zones.values(), *events]:
        combined.add_component(component)
    return combined.to_ical()


def _ics(raw, filename):
    if raw is None:
        abort(404)
    return Response(raw, mimetype="text/calendar", headers={"Content-Disposition": f"inline; filename={filename}"})


@bp.route("/ics")
def calendar_ics():
    """Every calendar that is switched on for the feed, in one file."""
    if not token_ok(request.args.get("token")):
        abort(403)
    sources = [s for s in settings["sources"] if s["feed"]]
    name = sources[0]["name"] if len(sources) == 1 else "Daytree"
    return _ics(combine(sources, name), "daytree.ics")


@bp.route("/ics/<source_id>")
def calendar_ics_one(source_id):
    """One calendar with its own token. An unknown calendar answers like a wrong token, so ids stay hidden."""
    source = next((s for s in settings["sources"] if s["id"] == source_id), None)
    if not source or not hmac.compare_digest(request.args.get("token") or "", source["token"]):
        abort(403)
    return _ics(combine([source], source["name"]), f"{source_id}.ics")


@bp.route("/calendar.ics")
def old_calendar_ics():
    """The old address of the feed, for calendars that subscribed before /ics. Keeps the token in the query."""
    query = request.query_string.decode()
    return redirect("/ics" + (f"?{query}" if query else ""), 301)

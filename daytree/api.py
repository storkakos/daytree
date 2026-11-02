"""The JSON API for calendars, tasks and status, the health check and the ICS feed."""
import secrets
import threading
from datetime import date, timedelta

from flask import Blueprint, Response, abort, jsonify, request

from daytree import sync, tunnel
from daytree.auth import token_ok
from daytree.core import ICS_FILE, TASKS_FILE, read_encrypted, settings, write_encrypted

bp = Blueprint("api", __name__)

_tasks_lock = threading.Lock()
MAX_DAYS = 366                      # longest range /api/calendars/<id>/events answers in one go


# ---------------------------------------------------------------- calendars
def calendars():
    """The calendars Daytree syncs. One for now; multiple calendars are #27."""
    if not settings["calendar_code"]:
        return []
    return [{"id": settings["calendar_code"], "name": settings["calendar_name"]}]


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
    return jsonify(events=sync.as_json(sync.occurrences(first, last)))


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


# ---------------------------------------------------------------- status, health check, ICS feed
@bp.route("/api/status")
def status():
    return jsonify({**sync.state, "running": sync.lock.locked(), "tunnel": tunnel.status["status"]})


@bp.get("/ping")
def ping():
    """Liveness check for uptime monitors. Public in every mode, so it must not reveal anything."""
    return Response("pong\n", mimetype="text/plain", headers={"Cache-Control": "no-store"})


@bp.route("/calendar.ics")
def calendar_ics():
    if not token_ok(request.args.get("token")):
        abort(403)
    if not ICS_FILE.exists():
        abort(404)
    return Response(ICS_FILE.read_bytes(), mimetype="text/calendar",
                    headers={"Content-Disposition": "inline; filename=timetree.ics"})

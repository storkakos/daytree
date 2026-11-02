"""The JSON API for tasks and status, the health check and the ICS feed."""
import secrets
import threading

from flask import Blueprint, Response, abort, jsonify, request

from daytree import sync, tunnel
from daytree.auth import token_ok
from daytree.core import ICS_FILE, TASKS_FILE, read_encrypted, write_encrypted

bp = Blueprint("api", __name__)

_tasks_lock = threading.Lock()


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return read_encrypted(TASKS_FILE)


def _task_title():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title or len(title) > 200:
        abort(400)
    return title


@bp.get("/api/tasks")
def api_tasks():
    return jsonify(tasks=load_tasks())


@bp.post("/api/tasks")
def api_task_add():
    task = {"id": secrets.token_hex(8), "title": _task_title(), "done": False}
    with _tasks_lock:
        write_encrypted(TASKS_FILE, load_tasks() + [task])
    return jsonify(task), 201


@bp.patch("/api/tasks/<task_id>")
def api_task_update(task_id):
    done = bool((request.get_json(silent=True) or {}).get("done"))
    with _tasks_lock:
        tasks = load_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            abort(404)
        task["done"] = done
        write_encrypted(TASKS_FILE, tasks)
    return jsonify(task)


@bp.delete("/api/tasks/<task_id>")
def api_task_delete(task_id):
    with _tasks_lock:
        tasks = load_tasks()
        remaining = [t for t in tasks if t["id"] != task_id]
        if len(remaining) == len(tasks):
            abort(404)
        write_encrypted(TASKS_FILE, remaining)
    return "", 204


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

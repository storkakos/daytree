"""The REST API: calendars with their events, and tasks one by one."""
from datetime import date, timedelta

import pytest
from conftest import ORIGIN

from daytree import core

TODAY = date.today()


def day(days):
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


@pytest.fixture
def calendar():
    core.settings.update(calendar_code="fam123", calendar_name="Family")
    core.ICS_FILE.write_bytes("\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN",
        "BEGIN:VEVENT", "UID:1", "SUMMARY:Soon", f"DTSTART;VALUE=DATE:{day(2)}", f"DTEND;VALUE=DATE:{day(3)}", "END:VEVENT",
        "BEGIN:VEVENT", "UID:2", "SUMMARY:Later", f"DTSTART;VALUE=DATE:{day(40)}", f"DTEND;VALUE=DATE:{day(41)}", "END:VEVENT",
        "END:VCALENDAR", "",
    ]).encode())
    yield
    core.ICS_FILE.unlink(missing_ok=True)


def test_calendars(client, calendar):
    assert client.get("/api/calendars").get_json() == {"calendars": [{"id": "fam123", "name": "Family"}]}
    core.settings["calendar_code"] = ""
    assert client.get("/api/calendars").get_json() == {"calendars": []}


def test_calendar_events(client, calendar):
    titles = lambda r: [e["title"] for e in r.get_json()["events"]]          # noqa: E731
    assert titles(client.get("/api/calendars/fam123/events")) == ["Soon"]    # today and the next 7 days
    r = client.get(f"/api/calendars/fam123/events?from={TODAY + timedelta(days=30)}&to={TODAY + timedelta(days=60)}")
    assert titles(r) == ["Later"] and r.get_json()["events"][0]["date"] == (TODAY + timedelta(days=40)).isoformat()
    assert client.get("/api/calendars/other/events").status_code == 404


@pytest.mark.parametrize("query", ["from=yesterday", "to=2026-13-01", f"from={TODAY}&to={TODAY}",
                                   f"from={TODAY}&to={TODAY + timedelta(days=400)}"])
def test_calendar_events_range_checked(client, calendar, query):
    assert client.get(f"/api/calendars/fam123/events?{query}").status_code == 400


def test_single_task(client):
    task = client.post("/api/tasks", json={"title": "Water plants"}, headers=ORIGIN).get_json()
    assert client.get(f"/api/tasks/{task['id']}").get_json() == task
    assert client.get("/api/tasks/nope").status_code == 404
    client.delete(f"/api/tasks/{task['id']}", headers=ORIGIN)


def test_put_replaces_and_patch_changes(client):
    task = client.post("/api/tasks", json={"title": "Old", "done": True}, headers=ORIGIN).get_json()
    assert task["done"] is True
    url = f"/api/tasks/{task['id']}"
    assert client.put(url, json={"title": "New"}, headers=ORIGIN).get_json() == {"id": task["id"], "title": "New", "done": False}
    assert client.patch(url, json={"done": True}, headers=ORIGIN).get_json()["title"] == "New"
    assert client.patch(url, json={"title": "Renamed"}, headers=ORIGIN).get_json() == {"id": task["id"], "title": "Renamed", "done": True}
    assert client.get(url).get_json()["title"] == "Renamed"
    assert client.put("/api/tasks/nope", json={"title": "x"}, headers=ORIGIN).status_code == 404
    client.delete(url, headers=ORIGIN)


@pytest.mark.parametrize("method, body", [
    ("put", {"done": True}), ("put", {"title": " "}), ("put", {"title": "x", "done": "yes"}),
    ("patch", {}), ("patch", {"done": 1}), ("patch", {"title": "x" * 201}), ("patch", ["done"]),
    ("post", {"title": "x", "done": "no"}),
])
def test_task_bodies_validated(client, method, body):
    task = client.post("/api/tasks", json={"title": "Check"}, headers=ORIGIN).get_json()
    url = "/api/tasks" if method == "post" else f"/api/tasks/{task['id']}"
    assert getattr(client, method)(url, json=body, headers=ORIGIN).status_code == 400
    assert client.get(f"/api/tasks/{task['id']}").get_json() == task                    # unchanged
    client.delete(f"/api/tasks/{task['id']}", headers=ORIGIN)

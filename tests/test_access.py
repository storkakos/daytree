"""Access control: who reaches which route, by side, exposure mode and credentials.

The table is the reference for what is public. Change it only together with the access rules in auth.py.
"""
import pytest
from conftest import ORIGIN, PUBLIC

from daytree import core
from daytree.web import app

GOOD, BAD = "good-token", "bad-token"
OK = 204           # every route except the ones below is replaced by a stub that answers 204

# (method, path): status internal, then public in mode off, ics and dashboard
TABLE = {
    ("GET", "/"):                                (OK, 404, 404, OK),
    ("GET", "/kiosk"):                           (OK, 404, 404, OK),
    ("GET", "/api/config"):                      (OK, 404, 404, OK),
    ("GET", "/api/events"):                      (OK, 404, 404, OK),
    ("GET", "/api/weather"):                     (OK, 404, 404, OK),
    ("GET", "/api/waste"):                       (OK, 404, 404, OK),
    ("GET", "/api/background"):                  (OK, 404, 404, OK),
    ("GET", "/api/status"):                      (OK, 404, 404, OK),
    ("POST", "/api/background/refresh"):         (OK, 404, 404, 404),
    ("GET", "/api/tasks"):                       (OK, 404, 404, 404),
    ("POST", "/api/tasks"):                      (OK, 404, 404, 404),
    ("PATCH", "/api/tasks/1"):                   (OK, 404, 404, 404),
    ("DELETE", "/api/tasks/1"):                  (OK, 404, 404, 404),
    ("GET", "/settings"):                        (OK, 404, 404, 404),
    ("POST", "/settings/login"):                 (OK, 404, 404, 404),
    ("POST", "/settings/save"):                  (OK, 404, 404, 404),
    ("POST", "/settings/display"):               (OK, 404, 404, 404),
    ("POST", "/settings/waste"):                 (OK, 404, 404, 404),
    ("POST", "/settings/tunnel"):                (OK, 404, 404, 404),
    ("POST", "/sync"):                           (OK, 404, 404, 404),
    ("GET", "/ping"):                            (200, 200, 200, 200),
    ("GET", f"/calendar.ics?token={GOOD}"):      (200, 404, 200, 200),
    ("GET", f"/calendar.ics?token={BAD}"):       (403, 404, 403, 403),
    ("GET", "/calendar.ics"):                    (403, 404, 403, 403),
}
SIDES = {
    "internal": {},
    "port 8081": {"environ_overrides": PUBLIC},
    "Cloudflare header": {"headers": {"Cf-Ray": "abc"}},
    "Cloudflare client IP": {"headers": {"Cf-Connecting-Ip": "203.0.113.9"}},
}
REAL = {"api.ping", "api.calendar_ics", "static"}


@pytest.fixture
def stubbed(monkeypatch):
    """Only the access rules decide: routes that would change data or call out answer 204 instead."""
    for endpoint in list(app.view_functions):
        if endpoint not in REAL:
            monkeypatch.setitem(app.view_functions, endpoint, lambda **_: ("", 204))
    core.settings["token"] = GOOD
    core.ICS_FILE.write_text("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    yield app.test_client()
    core.ICS_FILE.unlink(missing_ok=True)


def cases():
    for (method, path), expected in TABLE.items():
        yield pytest.param(method, path, "internal", "off", expected[0], id=f"{method} {path} internal")
        for side in list(SIDES)[1:]:
            for mode, status in zip(core.PUBLIC_MODES, expected[1:]):
                yield pytest.param(method, path, side, mode, status, id=f"{method} {path} {side} {mode}")


@pytest.mark.parametrize("method, path, side, mode, expected", list(cases()))
def test_access(stubbed, method, path, side, mode, expected):
    core.settings["public_mode"] = mode
    extra = SIDES[side]
    headers = {**ORIGIN, **extra.get("headers", {})}
    r = stubbed.open(path, method=method, headers=headers, environ_overrides=extra.get("environ_overrides"))
    assert r.status_code == expected


@pytest.mark.parametrize("mode", core.PUBLIC_MODES)
def test_internal_side_ignores_the_mode(stubbed, mode):
    core.settings["public_mode"] = mode
    assert stubbed.get("/settings").status_code == OK and stubbed.get(f"/calendar.ics?token={GOOD}").status_code == 200


@pytest.mark.parametrize("headers, expected", [
    ({}, 403),
    ({"Origin": "http://evil.example"}, 403),
    ({"Referer": "http://evil.example/settings"}, 403),
    ({"Origin": "http://localhost"}, OK),
    ({"Referer": "http://localhost/settings"}, OK),
])
@pytest.mark.parametrize("method, path", [(m, p) for m, p in TABLE if m != "GET"])
def test_csrf(stubbed, method, path, headers, expected):
    assert stubbed.open(path, method=method, headers=headers).status_code == expected


def test_public_homepage_reveals_nothing(client):
    core.settings.update(public_mode="dashboard", greeting_name="Private Name")
    page = client.get("/", environ_overrides=PUBLIC).data
    assert core.settings["token"].encode() not in page and b"/settings" not in page and b"Sync now" not in page
    assert b"/api/tasks" not in page and b'id="gear"' not in page and b'id="tasks"' not in page and b"Private Name" not in page
    assert client.get("/api/config", environ_overrides=PUBLIC).get_json()["name"] == ""
    core.settings["calendar_code"] = ""
    assert b'id="setup"' not in client.get("/", environ_overrides=PUBLIC).data       # the setup is never shown publicly
    assert core.settings["token"].encode() in client.get("/settings").data            # the internal view shows the URL


def test_mode_and_hostname_validated(client):
    def save(mode, **extra):
        assert client.post("/settings/tunnel", data={"public_mode": mode, **extra}, headers=ORIGIN).status_code == 302

    save("bogus")
    assert core.settings["public_mode"] == "off"
    save("ics", public_hostname="calendar.example.com")
    assert f"https://calendar.example.com/calendar.ics?token={core.settings['token']}".encode() in client.get("/settings").data
    save("ics", public_hostname="evil.com/<x>")
    assert core.settings["public_hostname"] == "calendar.example.com"

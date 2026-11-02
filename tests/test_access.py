"""Access control: who reaches which route, by side, shared areas and credentials.

The tables are the reference for what is public. Change them only together with the access rules in auth.py.
"""
import pytest
from conftest import ORIGIN, PUBLIC

from daytree import auth, core
from daytree.web import app

GOOD, BAD = "good-token", "bad-token"
OK = 204           # every route except the ones below is replaced by a stub that answers 204
PADLOCK = 200      # the settings page shows the padlock instead
UNAUTHORIZED = 401
LOCK_HASH = auth.hash_password("correct horse")
AREAS = ([], ["ics"], ["homepage"], ["api"])

# (method, path): status internal, internal with the padlock closed, then public without a token when the tunnel
# shares nothing, the ICS feed, the homepage, or the API
TABLE = {
    ("GET", "/"):                                (OK, OK, 404, 404, OK, 404),
    ("GET", "/kiosk"):                           (OK, OK, 404, 404, OK, 404),
    ("GET", "/api/config"):                      (OK, OK, 404, 404, OK, UNAUTHORIZED),
    ("GET", "/api/events"):                      (OK, OK, 404, 404, OK, UNAUTHORIZED),
    ("GET", "/api/weather"):                     (OK, OK, 404, 404, OK, UNAUTHORIZED),
    ("GET", "/api/waste"):                       (OK, OK, 404, 404, OK, UNAUTHORIZED),
    ("GET", "/api/background"):                  (OK, OK, 404, 404, OK, UNAUTHORIZED),
    ("GET", "/api/status"):                      (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("POST", "/api/background/refresh"):         (OK, 403, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/api/calendars"):                   (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/api/calendars/fam123/events"):     (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/api/tasks"):                       (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("POST", "/api/tasks"):                      (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/api/tasks/1"):                     (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("PUT", "/api/tasks/1"):                     (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("PATCH", "/api/tasks/1"):                   (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("DELETE", "/api/tasks/1"):                  (OK, OK, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/settings"):                        (OK, PADLOCK, 404, 404, 404, 404),
    ("POST", "/settings/login"):                 (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/save"):                  (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/display"):               (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/waste"):                 (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/tunnel"):                (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/unlock"):                (OK, OK, 404, 404, 404, 404),
    ("POST", "/settings/lock"):                  (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/password"):              (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/tokens"):                (OK, 403, 404, 404, 404, 404),
    ("POST", "/settings/tokens/revoke"):         (OK, 403, 404, 404, 404, 404),
    ("POST", "/sync"):                           (OK, 403, 404, 404, 404, 404),
    ("POST", "/api/sync"):                       (UNAUTHORIZED, UNAUTHORIZED, 404, 404, 404, UNAUTHORIZED),
    ("GET", "/ping"):                            (200, 200, 200, 200, 200, 200),
    ("GET", f"/calendar.ics?token={GOOD}"):      (200, 200, 404, 200, 404, 404),
    ("GET", f"/calendar.ics?token={BAD}"):       (403, 403, 404, 403, 404, 404),
    ("GET", "/calendar.ics"):                    (403, 403, 404, 403, 404, 404),
}
SIDES = {
    "internal": {},
    "internal, padlock closed": {},
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
    core.settings.update(token=GOOD, calendar_code="fam123", lock_hash=LOCK_HASH)
    core.ICS_FILE.write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    yield app.test_client()
    core.ICS_FILE.unlink(missing_ok=True)


def cases():
    for (method, path), expected in TABLE.items():
        yield pytest.param(method, path, "internal", [], expected[0], id=f"{method} {path} internal")
        yield pytest.param(method, path, "internal, padlock closed", [], expected[1], id=f"{method} {path} locked")
        for side in list(SIDES)[2:]:
            for areas, status in zip(AREAS, expected[2:]):
                yield pytest.param(method, path, side, areas, status,
                                   id=f"{method} {path} {side} {'+'.join(areas) or 'nothing'}")


@pytest.mark.parametrize("method, path, side, areas, expected", list(cases()))
def test_access(stubbed, monkeypatch, method, path, side, areas, expected):
    core.settings["public_areas"] = areas
    monkeypatch.setattr(auth, "SETTINGS_LOCK", side == "internal, padlock closed")
    extra = SIDES[side]
    headers = {**ORIGIN, **extra.get("headers", {})}
    r = stubbed.open(path, method=method, headers=headers, environ_overrides=extra.get("environ_overrides"))
    assert r.status_code == expected


READ, WRITE, WRONG = "dt_read-token", "dt_write-token", "dt_wrong-token"

# (method, path): status on the internal side without an Origin header, sending no token, a read-only token,
# a read-write token, or an unknown token. Tokens count only for /api/; elsewhere the header is ignored.
TOKENS = {
    ("GET", "/"):                                (OK, OK, OK, OK),
    ("GET", "/kiosk"):                           (OK, OK, OK, OK),
    ("GET", "/api/config"):                      (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/events"):                      (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/weather"):                     (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/waste"):                       (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/background"):                  (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/status"):                      (OK, OK, OK, UNAUTHORIZED),
    ("POST", "/api/background/refresh"):         (403, 403, OK, UNAUTHORIZED),
    ("GET", "/api/calendars"):                   (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/calendars/fam123/events"):     (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/tasks"):                       (OK, OK, OK, UNAUTHORIZED),
    ("POST", "/api/tasks"):                      (403, 403, OK, UNAUTHORIZED),
    ("GET", "/api/tasks/1"):                     (OK, OK, OK, UNAUTHORIZED),
    ("PUT", "/api/tasks/1"):                     (403, 403, OK, UNAUTHORIZED),
    ("PATCH", "/api/tasks/1"):                   (403, 403, OK, UNAUTHORIZED),
    ("DELETE", "/api/tasks/1"):                  (403, 403, OK, UNAUTHORIZED),
    ("GET", "/settings"):                        (OK, OK, OK, OK),
    ("POST", "/settings/login"):                 (403, 403, 403, 403),
    ("POST", "/settings/save"):                  (403, 403, 403, 403),
    ("POST", "/settings/display"):               (403, 403, 403, 403),
    ("POST", "/settings/waste"):                 (403, 403, 403, 403),
    ("POST", "/settings/tunnel"):                (403, 403, 403, 403),
    ("POST", "/settings/unlock"):                (403, 403, 403, 403),
    ("POST", "/settings/lock"):                  (403, 403, 403, 403),
    ("POST", "/settings/password"):              (403, 403, 403, 403),
    ("POST", "/settings/tokens"):                (403, 403, 403, 403),
    ("POST", "/settings/tokens/revoke"):         (403, 403, 403, 403),
    ("POST", "/sync"):                           (403, 403, 403, 403),
    ("POST", "/api/sync"):                       (UNAUTHORIZED, 403, OK, UNAUTHORIZED),
    ("GET", "/ping"):                            (200, 200, 200, 200),
    ("GET", f"/calendar.ics?token={GOOD}"):      (200, 200, 200, 200),
    ("GET", f"/calendar.ics?token={BAD}"):       (403, 403, 403, 403),
}


@pytest.mark.parametrize("method, path, credential, expected", [
    pytest.param(method, path, credential, status, id=f"{method} {path} {credential or 'no token'}")
    for (method, path), row in TOKENS.items() for credential, status in zip((None, READ, WRITE, WRONG), row)
])
def test_api_tokens(stubbed, method, path, credential, expected):
    add_tokens()
    headers = {"Authorization": f"Bearer {credential}"} if credential else {}
    assert stubbed.open(path, method=method, headers=headers).status_code == expected


def add_tokens():
    core.settings["api_tokens"] = [
        {"id": "r", "name": "read", "hash": auth.token_hash(READ), "write": False, "created": "", "last_used": None},
        {"id": "w", "name": "write", "hash": auth.token_hash(WRITE), "write": True, "created": "", "last_used": None},
    ]


# (method, path): status through the tunnel without an Origin header: a read-write token while the ICS feed and the
# homepage are shared but not the API, then with the API shared: a read-only, a read-write and an unknown token
PUBLIC_TOKENS = {
    ("GET", "/"):                                (OK, 404, 404, 404),
    ("GET", "/api/config"):                      (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/events"):                      (OK, OK, OK, UNAUTHORIZED),
    ("GET", "/api/status"):                      (404, OK, OK, UNAUTHORIZED),
    ("POST", "/api/background/refresh"):         (404, 403, OK, UNAUTHORIZED),
    ("GET", "/api/calendars"):                   (404, OK, OK, UNAUTHORIZED),
    ("GET", "/api/tasks"):                       (404, OK, OK, UNAUTHORIZED),
    ("POST", "/api/tasks"):                      (404, 403, OK, UNAUTHORIZED),
    ("PUT", "/api/tasks/1"):                     (404, 403, OK, UNAUTHORIZED),
    ("PATCH", "/api/tasks/1"):                   (404, 403, OK, UNAUTHORIZED),
    ("DELETE", "/api/tasks/1"):                  (404, 403, OK, UNAUTHORIZED),
    ("GET", "/settings"):                        (404, 404, 404, 404),
    ("POST", "/settings/tokens"):                (404, 404, 404, 404),
    ("POST", "/sync"):                           (404, 404, 404, 404),
    ("POST", "/api/sync"):                       (404, 403, OK, UNAUTHORIZED),
    ("GET", "/ping"):                            (200, 200, 200, 200),
}
PUBLIC_TOKEN_CASES = ((["ics", "homepage"], WRITE), (["api"], READ), (["api"], WRITE), (["api"], WRONG))


@pytest.mark.parametrize("method, path, areas, credential, expected", [
    pytest.param(method, path, areas, credential, status, id=f"{method} {path} {'+'.join(areas)} {credential}")
    for (method, path), row in PUBLIC_TOKENS.items()
    for (areas, credential), status in zip(PUBLIC_TOKEN_CASES, row)
])
def test_api_tokens_through_the_tunnel(stubbed, method, path, areas, credential, expected):
    add_tokens()
    core.settings["public_areas"] = areas
    r = stubbed.open(path, method=method, headers={"Authorization": f"Bearer {credential}"}, environ_overrides=PUBLIC)
    assert r.status_code == expected


@pytest.mark.parametrize("areas, expected", [([], 404), (["ics", "api"], 404), (["homepage"], 200)])
def test_static_files_belong_to_the_homepage(stubbed, areas, expected):
    core.settings["public_areas"] = areas
    assert stubbed.get("/static/favicon.png", environ_overrides=PUBLIC).status_code == expected


def test_every_route_is_in_the_tables():
    rules = {(m, r.rule) for r in app.url_map.iter_rules() if r.endpoint != "static" for m in r.methods - {"HEAD", "OPTIONS"}}
    names = {"/api/tasks/1": "/api/tasks/<task_id>", "/api/calendars/fam123/": "/api/calendars/<calendar_id>/"}
    listed = set()
    for method, path in TABLE:
        path = path.split("?")[0]
        for example, rule in names.items():
            path = path.replace(example, rule)
        listed.add((method, path))
    assert rules == listed, rules ^ listed
    assert set(TOKENS) <= set(TABLE) and set(PUBLIC_TOKENS) <= set(TABLE)


@pytest.mark.parametrize("areas", AREAS)
def test_internal_side_ignores_the_areas(stubbed, areas):
    core.settings["public_areas"] = areas
    assert stubbed.get("/settings").status_code == OK and stubbed.get(f"/calendar.ics?token={GOOD}").status_code == 200


@pytest.mark.parametrize("headers, expected", [
    ({}, 403),
    ({"Origin": "http://evil.example"}, 403),
    ({"Referer": "http://evil.example/settings"}, 403),
    ({"Origin": "http://localhost"}, OK),
    ({"Referer": "http://localhost/settings"}, OK),
])
@pytest.mark.parametrize("method, path", [(m, p) for m, p in TABLE if m != "GET" and p not in auth.TOKEN_ONLY_PATHS])
def test_csrf(stubbed, method, path, headers, expected):
    assert stubbed.open(path, method=method, headers=headers).status_code == expected


def test_public_homepage_reveals_nothing(client):
    core.settings.update(public_areas=["homepage"], greeting_name="Private Name")
    page = client.get("/", environ_overrides=PUBLIC).data
    assert core.settings["token"].encode() not in page and b"/settings" not in page and b"Sync now" not in page
    assert b"/api/tasks" not in page and b'id="gear"' not in page and b'id="tasks"' not in page and b"Private Name" not in page
    assert client.get("/api/config", environ_overrides=PUBLIC).get_json()["name"] == ""
    core.settings["calendar_code"] = ""
    assert b'id="setup"' not in client.get("/", environ_overrides=PUBLIC).data       # the setup is never shown publicly
    assert core.settings["token"].encode() in client.get("/settings").data            # the internal view shows the URL


def test_areas_and_hostname_validated(client):
    def save(areas, **extra):
        assert client.post("/settings/tunnel", data={"public_areas": areas, **extra}, headers=ORIGIN).status_code == 302

    save(["api", "bogus", "ics"])
    assert core.settings["public_areas"] == ["ics", "api"]
    page = client.get("/settings").data.decode()
    assert 'value="ics" checked' in page and 'value="api" checked' in page and 'value="homepage" >' in page
    save([])
    assert core.settings["public_areas"] == []
    save(["ics"], public_hostname="calendar.example.com")
    assert f"https://calendar.example.com/calendar.ics?token={core.settings['token']}".encode() in client.get("/settings").data
    save(["ics"], public_hostname="evil.com/<x>")
    assert core.settings["public_hostname"] == "calendar.example.com"


@pytest.mark.parametrize("mode, areas", [("off", []), ("ics", ["ics"]), ("dashboard", ["ics", "homepage"]), ("bogus", [])])
def test_old_mode_is_migrated(monkeypatch, tmp_path, mode, areas):
    stored = tmp_path / "settings.enc"
    core.write_encrypted(stored, {"email": "a@b.de", "token": "t", "public_mode": mode})
    monkeypatch.setattr(core, "SETTINGS_FILE", stored)
    migrated = core.load_settings()
    assert migrated["public_areas"] == areas and "public_mode" not in migrated
    assert "public_mode" not in core.read_encrypted(stored)                    # written back right away

"""HTTPS_URL: plain HTTP on the home network sends browsers to the reverse proxy and refuses writes and tokens."""
import pytest
from conftest import ORIGIN, PUBLIC

from daytree import auth, core

URL = "https://daytree.example.com"
PROXIED = {"X-Forwarded-Proto": "https"}         # what nginx adds in front of port 8080


@pytest.fixture
def https_on(monkeypatch):
    monkeypatch.setattr(auth, "HTTPS_URL", URL)


def test_off_by_default(client):
    assert auth.HTTPS_URL == ""
    assert client.get("/").status_code == 200 and client.get("/settings").status_code == 200


@pytest.mark.parametrize("path, target", [("/", URL + "/"), ("/settings", URL + "/settings"),
                                          ("/settings?updated=1", URL + "/settings?updated=1")])
def test_browsers_are_sent_to_https(client, https_on, path, target):
    r = client.get(path)
    assert r.status_code == 308 and r.headers["Location"] == target


@pytest.mark.parametrize("path", ["/kiosk", "/api/events", "/api/config", "/api/weather", "/api/waste", "/ping", "/api/status"])
def test_kiosk_and_healthcheck_stay_on_http(client, https_on, path):
    assert client.get(path).status_code == 200


def test_writes_and_tokens_refused_over_http(client, https_on):
    r = client.post("/api/tasks", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 403 and URL.encode() in r.data
    assert client.post("/settings/unlock", data={"password": "x"}, headers=ORIGIN).status_code == 403
    assert client.get("/api/tasks", headers={"Authorization": "Bearer dt_x"}).status_code == 403


def test_everything_works_through_the_proxy(client, https_on):
    assert client.get("/", headers=PROXIED).status_code == 200
    r = client.post("/api/tasks", json={"title": "Through nginx"}, headers={**PROXIED, "Origin": "https://localhost"})
    assert r.status_code == 201
    client.delete(f"/api/tasks/{r.get_json()['id']}", headers={**PROXIED, "Origin": "https://localhost"})


def test_unlock_cookie_is_secure_through_the_proxy(client, https_on, monkeypatch):
    monkeypatch.setattr(auth, "SETTINGS_LOCK", True)
    core.settings.update(sources=[core.new_source("timetree", "Family", "fam123")], lock_hash=auth.hash_password("correct horse"))
    r = client.post("/settings/unlock", data={"password": "correct horse"}, headers={**PROXIED, "Origin": "https://localhost"})
    assert "Secure" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]


def test_tunnel_is_not_affected(client, https_on):
    core.settings["public_areas"] = ["homepage"]
    assert client.get("/", environ_overrides=PUBLIC).status_code == 200
    assert client.get("/", headers={"Cf-Ray": "abc"}).status_code == 200


@pytest.mark.parametrize("value, expected", [("", ""), (" https://a.example.com/ ", "https://a.example.com"),
                                             ("https://a.example.com:8443", "https://a.example.com:8443")])
def test_valid_values(value, expected):
    assert auth._https_url(value) == expected


@pytest.mark.parametrize("value", ["http://a.example.com", "a.example.com", "https://a.example.com/daytree", "https://"])
def test_invalid_values_stop_the_start(value):
    with pytest.raises(SystemExit, match="HTTPS_URL"):
        auth._https_url(value)

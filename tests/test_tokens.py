"""API tokens: created in the settings, shown once, stored as a hash, sent as Bearer header, revoked one by one."""
import re

import pytest
from conftest import ORIGIN, PUBLIC

from daytree import auth, core


def create(client, name="Home Assistant", access="read"):
    r = client.post("/settings/tokens", data={"name": name, "access": access}, headers=ORIGIN)
    assert r.status_code == 200
    return re.search(rb'id="new-token" readonly value="(dt_[^"]+)"', r.data).group(1).decode()


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def test_shown_once_and_stored_as_hash(client):
    value = create(client)
    stored = core.settings["api_tokens"][-1]
    assert stored["name"] == "Home Assistant" and stored["write"] is False and stored["last_used"] is None
    assert stored["hash"] == auth.token_hash(value) and value not in str(core.settings["api_tokens"])
    assert value.encode() not in core.SETTINGS_FILE.read_bytes()
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert value.encode() not in page and stored["hash"].encode() not in page
    assert b"Home Assistant" in page and b"Read only" in page and b"Never used" in page


def test_name_required_and_limit(client):
    r = client.post("/settings/tokens", data={"name": " "}, headers=ORIGIN, follow_redirects=True)
    assert b"Enter a name" in r.data and core.settings["api_tokens"] == []
    core.settings["api_tokens"] = [{"id": str(i), "name": "x", "hash": "h", "write": False, "created": "", "last_used": None}
                                   for i in range(20)]
    r = client.post("/settings/tokens", data={"name": "one more"}, headers=ORIGIN, follow_redirects=True)
    assert b"Too many tokens" in r.data and len(core.settings["api_tokens"]) == 20


def test_read_token(client):
    value = create(client)
    assert client.get("/api/tasks", headers=bearer(value)).status_code == 200
    assert core.settings["api_tokens"][-1]["last_used"] is not None
    assert client.post("/api/tasks", json={"title": "x"}, headers=bearer(value)).status_code == 403


def test_write_token_needs_no_origin(client):
    value = create(client, "Script", "write")
    r = client.post("/api/tasks", json={"title": "From a script"}, headers=bearer(value))
    assert r.status_code == 201
    task = r.get_json()["id"]
    assert client.patch(f"/api/tasks/{task}", json={"done": True}, headers=bearer(value)).get_json()["done"] is True
    assert client.delete(f"/api/tasks/{task}", headers=bearer(value)).status_code == 204


def test_token_opens_only_the_api(client):
    value = create(client, "Script", "write")
    assert client.post("/settings/tokens", data={"name": "x"}, headers=bearer(value)).status_code == 403
    assert client.post("/sync", headers=bearer(value)).status_code == 403


@pytest.mark.parametrize("header", ["Bearer nope", "Bearer ", "Basic abc", "dt_abc"])
def test_invalid_token_is_rejected(client, header):
    r = client.get("/api/tasks", headers={"Authorization": header})
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"


def test_never_from_the_url(client):
    value = create(client, "Script", "write")
    for name in ("token", "access_token"):
        assert client.post(f"/api/tasks?{name}={value}", json={"title": "x"}).status_code == 403


def test_revoke(client):
    keep, drop = create(client, "Keep"), create(client, "Drop")
    drop_id = next(t["id"] for t in core.settings["api_tokens"] if t["name"] == "Drop")
    r = client.post("/settings/tokens/revoke", data={"id": drop_id}, headers=ORIGIN, follow_redirects=True)
    assert b"Token revoked." in r.data
    assert client.get("/api/tasks", headers=bearer(drop)).status_code == 401
    assert client.get("/api/tasks", headers=bearer(keep)).status_code == 200
    assert client.post("/settings/tokens/revoke", data={"id": drop_id}, headers=ORIGIN).status_code == 404


def test_tokens_open_nothing_from_outside(client):
    value = create(client, "Script", "write")
    core.settings["public_areas"] = ["ics", "homepage"]
    assert client.get("/api/tasks", headers=bearer(value), environ_overrides=PUBLIC).status_code == 404
    assert client.post("/api/tasks", json={"title": "x"}, headers=bearer(value), environ_overrides=PUBLIC).status_code == 404
    core.settings["public_areas"] = []
    assert client.get("/api/events", headers=bearer(value), environ_overrides=PUBLIC).status_code == 404


def test_api_area_opens_the_api_through_the_tunnel(client):
    value = create(client, "Phone", "write")
    core.settings["public_areas"] = ["api"]
    r = client.post("/api/tasks", json={"title": "From outside"}, headers=bearer(value), environ_overrides=PUBLIC)
    assert r.status_code == 201
    tasks = client.get("/api/tasks", headers=bearer(value), environ_overrides=PUBLIC).get_json()["tasks"]
    assert "From outside" in [t["title"] for t in tasks]
    assert client.get("/api/tasks", environ_overrides=PUBLIC).status_code == 401
    assert client.get("/", headers=bearer(value), environ_overrides=PUBLIC).status_code == 404

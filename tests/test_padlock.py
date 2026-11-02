"""The settings padlock: first password, unlocking, locking, the idle timeout, the lockout and password changes."""
import time

import pytest
from conftest import ORIGIN

from daytree import auth, core

PASSWORD = "correct horse"
LOCK_HASH = auth.hash_password(PASSWORD)


@pytest.fixture
def lock_on(monkeypatch):
    monkeypatch.setattr(auth, "SETTINGS_LOCK", True)
    core.settings.update(calendar_code="fam123", lock_hash=LOCK_HASH)


def unlock(client, password=PASSWORD):
    return client.post("/settings/unlock", data={"password": password}, headers=ORIGIN, follow_redirects=True)


def is_open(client):
    return b'id="display"' in client.get("/settings").data


def test_off_by_default(client):
    assert auth.SETTINGS_LOCK is False
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert b'id="display"' in page and b"Padlock" in page and b"Lock now" not in page


def test_password_can_be_set_while_off(client):
    """The recovery path: with the padlock off, a new password is set in the settings."""
    r = client.post("/settings/password", data={"password": "short", "confirm": "short"}, headers=ORIGIN, follow_redirects=True)
    assert b"at least 8 characters" in r.data and core.settings["lock_hash"] == ""
    r = client.post("/settings/password", data={"password": PASSWORD, "confirm": "other one"}, headers=ORIGIN, follow_redirects=True)
    assert b"do not match" in r.data and core.settings["lock_hash"] == ""
    client.post("/settings/password", data={"password": PASSWORD, "confirm": PASSWORD}, headers=ORIGIN)
    assert core.settings["lock_hash"].startswith("scrypt$") and auth.password_ok(PASSWORD) and not auth.password_ok("wrong")
    assert PASSWORD.encode() not in core.SETTINGS_FILE.read_bytes()
    page = client.get("/settings").data
    assert core.settings["lock_hash"].encode() not in page and b"Change password" in page


def test_first_password(client, lock_on):
    core.settings["lock_hash"] = ""
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert b"Set a password" in page and b'name="confirm"' in page and b'id="display"' not in page
    r = client.post("/settings/unlock", data={"password": PASSWORD, "confirm": "different"}, headers=ORIGIN, follow_redirects=True)
    assert b"do not match" in r.data and core.settings["lock_hash"] == "" and not is_open(client)
    r = client.post("/settings/unlock", data={"password": PASSWORD, "confirm": PASSWORD}, headers=ORIGIN)
    cookie = r.headers["Set-Cookie"]
    assert auth.LOCK_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert auth.password_ok(PASSWORD) and is_open(client)


def test_unlock_and_lock(client, lock_on):
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert b"Settings are locked" in page and b'name="confirm"' not in page and b'id="display"' not in page
    assert client.post("/sync", headers=ORIGIN).status_code == 403
    assert b"Wrong password." in unlock(client, "wrong").data and not is_open(client)
    unlock(client)
    assert is_open(client) and client.post("/sync", headers=ORIGIN).status_code == 302
    cookie = client.get_cookie(auth.LOCK_COOKIE).value
    client.post("/settings/lock", headers=ORIGIN)
    assert not is_open(client)
    client.set_cookie(auth.LOCK_COOKIE, cookie)                               # a copied cookie no longer opens it
    assert not is_open(client)


def test_closes_after_idle_time(client, lock_on):
    unlock(client)
    sid = client.get_cookie(auth.LOCK_COOKIE).value
    auth._sessions[sid] = time.time() - auth.LOCK_IDLE + 60
    assert is_open(client)                                                    # every request keeps it open
    auth._sessions[sid] = time.time() - auth.LOCK_IDLE - 1
    assert not is_open(client)


def test_lockout_after_wrong_passwords(client, lock_on):
    for _ in range(auth.MAX_FAILURES):
        unlock(client, "wrong")
    assert b"Too many wrong passwords" in unlock(client).data and not is_open(client)
    auth._failures["until"] = time.time() - 1
    unlock(client)
    assert is_open(client)


def test_password_change_locks_other_browsers(client, lock_on):
    from daytree.web import app
    other = app.test_client()
    unlock(client)
    unlock(other)
    assert is_open(client) and is_open(other)
    client.post("/settings/password", data={"password": "new password", "confirm": "new password"}, headers=ORIGIN)
    assert is_open(client) and not is_open(other)
    assert b"Wrong password." in unlock(other).data
    unlock(other, "new password")
    assert is_open(other)


def test_first_time_setup_works_while_locked(client, lock_on):
    core.settings["calendar_code"] = ""
    core.settings["calendars"] = [{"name": "Family", "code": "fam123"}]
    r = client.post("/settings/save", data={"calendar_code": "fam123", "interval": "180", "setup": "1"}, headers=ORIGIN)
    assert r.status_code == 302 and core.settings["calendar_code"] == "fam123"
    assert client.post("/settings/save", data={"interval": "180"}, headers=ORIGIN).status_code == 403   # set up now


def test_homepage_and_tasks_stay_open(client, lock_on):
    assert client.get("/").status_code == 200 and client.get("/kiosk").status_code == 200
    task = client.post("/api/tasks", json={"title": "Milk"}, headers=ORIGIN)
    assert task.status_code == 201
    assert client.delete(f"/api/tasks/{task.get_json()['id']}", headers=ORIGIN).status_code == 204

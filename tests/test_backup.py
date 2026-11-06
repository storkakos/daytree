"""Backup and restore: a password-encrypted file that moves settings, tasks and the waste calendar."""
import io
import json

import pytest
from conftest import ORIGIN

from daytree import backup, core, i18n

PASSWORD = "backup password"
WASTE = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20260101T000000Z\r\n"
         "DTSTART;VALUE=DATE:20261105\r\nSUMMARY:Papier\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.fixture
def state():
    """An installation with credentials, a task and a waste calendar."""
    core.settings.update(email="owner@example.com", password="TT-SECRET", sources=[core.new_source("timetree", "Family", "fam123")], greeting_name="Sam",
                         tunnel_token="eyJTUNNELSECRET", weather_lat=54.3)
    core.write_encrypted(core.TASKS_FILE, [{"id": "a1", "title": "Buy milk", "done": False}])
    core.write_encrypted(core.WASTE_FILE, {"ics": WASTE})
    yield
    core.TASKS_FILE.unlink(missing_ok=True)
    core.WASTE_FILE.unlink(missing_ok=True)


def download(client, password=PASSWORD, confirm=None):
    return client.post("/settings/backup", data={"password": password, "confirm": confirm or password}, headers=ORIGIN)


def upload(client, raw, password=PASSWORD):
    return client.post("/settings/restore", data={"backup_file": (io.BytesIO(raw), "backup.json"), "password": password},
                       headers=ORIGIN, content_type="multipart/form-data", follow_redirects=True)


def test_download_is_encrypted(client, state):
    r = download(client)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.headers["Content-Disposition"].startswith('attachment; filename="daytree-backup-')
    envelope = json.loads(r.data)
    assert envelope["format"] == "daytree-backup" and envelope["version"] == 1 and envelope["kdf"]["name"] == "scrypt"
    for secret in (b"TT-SECRET", b"owner@example.com", b"eyJTUNNELSECRET", b"Buy milk", b"Papier", core.settings["token"].encode()):
        assert secret not in r.data


def test_password_rules(client, state):
    r = client.post("/settings/backup", data={"password": "short", "confirm": "short"}, headers=ORIGIN, follow_redirects=True)
    assert b"at least 8" in r.data
    r = client.post("/settings/backup", data={"password": PASSWORD, "confirm": "other one"}, headers=ORIGIN, follow_redirects=True)
    assert b"do not match" in r.data


def test_restore_brings_everything_back(client, state):
    raw = download(client).data
    core.settings.update(email="someone@else", password="", greeting_name="", tunnel_token="", weather_lat=None)
    core.write_encrypted(core.TASKS_FILE, [])
    core.WASTE_FILE.unlink()
    r = upload(client, raw)
    assert b"Backup restored." in r.data
    assert core.settings["email"] == "owner@example.com" and core.settings["password"] == "TT-SECRET"
    assert core.settings["tunnel_token"] == "eyJTUNNELSECRET" and core.settings["weather_lat"] == 54.3
    assert core.read_encrypted(core.TASKS_FILE) == [{"id": "a1", "title": "Buy milk", "done": False}]
    assert core.read_encrypted(core.WASTE_FILE) == {"ics": WASTE}
    assert b"TT-SECRET" not in core.SETTINGS_FILE.read_bytes()


def test_backup_without_waste_removes_it(client, state):
    core.WASTE_FILE.unlink()
    raw = download(client).data
    core.write_encrypted(core.WASTE_FILE, {"ics": WASTE})
    upload(client, raw)
    assert not core.WASTE_FILE.exists()


def unchanged(client, raw, password, message):
    before = (dict(core.settings), core.TASKS_FILE.read_bytes(), core.WASTE_FILE.read_bytes())
    r = upload(client, raw, password)
    assert message.encode() in r.data, r.data[:300]
    assert (dict(core.settings), core.TASKS_FILE.read_bytes(), core.WASTE_FILE.read_bytes()) == before


def test_wrong_password_changes_nothing(client, state):
    unchanged(client, download(client).data, "wrong password", "Wrong password, or the file is damaged.")


@pytest.mark.parametrize("raw", [b"not json", b"{}", json.dumps({"format": "other"}).encode(), b"x" * (backup.MAX_BYTES + 1)])
def test_other_files_change_nothing(client, state, raw):
    r = upload(client, raw)
    assert b"not a Daytree backup" in r.data or b"too large" in r.data


def test_expensive_key_parameters_are_refused(client, state):
    envelope = json.loads(download(client).data)
    envelope["kdf"]["n"] = 2 ** 22
    unchanged(client, json.dumps(envelope).encode(), PASSWORD, "This is not a Daytree backup.")


@pytest.mark.parametrize("change", [
    {"settings": "not a dict"}, {"settings": {"interval_minutes": "often"}}, {"settings": {"tasks_enabled": "yes"}},
    {"tasks": [{"id": "x", "title": "", "done": False}]}, {"tasks": [{"id": 1, "title": "x", "done": False}]},
    {"waste": "not a calendar"},
])
def test_damaged_content_changes_nothing(client, state, change):
    content = {"settings": dict(core.settings), "tasks": [], "waste": None}
    content.update(change)
    n, r, p = backup.SCRYPT
    salt = b"s" * 16
    data = backup._fernet(PASSWORD, salt, n, r, p).encrypt(json.dumps(content).encode()).decode()
    raw = json.dumps({"format": "daytree-backup", "version": 1, "kdf": {"name": "scrypt", "n": n, "r": r, "p": p,
                                                                         "salt": salt.hex()}, "data": data}).encode()
    unchanged(client, raw, PASSWORD, "The backup is incomplete.")


def test_old_settings_in_a_backup_are_brought_up_to_date(client, state):
    content = {"settings": {"email": "a@b.de", "public_mode": "dashboard", "interval_minutes": 7}, "tasks": [], "waste": None}
    backup.restore(backup._checked(content))
    assert core.settings["public_areas"] == ["ics", "homepage"] and core.settings["interval_minutes"] == 30
    assert "public_mode" not in core.settings and core.settings["token"]


def test_messages_are_translated():
    for text in ("This is not a Daytree backup.", "Wrong password, or the file is damaged.", "The backup is incomplete.",
                 "The file is too large."):
        assert text in i18n.GERMAN

"""Encryption at rest: the settings, the key file and the tasks."""
import os
import stat
import subprocess
import sys

import pytest
from conftest import DATA, LEGACY, ORIGIN, ROOT, SECRETS

from daytree import core

posix_only = pytest.mark.skipif(os.name == "nt", reason="file modes need Linux")


def test_legacy_settings_migrated_and_encrypted():
    assert not (DATA / "settings.json").exists(), "plaintext file must be gone"
    enc = core.SETTINGS_FILE.read_bytes()
    for secret in LEGACY.values():
        assert secret.encode() not in enc, secret
    assert core.settings["password"] == LEGACY["password"] and core.settings["token"] == LEGACY["token"]
    assert not any(p.name == "secret.key" for p in DATA.iterdir()), "key must not live in the data dir"


@posix_only
def test_key_and_settings_files_are_private():
    assert stat.S_IMODE((SECRETS / "secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE(core.SETTINGS_FILE.stat().st_mode) == 0o600


def test_wrong_key_aborts_and_leaves_data_alone():
    enc = core.SETTINGS_FILE.read_bytes()
    res = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); import daytree.core"],
                         capture_output=True, text=True, timeout=60,
                         env={**os.environ, "SECRET_KEY_FILE": str(SECRETS / "other.key")})
    assert res.returncode != 0 and "cannot be decrypted" in (res.stderr + res.stdout), res.stderr[-300:]
    assert core.SETTINGS_FILE.read_bytes() == enc


def test_password_never_rendered(client):
    assert client.get("/").status_code == 200
    assert LEGACY["password"].encode() not in client.get("/settings").data


def test_tasks_lifecycle_and_encryption(client):
    task = client.post("/api/tasks", json={"title": "Secret task title"}, headers=ORIGIN).get_json()
    assert b"Secret task title" not in core.TASKS_FILE.read_bytes()
    if os.name != "nt":
        assert stat.S_IMODE(core.TASKS_FILE.stat().st_mode) == 0o600
    assert client.patch(f"/api/tasks/{task['id']}", json={"done": True}, headers=ORIGIN).get_json()["done"] is True
    assert client.get("/api/tasks").get_json()["tasks"][-1]["done"] is True
    assert client.post("/api/tasks", json={"title": " "}, headers=ORIGIN).status_code == 400
    assert client.delete(f"/api/tasks/{task['id']}", headers=ORIGIN).status_code == 204
    assert client.delete(f"/api/tasks/{task['id']}", headers=ORIGIN).status_code == 404

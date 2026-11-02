"""Shared test setup. No network, no Docker, no real TimeTree or Cloudflare.

The app reads its key and settings on import, so the temporary data and key directories, a fake `cloudflared`
and an old plaintext settings file (to migrate) are prepared here before anything imports the app.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp())
DATA, SECRETS, BIN = TMP / "data", TMP / "secrets", TMP / "bin"
for folder in (DATA, SECRETS, BIN):
    folder.mkdir()
os.environ.update(DATA_DIR=str(DATA), SECRET_KEY_FILE=str(SECRETS / "secret.key"))
os.environ.pop("SECRET_KEY", None)

# Fake cloudflared: reports a connection and writes the token it received via the environment
TOKEN_SEEN = TMP / "tunnel_token_seen"
fake = BIN / "cloudflared"
fake.write_text('#!/bin/sh\necho "2026 INF Registered tunnel connection connIndex=0"\n'
                f'echo "$TUNNEL_TOKEN" > {TOKEN_SEEN}\nexec sleep 300\n')
fake.chmod(0o755)
os.environ["PATH"] = f"{BIN}{os.pathsep}{os.environ['PATH']}"

LEGACY = {"email": "a@b.de", "password": "PLAINPW-123", "token": "legacytoken-ABC"}
(DATA / "settings.json").write_text(json.dumps(LEGACY))

from daytree import core, display  # noqa: E402
from daytree.web import app  # noqa: E402

ORIGIN = {"Origin": "http://localhost"}        # same-site header that passes the CSRF check
PUBLIC = {"SERVER_PORT": "8081"}               # environ_overrides for a request through the tunnel


@pytest.fixture
def client():
    return app.test_client()


@pytest.fixture(autouse=True)
def restore_settings():
    """Every test starts from the settings after the migration, and nothing it changes leaks into the next one."""
    saved = copy.deepcopy(core.settings)
    yield
    core.settings.clear()
    core.settings.update(saved)
    core.save_settings(core.settings)
    core.apply_timezone()
    display.backgrounds.clear()
    display.weather.clear()

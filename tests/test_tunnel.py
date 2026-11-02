"""The Cloudflare tunnel with a fake cloudflared: token extraction, start and stop, token only via the environment."""
import os
import threading
import time

import pytest
from conftest import ORIGIN, TOKEN_SEEN

from daytree import core, tunnel

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake cloudflared is a shell script")

TOKEN = "eyJhIjoiMTIzNDU2IiwidCI6ImFiY2RlZiIsInMiOiJ4eXoxMjM9In0="


def wait(condition, seconds=15):
    end = time.time() + seconds
    while time.time() < end and not condition():
        time.sleep(0.2)
    return condition()


@pytest.fixture(scope="module", autouse=True)
def supervisor():
    threading.Thread(target=tunnel.supervisor, daemon=True).start()


def test_token_checks(client):
    r = client.post("/settings/tunnel", data={"tunnel_token": "not a token", "public_mode": "ics"}, headers=ORIGIN, follow_redirects=True)
    assert b"not recognized" in r.data and core.settings["tunnel_token"] == ""
    r = client.post("/settings/tunnel", data={"tunnel_enabled": "1", "public_mode": "ics"}, headers=ORIGIN, follow_redirects=True)
    assert b"token first" in r.data and core.settings["tunnel_enabled"] is False


def test_start_and_stop(client):
    command = f"docker run cloudflare/cloudflared:latest tunnel --no-autoupdate run --token {TOKEN}"
    client.post("/settings/tunnel", data={"tunnel_token": command, "tunnel_enabled": "1", "public_mode": "ics"}, headers=ORIGIN)
    assert core.settings["tunnel_token"] == TOKEN and core.settings["tunnel_enabled"] is True
    assert wait(lambda: tunnel.status["status"] == "connected"), tunnel.status
    assert wait(lambda: TOKEN_SEEN.exists() and TOKEN_SEEN.read_text().strip())
    assert TOKEN_SEEN.read_text().strip() == TOKEN, "token must arrive via the TUNNEL_TOKEN environment variable"
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert TOKEN.encode() not in page and b">Status<" in page and b"Connected" in page
    assert TOKEN.encode() not in core.SETTINGS_FILE.read_bytes()

    proc = tunnel.status["proc"]
    client.post("/settings/tunnel", data={"public_mode": "ics"}, headers=ORIGIN)              # checkbox off -> stop
    assert wait(lambda: proc.poll() is not None and tunnel.status["status"] == "off"), tunnel.status
    assert core.settings["tunnel_token"] == TOKEN                                             # token stays stored
    client.post("/settings/tunnel", data={"clear_token": "1", "public_mode": "ics"}, headers=ORIGIN)
    assert core.settings["tunnel_token"] == ""

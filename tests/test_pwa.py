"""The homepage can be installed as an app; /kiosk cannot."""
import json
import struct

from conftest import ROOT


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_homepage_links_the_manifest_and_kiosk_does_not(client):
    assert b'rel="manifest" href="/static/manifest.json"' in client.get("/").data
    assert b'rel="manifest"' not in client.get("/kiosk").data


def test_manifest(client):
    r = client.get("/static/manifest.json")
    manifest = json.loads(r.data)
    assert r.status_code == 200 and manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["display"] == "standalone" and manifest["name"] == "Daytree"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
        width, height = png_size(ROOT / icon["src"].lstrip("/"))
        assert icon["sizes"] == f"{width}x{height}"
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}


def test_no_offline_mode(client):
    """Old calendar data is worse than a clear error, so there is no service worker caching pages."""
    assert b"serviceWorker" not in client.get("/").data
    assert not list((ROOT / "static").glob("*sw*.js"))

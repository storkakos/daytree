"""Appearance: automatic, light or dark for the settings pages, the padlock page and the setup popup."""
import pytest
from conftest import ORIGIN

from daytree import auth, core


def html_class(client, path):
    page = client.get(path).data.decode()
    return page[page.index("<html"):page.index(">", page.index("<html"))]


def test_automatic_by_default(client):
    assert core.settings["appearance"] == "auto"
    assert 'class=""' in html_class(client, "/settings")
    for path in ("/settings", "/"):
        assert b'<meta name="color-scheme" content="light dark">' in client.get(path).data


@pytest.mark.parametrize("value", ["light", "dark"])
def test_fixed_look_everywhere(client, monkeypatch, value):
    r = client.post("/settings/display", data={"tasks_enabled": "1", "appearance": value}, headers={**ORIGIN, "X-Autosave": "1"})
    assert r.status_code == 200 and core.settings["appearance"] == value
    for path in ("/settings", "/"):
        assert f'class="{value}"' in html_class(client, path)
        # Browsers with their own dark mode must not recolor a fixed look
        assert f'<meta name="color-scheme" content="only {value}">' in client.get(path).data.decode()
    monkeypatch.setattr(auth, "SETTINGS_LOCK", True)
    core.settings.update(calendar_code="fam123", lock_hash=auth.hash_password("correct horse"))
    page = client.get("/settings").data.decode()
    assert "Settings are locked" in page or "Einstellungen sind gesperrt" in page
    assert f'class="{value}"' in html_class(client, "/settings")


def test_invalid_value(client):
    assert client.post("/settings/display", data={"tasks_enabled": "1", "appearance": "sepia"}, headers=ORIGIN).status_code == 400


def test_settings_offer_the_choice(client):
    page = client.get("/settings", headers={"Accept-Language": "en"}).data.decode()
    assert 'name="appearance" value="auto" checked' in page and ">Light<" in page and ">Dark<" in page

"""The settings pages: TimeTree login, calendar and interval, first-time setup, language, time zone and translations."""
import os
import re
import time
from unittest import mock

import pytest
from conftest import ORIGIN, ROOT

from daytree import VERSION, core, display, i18n


class Resp:
    def __init__(self, code, js=None, cookies=None):
        self.status_code, self._js, self.cookies, self.text = code, js or {}, cookies or {}, "x"

    def json(self):
        return self._js


CALENDARS = {"calendars": [{"name": "Family", "alias_code": "fam123", "deactivated_at": None},
                           {"name": None, "alias_code": "work9", "deactivated_at": None},
                           {"name": "Old", "alias_code": "old1", "deactivated_at": 1}]}


def timetree():
    """TimeTree's HTTP calls, answered with a session and the calendar list above."""
    return mock.patch("requests.put", return_value=Resp(200, cookies={"_session_id": "S"})), \
        mock.patch("requests.Session.get", return_value=Resp(200, CALENDARS))


def test_login_lists_active_calendars(client):
    put, get = timetree()
    with put, get:
        r = client.post("/settings/login", data={"email": "a@b.de", "password": ""}, headers=ORIGIN, follow_redirects=True)
    assert b"2 calendars found" in r.data and b"old1" not in r.data and b"<small>work9</small>" in r.data
    assert b"PLAINPW-123" not in core.SETTINGS_FILE.read_bytes()


def test_calendar_and_interval(client):
    core.settings["calendars"] = [{"name": "Unnamed", "code": "work9"}]
    client.post("/settings/save", data={"calendar_code": "work9", "interval": "2"}, headers=ORIGIN)
    assert [s["id"] for s in core.settings["sources"]] == ["work9"] and core.settings["interval_minutes"] == 30   # clamped
    assert core.DEFAULTS["interval_minutes"] == 180
    for sent, stored in (("100", 90), ("1440", 1440), ("9999", 1440)):
        client.post("/settings/save", data={"interval": sent}, headers=ORIGIN)
        assert core.settings["interval_minutes"] == stored, sent
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert b'<option value="30" >0.5 h</option>' in page and b'<option value="1440" selected>24 h</option>' in page
    assert page.count(b"<option value=") >= 48 and b"Every 24 h" in page
    assert VERSION.encode() in page and b'class="about"' in page


def test_new_ics_token(client):
    old = core.settings["token"]
    client.post("/settings/save", data={"interval": "180", "new_token": "1"}, headers=ORIGIN)
    assert core.settings["token"] != old and core.settings["token"].encode() in client.get("/settings").data


def test_first_time_setup(client):
    core.settings.update(sources=[core.new_source("timetree", "Family", "fam123")])
    page = client.get("/").data
    assert b'id="setup"' not in page and b'id="gear"' in page and b'href="/settings"' in page
    core.settings.update(email="", password="", sources=[], calendars=[])
    page = client.get("/", headers={"Accept-Language": "en"}).data
    assert b'id="setup"' in page and b"Step 1 of 2" in page and b'id="gear"' not in page
    put, get = timetree()
    with put, get:
        r = client.post("/settings/login", data={"email": "a@b.de", "password": "PLAINPW-123", "setup": "1"}, headers=ORIGIN)
    assert r.status_code == 302 and r.location.endswith("/") and core.settings["sources"] == []
    assert b"Step 2 of 2" in client.get("/", headers={"Accept-Language": "en"}).data
    r = client.post("/settings/save", data={"calendar_code": "fam123", "interval": "180", "setup": "1"}, headers=ORIGIN)
    assert r.location.endswith("/") and [s["id"] for s in core.settings["sources"]] == ["fam123"]
    assert b'id="setup"' not in client.get("/").data


def test_language_follows_browser_then_setting(client):
    page = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9"}).data.decode()
    assert 'lang="de"' in page and "Heute" in page and "Aufgaben" in page
    assert b"Today" in client.get("/", headers={"Accept-Language": "fr"}).data     # English is the fallback
    client.post("/settings/display", data={"language": "de"}, headers=ORIGIN)
    page = client.get("/settings", headers={"Accept-Language": "en"}).data.decode()
    assert "Einstellungen" in page and "0,5 Std." in page                          # the setting overrides the browser
    core.settings["language"] = "auto"
    client.set_cookie("lang", "de")
    assert client.get("/api/config", headers={"Accept-Language": "en"}).get_json()["language"] == "de"
    client.delete_cookie("lang")
    assert client.get("/api/config", headers={"Accept-Language": "en"}).get_json()["language"] == "en"


def test_time_zone_and_formats(client):
    form = {"timezone": "Asia/Tokyo", "time_format": "12", "date_style": "numeric"}
    assert client.post("/settings/display", data=form, headers=ORIGIN).status_code == 302
    assert os.environ["TZ"] == "Asia/Tokyo"
    if hasattr(time, "tzset"):
        assert time.tzname[0] == "JST"
    page = client.get("/").data.decode()
    assert '"hour12": true' in page and '"timeZone": "Asia/Tokyo"' in page and '"dateStyle": "numeric"' in page
    assert b'id="tasks"' not in client.get("/").data and b"/api/tasks" not in client.get("/").data   # tasks switch off
    client.post("/settings/display", data={"timezone": "", "tasks_enabled": "1"}, headers=ORIGIN)
    assert core.settings["timezone"] == "" and b'id="tasks"' in client.get("/").data


@pytest.mark.parametrize("bad", [{"timezone": "Mars/Base"}, {"language": "xx"}, {"background_color": "red"}, {"font_family": "x"},
                                 {"background_every": "x"}, {"clock_size": "abc"}, {"weather_forecast": "minutes"}])
def test_display_settings_validated(client, bad):
    assert client.post("/settings/display", data={"tasks_enabled": "1", **bad}, headers=ORIGIN).status_code == 400


def test_display_numbers_clamped(client):
    client.post("/settings/display", data={"tasks_enabled": "1", "clock_size": "9999"}, headers=ORIGIN)
    assert core.settings["clock_size"] == 200
    client.post("/settings/display", data={"tasks_enabled": "1", "background_every": "custom", "background_minutes": "0"}, headers=ORIGIN)
    assert core.settings["background_every"] == "custom" and core.settings["background_minutes"] == 1


def test_every_ui_string_has_a_german_translation():
    used = set()
    for f in [*(ROOT / "daytree").glob("*.py"), *(ROOT / "templates").glob("*.html")]:
        used |= set(re.findall(r'_\("((?:[^"\\]|\\.)+)"\)', f.read_text(encoding="utf-8")))
    missing = (used | set(display.display_config_strings()) | set(display.WEATHER_TEXT)) - set(i18n.GERMAN)
    assert not missing, missing

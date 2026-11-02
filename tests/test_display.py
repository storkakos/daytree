"""The homepage: waste bins, background photos, weather, greeting and its config. External services are mocked."""
import io
import time
from datetime import date
from unittest import mock

import pytest
from conftest import ORIGIN, PUBLIC, ROOT

from daytree import VERSION, core, display

WASTE_DAY = date.today().strftime("%Y%m%d")
WASTE_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    f"BEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;VALUE=DATE:{WASTE_DAY}\r\nSUMMARY:Biotonne\r\n"
    "RRULE:FREQ=DAILY;INTERVAL=2;COUNT=9\r\nEND:VEVENT\r\n"
    f"BEGIN:VEVENT\r\nUID:2\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;VALUE=DATE:{WASTE_DAY}\r\nSUMMARY:Altpapier\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
AUTOSAVE = {**ORIGIN, "X-Autosave": "1"}


@pytest.fixture
def upload(client):
    def send(body, **form):
        return client.post("/settings/waste", data={**form, "waste_file": (io.BytesIO(body), "w.ics")}, headers=ORIGIN,
                           content_type="multipart/form-data")
    yield send
    core.WASTE_FILE.unlink(missing_ok=True)


def titles(day):
    return [b["title"] for b in day["bins"]]


def test_waste_upload_validated(client, upload):
    assert client.get("/api/waste").get_json() == {"enabled": False}
    upload(b"not a calendar", waste_enabled="1")
    assert not core.WASTE_FILE.exists() and core.settings["waste_enabled"] is False
    assert upload(b"x" * (display.WASTE_MAX_BYTES + 1)).status_code == 302 and not core.WASTE_FILE.exists()


def test_waste_days_colors_and_day_before(client, upload):
    upload(WASTE_ICS.encode())
    assert core.settings["waste_enabled"] is True and b"Biotonne" not in core.WASTE_FILE.read_bytes()   # encrypted
    days = client.get("/api/waste").get_json()["days"]
    assert len(days) == 8 and days[1]["bins"] == [] and titles(days[2]) == ["Biotonne"]
    assert {b["title"]: b["color"] for b in days[0]["bins"]} == {"Altpapier": "#2f6fde", "Biotonne": "#8b5a2b"}
    page = client.get("/settings").data.decode()
    assert "Altpapier" in page and 'name="waste_color_1"' in page and 'name="waste_day_before"' in page

    stamp = client.get("/api/config").get_json()["wasteStamp"]
    client.post("/settings/waste", data={"waste_enabled": "1", "waste_day_before": "1"}, headers=AUTOSAVE)
    days = client.get("/api/waste").get_json()["days"]
    assert client.get("/api/config").get_json()["wasteStamp"] != stamp and days[0]["bins"] == []
    assert titles(days[1]) == ["Biotonne"] and titles(days[7]) == ["Biotonne"]
    client.post("/settings/waste", data={"waste_enabled": "1"}, headers=AUTOSAVE)
    assert core.settings["waste_day_before"] is False and client.get("/api/waste").get_json()["days"][7]["bins"] == []

    stamp = client.get("/api/config").get_json()["wasteStamp"]
    r = client.post("/settings/waste", data={"waste_enabled": "1", "waste_color_0": "#112233", "waste_color_1": "bad"}, headers=ORIGIN)
    assert r.headers["Location"].endswith("/settings?updated=1") and client.get("/api/config").get_json()["wasteStamp"] != stamp
    assert core.settings["waste_colors"] == {"Altpapier": "#112233"}


def test_waste_switch_and_remove(client, upload):
    upload(WASTE_ICS.encode())
    r = client.post("/settings/waste", data={}, headers=AUTOSAVE)                       # switch turned off
    assert r.get_json()["message"] and core.settings["waste_enabled"] is False
    assert client.get("/api/waste").get_json() == {"enabled": False} and client.get("/api/config").get_json()["waste"] is False
    client.post("/settings/waste", data={"waste_enabled": "1"}, headers=AUTOSAVE)
    assert client.get("/api/waste").get_json()["enabled"] is True and client.get("/api/config").get_json()["waste"] is True
    client.post("/settings/waste", data={"remove": "1"}, headers=ORIGIN)
    assert not core.WASTE_FILE.exists() and core.settings["waste_enabled"] is False and core.settings["waste_colors"] == {}


def test_background_cached_and_refreshed(client):
    photos = [{"id": "7", "author": "Ann", "width": 300, "height": 200, "url": "https://u/7"}]
    with mock.patch.object(display, "fetch_json", return_value=photos) as fetch:
        assert client.get("/api/background").get_json()["author"] == "Ann"
        assert client.get("/api/background").get_json()["url"].endswith("/id/7/2560/1600")
        assert fetch.call_count == 1                                              # cached until the next change
        assert client.post("/api/background/refresh", headers=ORIGIN).status_code == 204
        client.get("/api/background")
        assert fetch.call_count == 2


def test_background_follows_the_change_setting(client):
    photo = {"url": "new", "author": "Bo", "link": "l", "location": ""}
    with mock.patch.object(display, "picsum_photo", return_value=photo):
        client.get("/api/background")
        assert display.load_backgrounds()["landscape"]["url"] == "new"            # survives a restart
        display.backgrounds["landscape"].update(url="old", fetched=time.time() - 7200)
        core.settings["background_every"] = "fixed"
        assert client.get("/api/background").get_json()["url"] == "old"
        core.settings["background_every"] = "hour"                                # the setting applies to the current photo
        assert client.get("/api/background").get_json()["url"] == "new"
        display.backgrounds["landscape"].update(url="old", fetched=time.time() - 600)
        core.settings.update(background_every="custom", background_minutes=30)
        assert client.get("/api/background").get_json()["url"] == "old"
        core.settings["background_minutes"] = 5
        assert client.get("/api/background").get_json()["url"] == "new"


def test_offline_services(client):
    with mock.patch.object(display, "fetch_json", side_effect=OSError("offline")):
        assert client.get("/api/background").get_json() == {"url": None}
        r = client.post("/settings/display", data={"tasks_enabled": "1", "weather_city": "Nowhere"}, headers=AUTOSAVE)
    assert core.settings["weather_lat"] is None and r.get_json()["message"] == "Place not found."


def test_weather_place_and_current(client):
    place = {"results": [{"latitude": 54.3, "longitude": 9.6, "name": "Rendsburg"}]}
    form = {"tasks_enabled": "1", "weather_enabled": "1", "weather_city": "rendsburg", "greeting_enabled": "1", "greeting_name": "Sam"}
    with mock.patch.object(display, "fetch_json", return_value=place):
        client.post("/settings/display", data=form, headers=ORIGIN)
    assert core.settings["weather_city"] == "Rendsburg" and core.settings["weather_lat"] == 54.3
    with mock.patch.object(display, "fetch_json", return_value={"current": {"temperature_2m": 9.6, "weather_code": 61}}):
        w = client.get("/api/weather", headers={"Accept-Language": "de"}).get_json()
    assert w == {"enabled": True, "temperature": 10, "text": "Regen", "icon": "weather-day-rain", "city": "Rendsburg", "hours": [], "days": []}
    cfg = client.get("/api/config").get_json()
    assert cfg["version"] == VERSION and cfg["name"] == "Sam" and cfg["greeting"] is True
    client.post("/settings/display", data={"tasks_enabled": "1", "weather_city": ""}, headers=ORIGIN)
    assert core.settings["weather_city"] == ""


def test_weather_forecast(client):
    core.settings.update(weather_enabled=True, weather_lat=54.3, weather_lon=9.7)
    times = [f"2026-11-{d:02d}T{h:02d}:00" for d in range(1, 9) for h in range(24)]
    answer = {
        "current": {"time": "2026-11-01T17:15", "temperature_2m": 9.6, "weather_code": 61},
        "hourly": {"time": times, "temperature_2m": [5.4] * len(times), "weather_code": [0] * len(times)},
        "daily": {"time": [f"2026-11-{d:02d}" for d in range(1, 9)], "temperature_2m_max": [8.6] * 8,
                  "temperature_2m_min": [1.2] * 8, "weather_code": [3] * 8},
    }
    with mock.patch.object(display, "fetch_json", return_value=answer):
        w = client.get("/api/weather").get_json()
    assert len(w["hours"]) == 10 and w["hours"][0] == {"time": "2026-11-01T17:00", "temperature": 5, "icon": "weather-day-clearsky"}
    assert len(w["days"]) == 7 and w["days"][0] == {"date": "2026-11-01", "high": 9, "low": 1, "icon": "weather-day-overcastclouds"}
    client.post("/settings/display", data={"tasks_enabled": "1", "weather_enabled": "1", "weather_forecast": "days"}, headers=ORIGIN)
    assert client.get("/api/config").get_json()["forecast"] == "days"


def test_weather_icons_exist():
    assert display.weather_icon(0, False) == "weather-night-clearsky" and display.weather_icon(80, False) == "weather-night-rain"
    sprite = (ROOT / "static" / "weather-sprites.svg").read_text()
    assert all(f'id="weather-{time}-{name}"' in sprite
               for name in display.WEATHER_ICONS for time in ("day", "night") if (time, name) != ("night", "sunnyrain"))


def test_pages_are_never_cached(client):
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_kiosk_is_read_only(client):
    core.settings.update(greeting_name="Private Name", public_mode="dashboard")
    page = client.get("/kiosk").data
    assert b"Private Name" in page and b'id="gear"' not in page and b'id="panel"' not in page
    assert b'id="tasks"' in page and b'id="task-form"' not in page and b"box.disabled = true" in page
    public_page = client.get("/kiosk", environ_overrides=PUBLIC).data
    assert b"Private Name" not in public_page and b'id="tasks"' not in public_page

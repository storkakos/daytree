"""Several calendars: selection, sync one after the other, colors, the combined feed and one feed per calendar."""
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pytest
from conftest import DATA, ORIGIN, PUBLIC, use_calendar
from icalendar import Calendar

from daytree import core, sync

TODAY = date.today()
BERLIN = ["BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "BEGIN:STANDARD", "DTSTART:19701025T030000", "TZOFFSETFROM:+0200",
          "TZOFFSETTO:+0100", "END:STANDARD", "END:VTIMEZONE"]


def ics(*events, zone=True):
    """A calendar with a Berlin time zone and timed events given as (uid, title, days from today)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", *(BERLIN if zone else [])]
    for uid, title, days in events:
        day = (TODAY + timedelta(days=days)).strftime("%Y%m%d")
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART;TZID=Europe/Berlin:{day}T090000",
                  f"DTEND;TZID=Europe/Berlin:{day}T100000", "END:VEVENT"]
    return "\r\n".join([*lines, "END:VCALENDAR", ""])


@pytest.fixture
def two():
    core.settings["sources"] = []
    family = use_calendar(ics(("same", "Dentist", 1), ("f2", "Football", 2)), "fam123", "Family", {"f2": "#e73b3b"})
    work = use_calendar(ics(("same", "Standup", 1)), "work9", "Work")
    core.settings["calendars"] = [{"name": "Family", "code": "fam123"}, {"name": "Work", "code": "work9"}]
    return family, work


def titles(events):
    return sorted(e["title"] for e in events)


def test_old_settings_become_a_calendar():
    s, dirty = core.normalize({"calendar_code": "fam123", "calendar_name": "Family"})
    assert dirty and "calendar_code" not in s and "calendar_name" not in s
    [source] = s["sources"]
    assert source["id"] == "fam123" and source["kind"] == "timetree" and source["name"] == "Family"
    assert source["feed"] and source["homepage"] and source["color"] == "" and len(source["token"]) >= 32


def test_old_unencrypted_file_moves_into_the_encrypted_one():
    use_calendar(code="fam123")
    core.LEGACY_ICS.write_bytes(ics(("1", "Secret meeting", 1)).encode())
    core.LEGACY_COLORS.write_text('{"1": "#47b2f7"}')
    sync.migrate_legacy()
    assert not core.LEGACY_ICS.exists() and not core.LEGACY_COLORS.exists()
    assert b"Secret meeting" not in core.calendar_file("fam123").read_bytes()
    assert not any(b"Secret meeting" in p.read_bytes() for p in Path(DATA).rglob("*") if p.is_file())
    [event] = sync.occurrences(TODAY, TODAY + timedelta(days=3))
    assert event["title"] == "Secret meeting" and event["color"] == "#47b2f7"


def test_sync_one_after_the_other(two, monkeypatch):
    core.settings.update(email="a@b.de", password="pw")
    calls = []

    def exporter(args, **kwargs):
        code, out = args[args.index("-c") + 1], Path(args[args.index("-o") + 1])
        calls.append(code)
        if code == "work9":
            return mock.MagicMock(returncode=1, stderr="Calendar not found", stdout="")
        out.write_text(ics(("n1", "New", 1)).replace("\r\n", "\n"))
        return mock.MagicMock(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(sync.subprocess, "run", exporter)
    sync.calendar_file("gone").parent.mkdir(exist_ok=True)
    sync.calendar_file("gone").write_bytes(b"old")                           # a calendar no longer selected
    sync.run_sync()
    assert calls == ["fam123", "work9"]
    assert sync.state["ok"] is False and sync.state["message"] == "Work: Calendar not found"
    assert titles(sync.occurrences(TODAY, TODAY + timedelta(days=3))) == ["New", "Standup"]   # Work keeps its last data
    assert b"X-WR-CALNAME:Family" in sync.load_calendar("fam123")["ics"].encode()
    assert not sync.calendar_file("gone").exists()


def test_homepage_shows_the_chosen_calendars(client, two):
    family, work = two
    events = client.get("/api/events").get_json()["events"]
    assert titles(events) == ["Dentist", "Football", "Standup"]
    assert {e["title"]: e["calendar"] for e in events} == {"Dentist": "Family", "Football": "Family", "Standup": "Work"}
    work["homepage"] = False
    assert titles(client.get("/api/events").get_json()["events"]) == ["Dentist", "Football"]
    assert titles(client.get("/api/calendars/work9/events").get_json()["events"]) == ["Standup"]   # the API still has it


def test_combined_feed(client, two):
    family, work = two
    r = client.get(f"/ics?token={core.settings['token']}")
    feed = r.data.replace(b"\r\n ", b"")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert feed.count(b"BEGIN:VTIMEZONE") == 1 and feed.count(b"BEGIN:VEVENT") == 3
    uids = [str(e["UID"]) for e in Calendar.from_ical(r.data).walk("VEVENT")]
    assert len(set(uids)) == 3 and "same" in uids and "same-work9" in uids
    work["feed"] = False
    single = client.get(f"/ics?token={core.settings['token']}").data
    assert single == sync.load_calendar("fam123")["ics"].encode()          # one calendar is served as it was synced
    family["feed"] = False
    assert client.get(f"/ics?token={core.settings['token']}").status_code == 404


def test_one_feed_per_calendar(client, two):
    family, work = two
    r = client.get(f"/ics/work9?token={work['token']}")
    assert r.status_code == 200 and b"Standup" in r.data and b"Dentist" not in r.data
    work["feed"] = False                                                   # the switch is about the combined feed
    assert client.get(f"/ics/work9?token={work['token']}").status_code == 200
    assert client.get(f"/ics/work9?token={family['token']}").status_code == 403
    assert client.get(f"/ics/work9?token={core.settings['token']}").status_code == 403
    assert client.get(f"/ics/unknown?token={work['token']}").status_code == 403
    core.settings["public_areas"] = ["ics"]
    assert client.get(f"/ics/work9?token={work['token']}", environ_overrides=PUBLIC).status_code == 200


def test_choose_calendars_in_the_settings(client, two):
    family, work = two
    family["color"] = "#112233"
    client.post("/settings/save", data={"calendars_form": "1", "calendar": ["work9"], "interval": "180"}, headers=ORIGIN)
    assert [s["id"] for s in core.settings["sources"]] == ["work9"]
    client.post("/settings/save", data={"calendars_form": "1", "calendar": ["work9", "fam123", "bogus"], "interval": "180"},
                headers=ORIGIN)
    assert [s["id"] for s in core.settings["sources"]] == ["fam123", "work9"]        # TimeTree's order
    assert core.settings["sources"][1]["token"] == work["token"]                      # kept
    r = client.post("/settings/save", data={"calendars_form": "1", "interval": "180"}, headers=ORIGIN, follow_redirects=True)
    assert "Choose at least one calendar." in r.data.decode() or "Wähle mindestens" in r.data.decode()
    assert len(core.settings["sources"]) == 2
    page = client.get("/settings").data.decode()
    assert 'name="calendar" value="fam123" checked' in page and f"/ics/work9?token={work['token']}" in page


def test_calendar_options(client, two):
    family, work = two
    data = {"color_fam123": "#8e8e93", "color_work9": "#AABBCC", "homepage_fam123": "1", "feed_work9": "1", "calendar_tags": "1"}
    r = client.post("/settings/calendars", data=data, headers={**ORIGIN, "X-Autosave": "1"})
    assert r.status_code == 200 and r.get_json()["message"]
    family, work = core.settings["sources"]
    assert family["color"] == "" and work["color"] == "#aabbcc"           # the picker's default is not a choice
    assert family["homepage"] and not family["feed"] and not work["homepage"] and work["feed"]
    assert core.settings["calendar_tags"] and client.get("/api/config").get_json()["calendarTags"] is True
    client.post("/settings/calendars", data={"color_work9": "nope"}, headers=ORIGIN)
    assert core.settings["sources"][1]["color"] == "#aabbcc" and core.settings["calendar_tags"] is False


def test_new_token_renews_every_feed(client, two):
    family, work = two
    old = {core.settings["token"], family["token"], work["token"]}
    client.post("/settings/save", data={"interval": "180", "new_token": "1"}, headers=ORIGIN)
    new = {core.settings["token"], *(s["token"] for s in core.settings["sources"])}
    assert not old & new and len(new) == 3


def test_calendar_tokens_stay_off_the_homepage(client, two):
    family, work = two
    core.settings["public_areas"] = ["homepage"]
    for page in (client.get("/").data, client.get("/", environ_overrides=PUBLIC).data, client.get("/kiosk").data):
        assert family["token"].encode() not in page and work["token"].encode() not in page
    assert b"Family, Work" in client.get("/").data                          # the page title names the calendars


def test_calendar_data_is_encrypted(two):
    assert b"Dentist" not in core.calendar_file("fam123").read_bytes()

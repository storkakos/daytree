"""Calendars from a file or a URL: adding, fetching on their own interval, errors without the URL, backup."""
import io
from datetime import date, timedelta
from unittest import mock

import pytest
import requests
from conftest import ORIGIN, PUBLIC, use_calendar

from daytree import backup, core, sync

TODAY = date.today()
SECRET_URL = "https://holidays.example.com/feed.ics?key=PRIVATE-KEY-123"


def holidays(title="Autumn holidays", days=2):
    day = (TODAY + timedelta(days=days)).strftime("%Y%m%d")
    end = (TODAY + timedelta(days=days + 1)).strftime("%Y%m%d")
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", "BEGIN:VEVENT", "UID:h1",
                        f"SUMMARY:{title}", f"DTSTART;VALUE=DATE:{day}", f"DTEND;VALUE=DATE:{end}", "COLOR:#ff9500",
                        "END:VEVENT", "END:VCALENDAR", ""]).encode()


def answer(body=b"", status=200):
    response = mock.MagicMock(status_code=status)
    stream = io.BytesIO(body)
    response.raw.read = lambda size, decode_content=False: stream.read(size)
    response.__enter__.return_value = response
    return response


@pytest.fixture
def fetched(monkeypatch):
    """requests.get answers with whatever the test puts in the list, last first."""
    answers = []

    def respond(*args, **kwargs):
        item = answers.pop()
        if isinstance(item, Exception):
            raise item
        return item

    get = mock.MagicMock(side_effect=respond)
    monkeypatch.setattr(sync.requests, "get", get)
    return answers, get


def add(client, **data):
    files = data.pop("file", None)
    if files is not None:
        data["file"] = (io.BytesIO(files), "Holidays 2026.ics")
    return client.post("/settings/sources", data=data, headers=ORIGIN, content_type="multipart/form-data", follow_redirects=True)


def test_add_a_file(client):
    use_calendar(code="fam123")
    r = add(client, file=holidays())
    assert "Calendar added." in r.data.decode() or "Kalender hinzugefügt." in r.data.decode()
    source = core.settings["sources"][-1]
    assert source["kind"] == "file" and source["name"] == "Holidays 2026" and source["feed"] and source["homepage"]
    [event] = [e for e in client.get("/api/events").get_json()["events"] if e["calendar_id"] == source["id"]]
    assert event["title"] == "Autumn holidays" and event["color"] == "#ff9500" and event["calendar"] == "Holidays 2026"
    assert b"Autumn holidays" not in core.calendar_file(source["id"]).read_bytes()


@pytest.mark.parametrize("body", [b"not a calendar", b"x" * (sync.MAX_SOURCE_BYTES + 1)])
def test_bad_files_are_refused(client, body):
    before = list(core.settings["sources"])
    r = add(client, file=body)
    assert "Could not load" in r.data.decode() or "konnte nicht geladen" in r.data.decode()
    assert core.settings["sources"] == before and not list(core.CALENDAR_DIR.glob("*.enc"))


def test_add_a_url(client, fetched):
    answers, get = fetched
    answers.append(answer(holidays()))
    add(client, url=SECRET_URL.replace("https://", "webcal://"), name="Holidays", interval="720")
    source = core.settings["sources"][-1]
    assert source["kind"] == "url" and source["interval_minutes"] == 720 and source["url"].startswith("webcal://")
    assert get.call_args.args[0] == SECRET_URL                              # webcal is fetched over https
    assert b"PRIVATE-KEY-123" not in core.SETTINGS_FILE.read_bytes()
    page = client.get("/settings").data.decode()
    assert "PRIVATE-KEY-123" not in page and "holidays.example.com" in page
    assert "PRIVATE-KEY-123" not in client.get("/").data.decode() and "PRIVATE-KEY-123" not in client.get("/api/calendars").data.decode()
    assert "Autumn holidays" in client.get(f"/ics?token={core.settings['token']}").data.decode()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x.ics", "https://", "not a url"])
def test_only_web_addresses(client, fetched, url):
    before = list(core.settings["sources"])
    r = add(client, url=url)
    assert "https://" in r.data.decode() and core.settings["sources"] == before
    assert not fetched[1].called


@pytest.mark.parametrize("failure, message", [
    (answer(status=404), "HTTP 404"),
    (requests.ConnectionError(f"Max retries exceeded with url: {SECRET_URL}"), "The server could not be reached."),
    (requests.Timeout(SECRET_URL), "The server did not answer in time."),
    (answer(b"<html>login</html>"), "The address is neither a calendar file nor an events page Daytree can read."),
    (answer(b"BEGIN:VCALENDAR broken"), "The file is not a usable calendar."),
])
def test_fetch_errors_never_show_the_url(client, fetched, failure, message):
    answers, _ = fetched
    answers.append(answer(holidays()))
    add(client, url=SECRET_URL, name="Holidays")
    source = core.settings["sources"][-1]
    answers.append(failure)
    r = client.post("/settings/sources", data={"refresh": source["id"]}, headers=ORIGIN, follow_redirects=True)
    assert sync.state["sources"][source["id"]]["message"] == message
    assert "PRIVATE-KEY-123" not in r.data.decode() and "PRIVATE-KEY-123" not in core.STATE_FILE.read_text()
    assert [e["title"] for e in sync.occurrences(TODAY, TODAY + timedelta(days=5), [source["id"]])] == ["Autumn holidays"]


def test_each_url_on_its_own_interval(client, fetched):
    answers, get = fetched
    answers.append(answer(holidays()))
    add(client, url=SECRET_URL, interval="60")
    source = core.settings["sources"][-1]
    last = sync.state["sources"][source["id"]]["last_run"]
    sync.refresh_due(last + 59 * 60)
    assert get.call_count == 1
    answers.append(answer(holidays("Winter holidays")))
    sync.refresh_due(last + 60 * 60)
    assert get.call_count == 2
    assert "Winter holidays" in [e["title"] for e in sync.occurrences(TODAY, TODAY + timedelta(days=5), [source["id"]])]
    client.post("/settings/calendars", data={f"interval_{source['id']}": "9999", f"homepage_{source['id']}": "1"}, headers=ORIGIN)
    assert core.settings["sources"][-1]["interval_minutes"] == 1440


def test_remove(client, fetched):
    use_calendar(code="fam123")
    add(client, file=holidays())
    source = core.settings["sources"][-1]
    assert client.post("/settings/sources", data={"remove": "fam123"}, headers=ORIGIN).status_code == 404   # TimeTree's stay
    client.post("/settings/sources", data={"remove": source["id"]}, headers=ORIGIN)
    assert [s["id"] for s in core.settings["sources"]] == ["fam123"] and not core.calendar_file(source["id"]).exists()


def test_public_feed_of_a_file_calendar(client):
    add(client, file=holidays())
    source = core.settings["sources"][-1]
    core.settings["public_areas"] = ["ics"]
    r = client.get(f"/ics/{source['id']}?token={source['token']}", environ_overrides=PUBLIC)
    assert r.status_code == 200 and b"Autumn holidays" in r.data


def test_backup_keeps_uploaded_calendars(client, fetched):
    add(client, file=holidays())
    answers, _ = fetched
    answers.append(answer(holidays("From the web")))
    add(client, url=SECRET_URL)
    file_source, url_source = core.settings["sources"][-2:]
    raw = backup.export("long enough password")
    content = backup.read(raw, "long enough password")
    assert list(content["calendars"]) == [file_source["id"]]                 # URL calendars are fetched again
    core.calendar_file(file_source["id"]).unlink()
    backup.restore(content)
    assert [e["title"] for e in sync.occurrences(TODAY, TODAY + timedelta(days=5), [file_source["id"]])] == ["Autumn holidays"]


@pytest.mark.parametrize("sources", [
    [{"id": "../settings", "kind": "file", "name": "x"}],
    [{"id": "a", "kind": "shell", "name": "x"}],
    [{"id": "a", "kind": "url", "name": "x"}],
    [{"id": "a", "kind": "file", "name": "x"}, {"id": "a", "kind": "file", "name": "y"}],
    "not a list",
])
def test_odd_calendars_are_dropped(sources):
    s, dirty = core.normalize({"sources": sources})
    assert dirty and all(core.SOURCE_ID.fullmatch(x["id"]) for x in s["sources"]) and len(s["sources"]) <= 1


def test_backup_with_a_bad_calendar_is_refused():
    with pytest.raises(backup.BackupError):
        backup._checked({"settings": {}, "calendars": {"../x": {"ics": holidays().decode()}}})
    with pytest.raises(backup.BackupError):
        backup._checked({"settings": {}, "calendars": {"a": {"ics": "nope"}}})

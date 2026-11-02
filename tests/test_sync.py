"""The ICS file: repairing the exporter's output and reading events back for the homepage."""
import json
from datetime import date, datetime, timedelta

import pytest

from daytree import core, sync

TODAY = date.today()


def d(days, fmt="%Y%m%d"):
    return (TODAY + timedelta(days=days)).strftime(fmt)


@pytest.fixture
def calendar():
    """All-day, time zone, UTC, recurring (with EXDATE), past and far-future events."""
    core.ICS_FILE.write_bytes("\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN",
        "BEGIN:VEVENT", "UID:1", "SUMMARY:AllDay", f"DTSTART;VALUE=DATE:{d(2)}", f"DTEND;VALUE=DATE:{d(3)}", "END:VEVENT",
        "BEGIN:VEVENT", "UID:2", "SUMMARY:Tokyo", f"DTSTART;TZID=Asia/Tokyo:{d(3)}T090000", f"DTEND;TZID=Asia/Tokyo:{d(3)}T100000", "END:VEVENT",
        "BEGIN:VEVENT", "UID:3", "SUMMARY:Utc", f"DTSTART:{d(4)}T120000Z", f"DTEND:{d(4)}T130000Z", "END:VEVENT",
        "BEGIN:VEVENT", "UID:4", "SUMMARY:Weekly", f"DTSTART;TZID=Europe/Berlin:{d(-14)}T080000",
        f"DTEND;TZID=Europe/Berlin:{d(-14)}T090000", "RRULE:FREQ=WEEKLY;COUNT=6", f"EXDATE;TZID=Europe/Berlin:{d(7)}T080000", "END:VEVENT",
        "BEGIN:VEVENT", "UID:5", "SUMMARY:Past", f"DTSTART;VALUE=DATE:{d(-30)}", f"DTEND;VALUE=DATE:{d(-29)}", "END:VEVENT",
        "BEGIN:VEVENT", "UID:6", "SUMMARY:Far", f"DTSTART;VALUE=DATE:{d(90)}", f"DTEND;VALUE=DATE:{d(91)}", "END:VEVENT",
        "END:VCALENDAR", "",
    ]).encode())
    yield
    core.ICS_FILE.unlink(missing_ok=True)
    core.COLORS_FILE.unlink(missing_ok=True)


def test_occurrences(calendar):
    events = sync.occurrences(TODAY, TODAY + timedelta(days=61))
    titles = [e["title"] for e in events]
    assert titles.count("Weekly") == 3, titles                                # today, +14, +21 (+7 excluded by EXDATE)
    assert {"AllDay", "Tokyo", "Utc"} <= set(titles) and "Past" not in titles and "Far" not in titles, titles
    assert [e["sort"] for e in events] == sorted(e["sort"] for e in events)
    allday = next(e for e in events if e["title"] == "AllDay")
    assert allday["all_day"] and allday["date"] == allday["end_date"]         # exclusive DTEND is not a second day


def test_event_colors(calendar):
    raw = b"BEGIN:VEVENT\r\nUID:long-\r\n id\r\nCOLOR:#47b2f7\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:2\r\nCOLOR:red\r\nEND:VEVENT\r\n"
    assert sync.event_colors(raw) == {"long-id": "#47b2f7"}
    core.COLORS_FILE.write_text(json.dumps({"1": "#e73b3b"}))
    events = sync.occurrences(TODAY, TODAY + timedelta(days=61))
    assert next(e for e in events if e["title"] == "AllDay")["color"] == "#e73b3b"
    assert next(e for e in events if e["title"] == "Utc")["color"] is None


def test_api_events(client, calendar):
    events = client.get("/api/events").get_json()["events"]
    assert {"title", "date", "end_date", "time", "all_day", "start_ts"} <= set(events[0])
    assert "AllDay" in [e["title"] for e in events]
    timed = next(e for e in events if e["title"] == "Utc")
    assert timed["start_ts"] == int(datetime.strptime(d(4) + "120000+0000", "%Y%m%d%H%M%S%z").timestamp())
    assert client.get("/").status_code == 200


def test_fix_ics():
    """Exporter output without VALUE=DATE and with a VTIMEZONE lacking daylight saving."""
    broken = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN",
        "BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "BEGIN:STANDARD", "DTSTART:20261119T000000", "TZNAME:CET",
        "TZOFFSETFROM:+0100", "TZOFFSETTO:+0100", "END:STANDARD", "END:VTIMEZONE",
        "BEGIN:VEVENT", "UID:a", "DTSTAMP:20261101T114329Z", "DTSTART:20261203", "DTEND:20261204", "EXDATE:20261231",
        "RRULE:FREQ=WEEKLY;INTERVAL=4", "COLOR:#47b2f7", "END:VEVENT",
        "BEGIN:VEVENT", "UID:b", "DTSTAMP:20261101T114329Z", "DTSTART;VALUE=DATE:20261205", "END:VEVENT",
        "BEGIN:VEVENT", "UID:c", "DTSTAMP:20261101T114329Z", "DTSTART;TZID=Europe/Berlin:20261120T130000",
        "DTEND;TZID=Europe/Berlin:20261120T140000", "RRULE:FREQ=WEEKLY", "END:VEVENT",
        "END:VCALENDAR", "",
    ]).encode()
    fixed = sync.fix_ics(broken).replace(b"\r\n ", b"")
    for line in (b"DTSTART;VALUE=DATE:20261203\r\n", b"DTEND;VALUE=DATE:20261204\r\n", b"EXDATE;VALUE=DATE:20261231\r\n",
                 b"DTSTART;VALUE=DATE:20261205\r\n", b"DTSTART;TZID=Europe/Berlin:20261120T130000\r\n",
                 b"CALSCALE:GREGORIAN\r\n", b"METHOD:PUBLISH\r\n"):
        assert line in fixed, line
    assert b"VALUE=DATE;VALUE=DATE" not in fixed and fixed.count(b"BEGIN:VTIMEZONE") == 1 and b"BEGIN:DAYLIGHT" in fixed
    assert b"COLOR" not in fixed and b"COMMENT" not in fixed and fixed.count(b"\n") == fixed.count(b"\r\n")
    assert sync.fix_ics(b"not an ics") == b"not an ics"                       # unreadable input is passed through

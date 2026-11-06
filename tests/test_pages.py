"""Events read from a web page with a Contao calendar month view, as school websites often have."""
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from conftest import ORIGIN
from icalendar import Calendar

from daytree import core, pages, sync

BERLIN = ZoneInfo("Europe/Berlin")
TODAY = date.today()


def d(days):
    return (TODAY + timedelta(days=days)).strftime("%d.%m.%Y")


def month(events, next_href=None):
    """A month view like the one Contao renders: event links with the date in their title, and a next link."""
    cells = "".join(f'<td class="days"><div class="header">1</div><div class="event cal_4 upcoming">'
                    f'<a href="{href}" title="{title}">{title.rsplit(" (", 1)[0]}</a></div></td>' for href, title in events)
    nxt = f'<a href="{next_href}" title="next">Next &gt;</a>' if next_href else "&nbsp;"
    return (f'<html><body><div class="mod_calendar block"><table class="calendar"><thead><tr>'
            f'<th colspan="2" class="head previous"><a href="/termine.html?month=202610">&lt; Prev</a></th>'
            f'<th colspan="3" class="head current">Month</th><th colspan="2" class="head next">{nxt}</th></tr></thead>'
            f'<tbody><tr>{cells}</tr></tbody></table></div></body></html>').encode()


FIRST = month([
    ("/termindetail/laternenumzug.html", f"Laternenumzug &#40;für Infos bitte klicken&#41; (Dienstag, {d(1)} 17:30)"),
    ("/termindetail/adventsbasteln.html", f"Adventsbasteln (Freitag, {d(2)})"),
    ("/termindetail/projektwoche.html", f"Projektwoche (Montag, {d(3)} – Freitag, {d(5)})"),
    ("/termindetail/projektwoche.html", f"Projektwoche (Montag, {d(3)} – Freitag, {d(5)})"),   # shown on each day
], next_href="/termine.html?month=202612")
SECOND = month([("/termindetail/elternabend.html", f"Elternabend (Mittwoch, {d(6)} 19:00 – 20:30)")],
               next_href="https://elsewhere.example.com/termine.html?month=202701")


def test_dates_in_titles():
    name, start, end = pages.when("Laternenumzug (Dienstag, 03.11.2026 17:30)", BERLIN)
    assert name == "Laternenumzug" and start == datetime(2026, 11, 3, 17, 30, tzinfo=BERLIN) and end is None
    assert pages.when("Adventsbasteln (Freitag, 27.11.2026)", BERLIN)[1:] == (date(2026, 11, 27), date(2026, 11, 28))
    assert pages.when("Woche (02.11.2026 - 06.11.2026)", BERLIN)[1:] == (date(2026, 11, 2), date(2026, 11, 7))
    _, start, end = pages.when("Elternabend (Mittwoch, 11.11.2026 19:00 – 20:30)", BERLIN)
    assert (start.hour, end.hour, end.minute) == (19, 20, 30)
    assert pages.when("Weihnachtsmärchen (Klasse 1 und 2, Info-Brief folgt)", BERLIN) is None
    assert pages.when("Kaputt (31.02.2026)", BERLIN) is None


def test_reads_this_month_and_follows_next_on_the_same_server():
    download = mock.MagicMock(return_value=SECOND)
    events = pages.read("https://school.example.com/termine.html", FIRST, download)
    download.assert_called_once_with("https://school.example.com/termine.html?month=202612")   # not elsewhere.example.com
    assert events[0] == ("https://school.example.com/termindetail/laternenumzug.html",
                         f"Laternenumzug (für Infos bitte klicken) (Dienstag, {d(1)} 17:30)")
    cal = Calendar.from_ical(pages.to_ics(events, "Europe/Berlin"))
    found = {str(e["SUMMARY"]): e for e in cal.walk("VEVENT")}
    assert sorted(found) == ["Adventsbasteln", "Elternabend", "Laternenumzug (für Infos bitte klicken)", "Projektwoche"]
    assert str(found["Adventsbasteln"]["URL"]) == "https://school.example.com/termindetail/adventsbasteln.html"
    assert found["Projektwoche"].decoded("DTEND") - found["Projektwoche"].decoded("DTSTART") == timedelta(days=3)


def test_stops_after_a_few_months():
    looping = [month([], next_href=f"/termine.html?month=2027{i:02d}") for i in range(1, 20)]
    download = mock.MagicMock(side_effect=looping[1:])
    pages.read("https://school.example.com/termine.html", looping[0], download)
    assert download.call_count == pages.MONTHS - 1


def test_not_a_calendar_page():
    with pytest.raises(ValueError, match="neither a calendar file nor an events page"):
        pages.read("https://example.com/", b"<html><body><a title='x (01.01.2027)'>x</a></body></html>", mock.MagicMock())


def test_added_as_an_address(client, monkeypatch):
    bodies = {"https://school.example.com/termine.html": FIRST,
              "https://school.example.com/termine.html?month=202612": SECOND}
    monkeypatch.setattr(sync, "download", lambda url: bodies[url])
    client.post("/settings/sources", data={"url": "https://school.example.com/termine.html", "name": "School"},
                headers=ORIGIN, content_type="multipart/form-data")
    source = core.settings["sources"][-1]
    assert source["kind"] == "url" and source["name"] == "School" and sync.state["sources"][source["id"]]["format"] == "page"
    assert "school.example.com · Terminseite" in client.get("/settings", headers={"Accept-Language": "de"}).data.decode()
    events = sync.occurrences(TODAY, TODAY + timedelta(days=8), [source["id"]])
    assert [e["title"] for e in events] == ["Laternenumzug (für Infos bitte klicken)", "Adventsbasteln", "Projektwoche",
                                            "Elternabend"]
    evening = events[-1]                     # the page's times are in the time zone of the settings
    zone = ZoneInfo(core.effective_timezone())
    day = TODAY + timedelta(days=6)
    assert evening["start_ts"] == int(datetime(day.year, day.month, day.day, 19, 0, tzinfo=zone).timestamp())
    assert evening["end_ts"] - evening["start_ts"] == 90 * 60

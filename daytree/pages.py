"""Events from web pages that show a Contao calendar month view, as many school websites do instead of a calendar
subscription.

Each event there is a link whose title carries its date, such as "Adventsbasteln (Freitag, 27.11.2026)",
"Laternenumzug (Dienstag, 03.11.2026 17:30)" or a range with two dates or two times. The page links to the next
month, which is followed for a few months, on the same server only.
"""
import hashlib
import re
import urllib.parse
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

MONTHS = 4                      # the current month and the next three
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
WHEN = re.compile(r"\(([^()]*\d{1,2}\.\d{1,2}\.\d{4}[^()]*)\)\s*$")


class _MonthView(HTMLParser):
    """Collects the event links, the link to the next month and whether this is a calendar at all."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.next, self.calendar = [], None, False
        self._event = self._next = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if (tag == "table" and "calendar" in classes) or (tag == "div" and "mod_calendar" in classes):
            self.calendar = True
        elif tag == "div" and "event" in classes:
            self._event = True
        elif tag in ("th", "td") and "next" in classes:
            self._next = True
        elif tag == "a" and self._event and attrs.get("title"):
            self.events.append((attrs.get("href") or "", attrs["title"]))
        elif tag == "a" and self._next and attrs.get("href") and not self.next:
            self.next = attrs["href"]

    def handle_endtag(self, tag):
        if tag == "div":
            self._event = False
        elif tag in ("th", "td"):
            self._next = False


def when(title, zone):
    """Splits "Name (Weekday, 03.11.2026 17:30)" into the name, the start and the end (exclusive for all-day
    events, None for a time without an end). None if the title carries no date."""
    match = WHEN.search(title)
    if not match:
        return None
    try:
        days = [date(int(y), int(m), int(d)) for d, m, y in DATE.findall(match.group(1))]
        times = [time(int(h), int(m)) for h, m in TIME.findall(match.group(1))]
    except ValueError:
        return None
    name = title[:match.start()].strip()
    if not times:
        return name, days[0], days[-1] + timedelta(days=1)
    start = datetime.combine(days[0], times[0], zone)
    end = datetime.combine(days[-1], times[-1], zone) if len(times) > 1 else None
    return name, start, end if end and end > start else None


def read(url, raw, download):
    """Events of this month and the next ones as (link, title). download(url) fetches a further month.
    Raises ValueError if the page is no calendar month view."""
    host = urllib.parse.urlsplit(url).hostname
    events, seen = [], {url}
    for count in range(1, MONTHS + 1):
        view = _MonthView()
        view.feed(raw.decode("utf-8", "replace"))
        if not view.calendar:
            raise ValueError("The address is neither a calendar file nor an events page Daytree can read.")
        events += [(urllib.parse.urljoin(url, href), title) for href, title in view.events]
        following = urllib.parse.urljoin(url, view.next) if view.next else None
        if count == MONTHS or not following or following in seen or urllib.parse.urlsplit(following).hostname != host:
            break
        seen.add(following)
        url, raw = following, download(following)
    return events


def to_ics(events, zone_name):
    """The events as an ICS file. An event spread over several days appears once; its link becomes its URL."""
    zone = ZoneInfo(zone_name)
    calendar = Calendar()
    calendar["VERSION"], calendar["PRODID"] = "2.0", "-//Daytree//Web page//EN"
    done = set()
    for link, title in events:
        parsed = when(title, zone)
        if not parsed or (link, title) in done:
            continue
        done.add((link, title))
        name, start, end = parsed
        event = Event()
        event["UID"] = hashlib.sha256(f"{link}|{title}".encode()).hexdigest()[:32] + "@daytree"
        event.add("SUMMARY", name or title)
        event.add("DTSTART", start)
        if end:
            event.add("DTEND", end)
        event.add("DTSTAMP", datetime.now(ZoneInfo("UTC")))
        if link.startswith(("https://", "http://")):
            event.add("URL", link)
        calendar.add_component(event)
    return calendar.to_ical()

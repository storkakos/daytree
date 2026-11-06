"""The homepage: its pages, its config and what it shows - events, weather, background photos and waste bins."""
import hashlib
import json
import random
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import recurring_ical_events
from cryptography.fernet import InvalidToken
from flask import Blueprint, jsonify, render_template, request
from icalendar import Calendar

from daytree import VERSION
from daytree.auth import is_public
from daytree.core import (BACKGROUND_FILE, DEFAULTS, LANGUAGES, SECRET_KEYS, WASTE_FILE, effective_timezone, needs_setup,
                          read_encrypted, settings)
from daytree.i18n import _, hours, language
from daytree.sync import as_json, local_time, occurrences
from daytree.sync import state as sync_state

bp = Blueprint("display", __name__)

SYSTEM_FONT = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif'
FONTS = {"system": None, "inter": "Inter", "nunito": "Nunito", "poppins": "Poppins", "lora": "Lora", "playfair": "Playfair Display"}
WASTE_MAX_BYTES = 2 * 1024 * 1024
# First match in the lowercase collection name; the user can change every color in the settings
WASTE_COLORS = (("bio", "#8b5a2b"), ("papier", "#2f6fde"), ("paper", "#2f6fde"), ("gelb", "#f2c40f"), ("plast", "#f2c40f"),
                ("rest", "#6b7280"), ("glas", "#2e9e5b"), ("glass", "#2e9e5b"))
BACKGROUND_SECONDS = {"hour": 3600, "day": 86400, "fixed": float("inf")}
WEATHER_TEXT = {
    "Clear sky": (0,),
    "Partly cloudy": (1, 2),
    "Overcast": (3,),
    "Fog": (45, 48),
    "Drizzle": (51, 53, 55, 56, 57),
    "Rain": (61, 63, 65, 66, 67, 80, 81, 82),
    "Snow": (71, 73, 75, 77, 85, 86),
    "Thunderstorm": (95, 96, 99),
}
WEATHER_ICONS = {       # names in static/weather-sprites.svg
    "clearsky": (0,), "fewclouds": (1,), "brokenclouds": (2,), "overcastclouds": (3,), "mist": (45, 48),
    "lightrain": (51, 53, 55, 56, 57), "rain": (61, 63, 65, 66, 67), "sunnyrain": (80, 81, 82),
    "snow": (71, 73, 75, 77, 85, 86), "thunderstorm": (95, 96, 99),
}


def view():
    """Settings for templates - without secrets (password, tunnel token)."""
    v = {k: val for k, val in settings.items() if k not in SECRET_KEYS}
    v["has_password"] = bool(settings["password"])
    v["has_tunnel_token"] = bool(settings["tunnel_token"])
    v["has_lock_password"] = bool(settings["lock_hash"])
    v["has_notify_url"] = bool(settings["notify_url"])
    v["interval_label"] = hours(settings["interval_minutes"])
    # A calendar URL can hold a private token: pages only see its host
    v["sources"] = [{**{k: val for k, val in s.items() if k != "url"},
                     "host": urllib.parse.urlsplit(s["url"]).hostname if s["kind"] == "url" else None} for s in settings["sources"]]
    v["calendar_name"] = ", ".join(s["name"] for s in settings["sources"] if s["kind"] == "timetree")
    v["timetree_codes"] = [s["id"] for s in settings["sources"] if s["kind"] == "timetree"]
    return v


def display_config_strings():
    return ("No connection to Daytree", "All day", "Nothing planned today", "No tasks", "Delete task", "Starts at {time} · tap to dismiss",
            "Good morning", "Good afternoon", "Good evening", "Good night", "Photo by {name}")


def display_config():
    """Everything the homepage needs to draw itself. Free of secrets: it also reaches the public read-only view."""
    font = FONTS[settings["font_family"]]
    font_url = None
    if font:
        font_url = f"https://fonts.googleapis.com/css2?family={urllib.parse.quote_plus(font)}:wght@300;400;500;600;700&display=swap"
    return {
        "language": language(),
        "languageAuto": settings["language"] == "auto",
        "languages": LANGUAGES,
        "locale": "de-DE" if language() == "de" else "en-GB",
        "hour12": settings["time_format"] == "12",
        "timeZone": effective_timezone(),
        "dateStyle": settings["date_style"],
        "showSeconds": settings["show_seconds"],
        "clockStyle": settings["clock_style"],
        "clockSize": settings["clock_size"],
        "showTasks": settings["tasks_enabled"] and not is_public(),
        "upcomingDays": settings["upcoming_days"],
        "greeting": settings["greeting_enabled"],
        "name": "" if is_public() else settings["greeting_name"],
        "weather": settings["weather_enabled"],
        "forecast": settings["weather_forecast"],
        "waste": settings["waste_enabled"],
        "wasteStamp": waste_stamp(),
        "backgroundType": settings["background_type"],
        "backgroundColor": settings["background_color"],
        "blur": settings["background_blur"],
        "brightness": settings["background_brightness"],
        "fontFamily": f'"{font}",{SYSTEM_FONT}' if font else SYSTEM_FONT,
        "fontUrl": font_url,
        "fontSize": settings["font_size"],
        "fontWeight": settings["font_weight"],
        "text": {t: _(t) for t in display_config_strings()},
        "version": VERSION,
    }


@bp.route("/")
def dashboard():
    return render_template(
        "kiosk.html", s=view(), setup=needs_setup() and not is_public(), default_minutes=DEFAULTS["interval_minutes"],
        show_tasks=settings["tasks_enabled"] and not is_public(), config=display_config(), read_only=is_public(),
    )


@bp.get("/kiosk")
def kiosk():
    """The homepage for a fixed screen: tasks can be read but not changed, and there is no way into the settings."""
    return render_template(
        "kiosk.html", s=view(), setup=False, show_tasks=settings["tasks_enabled"] and not is_public(),
        config=display_config(), read_only=True,
    )


@bp.get("/api/config")
def api_config():
    return jsonify(display_config())


@bp.get("/api/events")
def api_events():
    today = date.today()
    last = today + timedelta(days=settings["upcoming_days"] + 1)
    return jsonify(events=as_json(occurrences(today, last)), stale=freshness())


def freshness():
    """A note for the homepage when its events may be out of date: the last sync failed, or the last success is older
    than two sync intervals. Empty while everything is fresh."""
    if needs_setup():
        return ""
    last = sync_state["last_success"]
    if last is None:
        return _("Calendar not updated yet") if sync_state["ok"] is False else ""
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
    if sync_state["ok"] is not False and age < settings["interval_minutes"] * 120 + 1800:
        return ""
    minutes = max(1, int(age // 60))
    if minutes < 120:
        return _("Last updated {count} min ago").format(count=minutes)
    if minutes < 48 * 60:
        return _("Last updated {count} h ago").format(count=minutes // 60)
    return _("Last updated {count} days ago").format(count=minutes // 1440)


# ---------------------------------------------------------------- waste collection
def waste_stamp():
    """Changes with the file, colors and timing, so open homepages know when to redraw the bins. Reveals neither."""
    modified = WASTE_FILE.stat().st_mtime if WASTE_FILE.exists() else 0
    state = f"{modified}{sorted(settings['waste_colors'].items())}{settings['waste_day_before']}"
    return hashlib.sha256(state.encode()).hexdigest()[:12]


def waste_calendar():
    try:
        return Calendar.from_ical(read_encrypted(WASTE_FILE)["ics"])
    except (OSError, InvalidToken, ValueError, KeyError):
        return None


def waste_types(calendar=None):
    calendar = calendar or waste_calendar()
    return sorted({str(event.get("SUMMARY", "")).strip() for event in calendar.walk("VEVENT")} - {""}) if calendar else []


def waste_color(title):
    if title in settings["waste_colors"]:
        return settings["waste_colors"][title]
    return next((color for word, color in WASTE_COLORS if word in title.lower()), "#9ca3af")


def waste_days():
    """The bins to show on today and the upcoming days after it, one entry per day."""
    today = date.today()
    early = timedelta(days=1 if settings["waste_day_before"] else 0)
    count = settings["upcoming_days"] + 1
    days = {today + timedelta(days=i): [] for i in range(count)}
    calendar = waste_calendar()
    for event in recurring_ical_events.of(calendar).between(today, today + timedelta(days=count) + early) if calendar else []:
        start = local_time(event.decoded("DTSTART"))
        day = (start.date() if isinstance(start, datetime) else start) - early
        title = str(event.get("SUMMARY", "")).strip()
        if day in days and title and title not in [item["title"] for item in days[day]]:
            days[day].append({"title": title, "color": waste_color(title)})
    return [{"date": day.isoformat(), "bins": bins} for day, bins in days.items()]


@bp.get("/api/waste")
def api_waste():
    return jsonify(enabled=True, days=waste_days()) if settings["waste_enabled"] else jsonify(enabled=False)


# ---------------------------------------------------------------- background photos
def fetch_json(url, headers=None):
    """External lookups stay short: the homepage keeps its last values if a service is unreachable."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=8) as response:
        return json.loads(response.read())


def background_seconds():
    every = settings["background_every"]
    return settings["background_minutes"] * 60 if every == "custom" else BACKGROUND_SECONDS[every]


def load_backgrounds():
    try:
        return json.loads(BACKGROUND_FILE.read_text())
    except (OSError, ValueError):
        return {}


backgrounds = load_backgrounds()      # orientation -> {"url", "author", "link", "location", "fetched"}
weather = {}


def picsum_photo(orientation):
    photos = fetch_json(f"https://picsum.photos/v2/list?page={random.randint(1, 30)}&limit=30")
    wide = orientation == "landscape"
    matching = [p for p in photos if (p["width"] >= p["height"]) == wide] or photos
    photo = random.choice(matching)
    size = "2560/1600" if wide else "1600/2560"
    return {"url": f"https://picsum.photos/id/{photo['id']}/{size}", "author": photo["author"], "link": photo["url"], "location": ""}


@bp.get("/api/background")
def api_background():
    orientation = "portrait" if request.args.get("orientation") == "portrait" else "landscape"
    entry = backgrounds.get(orientation)
    if not entry or time.time() - entry["fetched"] >= background_seconds():
        try:
            entry = {**picsum_photo(orientation), "fetched": time.time()}
            backgrounds[orientation] = entry
            BACKGROUND_FILE.write_text(json.dumps(backgrounds))
        except Exception:  # noqa: BLE001
            pass
    if not entry:
        return jsonify(url=None)
    return jsonify({k: entry[k] for k in ("url", "author", "link", "location")})


@bp.post("/api/background/refresh")
def api_background_refresh():
    backgrounds.clear()
    BACKGROUND_FILE.unlink(missing_ok=True)
    return "", 204


# ---------------------------------------------------------------- weather
def geocode(city):
    """First match of a place name as (lat, lon, label), or None."""
    query = urllib.parse.urlencode({"name": city, "count": 1, "language": language()})
    results = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}").get("results")
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"], results[0]["name"]


def weather_icon(code, day=True):
    name = next((icon for icon, codes in WEATHER_ICONS.items() if code in codes), "overcastclouds")
    if name == "sunnyrain" and not day:
        name = "rain"                # the sprite has no night version of this one
    return f"weather-{'day' if day else 'night'}-{name}"


def forecast(data):
    """The next 10 hours and every day from an Open-Meteo answer. Times stay local to the place."""
    now = data["current"].get("time", "")[:13]
    hourly, daily = data.get("hourly", {}), data.get("daily", {})
    hours = [
        {"time": t, "temperature": round(value), "icon": weather_icon(code, bool(day))}
        for t, value, code, day in zip(hourly.get("time", []), hourly.get("temperature_2m", []), hourly.get("weather_code", []),
                                       hourly.get("is_day", [1] * len(hourly.get("time", []))))
        if t[:13] >= now and value is not None
    ]
    days = [
        {"date": d, "high": round(high), "low": round(low), "icon": weather_icon(code)}
        for d, high, low, code in zip(daily.get("time", []), daily.get("temperature_2m_max", []),
                                      daily.get("temperature_2m_min", []), daily.get("weather_code", []))
        if high is not None and low is not None
    ]
    return {"hours": hours[:10], "days": days}


@bp.get("/api/weather")
def api_weather():
    lat, lon = settings["weather_lat"], settings["weather_lon"]
    if not settings["weather_enabled"] or lat is None:
        return jsonify(enabled=False)
    if time.time() >= weather.get("expires", 0):
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,is_day",
            "hourly": "temperature_2m,weather_code,is_day", "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": 15,         # today and the longest upcoming range
            "temperature_unit": settings["weather_unit"], "timezone": "auto",
        })
        try:
            data = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}")
            current = data["current"]
            weather.update(temperature=round(current["temperature_2m"]), code=current["weather_code"], expires=time.time() + 1800,
                           day=bool(current.get("is_day", 1)), **forecast(data))
        except Exception:  # noqa: BLE001
            if "code" not in weather:
                return jsonify(enabled=False)
    text = next((t for t, codes in WEATHER_TEXT.items() if weather["code"] in codes), "Overcast")
    return jsonify(enabled=True, temperature=weather["temperature"], text=_(text), city=settings["weather_city"],
                   icon=weather_icon(weather["code"], weather["day"]),
                   hours=weather["hours"], days=weather["days"][:settings["upcoming_days"]])

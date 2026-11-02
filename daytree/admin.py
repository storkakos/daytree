"""The settings pages: TimeTree login, sync, homepage appearance, waste collection and sharing."""
import re
import secrets
from datetime import datetime
from zoneinfo import available_timezones

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from icalendar import Calendar

from daytree import VERSION, auth, display, sync, tunnel
from daytree.auth import PUBLIC_PORT
from daytree.core import (DEFAULT_TZ, LANGUAGES, MAX_MINUTES, MIN_MINUTES, PUBLIC_MODES, STEP_MINUTES, WASTE_FILE,
                          apply_timezone, clamp, save_settings, settings, write_encrypted)
from daytree.i18n import _, hours

bp = Blueprint("admin", __name__)

MIN_PASSWORD = 8
TIMEZONE_REGIONS = ("Africa/", "America/", "Antarctica/", "Asia/", "Atlantic/", "Australia/", "Europe/", "Indian/", "Pacific/", "UTC")


def ics_urls():
    path = "/calendar.ics?token=" + settings["token"]
    local = request.host_url.rstrip("/") + path
    pub = f"https://{settings['public_hostname']}{path}" if settings["public_hostname"] else None
    return local, pub


@bp.post("/sync")
def sync_now():
    sync.reset_timer()
    sync.start_in_background()
    return redirect(url_for("admin.settings_page"))


@bp.get("/settings")
def settings_page():
    local, pub = ics_urls()
    return render_template(
        "settings.html", s=display.view(), state=sync.state, running=sync.lock.locked(),
        next_run=datetime.fromtimestamp(sync.next_due).strftime("%H:%M") if sync.next_due else None,
        intervals=[(m, hours(m)) for m in range(MIN_MINUTES, MAX_MINUTES + 1, STEP_MINUTES)],
        timezones=sorted(z for z in available_timezones() if z.startswith(TIMEZONE_REGIONS)),
        default_timezone=DEFAULT_TZ or "UTC", fonts=display.FONTS, version=VERSION,
        waste=[(title, display.waste_color(title)) for title in display.waste_types()],
        ics_local=local, ics_pub=pub, tunnel=tunnel.status, public_port=PUBLIC_PORT, lock_enabled=auth.SETTINGS_LOCK,
    )


def _back():
    """Forms of the first-time setup return to the homepage, all others to the settings page."""
    return redirect(url_for("display.dashboard" if request.form.get("setup") else "admin.settings_page"))


@bp.post("/settings/login")
def settings_login():
    """Store credentials and load the calendar list from TimeTree."""
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "") or settings["password"]  # empty = keep stored password
    if not email or not password:
        flash(_("Enter e-mail and password."), "err")
        return _back()
    try:
        cals = sync.fetch_calendars(email, password)
    except Exception as e:  # noqa: BLE001
        flash(_("Login failed: {error}").format(error=str(e)[:200]), "err")
        return _back()
    settings.update(email=email, password=password, calendars=cals)
    if request.form.get("setup"):
        settings.update(calendar_code="", calendar_name="")              # the setup asks for the calendar next
    elif not any(c["code"] == settings["calendar_code"] for c in cals):
        # previous selection no longer valid -> preselect the first calendar
        settings.update(calendar_code=cals[0]["code"] if cals else "", calendar_name=cals[0]["name"] if cals else "")
    save_settings(settings)
    flash(_("Signed in. {count} calendars found.").format(count=len(cals)), "ok")
    return _back()


@bp.post("/settings/save")
def settings_save():
    """Save calendar selection and sync interval."""
    try:
        settings["interval_minutes"] = clamp(request.form["interval"])
    except (KeyError, ValueError):
        abort(400)
    code = request.form.get("calendar_code", "")
    match = next((c for c in settings["calendars"] if c["code"] == code), None)
    if match:
        settings.update(calendar_code=match["code"], calendar_name=match["name"])
    if request.form.get("new_token"):
        settings["token"] = secrets.token_urlsafe(24)
        flash(_("New token generated. Update the ICS URL in your calendar apps."), "ok")
    save_settings(settings)
    sync.reset_timer()
    flash(_("Settings saved."), "ok")
    if request.form.get("sync_now"):
        sync.start_in_background()
    return _back()


def _number(name, low, high):
    try:
        return max(low, min(high, int(request.form.get(name, settings[name]))))
    except ValueError:
        abort(400)


def _choice(name, allowed):
    value = request.form.get(name, settings[name])
    if value not in allowed:
        abort(400)
    return value


@bp.post("/settings/display")
def settings_display():
    """Homepage appearance: language, time and date, greeting, weather, background and font."""
    zone = request.form.get("timezone", settings["timezone"])
    color = request.form.get("background_color", settings["background_color"])
    if (zone and zone not in available_timezones()) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        abort(400)
    new = {
        "timezone": zone, "background_color": color,
        "language": _choice("language", ("auto", *LANGUAGES)),
        "time_format": _choice("time_format", ("24", "12")),
        "date_style": _choice("date_style", ("long", "numeric")),
        "clock_style": _choice("clock_style", ("digital", "analog")),
        "weather_unit": _choice("weather_unit", ("celsius", "fahrenheit")),
        "weather_forecast": _choice("weather_forecast", ("off", "hours", "days")),
        "background_type": _choice("background_type", ("photo", "color")),
        "background_every": _choice("background_every", (*display.BACKGROUND_SECONDS, "custom")),
        "background_minutes": _number("background_minutes", 1, 1440),
        "font_family": _choice("font_family", tuple(display.FONTS)),
        "clock_size": _number("clock_size", 50, 200),
        "background_blur": _number("background_blur", 0, 50),
        "background_brightness": _number("background_brightness", 20, 100),
        "font_size": _number("font_size", 70, 150),
        "font_weight": _number("font_weight", 300, 700),
        "tasks_enabled": bool(request.form.get("tasks_enabled")),
        "show_seconds": bool(request.form.get("show_seconds")),
        "greeting_enabled": bool(request.form.get("greeting_enabled")),
        "weather_enabled": bool(request.form.get("weather_enabled")),
        "greeting_name": request.form.get("greeting_name", settings["greeting_name"]).strip()[:40],
    }
    message = _("Settings saved.")
    city = request.form.get("weather_city", settings["weather_city"]).strip()[:80]
    if city != settings["weather_city"]:
        new.update(weather_city=city, weather_lat=None, weather_lon=None)
        display.weather.clear()
        if city:
            try:
                place = display.geocode(city)
            except Exception:  # noqa: BLE001
                place = None
            if place:
                new.update(weather_lat=place[0], weather_lon=place[1], weather_city=place[2])
            else:
                message = _("Place not found.")
    if settings["weather_unit"] != new["weather_unit"]:
        display.weather.clear()
    settings.update(new)
    save_settings(settings)
    apply_timezone()
    if request.headers.get("X-Autosave"):
        return jsonify(message=message, weather_city=settings["weather_city"])
    flash(message, "ok")
    return redirect(url_for("admin.settings_page"))


@bp.post("/settings/waste")
def settings_waste():
    """Waste collection calendar: upload, remove, colors and whether the homepage shows it."""
    upload = request.files.get("waste_file")
    enabled = bool(request.form.get("waste_enabled"))
    if request.form.get("remove"):
        WASTE_FILE.unlink(missing_ok=True)
        settings["waste_colors"] = {}
        settings["waste_day_before"] = False
        enabled = False
    elif upload and upload.filename:
        raw = upload.read(display.WASTE_MAX_BYTES + 1)
        if len(raw) > display.WASTE_MAX_BYTES:
            flash(_("The file is too large."), "err")
            return redirect(url_for("admin.settings_page"))
        text = raw.decode("utf-8-sig", "replace")
        try:
            valid = bool(display.waste_types(Calendar.from_ical(text)))
        except ValueError:
            valid = False
        if not valid:
            flash(_("The file is not a usable calendar."), "err")
            return redirect(url_for("admin.settings_page"))
        write_encrypted(WASTE_FILE, {"ics": text})
        enabled = True
    for index, title in enumerate(display.waste_types()):
        color = request.form.get(f"waste_color_{index}", "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            settings["waste_colors"][title] = color
    settings["waste_enabled"] = enabled
    if not request.form.get("remove"):
        settings["waste_day_before"] = bool(request.form.get("waste_day_before"))
    save_settings(settings)
    if request.headers.get("X-Autosave"):
        return jsonify(message=_("Settings saved."))
    flash(_("Settings saved."), "ok")
    return redirect(url_for("admin.settings_page", updated=1))


@bp.post("/settings/tunnel")
def settings_tunnel():
    """Cloudflare tunnel and exposure mode."""
    raw = request.form.get("tunnel_token", "").strip()
    if raw:
        m = tunnel.TOKEN_RE.search(raw)   # also accepts a pasted "cloudflared ... --token XYZ" command
        if not m:
            flash(_("Tunnel token not recognized (it normally starts with “eyJ”)."), "err")
            return redirect(url_for("admin.settings_page"))
        settings["tunnel_token"] = m.group(0)
    if request.form.get("clear_token"):
        settings["tunnel_token"] = ""

    host = request.form.get("public_hostname", "").strip().lower()
    if host and not re.fullmatch(r"[a-z0-9]([a-z0-9.\-]*[a-z0-9])?", host):
        flash(_("Invalid hostname."), "err")
        return redirect(url_for("admin.settings_page"))
    settings["public_hostname"] = host

    mode = request.form.get("public_mode", "off")
    settings["public_mode"] = mode if mode in PUBLIC_MODES else "off"

    want = bool(request.form.get("tunnel_enabled"))
    if want and not settings["tunnel_token"]:
        flash(_("Enter the tunnel token first to enable the tunnel."), "err")
        want = False
    settings["tunnel_enabled"] = want
    save_settings(settings)
    tunnel.restart.set()
    flash(_("Sharing settings saved."), "ok")
    return redirect(url_for("admin.settings_page"))


@bp.post("/settings/unlock")
def settings_unlock():
    """Open the padlock. Until a password exists, the first one entered here becomes the password."""
    password = request.form.get("password", "")
    if not settings["lock_hash"]:
        error = _new_password_error(password, request.form.get("confirm", ""))
        if error:
            flash(error, "err")
            return redirect(url_for("admin.settings_page"))
        settings["lock_hash"] = auth.hash_password(password)
        save_settings(settings)
    else:
        result = auth.try_unlock(password)
        if result != "ok":
            flash(_("Too many wrong passwords. Try again in a minute.") if result == "blocked" else _("Wrong password."), "err")
            return redirect(url_for("admin.settings_page"))
    response = redirect(url_for("admin.settings_page"))
    auth.open_session(response)
    return response


@bp.post("/settings/lock")
def settings_lock():
    response = redirect(url_for("admin.settings_page"))
    auth.close_session(response)
    return response


@bp.post("/settings/password")
def settings_password():
    """Set or change the padlock password. Other unlocked browsers are locked again."""
    password = request.form.get("password", "")
    error = _new_password_error(password, request.form.get("confirm", ""))
    if error:
        flash(error, "err")
        return redirect(url_for("admin.settings_page"))
    settings["lock_hash"] = auth.hash_password(password)
    save_settings(settings)
    auth.close_other_sessions()
    flash(_("Password saved."), "ok")
    response = redirect(url_for("admin.settings_page"))
    if auth.SETTINGS_LOCK and not auth.unlocked():
        auth.open_session(response)
    return response


def _new_password_error(password, confirm):
    if len(password) < MIN_PASSWORD:
        return _("The password needs at least {count} characters.").format(count=MIN_PASSWORD)
    if password != confirm:
        return _("The passwords do not match.")
    return None

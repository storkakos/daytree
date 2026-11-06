"""The settings pages: TimeTree login, sync, homepage appearance, waste collection and sharing."""
import re
import secrets
import urllib.parse
from datetime import date, datetime
from zoneinfo import available_timezones

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from icalendar import Calendar

from daytree import VERSION, auth, backup, display, notify, sync, tunnel
from daytree.core import (DEFAULT_TZ, LANGUAGES, MAX_MINUTES, MIN_MINUTES, PUBLIC_AREAS, STEP_MINUTES, URL_MINUTES, WASTE_FILE,
                          apply_timezone, clamp, new_source, save_settings, settings, timetree_sources, write_encrypted)
from daytree.i18n import _, hours

bp = Blueprint("admin", __name__)

MIN_PASSWORD = 8
MAX_TOKENS = 20
TIMEZONE_REGIONS = ("Africa/", "America/", "Antarctica/", "Asia/", "Atlantic/", "Australia/", "Europe/", "Indian/", "Pacific/", "UTC")


DEFAULT_COLOR = "#8e8e93"       # what the color picker shows for a calendar without a color of its own
MAX_SOURCES = 20                # calendars from files and URLs


def ics_urls(path=None):
    path = path or "/ics?token=" + settings["token"]
    local = request.host_url.rstrip("/") + path
    pub = f"https://{settings['public_hostname']}{path}" if settings["public_hostname"] else None
    return local, pub


def select_timetree(codes):
    """Makes the TimeTree calendars with these codes the selected ones, in TimeTree's order. Calendars that stay
    selected keep their name, color, switches and token."""
    current = {s["id"]: s for s in timetree_sources()}
    chosen = []
    for calendar in settings["calendars"]:
        if calendar["code"] in codes:
            chosen.append(current.get(calendar["code"]) or new_source("timetree", calendar["name"], calendar["code"]))
    settings["sources"] = chosen + [s for s in settings["sources"] if s["kind"] != "timetree"]


@bp.post("/sync")
def sync_now():
    sync.reset_timer()
    sync.start_in_background()
    return redirect(url_for("admin.settings_page"))


@bp.get("/settings")
def settings_page():
    return _render_settings()


def _render_settings(**extra):
    local, pub = ics_urls()
    tokens = [{k: v for k, v in t.items() if k != "hash"} for t in settings["api_tokens"]]
    return render_template(
        "settings.html", s=display.view(), state=sync.state, running=sync.lock.locked(),
        next_run=datetime.fromtimestamp(sync.next_due).strftime("%H:%M") if sync.next_due else None,
        intervals=[(m, hours(m)) for m in range(MIN_MINUTES, MAX_MINUTES + 1, STEP_MINUTES)],
        timezones=sorted(z for z in available_timezones() if z.startswith(TIMEZONE_REGIONS)),
        default_timezone=DEFAULT_TZ or "UTC", fonts=display.FONTS, version=VERSION,
        waste=[(title, display.waste_color(title)) for title in display.waste_types()],
        ics_local=local, ics_pub=pub, tunnel=tunnel.status, lock_enabled=auth.SETTINGS_LOCK, default_color=DEFAULT_COLOR,
        calendar_feeds={s["id"]: ics_urls(f"/ics/{s['id']}?token={s['token']}") for s in settings["sources"]},
        source_states=sync.state.get("sources", {}), url_minutes=URL_MINUTES,
        api_tokens=tokens, **extra,
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
        select_timetree(set())                                         # the setup asks for the calendar next
    else:
        # calendars TimeTree no longer offers drop out; if none is left, the first one is preselected
        codes = {s["id"] for s in timetree_sources()} & {c["code"] for c in cals}
        select_timetree(codes or {c["code"] for c in cals[:1]})
    save_settings(settings)
    sync.prune_calendars()
    flash(_("Signed in. {count} calendars found.").format(count=len(cals)), "ok")
    return _back()


@bp.post("/settings/save")
def settings_save():
    """Save the calendar selection and the sync interval. The setup sends one calendar_code, the settings a list."""
    try:
        settings["interval_minutes"] = clamp(request.form["interval"])
    except (KeyError, ValueError):
        abort(400)
    offered = {c["code"] for c in settings["calendars"]}
    if request.form.get("calendars_form"):
        codes = set(request.form.getlist("calendar")) & offered
        if not codes:
            flash(_("Choose at least one calendar."), "err")
            return _back()
        select_timetree(codes)
    elif request.form.get("calendar_code") in offered:
        select_timetree({request.form["calendar_code"]})
    if request.form.get("new_token"):
        settings["token"] = secrets.token_urlsafe(24)
        for source in settings["sources"]:
            source["token"] = secrets.token_urlsafe(24)
        flash(_("New token generated. Update the ICS URL in your calendar apps."), "ok")
    save_settings(settings)
    sync.prune_calendars()
    sync.reset_timer()
    flash(_("Settings saved."), "ok")
    if request.form.get("sync_now"):
        sync.start_in_background()
    return _back()


@bp.post("/settings/calendars")
def settings_calendars():
    """Per calendar: its name, color, whether the homepage shows it and its name tag, and whether it is part of /ics."""
    for source in settings["sources"]:
        name = request.form.get(f"name_{source['id']}", "").strip()[:60]
        if name:
            source["name"] = name
        color = request.form.get(f"color_{source['id']}", "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color) and not (color == DEFAULT_COLOR and not source["color"]):
            source["color"] = color.lower()
        if source["kind"] == "url" and request.form.get(f"interval_{source['id']}"):
            try:
                source["interval_minutes"] = clamp(request.form[f"interval_{source['id']}"])
            except ValueError:
                abort(400)
        source["homepage"] = bool(request.form.get(f"homepage_{source['id']}"))
        source["feed"] = bool(request.form.get(f"feed_{source['id']}"))
        source["tag"] = bool(request.form.get(f"tag_{source['id']}"))
    save_settings(settings)
    if request.headers.get("X-Autosave"):
        return jsonify(message=_("Settings saved."))
    flash(_("Settings saved."), "ok")
    return redirect(url_for("admin.settings_page"))


@bp.post("/settings/sources")
def settings_sources():
    """Calendars from a file or a URL: add one, fetch a URL calendar now, or remove one. TimeTree's are chosen above."""
    def find(source_id):
        return next((s for s in settings["sources"] if s["id"] == source_id and s["kind"] != "timetree"), None) or abort(404)

    if request.form.get("remove"):
        source = find(request.form["remove"])
        settings["sources"].remove(source)
        save_settings(settings)
        sync.prune_calendars()
        flash(_("Calendar removed."), "ok")
        return redirect(url_for("admin.settings_page"))
    if request.form.get("refresh"):
        source = find(request.form["refresh"])
        if source["kind"] == "url":
            error = sync.refresh(source)
            flash(_("Could not load the calendar: {error}").format(error=_(error)) if error else _("Calendar updated."),
                  "err" if error else "ok")
        return redirect(url_for("admin.settings_page"))

    if sum(s["kind"] != "timetree" for s in settings["sources"]) >= MAX_SOURCES:
        flash(_("Remove a calendar first."), "err")
        return redirect(url_for("admin.settings_page"))
    name = request.form.get("name", "").strip()[:60]
    upload, url = request.files.get("file"), request.form.get("url", "").strip()
    try:
        if upload and upload.filename:
            raw = upload.read(sync.MAX_SOURCE_BYTES + 1)
            if len(raw) > sync.MAX_SOURCE_BYTES:
                raise ValueError("The file is too large.")
            source = new_source("file", name or upload.filename.rsplit(".", 1)[0][:60] or _("Calendar"))
            sync.store_source(source, raw)
        elif url:
            parts = urllib.parse.urlsplit(url)
            if parts.scheme.lower() not in ("https", "http", "webcal") or not parts.hostname or len(url) > 2000:
                raise ValueError("Enter an address that starts with https://, http:// or webcal://.")
            source = new_source("url", name or parts.hostname, url=url,
                                interval_minutes=clamp(request.form.get("interval") or URL_MINUTES))
            found = sync.fetch_url(source)
        else:
            raise ValueError("Choose a file or enter an address.")
    except ValueError as e:
        sync.prune_calendars()                                         # a half-stored calendar does not stay
        flash(_("Could not load the calendar: {error}").format(error=_(str(e))), "err")
        return redirect(url_for("admin.settings_page"))
    settings["sources"].append(source)
    save_settings(settings)
    if source["kind"] == "url":
        sync.state.setdefault("sources", {})[source["id"]] = {"last_run": sync.time.time(), "ok": True, "message": "OK",
                                                               "last_success": sync._now(), "format": found}
    flash(_("Calendar added."), "ok")
    return redirect(url_for("admin.settings_page"))


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
        "appearance": _choice("appearance", ("auto", "light", "dark")),
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
        "upcoming_days": _number("upcoming_days", 3, 14),
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
    """Cloudflare tunnel and the areas it may reach."""
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

    chosen = request.form.getlist("public_areas")
    settings["public_areas"] = [area for area in PUBLIC_AREAS if area in chosen]

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


@bp.post("/settings/tokens")
def settings_token_create():
    """Create an API token. Its value is shown once, in this answer, and only its hash is stored."""
    name = request.form.get("name", "").strip()[:40]
    if not name:
        flash(_("Enter a name for the token."), "err")
        return redirect(url_for("admin.settings_page"))
    if len(settings["api_tokens"]) >= MAX_TOKENS:
        flash(_("Too many tokens. Revoke one first."), "err")
        return redirect(url_for("admin.settings_page"))
    value = "dt_" + secrets.token_urlsafe(32)
    settings["api_tokens"].append({
        "id": secrets.token_hex(6), "name": name, "hash": auth.token_hash(value),
        "write": request.form.get("access") == "write", "created": date.today().isoformat(), "last_used": None,
    })
    save_settings(settings)
    return _render_settings(new_token={"name": name, "value": value})


@bp.post("/settings/tokens/revoke")
def settings_token_revoke():
    token_id = request.form.get("id", "")
    remaining = [t for t in settings["api_tokens"] if t["id"] != token_id]
    if len(remaining) == len(settings["api_tokens"]):
        abort(404)
    settings["api_tokens"] = remaining
    save_settings(settings)
    flash(_("Token revoked."), "ok")
    return redirect(url_for("admin.settings_page"))


@bp.post("/settings/notify")
def settings_notify():
    """Where sync failures are reported. The URL is stored encrypted and never shown again."""
    url = request.form.get("notify_url", "").strip()
    if url:
        if not re.fullmatch(r"https?://[^\s]+", url) or len(url) > 500:
            flash(_("The address must start with http:// or https://."), "err")
            return redirect(url_for("admin.settings_page"))
        settings["notify_url"] = url
    if request.form.get("clear_notify"):
        settings["notify_url"] = ""
    fmt = request.form.get("notify_format", settings["notify_format"])
    settings["notify_format"] = fmt if fmt in notify.FORMATS else "ntfy"
    save_settings(settings)
    if request.form.get("test") and settings["notify_url"]:
        if notify.test():
            flash(_("Test message sent."), "ok")
        else:
            flash(_("The test message could not be sent."), "err")
    else:
        flash(_("Settings saved."), "ok")
    return redirect(url_for("admin.settings_page"))


@bp.post("/settings/backup")
def settings_backup():
    """Download settings, tasks and the waste calendar, encrypted with a password of its own."""
    password = request.form.get("password", "")
    error = _new_password_error(password, request.form.get("confirm", ""))
    if error:
        flash(error, "err")
        return redirect(url_for("admin.settings_page"))
    name = f"daytree-backup-{date.today().isoformat()}.json"
    return Response(backup.export(password), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@bp.post("/settings/restore")
def settings_restore():
    """Replace settings, tasks and the waste calendar from a backup, after checking all of it."""
    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        flash(_("Choose a backup file."), "err")
        return redirect(url_for("admin.settings_page"))
    try:
        content = backup.read(upload.read(backup.MAX_BYTES + 1), request.form.get("password", ""))
    except backup.BackupError as error:
        flash(_(str(error)), "err")
        return redirect(url_for("admin.settings_page"))
    backup.restore(content)
    auth.close_other_sessions()
    display.weather.clear()
    sync.reset_timer()
    tunnel.restart.set()
    flash(_("Backup restored."), "ok")
    return redirect(url_for("admin.settings_page", updated=1))

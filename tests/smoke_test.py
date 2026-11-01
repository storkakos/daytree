"""Smoke test for the whole app logic. No network, no Docker, no real TimeTree/Cloudflare needed.

Run from the repository root after `pip install -r requirements.txt`:

    python tests/smoke_test.py

TimeTree HTTP calls are mocked; `cloudflared` is replaced by a fake shell script on PATH.
Prints "ALL OK" on success; any failed assertion exits non-zero.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

root = Path(tempfile.mkdtemp())
data, secrets_dir, bindir = root / "data", root / "secrets", root / "bin"
for d in (data, secrets_dir, bindir):
    d.mkdir()
os.environ.update(DATA_DIR=str(data), SECRET_KEY_FILE=str(secrets_dir / "secret.key"))
os.environ.pop("SECRET_KEY", None)

# Fake cloudflared: reports a connection and writes the token it received via the environment
fake = bindir / "cloudflared"
fake.write_text('#!/bin/sh\necho "2026 INF Registered tunnel connection connIndex=0"\n'
                f'echo "$TUNNEL_TOKEN" > {root}/tunnel_token_seen\nexec sleep 300\n')
fake.chmod(0o755)
os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"

# Legacy plaintext settings (must be migrated to the encrypted format)
(data / "settings.json").write_text(json.dumps({"email": "a@b.de", "password": "PLAINPW-123", "token": "legacytoken-ABC"}))

import app as A  # noqa: E402


def ok(msg):
    print("ok:", msg)


# ---- 1) Encryption at rest / migration
enc = (data / "settings.enc").read_bytes()
assert not (data / "settings.json").exists(), "plaintext file must be gone"
for secret in (b"PLAINPW-123", b"legacytoken-ABC", b"a@b.de"):
    assert secret not in enc, secret
assert A.settings["password"] == "PLAINPW-123" and A.settings["token"] == "legacytoken-ABC"
key = secrets_dir / "secret.key"
assert key.exists() and stat.S_IMODE(key.stat().st_mode) == 0o600
assert not any(p.name == "secret.key" for p in data.iterdir()), "key must not live in the data dir"
assert stat.S_IMODE((data / "settings.enc").stat().st_mode) == 0o600
ok("settings.enc encrypted, legacy file migrated and deleted, key kept separate (mode 600)")

# ---- 2) Wrong key -> startup aborts with a clear message, data untouched
other = secrets_dir / "other.key"
res = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); import app"],
                     capture_output=True, text=True, timeout=60,
                     env={**os.environ, "SECRET_KEY_FILE": str(other)})
assert res.returncode != 0 and "cannot be decrypted" in (res.stderr + res.stdout), res.stderr[-300:]
assert (data / "settings.enc").read_bytes() == enc
ok("wrong key -> abort, data unchanged")

# ---- 3) Pages load, password never rendered
c = A.app.test_client()
H = {"Origin": "http://localhost"}
tok = A.settings["token"]
A.settings["tunnel_token"] = ""
assert c.get("/").status_code == 200
assert b"PLAINPW-123" not in c.get("/settings").data
ok("pages load, password not in HTML")


# ---- 4) Login + calendar list (TimeTree mocked)
class Resp:
    def __init__(self, code, js=None, cookies=None):
        self.status_code, self._js, self.cookies, self.text = code, js or {}, cookies or {}, "x"

    def json(self):
        return self._js


cal_json = {"calendars": [{"name": "Family", "alias_code": "fam123", "deactivated_at": None},
                          {"name": None, "alias_code": "work9", "deactivated_at": None},
                          {"name": "Old", "alias_code": "old1", "deactivated_at": 1}]}
with mock.patch("requests.put", return_value=Resp(200, cookies={"_session_id": "S"})), \
     mock.patch("requests.Session.get", return_value=Resp(200, cal_json)):
    r = c.post("/settings/login", data={"email": "a@b.de", "password": ""}, headers=H, follow_redirects=True)
assert b"2 calendars found" in r.data and b"old1" not in r.data and b"Unnamed (work9)" in r.data
c.post("/settings/save", data={"calendar_code": "work9", "interval": "2"}, headers=H)
assert A.settings["calendar_code"] == "work9" and A.settings["interval_minutes"] == 30  # clamped to minimum
assert A.DEFAULTS["interval_minutes"] == 180
for sent, stored in (("100", 90), ("1440", 1440), ("9999", 1440)):
    c.post("/settings/save", data={"interval": sent}, headers=H)
    assert A.settings["interval_minutes"] == stored, sent
r = c.get("/settings").data
assert b'<option value="30" >0.5 h</option>' in r and b'<option value="1440" selected>24 h</option>' in r
assert r.count(b"<option value=") >= 48
assert b"Every 24 h" in c.get("/").data
assert b"PLAINPW-123" not in (data / "settings.enc").read_bytes()
ok("login, calendar list, selection, interval minimum")

# ---- 5) CSRF
assert c.post("/sync", headers={"Origin": "http://evil.example"}).status_code == 403
assert c.post("/sync").status_code == 403
assert c.post("/sync", headers={"Referer": "http://localhost/settings"}).status_code == 302
ok("foreign or missing Origin/Referer -> 403, same-site Referer accepted")

# ---- 6) Exposure modes (public side = port 8081 or Cloudflare headers)
(data / "timetree.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
PUB = {"environ_overrides": {"SERVER_PORT": "8081"}}


def set_mode(mode, **extra):
    r = c.post("/settings/tunnel", data={"public_mode": mode, **extra}, headers=H)
    assert r.status_code == 302, r.status_code


set_mode("off")
assert c.get("/", **PUB).status_code == 404
assert c.get(f"/calendar.ics?token={tok}", **PUB).status_code == 404
assert c.get(f"/calendar.ics?token={tok}").status_code == 200           # internal still works
r = c.get("/ping", **PUB)
assert r.status_code == 200 and r.data == b"pong\n"                     # health check is public in every mode
assert c.get("/ping", headers={"Cf-Ray": "abc"}).status_code == 200
ok("mode off: public 404 except /ping, internal ok")

tok = A.settings["token"]
set_mode("ics")
assert c.get(f"/calendar.ics?token={tok}", **PUB).status_code == 200
assert c.get("/calendar.ics?token=wrong", **PUB).status_code == 403
for p in ("/", "/settings", "/api/status"):
    assert c.get(p, **PUB).status_code == 404, p
assert c.post("/sync", headers=H, **PUB).status_code == 404
assert c.get("/", headers={"Cf-Ray": "abc"}).status_code == 404         # Cloudflare header on port 8080 too
assert c.get("/").status_code == 200
assert c.get("/ping", **PUB).status_code == 200
ok("mode ics: only /calendar.ics and /ping public, Cloudflare headers detected")

set_mode("dashboard")
r = c.get("/", **PUB)
assert r.status_code == 200 and tok.encode() not in r.data and b"/settings" not in r.data and b"Sync now" not in r.data
assert c.get("/settings", **PUB).status_code == 404
assert c.post("/sync", headers=H, **PUB).status_code == 404
assert c.post("/settings/save", data={"interval": "10"}, headers=H, **PUB).status_code == 404
assert c.get(f"/calendar.ics?token={tok}", **PUB).status_code == 200
assert c.get("/ping", **PUB).status_code == 200
assert tok.encode() in c.get("/").data                                   # internal view shows the URL
ok("mode dashboard: read-only; no token/settings/sync on the public side")

set_mode("bogus")
assert A.settings["public_mode"] == "off"
ok("invalid mode falls back to off")

# ---- 7) Public hostname
set_mode("ics", public_hostname="calendar.example.com")
assert f"https://calendar.example.com/calendar.ics?token={A.settings['token']}".encode() in c.get("/settings").data
set_mode("ics", public_hostname="evil.com/<x>")
assert A.settings["public_hostname"] == "calendar.example.com"
ok("hostname validated and used in the ICS URL")

# ---- 8) Event preview: all-day, timezone-aware, UTC, recurring (with EXDATE), past and far-future events
today = date.today()


def d(days, fmt="%Y%m%d"):
    return (today + timedelta(days=days)).strftime(fmt)


(data / "timetree.ics").write_text("\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN",
    "BEGIN:VEVENT", "UID:1", "SUMMARY:AllDay", f"DTSTART;VALUE=DATE:{d(2)}", f"DTEND;VALUE=DATE:{d(3)}", "END:VEVENT",
    "BEGIN:VEVENT", "UID:2", "SUMMARY:Tokyo", f"DTSTART;TZID=Asia/Tokyo:{d(3)}T090000", f"DTEND;TZID=Asia/Tokyo:{d(3)}T100000", "END:VEVENT",
    "BEGIN:VEVENT", "UID:3", "SUMMARY:Utc", f"DTSTART:{d(4)}T120000Z", f"DTEND:{d(4)}T130000Z", "END:VEVENT",
    "BEGIN:VEVENT", "UID:4", "SUMMARY:Weekly", f"DTSTART;TZID=Europe/Berlin:{d(-14)}T080000",
    f"DTEND;TZID=Europe/Berlin:{d(-14)}T090000", "RRULE:FREQ=WEEKLY;COUNT=6", f"EXDATE;TZID=Europe/Berlin:{d(7)}T080000", "END:VEVENT",
    "BEGIN:VEVENT", "UID:5", "SUMMARY:Past", f"DTSTART;VALUE=DATE:{d(-30)}", f"DTEND;VALUE=DATE:{d(-29)}", "END:VEVENT",
    "BEGIN:VEVENT", "UID:6", "SUMMARY:Far", f"DTSTART;VALUE=DATE:{d(90)}", f"DTEND;VALUE=DATE:{d(91)}", "END:VEVENT",
    "END:VCALENDAR", "",
]))
ev = A.upcoming(limit=50)
titles = [e["title"] for e in ev]
assert titles.count("Weekly") == 3, titles                                # today, +14, +21 (+7 excluded by EXDATE)
assert {"AllDay", "Tokyo", "Utc"} <= set(titles) and "Past" not in titles and "Far" not in titles, titles
assert [e["sort"] for e in ev] == sorted(e["sort"] for e in ev)
assert next(e for e in ev if e["title"] == "AllDay")["time"] == "all day"
assert c.get("/").status_code == 200
ok("preview: all-day, timezones, recurrences and EXDATE handled")

# ---- 9) ICS repair: exporter output without VALUE=DATE and with a VTIMEZONE lacking daylight saving
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
fixed = A.fix_ics(broken).replace(b"\r\n ", b"")
for line in (b"DTSTART;VALUE=DATE:20261203\r\n", b"DTEND;VALUE=DATE:20261204\r\n", b"EXDATE;VALUE=DATE:20261231\r\n",
             b"DTSTART;VALUE=DATE:20261205\r\n", b"DTSTART;TZID=Europe/Berlin:20261120T130000\r\n",
             b"CALSCALE:GREGORIAN\r\n", b"METHOD:PUBLISH\r\n"):
    assert line in fixed, line
assert b"VALUE=DATE;VALUE=DATE" not in fixed and fixed.count(b"BEGIN:VTIMEZONE") == 1 and b"BEGIN:DAYLIGHT" in fixed
assert b"COLOR" not in fixed and b"COMMENT" not in fixed and fixed.count(b"\n") == fixed.count(b"\r\n")
assert A.fix_ics(b"not an ics") == b"not an ics"                          # unreadable input is passed through
ok("ICS repair: VALUE=DATE added, VTIMEZONE covers daylight saving")

# ---- 10) Tunnel: token extraction, start/stop, token only via environment, never in HTML/plaintext
T = "eyJhIjoiMTIzNDU2IiwidCI6ImFiY2RlZiIsInMiOiJ4eXoxMjM9In0="
r = c.post("/settings/tunnel", data={"tunnel_token": "not a token", "public_mode": "ics"}, headers=H, follow_redirects=True)
assert b"not recognized" in r.data and A.settings["tunnel_token"] == ""
r = c.post("/settings/tunnel", data={"tunnel_enabled": "1", "public_mode": "ics"}, headers=H, follow_redirects=True)
assert b"token first" in r.data and A.settings["tunnel_enabled"] is False
cmd = f"docker run cloudflare/cloudflared:latest tunnel --no-autoupdate run --token {T}"
c.post("/settings/tunnel", data={"tunnel_token": cmd, "tunnel_enabled": "1", "public_mode": "ics"}, headers=H)
assert A.settings["tunnel_token"] == T and A.settings["tunnel_enabled"] is True
for _ in range(30):
    if A._tunnel["status"] == "connected":
        break
    time.sleep(0.5)
assert A._tunnel["status"] == "connected", A._tunnel
seen = root / "tunnel_token_seen"
for _ in range(10):
    if seen.exists() and seen.read_text().strip():
        break
    time.sleep(0.2)
assert seen.read_text().strip() == T, "token must arrive via the TUNNEL_TOKEN environment variable"
page = c.get("/settings").data
assert T.encode() not in page and b"Status:" in page and b"connected" in page
assert T.encode() not in (data / "settings.enc").read_bytes()
proc = A._tunnel["proc"]
ok("tunnel starts, token via environment, not in HTML/plaintext, status connected")

c.post("/settings/tunnel", data={"public_mode": "ics"}, headers=H)       # checkbox off -> stop
for _ in range(30):
    if proc.poll() is not None and A._tunnel["status"] == "off":
        break
    time.sleep(0.5)
assert proc.poll() is not None and A._tunnel["status"] == "off", A._tunnel
assert A.settings["tunnel_token"] == T                                    # token stays stored
c.post("/settings/tunnel", data={"clear_token": "1", "public_mode": "ics"}, headers=H)
assert A.settings["tunnel_token"] == ""
ok("tunnel stops, token can be deleted")

print("ALL OK")
sys.stdout.flush()
os._exit(0)   # background threads (scheduler, tunnel supervisor) never end on their own

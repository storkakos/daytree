# AGENT.md — instructions for the implementing AI agent

Read this file completely before touching anything. It is the single source of truth for what this
repository must do, what has already been built and what has **not** been verified. Planned work is
tracked in GitHub issues. Everything in this repository (code, UI strings, comments, docs, commit
messages) must be written in **English**.

---

## 1. Goal

A small, self-hosted Docker service that:

1. Exports a **TimeTree** calendar to an **iCalendar (`.ics`)** file on a schedule.
2. Serves that file under a secret-token URL so **Microsoft Outlook** can subscribe to it
   ("Add calendar → Subscribe from web").
3. Provides a **homepage** with events, tasks, weather and waste collection, a read-only `/kiosk` view for
   fixed screens, and a separate **Settings** menu with the sync status.
4. Optionally exposes the dashboard and/or the ICS file to the internet through a **Cloudflare tunnel**
   that is started from inside the container using a tunnel token entered in the dashboard.
5. Stores **no secret in plaintext**, anywhere.
6. Ships as a **slim Alpine-based image**.

Owner/user context: a single technical user running this on a home server / homelab (Docker). Not a
multi-tenant product.

---

## 2. Requirements history (what the user asked for, in order)

The user wrote in German; these are faithful English translations. Treat every item as a binding requirement.

1. Links given: `eoleedi/TimeTree-Exporter` and `gottfried1980/timetable-docker`.
   *"Can we turn these two into one TimeTree-to-ICS solution together with a dashboard, in Docker?
   We talked before about exporting TimeTree and linking it to my Outlook calendar."*
2. Link given: `Poeschl/docker-timetree-exporter`, *"And what did he do?"* — (answered: it runs
   cron + `timetree-exporter` and serves the ICS via nginx on port 8080; no dashboard, no token
   protection.) Reference only; its code is not used.
3. *"Can we also set the sync intervals in our dashboard?"*
4. *"Calendar selection must also be in the dashboard. All settings must be in the dashboard — in a
   separate menu."*
5. *"No, nothing may be stored in plaintext!"* (answer to the question whether to add a dashboard
   password, plus the plaintext-credentials complaint). Also: *"I may also want to set up a Cloudflare
   tunnel from the dashboard using the Cloudflare token. Uptime Kuma does that [in the same way].
   And I want to define whether Cloudflare exposes the dashboard or only the ICS. The Docker
   container should be slim — maybe with Alpine?"*
6. *"Create a ZIP with agent.md, the build and a README. Everything must be in English, including
   what I asked you. I will put it into a repo and another AI agent should implement it."*
   → this file, the code, and `README.md`.

### Decisions that follow from those requirements

| Decision | Rationale |
|---|---|
| **No built-in dashboard login.** | The user answered "No" to adding a dashboard password. Compensation: public dashboard mode is read-only, settings/sync are never reachable from the public side, and the UI recommends a Cloudflare Access policy. The one exception the owner asked for is the settings padlock (`SETTINGS_LOCK`): a password for the settings only, with no login page and no user accounts. **Do not add other app-level auth unless the user asks.** |
| Everything in **English**. | "Everything must be in English." German exists only as the app's second language, through the translations in `daytree/i18n.py`. |
| Tunnel is controlled **from the dashboard** (token pasted in Settings), `cloudflared` runs inside the same container. | Mirrors how Uptime Kuma's tunnel feature works. Users who prefer an external `cloudflared` (e.g. the one in Uptime Kuma) can point its public hostname to `http://daytree:8081`. |
| The shared areas (**ICS feed, homepage read-only, API with token**, any combination, none by default) are **enforced by the app**, not only by Cloudflare config. | The tunnel token is remotely managed, so path rules live in Cloudflare; the app must not rely on them. |
| One container, Flask, **no database**. | Keep it slim. State = one encrypted settings file + one ICS file + a small status JSON. |

---

## 3. Architecture

```
Browser / Outlook ──► :8080 (internal)  ┐
Cloudflare tunnel ──► :8081 (public)    ├─► gunicorn (1 worker, 4 threads) ─► Flask app (daytree/)
                                         ┘        │
                                                  ├─ scheduler thread  ─► `timetree-exporter` subprocess ─► /data/timetree.ics
                                                  ├─ tunnel supervisor ─► `cloudflared tunnel run` subprocess (TUNNEL_TOKEN via env)
                                                  └─ /data/settings.enc (Fernet), key in /secrets/secret.key
```

### Files

| Path | Purpose |
|---|---|
| `app.py` | Entry point for gunicorn (`app:app`). Builds the app and starts the background threads. |
| `daytree/core.py` | Data paths, the encryption key, encrypted files, the settings and the time zone. No Flask. |
| `daytree/auth.py` | Access control: internal and public side, shared areas, CSRF check, API and ICS tokens, padlock. |
| `daytree/sync.py` | TimeTree login and export, ICS repair, the scheduler, and reading events from the ICS file. |
| `daytree/tunnel.py` | The `cloudflared` supervisor. |
| `daytree/display.py` | The homepage, `/kiosk` and their data: config, events, weather, background photos, waste bins. |
| `daytree/api.py` | Tasks API, `/api/status`, `/ping` and `/calendar.ics`. |
| `daytree/admin.py` | The settings pages and their forms. |
| `daytree/web.py` | Builds the Flask app from the areas above, registers the access guard and starts the threads. |
| `daytree/i18n.py` | German translations (English strings are the keys) and the language of a request. |
| `daytree/__init__.py` | `VERSION`. |
| `templates/kiosk.html` | Homepage and `/kiosk`: agenda, week, tasks, reminders, weather forecast, waste collection and setup popup. Vanilla JS, responsive for landscape, portrait and phones. |
| `templates/base.html`, `settings.html` | Settings UI with sync status (server-rendered, light/dark via CSS). |
| `Dockerfile` | `python:3.12-alpine`; copies the static `cloudflared` binary from the official image; non-root user `appuser` (uid 1000). |
| `docker-compose.yml` | One service; **named volumes** `daytree-data` (`/data`) and `daytree-secrets` (`/secrets`). Only port 8080 is published. |
| `requirements.in` | Direct dependencies: `timetree-exporter`, `flask`, `gunicorn`, `icalendar`, `recurring-ical-events`, `cryptography`. |
| `requirements.txt` | All pinned versions, generated from `requirements.in` with `pip-compile`. Do not edit by hand. |
| `requirements-dev.in`, `requirements-dev.txt` | Test tools (`pytest`), pinned the same way. Not installed in the image. |
| `.github/` | Tests on every pull request, and Dependabot updates against `dev`. |
| `static/` | Logo, favicon, home screen icons, `manifest.json` (app install for `/` only; `icon-192.png` is the logo scaled down) and weather icons (`weather-sprites.svg`, from Bonjourr). No service worker: no offline mode on purpose. |
| `tests/`, `pytest.ini` | pytest suite (no network/Docker). `test_access.py` holds the access table. File modes and the fake `cloudflared` need Linux; those tests are skipped on Windows. |
| `tests/port_check.py` | Checks internal/public port detection against the running container. Prints `ALL OK`. |
| `.env.example` | Only `PORT` and an optional `SECRET_KEY`. **No credentials belong in env files.** |

### Runtime data

| File | Content | Protection |
|---|---|---|
| `/data/settings.enc` | **All** settings as one Fernet-encrypted JSON blob: TimeTree e-mail + password, selected calendar, interval, ICS token, tunnel token, scrypt hash of the padlock password, API tokens (name, access, SHA-256 hash, dates), shared tunnel areas, public hostname, language, time zone, date/time formats, tasks switch. | Encrypted, mode 600 |
| `/data/tasks.enc` | Tasks shown on the homepage, as one Fernet-encrypted JSON blob. | Encrypted, mode 600 |
| `/data/waste.enc` | The uploaded waste collection calendar, as one Fernet-encrypted JSON blob. | Encrypted, mode 600 |
| `/secrets/secret.key` | Fernet key, generated on first start (or `SECRET_KEY` env / `SECRET_KEY_FILE`). | Separate volume, mode 600 |
| `/data/timetree.ics` | Latest successful export. Replaced atomically only on success. | **Not encrypted** (it must be served; contains event titles) |
| `/data/event_colors.json` | Event UID to TimeTree color, for the homepage bullets. `fix_ics()` removes COLOR from the published feed. | Plain, contains no secrets |
| `/data/state.json` | Last run / success time, event count, last message. | Plain, contains no secrets |
| `/data/background.json` | Current photos and when they were fetched, so a restart does not change them. | Plain, contains no secrets |

Legacy `/data/settings.json` (plaintext, from an earlier iteration) is migrated to `settings.enc` and deleted on startup.

A backup from **Settings → Backup** (`daytree/backup.py`) is a JSON file with a readable header (`format`, `version`,
`app`, scrypt parameters and salt) and `data`: settings, tasks and waste ICS, Fernet-encrypted with a key derived from
the backup password. A restore refuses scrypt parameters outside what real backups use, checks every settings type,
task and the waste calendar, and only then replaces anything; old settings formats go through `core.normalize()`.

### HTTP routes

| Route | Internal (8080) | Public side (8081 or request with `Cf-Ray` / `Cf-Connecting-Ip`) |
|---|---|---|
| `GET /` kiosk homepage with first-time setup popup | yes | area `homepage` (read-only view: no settings gear, no tasks, no setup popup) |
| `GET /kiosk` read-only homepage for fixed screens: tasks can be read, no settings gear, no setup popup | yes | area `homepage` (without tasks) |
| `GET /api/config`, `/api/events`, `/api/background`, `/api/weather`, `/api/waste`, `/static/…` | yes | area `homepage` without a token (the greeting name is removed); area `api` with a token |
| `/api/tasks` (GET, POST), `/api/tasks/<id>` (GET, PUT, PATCH, DELETE), `GET /api/calendars`, `GET /api/calendars/<id>/events`, `GET /api/status` | yes | area `api` with a token |
| `POST /api/background/refresh` | yes, padlock applies | area `api` with a read-write token |
| `POST /api/sync` | only with a read-write token (`TOKEN_ONLY_PATHS`), so a forged `Origin` cannot get around the padlock | area `api` with a read-write token |
| `POST /sync` | yes, padlock applies | never |
| `GET /settings`, `POST /settings/login`, `/settings/save`, `/settings/display`, `/settings/waste`, `/settings/tunnel`, `/settings/lock`, `/settings/password`, `/settings/tokens`, `/settings/tokens/revoke` | yes, padlock applies | never |
| `POST /settings/unlock` (opens the padlock, or sets its first password) | yes | never |
| `GET /calendar.ics?token=…` | yes (token required) | area `ics` (token required) |
| `GET /ping` (health check for uptime monitors, returns only `pong`) | yes | always, whatever is shared |
| anything else on the public side | – | 404; `/api/` in area `api` without a token: 401 |

All state-changing requests additionally require a same-origin `Origin`/`Referer` (basic CSRF protection), except
`/api/` requests with a valid read-write API token. API tokens:

- count only in the `Authorization: Bearer` header of an `/api/` request, never in the URL;
- an unknown token on `/api/` gets 401, a read-only token gets 403 for anything but `GET`;
- open the API on the public side only when the area `api` is shared; they never open the settings or `/sync`.

"Padlock applies": with `SETTINGS_LOCK=true` these routes answer 403 until the browser is unlocked, and `GET /settings`
shows the padlock instead. `/settings/login` and `/settings/save` stay open while the app is not set up yet, so the
first-time setup on the homepage works.

### Key behaviours you must preserve

- **Single gunicorn worker** (scheduler, tunnel supervisor and state live in-process).
- **Secrets never reach templates, logs, argv or the browser.** `view()` strips `password` and
  `tunnel_token`; the TimeTree password is passed to the exporter and the tunnel token to
  `cloudflared` **only through the child's environment** (`TIMETREE_PASSWORD`, `TUNNEL_TOKEN`), and
  `SECRET_KEY` is removed from the child environment.
- A blank password field means "keep the stored password".
- API tokens are `dt_` plus 32 random bytes. Only their SHA-256 hash is stored; `last_used` is saved at most once a day.
- With `HTTPS_URL` set, plain HTTP on the internal side redirects `/` and `/settings` there (308) and refuses writes
  and any request with an `Authorization` header (403). `/kiosk`, its reads, `/ping` and the healthcheck stay on HTTP.
  `ProxyFix` trusts `X-Forwarded-Proto` for `request.is_secure`; the tunnel side is never redirected. An invalid
  `HTTPS_URL` stops the start.
- The padlock keeps unlocked sessions in process memory only, so a restart locks every browser. The cookie is
  `HttpOnly` and `SameSite=Strict`; a session closes after 30 minutes without a request, with **Lock now**, or when the
  password changes in another browser. Five wrong passwords block unlocking for 60 seconds.
- The ICS file is replaced only if the exporter exits 0 and produced a non-empty file.
- The sync interval defaults to 3 hours, is limited to 30–1440 minutes in 30-minute steps, and changes take effect immediately.
- Public-side access control fails **closed** (no area shared by default; the homepage area is an explicit list of
  paths in `HOMEPAGE_PATHS`); `is_public()` also treats
  Cloudflare headers as public, so a mis-pointed tunnel (to port 8080) is still restricted.
- If `settings.enc` cannot be decrypted, startup aborts with a clear message and **does not overwrite** the file.

### Facts learned about upstream tools (verified by reading their source)

- `timetree-exporter` (PyPI, MIT, Python ≥ 3.11, uses an **unofficial** TimeTree web API):
  - CLI: `timetree-exporter -c <calendar_code> -o <file.ics>`; reads `TIMETREE_EMAIL` / `TIMETREE_PASSWORD`.
  - If the calendar code is not found it falls back to an **interactive prompt** → the app runs it
    with `stdin=DEVNULL` so it fails instead of hanging.
  - The app imports `timetree_exporter.api.auth.login` and `timetree_exporter.api.calendar.TimeTreeCalendar.get_metadata()`
    to list calendars (fields used: `name`, `alias_code`, `deactivated_at`).
  - Accounts without an e-mail/password (Google/Apple-only login) cannot log in.
  - Upstream warns about rate limiting and API breakage; keep the minimum interval conservative.
  - Version 1.0.0 writes all-day dates without `VALUE=DATE` and a VTIMEZONE that only covers the stored
    start dates. `fix_ics()` in `daytree/sync.py` repairs both after every export and also adds `CALSCALE`, `METHOD`
    and `X-WR-CALNAME` and removes `COLOR`. Keep the header changes when upstream fixes the defects.
- Every `/calendar.ics` request is logged with method, status and user agent, never with the query string.
- `cloudflared` official image: binary at `/usr/local/bin/cloudflared`, built with `CGO_ENABLED=0`
  (static → runs on Alpine); reads `TUNNEL_TOKEN` from the environment; logs the line
  `Registered tunnel connection` when a connection is established (used for the "connected" status).
- `gottfried1980/timetable-docker` is a separate TV/kiosk dashboard with its own TimeTree sync
  (session cookie, German UI). It is **not integrated**.

---

## 4. Verification status — READ THIS

The original author worked in a sandbox **without a Docker daemon, without access to PyPI, and without
real TimeTree/Cloudflare credentials**.

### Verified on real Docker (2026-11-01, Debian 13, Docker 29.9, `timetree-exporter` 1.0.0)

- The image builds and the container becomes healthy; it runs as non-root (uid 1000) without compiler tools.
- The test suite (then `tests/smoke_test.py`) passed with the real packages inside the image.
- `tests/port_check.py` against real gunicorn: `SERVER_PORT` reflects the listening socket and is not
  influenced by the `Host` header, so `is_public()` is correct as written.
- `/secrets/secret.key` and `/data/settings.enc` have mode 600; `/calendar.ics` returns 403 without or
  with a wrong token.
- The upstream functions used by the app exist with the expected signatures (not called against the live API).
- Event preview against a realistic fixture: all-day, timezone-aware, UTC, recurring events and EXDATE.
  Recurrences are expanded with `recurring-ical-events`.
- With the owner's real TimeTree account: sign-in, calendar list, export and event preview work
  (one small calendar including recurring and all-day events); `/calendar.ics` returns 200 with the
  correct token; saving a new interval changes the next-sync time without a restart; the stored password
  and ICS token were not found in plaintext in `/data`, `/secrets`, process arguments or HTML.
- With a real `cloudflared` and public hostname: `Registered tunnel connection` detection, and the then
  modes `off`, `ics` and `dashboard` (now no area, `ics`, `ics` + `homepage`) behaved as specified, both with the hostname pointing to port 8081 and
  to port 8080 (restricted by the Cloudflare-header fallback).
- `PORT` in `.env` changes the host port (tested with 8090) without affecting the tunnel.
- After the split into the `daytree/` package (2026-11-02): the image builds, the container becomes healthy as uid 1000,
  `tests/port_check.py` prints `ALL OK`, the key and settings files have mode 600, and `pytest` passes on Linux.

### Verified (by the pytest suite in `tests/`, with TimeTree HTTP mocked and a fake `cloudflared` script)

- Settings are encrypted at rest; legacy plaintext is migrated and deleted; key is stored outside `/data`.
- Wrong key → clean abort, data untouched.
- Login flow, calendar list (deactivated calendars filtered), calendar selection, interval clamping.
- CSRF origin check for every state-changing route.
- Access table: every route against the internal side, the internal side with the padlock closed, and the public side
  (port 8081, `Cf-Ray`, `Cf-Connecting-Ip`) with no area, `ics`, `homepage` or `api` shared, with a correct, wrong or
  missing ICS token; and API tokens through the tunnel with and without the `api` area.
- Settings padlock: first password, unlock, lock, idle timeout, lockout, password change, setup while locked.
- API tokens: shown once, stored as a hash, read and write access, revoke, never from the URL, nothing from outside;
  a table of every route with no, read-only, read-write and unknown token; every route is listed in the tables.
- Tasks, languages and date formats, the waste collection calendar, and background and weather handling.
- Tunnel token extraction from a pasted command, start/stop supervision, token delivered via `TUNNEL_TOKEN` only,
  token absent from HTML and from the encrypted file's plaintext, status `connected`.

### NOT verified — you must check these

1. **Real `cloudflared` failure paths**: error surfacing and behaviour after a token is revoked.
2. **Outlook/Google refresh interval**: subscribing works in both, confirmed by the owner. The refresh interval
   is believed to be hours and not controllable from the server; this was not measured. A subscription
   shows all events in one calendar color. The owner decided against one ICS per label.
3. Scheduler timing and the exporter timeout path have no automated test.
4. **Browsers**: there is no automated browser test. The homepage has been tested in current headless Chromium, and
   by the owner on their screens. Chromium 56 itself was not run here.

---

## 5. Planned work

Planned work is tracked in [GitHub issues](https://github.com/storkakos/daytree/issues). Work in the order their
dependencies give. After a change, update section 4 of this file with what you actually verified.

### Acceptance criteria

- [ ] `docker compose up -d --build` succeeds; container becomes healthy.
- [ ] First start creates `/secrets/secret.key` (600) and `/data/settings.enc`; `grep` for the TimeTree password, ICS token and tunnel token in `/data` and `/secrets` finds nothing in plaintext.
- [ ] Settings → sign-in lists the real calendars; selecting one and saving works; **Sync now** produces `/data/timetree.ics`.
- [ ] Changing the interval in the UI changes the next-sync time without a restart.
- [ ] `GET /calendar.ics` → 403 without/with wrong token, 200 with the correct one.
- [ ] Area `ics` only: via the public hostname only `/calendar.ics?token=…` and `/ping` work; `/`, `/settings`, `/api/status` → 404.
- [ ] Area `homepage`: `/` works read-only (no token, no Settings link, no Sync button); `/settings`, `/api/status` and all POSTs → 404.
- [ ] Area `api`: `/api/tasks` works with a token and gives 401 without one; `/` and `/settings` → 404.
- [ ] Nothing shared: everything on the public side except `/ping` → 404.
- [ ] No password, token or key appears in container logs, HTML responses or process arguments (`ps`).
- [ ] `pytest` passes.
- [ ] Final image runs as non-root and does not contain build tools.

---

## 6. Rules for working in this repo

### Language

- Python is the project's language for the backend, tools and tests. The homepage and settings stay plain HTML, CSS
  and JavaScript in `templates/`, with no build step.
- **English only** — code, comments, docs, commit messages, issues and pull requests. Translate anything written in
  German into English. German is only the app's second language: UI strings are written in English and translated in
  `daytree/i18n.py`; every string needs a German entry (checked by the tests).

### Security

- Never introduce plaintext secret storage, never log secrets, never put secrets in argv, env files,
  compose files, templates or test fixtures. Redact tokens in any error output you add.
- Do not publish port 8081 in `docker-compose.yml`.
- Do not add a login/auth system, a database, or a JS framework without the owner's explicit request.
- Every new route goes into the access table in `tests/test_access.py`. Routes are closed by default, and opening one is the exception.
- Do not change what an area shares without updating the UI text, README, the access table and this file.
- The repo is public. No device names, IP addresses or personal data in code, commits, issues or screenshots.

### Architecture

- Keep the single-worker constraint.
- Put new code into the module of its area in `daytree/`. `web.py` only joins the areas, and `core.py` stays free of
  Flask.
- Keep dependencies minimal; every new package must be justified (image size).
- Background and weather lookups and the notification address the owner sets are the only outgoing calls besides
  TimeTree. They must fail quietly with a short timeout and never use Bonjourr's own backend. Notifications never
  contain the sync error.
- The project is GPL-3.0 since the release after 0.9.0. Code from Bonjourr (GPL-3.0) may be reused with credit.
- The oldest supported browser is Chromium 56. Do not use object spread, CSS grid, `clamp()`, `min()`, `max()`,
  `inset`, flex `gap` or alpha hex colors on the homepage.
- Updates must not break an installation. New settings need a default that keeps the previous behavior, and breaking
  changes need a migration and a changelog note.

### Workflow

- Do not work without an issue. Every change belongs to one, and every commit names its number, for example `#19`.
- One issue can have several commits, but one commit must not belong to several issues.
- Settle open questions in an issue before building it.
- Use one branch per issue, for example `feature/19-waste-bins`. It goes by pull request into `dev`, then `dev` goes
  into `main`. The pull request closes the issue with `Closes #19`.
- A pull request into `dev` is merged as soon as its checks pass, so the owner can test it on the test box. Merges
  into `main` and GitHub releases need the owner's approval.

### Quality

- When you finish a task, run `pytest`. CI runs it on Linux, where the file-mode and tunnel tests are not skipped.
- Every bug gets a test: reproduce the fault first, then fix it.
- A fix is finished only after it was checked in the real app. Say plainly what could not be checked.
- Check `README.md` and `CHANGELOG.md` after every change, and correct them when they no longer match the app.
  Add every visible change to `CHANGELOG.md` under `Unreleased` right away, not at release time.

### Releases

- Every issue that changes what users get raises the version in its own branch: bug fixes raise the last number
  (`x.x.+`), new functions the middle number (`x.+.x`) and very large changes the first number (`+.x.x`). Refactoring,
  tests, CI and changes to text only raise nothing; their notes, if any, wait under `Unreleased` for the next version.
- Steps in the issue's branch, before the pull request:
  1. Set `VERSION` in `daytree/__init__.py`.
  2. Move the notes under `Unreleased` in `CHANGELOG.md` to a section with the version and date, start it with one
     sentence for users, and add the compare link.
  3. Run `pytest`, and check `README.md` and the changelog section against the app.
- After the pull request is merged into `dev`, tag the merge commit with an annotated tag and push it:
  `git tag -a vx.y.z -m "Release x.y.z"`.
- `dev` goes into `main` only when the owner says so. Then:
  1. Run `pytest`, and check `README.md` and `CHANGELOG.md` against the app.
  2. Merge `dev` into `main` by pull request.
  3. Publish a GitHub release for the newest version. Below 1.0.0 name it `vx.y.z (Beta)` and mark it as pre-release.
     From 1.0.0 on name it `vx.y.z` and publish it as a normal release. Use every changelog section since the last
     GitHub release as the text, and add upgrade notes.

---

## 7. Open questions for the owner (do not decide these yourself)

Decided by the owner: the homepage is a TV/kiosk view for landscape and portrait screens, without an
Austrian or regional calendar or music. Multiple calendars are planned for later: one ICS token per
calendar, and a choice of which calendars appear on the homepage.

1. Should Settings ever be reachable through the tunnel (currently never; internal only)?

---

## 8. Known limitations (accepted)

- Uses an unofficial TimeTree API; it can break or rate-limit at any time.
- Only e-mail/password TimeTree accounts are supported.
- The dashboard is unauthenticated by design (owner decision); protect it with Cloudflare Access or keep it on the LAN.
- Anyone with both `/data` and `/secrets` (or the running container) can decrypt the settings — the password
  must be recoverable because the TimeTree API needs it.
- Only the *event preview* in the dashboard is limited to the next 60 days / 15 entries; the ICS file contains everything the exporter returns.

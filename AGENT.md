# AGENT.md — instructions for the implementing AI agent

Read this file completely before touching anything. It is the single source of truth for what this
repository must do, what has already been built, what has **not** been verified, and what you are
expected to do next. Everything in this repository (code, UI strings, comments, docs, commit
messages) must be written in **English**.

---

## 1. Goal

A small, self-hosted Docker service that:

1. Exports a **TimeTree** calendar to an **iCalendar (`.ics`)** file on a schedule.
2. Serves that file under a secret-token URL so **Microsoft Outlook** can subscribe to it
   ("Add calendar → Subscribe from web").
3. Provides a **web dashboard** with sync status, upcoming events and a separate **Settings** menu.
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
| **No built-in dashboard login.** | The user answered "No" to adding a dashboard password. Compensation: public dashboard mode is read-only, settings/sync are never reachable from the public side, and the UI recommends a Cloudflare Access policy. **Do not add app-level auth unless the user asks.** |
| All UI strings/docs in **English**. | "Everything must be in English." German i18n is an optional future feature, not required. |
| Tunnel is controlled **from the dashboard** (token pasted in Settings), `cloudflared` runs inside the same container. | Mirrors how Uptime Kuma's tunnel feature works. Users who prefer an external `cloudflared` (e.g. the one in Uptime Kuma) can point its public hostname to `http://timetree-ics:8081`. |
| Exposure mode (**off / ICS only / dashboard read-only**) is **enforced by the app**, not only by Cloudflare config. | The tunnel token is remotely managed, so path rules live in Cloudflare; the app must not rely on them. |
| One container, Flask, **no database**. | Keep it slim. State = one encrypted settings file + one ICS file + a small status JSON. |

---

## 3. Architecture

```
Browser / Outlook ──► :8080 (internal)  ┐
Cloudflare tunnel ──► :8081 (public)    ├─► gunicorn (1 worker, 4 threads) ─► Flask app (app.py)
                                         ┘        │
                                                  ├─ scheduler thread  ─► `timetree-exporter` subprocess ─► /data/timetree.ics
                                                  ├─ tunnel supervisor ─► `cloudflared tunnel run` subprocess (TUNNEL_TOKEN via env)
                                                  └─ /data/settings.enc (Fernet), key in /secrets/secret.key
```

### Files

| Path | Purpose |
|---|---|
| `app.py` | The whole backend: encryption, settings, sync scheduler, tunnel supervisor, access guard, routes. |
| `templates/base.html`, `dashboard.html`, `settings.html` | UI (server-rendered, no JS, light/dark via CSS). |
| `Dockerfile` | `python:3.12-alpine`; copies the static `cloudflared` binary from the official image; non-root user `appuser` (uid 1000). |
| `docker-compose.yml` | One service; **named volumes** `timetree-data` (`/data`) and `timetree-secrets` (`/secrets`). Only port 8080 is published. |
| `requirements.txt` | `timetree-exporter`, `flask`, `gunicorn`, `icalendar`, `recurring-ical-events`, `cryptography`. |
| `tests/smoke_test.py` | End-to-end logic test (no network/Docker). Prints `ALL OK`. Needs Linux (file modes, fake `cloudflared`). |
| `tests/port_check.py` | Checks internal/public port detection against the running container. Prints `ALL OK`. |
| `.env.example` | Only `PORT` and an optional `SECRET_KEY`. **No credentials belong in env files.** |

### Runtime data

| File | Content | Protection |
|---|---|---|
| `/data/settings.enc` | **All** settings as one Fernet-encrypted JSON blob: TimeTree e-mail + password, selected calendar, interval, ICS token, tunnel token, exposure mode, public hostname. | Encrypted, mode 600 |
| `/secrets/secret.key` | Fernet key, generated on first start (or `SECRET_KEY` env / `SECRET_KEY_FILE`). | Separate volume, mode 600 |
| `/data/timetree.ics` | Latest successful export. Replaced atomically only on success. | **Not encrypted** (it must be served; contains event titles) |
| `/data/state.json` | Last run / success time, event count, last message. | Plain, contains no secrets |

Legacy `/data/settings.json` (plaintext, from an earlier iteration) is migrated to `settings.enc` and deleted on startup.

### HTTP routes

| Route | Internal (8080) | Public side (8081 or request with `Cf-Ray` / `Cf-Connecting-Ip`) |
|---|---|---|
| `GET /` dashboard | yes | only in mode `dashboard` (read-only view: no ICS URL, no settings link, no sync button) |
| `POST /sync` | yes | never |
| `GET /settings`, `POST /settings/login`, `/settings/save`, `/settings/tunnel` | yes | never |
| `GET /api/status` | yes | only in mode `dashboard` |
| `GET /calendar.ics?token=…` | yes (token required) | modes `ics` and `dashboard` (token required) |
| `GET /ping` (health check for uptime monitors, returns only `pong`) | yes | always, in every mode |
| anything else on public side in mode `off` | – | 404 |

All `POST`s additionally require a same-origin `Origin`/`Referer` (basic CSRF protection).

### Key behaviours you must preserve

- **Single gunicorn worker** (scheduler, tunnel supervisor and state live in-process).
- **Secrets never reach templates, logs, argv or the browser.** `view()` strips `password` and
  `tunnel_token`; the TimeTree password is passed to the exporter and the tunnel token to
  `cloudflared` **only through the child's environment** (`TIMETREE_PASSWORD`, `TUNNEL_TOKEN`), and
  `SECRET_KEY` is removed from the child environment.
- A blank password field means "keep the stored password".
- The ICS file is replaced only if the exporter exits 0 and produced a non-empty file.
- The sync interval defaults to 3 hours, is limited to 30–1440 minutes in 30-minute steps, and changes take effect immediately.
- Public-side access control fails **closed** (default mode `off`); `is_public()` also treats
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
    start dates. `fix_ics()` in `app.py` repairs both after every export and also adds `CALSCALE`, `METHOD`
    and `X-WR-CALNAME` and removes `COLOR`. Keep the header changes when upstream fixes the defects.
- Every `/calendar.ics` request is logged with method, status and user agent, never with the query string.
- `cloudflared` official image: binary at `/usr/local/bin/cloudflared`, built with `CGO_ENABLED=0`
  (static → runs on Alpine); reads `TUNNEL_TOKEN` from the environment; logs the line
  `Registered tunnel connection` when a connection is established (used for the "connected" status).
- `gottfried1980/timetable-docker` is a separate TV/kiosk dashboard with its own TimeTree sync
  (session cookie, German UI). It is **not integrated**; see "Open questions".

---

## 4. Verification status — READ THIS

The original author worked in a sandbox **without a Docker daemon, without access to PyPI, and without
real TimeTree/Cloudflare credentials**.

### Verified on real Docker (2026-11-01, Debian 13, Docker 29.9, `timetree-exporter` 1.0.0)

- The image builds and the container becomes healthy; it runs as non-root (uid 1000) without compiler tools.
- `tests/smoke_test.py` prints `ALL OK` with the real packages inside the image.
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
- With a real `cloudflared` and public hostname: `Registered tunnel connection` detection, and modes
  `off`, `ics` and `dashboard` behave as specified, both with the hostname pointing to port 8081 and
  to port 8080 (restricted by the Cloudflare-header fallback).
- `PORT` in `.env` changes the host port (tested with 8090) without affecting the tunnel.

### Verified (by `tests/smoke_test.py`, with TimeTree HTTP mocked and a fake `cloudflared` script)

- Settings are encrypted at rest; legacy plaintext is migrated and deleted; key is stored outside `/data`.
- Wrong key → clean abort, data untouched.
- Login flow, calendar list (deactivated calendars filtered), calendar selection, interval clamping.
- CSRF origin check.
- Exposure modes `off` / `ics` / `dashboard` on the public side, and Cloudflare-header detection on port 8080.
- Tunnel token extraction from a pasted command, start/stop supervision, token delivered via `TUNNEL_TOKEN` only,
  token absent from HTML and from the encrypted file's plaintext, status `connected`.

### NOT verified — you must check these

1. **Real `cloudflared` failure paths**: error surfacing and behaviour after a token is revoked.
2. **Outlook/Google refresh interval**: subscribing works in both, confirmed by the owner. The refresh interval
   is believed to be hours and not controllable from the server; this was not measured. A subscription
   shows all events in one calendar color. The owner decided against one ICS per label.
3. Scheduler timing and the exporter timeout path have no automated test.

---

## 5. Your tasks (in this order)

1. **Build and run**: `docker compose up -d --build`. Fix any build/runtime problem with minimal changes.
   Keep the image slim (Alpine, no compiler toolchain in the final image; if a wheel is missing, use a
   multi-stage build rather than installing build tools in the final stage).
2. **Run the smoke test without stand-ins**: `pip install -r requirements.txt && python tests/smoke_test.py`.
3. **Add a gunicorn-based test** (or a documented manual check) proving that requests on port 8081 are
   treated as public and on 8080 as internal. If `SERVER_PORT` is unreliable, switch `is_public()` to a
   mechanism that is verified to work, keeping fail-closed behaviour.
4. **Verify event parsing** against real or realistic exporter output (create fixtures if no account is available).
5. **Manual end-to-end check with the owner** (ask for credentials/token interactively; never store them
   in the repo, logs or test fixtures): sign in, list calendars, sync, subscribe in Outlook, start the tunnel
   in each of the three modes and confirm that only the intended paths respond.
6. Optional hardening/QA: convert `tests/smoke_test.py` into `pytest`, add CI (lint, tests, `docker build`),
   pin `CLOUDFLARED_TAG` to a concrete version.
7. **Update section 4 of this file and the README** with what you actually verified.

### Acceptance criteria

- [ ] `docker compose up -d --build` succeeds; container becomes healthy.
- [ ] First start creates `/secrets/secret.key` (600) and `/data/settings.enc`; `grep` for the TimeTree password, ICS token and tunnel token in `/data` and `/secrets` finds nothing in plaintext.
- [ ] Settings → sign-in lists the real calendars; selecting one and saving works; **Sync now** produces `/data/timetree.ics`.
- [ ] Changing the interval in the UI changes the next-sync time without a restart.
- [ ] `GET /calendar.ics` → 403 without/with wrong token, 200 with the correct one.
- [ ] Tunnel mode `ics`: via the public hostname only `/calendar.ics?token=…` and `/ping` work; `/`, `/settings`, `/api/status` → 404.
- [ ] Tunnel mode `dashboard`: `/` works read-only (no token, no Settings link, no Sync button); `/settings` and all POSTs → 404.
- [ ] Tunnel mode `off`: everything on the public side except `/ping` → 404.
- [ ] No password, token or key appears in container logs, HTML responses or process arguments (`ps`).
- [ ] `python tests/smoke_test.py` prints `ALL OK`.
- [ ] Final image runs as non-root and does not contain build tools.

---

## 6. Rules for working in this repo

- **English only** — code, comments, UI, docs, commit messages.
- Never introduce plaintext secret storage, never log secrets, never put secrets in argv, env files,
  compose files, templates or test fixtures. Redact tokens in any error output you add.
- Do not publish port 8081 in `docker-compose.yml`.
- Do not add a login/auth system, a database, or a JS framework without the owner's explicit request.
- Keep dependencies minimal; every new package must be justified (image size).
- Do not change the exposure-mode semantics without updating the UI text, README, the smoke test and this file.
- Keep the single-worker constraint.
- When you finish a task, run `tests/smoke_test.py`.

---

## 7. Open questions for the owner (do not decide these yourself)

1. Should the TV/kiosk dashboard from `gottfried1980/timetable-docker` (daily agenda, week view, tasks,
   reminders) be integrated as an additional container/view? (Offered earlier; unanswered.)
2. Is a German UI (i18n) wanted later?
3. Should Settings ever be reachable through the tunnel (currently never; internal only)?

---

## 8. Known limitations (accepted)

- Uses an unofficial TimeTree API; it can break or rate-limit at any time.
- Only e-mail/password TimeTree accounts are supported.
- The dashboard is unauthenticated by design (owner decision); protect it with Cloudflare Access or keep it on the LAN.
- Anyone with both `/data` and `/secrets` (or the running container) can decrypt the settings — the password
  must be recoverable because the TimeTree API needs it.
- Only the *event preview* in the dashboard is limited to the next 60 days / 15 entries; the ICS file contains everything the exporter returns.

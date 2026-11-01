# TimeTree → ICS

A small self-hosted Docker service that exports a [TimeTree](https://timetreeapp.com) calendar to an
`.ics` file on a schedule, serves it under a secret URL (so Outlook, Google Calendar, Apple Calendar
and others can subscribe), and gives you a web dashboard to manage everything.

- Periodic export of one TimeTree calendar (interval configurable in the dashboard)
- Dashboard: sync status, upcoming events, **Sync now** button
- **Settings menu**: TimeTree sign-in, calendar picker, interval, ICS token, Cloudflare tunnel
- Optional built-in **Cloudflare tunnel**; choose what it exposes: nothing / ICS only / read-only dashboard
- **Nothing stored in plaintext**: all settings are encrypted, the key lives in a separate volume
- Slim Alpine-based image, runs as a non-root user

> **Status:** tested end to end with a real TimeTree account, a Cloudflare tunnel, Outlook and Google
> Calendar. Remaining unknowns are listed in
> [`AGENT.md`](AGENT.md) §4 for the exact list.

## Quick start

```bash
docker compose up -d --build
```

Open `http://<host>:8080`, then go to **Settings**:

1. **TimeTree account** – enter e-mail and password, click *Sign in & load calendars*.
   (A TimeTree account with e-mail/password is required; Google/Apple-only logins have no password.)
2. **Calendar & sync** – pick the calendar, set the interval (0.5–24 h in 30-minute steps, default 3 h), save. Tick *Sync immediately*
   or use **Sync now** on the dashboard.
3. Copy the **ICS URL** and subscribe to it in your calendar app.

No credentials go into `docker-compose.yml` or `.env`.

To use another host port or time zone, set them in a `.env` file next to `docker-compose.yml` and recreate the
container:

```bash
cat > .env <<EOF
PORT=8090
TZ=Europe/London
EOF
docker compose up -d
```

`TZ` must be a valid time zone name, otherwise the container silently falls back to UTC. It affects the dashboard
times, not the event times in the ICS file.

This cannot be changed in the dashboard: port mappings belong to Docker on the host, and giving the app access to
the Docker socket would be a security risk. The app keeps listening on 8080/8081 inside the container, so the
Cloudflare tunnel is not affected.

## Subscribing in Outlook

*Outlook → Add calendar → Subscribe from web → paste the ICS URL.*

- Outlook on the web / Microsoft 365 fetches the URL from Microsoft's servers, so it must be a
  **publicly reachable HTTPS URL** (see the Cloudflare tunnel below).
- Outlook decides how often it refreshes subscribed calendars (typically hours); the sync interval of
  this service only controls how fresh the ICS file is.

## Sharing through a Cloudflare tunnel (optional)

1. In Cloudflare Zero Trust create a tunnel (*Networks → Tunnels → Create a tunnel → Cloudflared*) and copy the **token**
   (or the whole install command – the app extracts the token).
2. In the tunnel's **Public Hostname** settings add your hostname with service **`HTTP` → `localhost:8081`**.
3. In this app: **Settings → Sharing via Cloudflare tunnel** → paste the token, tick *Tunnel enabled*,
   choose a mode, optionally enter the public hostname (used to display the public ICS URL), save.

| Mode | What is reachable through the tunnel |
|---|---|
| **Nothing** (default) | Only the `/ping` health check – all other public requests get 404. |
| **ICS file only** | Only `/calendar.ics?token=…` and `/ping`. Everything else → 404. |
| **Dashboard (read-only) + ICS** | Status and upcoming events (no ICS URL/token shown, no Settings, no Sync button) plus the ICS and `/ping`. |

For uptime monitors such as Uptime Kuma, use `https://<hostname>/ping`. It is reachable in every mode, needs no
token and only returns `pong`. It shows that the service is up, not whether the last sync succeeded.

The mode is enforced by the application on port 8081 (and for any request carrying Cloudflare headers),
independent of the Cloudflare configuration. Settings and **Sync now** are only reachable internally
(port 8080).

> The dashboard has **no login of its own**. If you expose it, protect the hostname with a
> Cloudflare Zero Trust **Access policy**, or keep it on your home network.

If you prefer to run `cloudflared` elsewhere (for example inside Uptime Kuma), simply point its public
hostname at `http://timetree-ics:8081` on the same Docker network and leave the built-in tunnel disabled.

## Security model

- All settings (TimeTree e-mail + password, ICS token, tunnel token, …) are stored in
  `/data/settings.enc`, encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
- The key is generated on first start and stored in `/secrets/secret.key` — a **separate volume**.
  Do not back up the two volumes together. Alternatively provide the key via the `SECRET_KEY` environment variable
  (see `.env.example`).
- Secrets are never rendered in the UI, never written to logs, and are passed to child processes via
  their environment only.
- The ICS file itself (`/data/timetree.ics`, event titles) is stored unencrypted because it has to be served.
- Anyone who has both volumes (or shell access to the running container) can decrypt the settings –
  the TimeTree password has to be recoverable to log in.
- Losing the key means re-entering the settings; the encrypted file cannot be recovered without it.

## Volumes, backup, upgrade

| Volume | Content |
|---|---|
| `timetree-data` → `/data` | `settings.enc`, `timetree.ics`, `state.json` |
| `timetree-secrets` → `/secrets` | `secret.key` |

Upgrade: `git pull && docker compose up -d --build`. Named volumes are used so that the non-root container
user (uid 1000) can write; if you switch to bind mounts, `chown 1000:1000` the host folders.

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Host port for the dashboard (compose). |
| `SECRET_KEY` | – | Fernet key; overrides the key file. |
| `SECRET_KEY_FILE` | `/secrets/secret.key` | Key file location. |
| `DATA_DIR` | `/data` | Data directory. |
| `PUBLIC_PORT` | `8081` | Port treated as "public" (where the tunnel points). |
| `TZ` | `Europe/Berlin` | Container time zone (compose / `.env`). |
| `CLOUDFLARED_TAG` (build arg) | `2026.10.7` | Version of the `cloudflared` image to copy the binary from. |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python tests/smoke_test.py          # prints ALL OK
docker exec -i timetree-ics python - < tests/port_check.py   # needs the running container, prints ALL OK
```

The smoke test needs Linux, but no network, Docker, TimeTree account or Cloudflare account (TimeTree calls
are mocked and `cloudflared` is replaced by a fake script).

Layout: `app.py` (backend), `templates/` (UI), `Dockerfile`, `docker-compose.yml`, `tests/`, `AGENT.md`
(spec and instructions for AI agents working on this repo).

## Limitations

- Uses TimeTree's **unofficial** web API (via the `timetree-exporter` package). It may break or rate-limit
  without notice; avoid very short sync intervals.
- Only e-mail/password TimeTree accounts are supported.
- One calendar at a time.
- Subscribed calendars show all events in one color; TimeTree label colors are not exported.

## License

[MIT](LICENSE)

## Credits

- [eoleedi/TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter) (MIT) – the exporter used under the hood.
- [Cloudflare `cloudflared`](https://github.com/cloudflare/cloudflared) – tunnel client.
- Inspired by [gottfried1980/timetable-docker](https://github.com/gottfried1980/timetable-docker) and
  [Poeschl/docker-timetree-exporter](https://github.com/Poeschl/docker-timetree-exporter); no code from them is used.
  TimeTree is a trademark of its owner; this project is not affiliated with TimeTree, Inc.

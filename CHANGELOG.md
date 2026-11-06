# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.24.2] - 2026-11-06

### Changed

- Daytree runs on Python 3.15 instead of 3.12.

## [0.24.1] - 2026-11-02

### Fixed

- Browsers with their own dark mode no longer override the appearance setting. The pages now tell the browser
  which color schemes they have: both for Automatic, only the chosen one for Light or Dark.

## [0.24.0] - 2026-11-02

The settings can stay light or dark, whatever the device does.

### Added

- **Appearance** in the settings: automatic, light or dark. It applies to the settings, the padlock page and the
  setup window, and changes at once. The homepage keeps its dark glass look.

### Changed

- The setup window and the settings panel on the homepage are dark when the device or the setting is.

## [0.23.0] - 2026-11-02

Daytree can be installed without building it.

### Added

- A prebuilt image for amd64 and arm64 at `ghcr.io/storkakos/daytree`, published with every release as `x.y.z`, `x.y`
  and `latest`. `docker compose pull` gets it; `docker compose up -d --build` still builds from source.
- `DAYTREE_VERSION` in `.env` pins the prebuilt image to a version.

## [0.22.0] - 2026-11-02

The homepage can be installed as an app.

### Added

- A web app manifest for `/`: browsers offer to install the homepage, which then opens without the address bar.
  It needs HTTPS. `/kiosk` stays a plain page, and there is no offline mode.

## [0.21.0] - 2026-11-02

Daytree can be backed up and moved with one file.

### Added

- **Backup** in the settings: download the settings with all credentials, the tasks and the waste calendar as one
  file, encrypted with a password of its own. It does not need the key file.
- **Restore** checks the whole file before it replaces anything, and changes nothing if the password is wrong or the
  file is damaged.

## [0.20.0] - 2026-11-02

Daytree tells you when the calendar is out of date.

### Added

- The homepage shows a note such as *Last updated 3 days ago* after a failed sync, or once the last success is
  older than two sync intervals, and *No connection to Daytree* when it cannot reach the app.
- Notifications in the settings: an ntfy topic or a JSON webhook gets one message when syncing stops working and one
  when it works again. The address is stored encrypted, and a test message checks it.

## [0.19.0] - 2026-11-02

Daytree can send browsers to your HTTPS reverse proxy.

### Added

- `HTTPS_URL` in `.env`: the homepage and the settings move to that address, and writes and API tokens over plain
  HTTP are refused. `/kiosk`, `/ping` and the healthcheck stay on HTTP. The padlock cookie is `Secure` behind HTTPS.

## [0.18.0] - 2026-11-02

Automations can start a sync.

### Added

- `POST /api/sync` starts a sync without waiting for it. It always needs a read-write API token, also on the home
  network, and answers `409` while a sync is running.

## [0.17.0] - 2026-11-02

The API covers calendars and every task operation.

### Added

- `GET /api/calendars` lists the synced calendar, and `GET /api/calendars/<id>/events` returns its events for a date
  range of up to 366 days.
- `GET` and `PUT` for a single task under `/api/tasks/<id>`. `PATCH` can change the title too, and `POST` can create
  a task that is already done.

### Changed

- The task API answers 400 for a body that is not a JSON object or has a `done` that is not true or false.

## [0.16.0] - 2026-11-02

You choose exactly what the tunnel shares.

### Changed

- The tunnel settings offer checkboxes instead of three modes: ICS feed, homepage (read-only) and API, in any
  combination. Your previous choice is taken over.
- A shared homepage no longer makes `/api/status` public, since the homepage does not use it.

### Added

- The API can be shared through the tunnel. It then needs an API token for every request.

## [0.15.1] - 2026-11-02

The chosen option of a switch with several choices is easy to see again.

### Fixed

- In dark mode, the chosen option of controls such as *Read only* / *Read and write* or the clock style was hard to
  tell apart. It is now blue with white text in both modes.

## [0.15.0] - 2026-11-02

Apps and scripts can use the API with their own tokens.

### Added

- API tokens in the settings: give each one a name, choose read-only or read-write, and revoke them one by one.
  A token is shown once and sent as `Authorization: Bearer`. Writing to the API from outside the homepage needs one.

### Changed

- Warnings below the settings sections are red again.

## [0.14.0] - 2026-11-02

The settings can be locked with a password.

### Added

- A padlock for the settings. With `SETTINGS_LOCK=true`, the settings, **Sync now** and new photos need a password,
  which you set in the browser. It locks again after 30 minutes without use, and with **Lock now**.

## [0.13.0] - 2026-11-02

The bins move next to the days they belong to.

### Changed

- Waste bins moved from their own panel into Today and Next 7 days, at the right of each day.
- The weather forecast has no title any more, because it explains itself.

### Added

- A switch shows the bins one day earlier, on the day they have to be put out.

## [0.12.0] - 2026-11-01

Daytree now shows when the bins go out.

### Added

- A waste collection calendar: upload an ICS file, pick a color per collection and see small bins for the next 7 days.
  The file is stored encrypted, and changes reach open homepages without a reload.

## [0.11.0] - 2026-11-01

The homepage gets a weather forecast and becomes a self-updating screen.

### Changed

- Weather icons come from Bonjourr, look the same in every browser and have night versions.
- The photo credit moved to the top left. The settings icon moved to the top right and appears on mouse hover.

### Added

- The homepage takes over settings changed on another device within 30 seconds and reloads itself after an app update.
- `/kiosk` shows the homepage for a fixed screen: tasks are read-only and there is no access to the settings.
- The background photo can change after a custom number of minutes.
- A weather forecast above the day view, for the next 10 hours or 7 days. Choose it under Settings → Weather.

### Fixed

- In automatic language mode the homepage opens in the browser's language straight away, and pages are no longer stored by the browser.
- On phones the homepage now scrolls, follows the system font size and no longer hides tasks and events.
- The background photo no longer changes when the app restarts, and a new change interval applies to the current photo.
- The homepage stayed empty on older browsers (Chromium 56 and similar).
  Homepage and settings no longer depend on newer JavaScript and CSS features.

## [0.10.0] - 2026-11-01

The project is now called Daytree and has grown from a calendar export into a home calendar app.

### Added

- Kiosk homepage for TV, monitor and tablet, in landscape and portrait, with clock, today's agenda, the next
  7 days, tasks and reminders 30 minutes before an event.
- First-time setup popup that requires the TimeTree sign-in and calendar selection.
- Encrypted task storage, with a setting to show or hide tasks on the homepage.
- German user interface, with automatic language detection and a language setting.
- Settings for the time zone, 12 or 24 hour clock and date style.
- Daytree logo, favicon and home screen icon.
- Photo or color backgrounds with blur and brightness settings. Photos come from Lorem Picsum.
- Colored bullets on the homepage that match the TimeTree event colors.
- Greeting with your name, and current weather from Open-Meteo.
- Analog clock, seconds, clock size, font family, font size and font weight settings.
- Settings panel on the homepage that saves changes immediately and shows them live.
- About section in the settings, with project links and the version.

### Changed

- The project is now called Daytree. The Compose project, container and volumes were renamed; see the README to
  keep your data when you upgrade.
- The sync status and **Sync now** moved to the settings page, opened with the gear icon on the homepage.
- The read-only public dashboard is now the kiosk view without tasks and settings.
- The CSRF check now covers all state-changing requests.
- New design for the homepage and settings, inspired by Bonjourr.
- The license changed from MIT to GPL-3.0.

## [0.9.0] - 2026-11-01

First public beta. Tested end to end with a real TimeTree account, a Cloudflare tunnel, Outlook and Google Calendar.

### Added

- Scheduled export of one TimeTree calendar, from every 0.5 to 24 hours (default: 3 hours).
- Private ICS feed under a secret token URL, with token replacement.
- Web dashboard with sync status, upcoming events including recurrences, and a **Sync now** button.
- Settings menu for sign-in, calendar selection, sync interval, feed token and tunnel.
- Optional built-in Cloudflare tunnel with the exposure modes *Nothing*, *ICS file only* and *Dashboard (read-only) + ICS*.
- `/ping` health check for uptime monitors, available in every exposure mode.
- Encrypted settings, with the key stored on a separate volume.
- Host port and time zone configuration through `.env`.
- ICS repair for strict calendar clients: `VALUE=DATE` on all-day events, complete `VTIMEZONE` data,
  and the `CALSCALE`, `METHOD` and `X-WR-CALNAME` properties.
- Logging of ICS requests without the token.

### Known limitations

- Depends on an unofficial TimeTree web API, which can break or rate-limit without notice.
- Exports one calendar at a time, and only for accounts with an e-mail address and password.
- Subscribed calendars show all events in one color.
- Behavior after a Cloudflare tunnel token is revoked has not been tested.

[Unreleased]: https://github.com/storkakos/daytree/compare/v0.24.2...dev
[0.24.2]: https://github.com/storkakos/daytree/compare/v0.24.1...v0.24.2
[0.24.1]: https://github.com/storkakos/daytree/compare/v0.24.0...v0.24.1
[0.24.0]: https://github.com/storkakos/daytree/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/storkakos/daytree/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/storkakos/daytree/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/storkakos/daytree/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/storkakos/daytree/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/storkakos/daytree/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/storkakos/daytree/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/storkakos/daytree/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/storkakos/daytree/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/storkakos/daytree/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/storkakos/daytree/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/storkakos/daytree/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/storkakos/daytree/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/storkakos/daytree/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/storkakos/daytree/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/storkakos/daytree/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/storkakos/daytree/releases/tag/v0.9.0

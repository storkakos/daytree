# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Kiosk homepage for TV, monitor and tablet, in landscape and portrait, with clock, today's agenda, the next
  7 days, tasks and reminders 30 minutes before an event.
- First-time setup popup that requires the TimeTree sign-in and calendar selection.
- Encrypted task storage, with a setting to show or hide tasks on the homepage.
- German user interface, with automatic language detection and a language setting.
- Settings for the time zone, 12 or 24 hour clock and date style.
- Favicon and home screen icon.
- Photo or color backgrounds with blur and brightness settings. Photos come from Lorem Picsum.
- Colored bullets on the homepage that match the TimeTree event colors.
- Greeting with your name, and current weather from Open-Meteo.
- Analog clock, seconds, clock size, font family, font size and font weight settings.
- Settings panel on the homepage that saves changes immediately and shows them live.

### Changed

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

[Unreleased]: https://github.com/storkakos/timetree-ics/compare/v0.9.0...dev
[0.9.0]: https://github.com/storkakos/timetree-ics/releases/tag/v0.9.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/storkakos/daytree/compare/v0.15.1...dev
[0.15.1]: https://github.com/storkakos/daytree/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/storkakos/daytree/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/storkakos/daytree/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/storkakos/daytree/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/storkakos/daytree/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/storkakos/daytree/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/storkakos/daytree/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/storkakos/daytree/releases/tag/v0.9.0

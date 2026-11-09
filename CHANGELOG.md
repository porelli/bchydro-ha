# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-11-09

### Fixed
- Energy dashboard figures now follow BC Hydro's corrections. BC Hydro revises
  data it already published, sometimes weeks later: it relabelled the hours
  around its November 2026 clock change (BC itself stayed on UTC-7), and
  revised whole days of readings by up to 4.8 kWh. The integration never
  updated an hour once recorded, so those days stayed wrong. Every import now
  rebuilds the last 30 days from BC Hydro's current data, writing only from the
  first hour that differs. On one account this brought every day from Oct 20
  to Nov 8 in line with BC Hydro's own daily totals (13 of 20 before); the
  first import after updating repairs existing history the same way.
- Daily figures (yesterday's consumption and cost, the daily breakdown) follow
  BC's clock again. BC Hydro labels its data -08:00 while BC is at UTC-7, so
  since November 1 days were split at 1 AM and "yesterday" was an hour short.
- The *Rate plan* sensor showed "unknown" on every account: it read a field BC
  Hydro does not send. It now shows the plan BC Hydro reports (for example
  "Residential tiered rate"), with the plan code in its attributes.

## [0.2.0] - 2026-11-09

### Added
- A daily update schedule, now the default. BC Hydro publishes usage once a
  day, usually in the morning, so instead of polling around the clock the
  integration checks from a configurable *daily check time* (8:00 by default),
  checks again while that day's data is missing (every hour by default, from
  every 15 minutes to every 3 hours, or not until the next day), then waits for
  the next morning.
  Most days that is one login instead of about twelve, which also makes BC
  Hydro's bot protection less likely to turn the integration away.
- A *Data arrival time* diagnostic sensor showing when data usually arrives for
  your account (typical, earliest and latest time over the last 30 days). It is
  most precise with checks at most two hours apart, as on the daily schedule's
  retries or hourly polling. When the data is always already there at the check,
  it shows when it was ready by (for example "≤08:00"), a sign the check time
  can move earlier. The same summary appears in the options form.

### Changed
- Existing installations move to the daily schedule unless they had set an
  update interval other than the 60-minute default; fixed-interval polling stays
  available under the integration's options.
- The 20-minute keepalive only runs with fixed-interval polling faster than two
  hours, where it can still spare a login; on the daily schedule every check
  signs in afresh anyway.

## [0.1.1] - 2026-11-08

### Fixed
- The integration no longer goes offline about two hours after starting until
  Home Assistant is restarted. BC Hydro ends every login after roughly two
  hours, even an active one; signing in again reused the expired session's
  cookies, which BC Hydro answers with "invalid username or password" and then
  with its login page on every later attempt. Each login now starts from a
  clean session, so the integration recovers on its own at the next refresh.

### Added
- A repair issue under Settings > System > Repairs when BC Hydro data has not
  updated for three hours, showing how long and the last error. It clears
  itself on the next successful update. Previously an outage only showed as
  unavailable sensors and log warnings, which is easy to miss.

### Changed
- Corrected the 0.1.0 notes: the keepalive cannot hold a login beyond BC
  Hydro's two-hour limit, so the integration signs in roughly every two hours
  (about 12 logins a day, down from about 72 before 0.1.0), not once per
  restart.

## [0.1.0] - 2026-11-04

### Fixed
- Stop the repeated re-authentication prompts caused by BC Hydro's invisible
  reCAPTCHA. The integration previously logged in on every request (~72 logins
  a day), which tripped BC Hydro's bot scoring; the resulting bounce back to the
  reCAPTCHA page was then misread as a wrong password and, after three bounces,
  forced a re-authentication.
- A genuinely wrong password (now returned as HTTP 401) is correctly surfaced
  for re-authentication instead of being retried indefinitely as a network
  error.

### Added
- Reuse of a single authenticated session across requests, with a 20-minute
  keepalive that holds one login across the hourly refresh (roughly one login
  per restart instead of ~72 a day).
- A distinct `BCHydroLoginBlockedError` for transient bot-protection blocks,
  which are retried and never trigger re-authentication.
- `tests/test_session_reuse.py` covering session reuse, login classification,
  error-page handling, and the keepalive.

### Changed
- Reworded the "temporarily blocked" message in all 16 languages so it no longer
  blames the user's password for a temporary bot block.
- Relaxed the `beautifulsoup4` requirement from `==4.12.3` to `>=4.12.0` so it
  follows the version Home Assistant bundles. A strict pin is rejected by
  hassfest and can conflict with other integrations.

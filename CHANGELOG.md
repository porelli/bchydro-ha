# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

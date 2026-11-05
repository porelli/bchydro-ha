# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

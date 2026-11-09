# BC Hydro Home Assistant Integration

[![Tests](https://github.com/porelli/bchydro-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/porelli/bchydro-ha/actions/workflows/tests.yml)
[![Validate](https://github.com/porelli/bchydro-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/porelli/bchydro-ha/actions/workflows/validate.yml)
[![codecov](https://codecov.io/gh/porelli/bchydro-ha/graph/badge.svg)](https://codecov.io/gh/porelli/bchydro-ha)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/porelli/bchydro-ha)](https://github.com/porelli/bchydro-ha/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8%2B-blue.svg)](https://www.home-assistant.io/)

This custom integration allows you to monitor your BC Hydro electricity consumption and costs in Home Assistant.

## Translations

| Language | Status |
|----------|--------|
| English | ![100%](https://img.shields.io/badge/100%25-green) |
| Deutsch | ![100%](https://img.shields.io/badge/100%25-green) |
| Español | ![100%](https://img.shields.io/badge/100%25-green) |
| فارسی (Farsi) | ![100%](https://img.shields.io/badge/100%25-green) |
| Français | ![100%](https://img.shields.io/badge/100%25-green) |
| Italiano | ![100%](https://img.shields.io/badge/100%25-green) |
| 한국어 (Korean) | ![100%](https://img.shields.io/badge/100%25-green) |
| Nederlands | ![100%](https://img.shields.io/badge/100%25-green) |
| Polski | ![100%](https://img.shields.io/badge/100%25-green) |
| Português | ![100%](https://img.shields.io/badge/100%25-green) |
| ਪੰਜਾਬੀ (Punjabi) | ![100%](https://img.shields.io/badge/100%25-green) |
| Русский (Russian) | ![100%](https://img.shields.io/badge/100%25-green) |
| Tagalog | ![100%](https://img.shields.io/badge/100%25-green) |
| Tiếng Việt (Vietnamese) | ![100%](https://img.shields.io/badge/100%25-green) |
| 简体中文 | ![100%](https://img.shields.io/badge/100%25-green) |
| 繁體中文 | ![100%](https://img.shields.io/badge/100%25-green) |

Want to help translate? Submit a PR with a new translation file in `custom_components/bchydro/translations/`.

## Features

- **Consumption tracking**: Monitor your daily and period-to-date electricity consumption
- **Backfill consumption data in HA**: Backfill your grid power consumption up to 90 days in the past
- **Cost monitoring**: Track your electricity costs
- **Billing information**: View billing period details, rate plans, and estimates
- **Daily breakdown**: See daily consumption and cost data as sensor attributes
- **Yesterday's data**: Quick access to yesterday's consumption and cost

## Sensors

The integration provides the following sensors:

- **Consumption to Date**: Total consumption in the current billing period (kWh)
- **Cost to Date**: Total cost in the current billing period ($)
- **Estimated Consumption**: Estimated total consumption for the billing period (kWh)
- **Estimated Cost**: Estimated total cost for the billing period ($)
- **Yesterday Consumption**: Yesterday's electricity consumption (kWh)
- **Yesterday Cost**: Yesterday's electricity cost ($)
- **Rate Plan**: Your current rate plan name
- **Billing Period**: Current billing period start date
- **Days in Billing Period**: Total days in the current billing period
- **Billing Period Percentage**: Percentage of billing period elapsed

## Requirements

- **Home Assistant 2024.8.0 or newer**
- A BC Hydro account with an active smart meter

On Home Assistant 2026.3 or newer the BC Hydro logo is served from this repository
(`custom_components/bchydro/brand/`). Older versions show Home Assistant's generic
placeholder instead, which is cosmetic only.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL and select "Integration" as the category
6. Click "Install"
7. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/bchydro` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "BC Hydro" and select it
4. Enter your BC Hydro username (email) and password
5. Click Submit

That's it! The integration will:
- Automatically authenticate with BC Hydro
- Follow all SSO redirects
- Extract and store necessary cookies
- Re-authenticate automatically when sessions expire

### Multiple accounts

If your BC Hydro login has access to more than one account - a previous address,
a second service on the property, or access shared with you - the setup asks
which account to follow and what to call it (for example "Home" or "Cabin").

You can follow **several accounts at once**: add the integration again with the
same login and pick the next account. Accounts that are already set up are left
out of the list. Each account gets:

- its own device, named after what you called it, so the entities are easy to
  tell apart (`sensor.bc_hydro_cabin_cost_to_date`)
- its own Energy dashboard statistics (*BC Hydro Energy Consumption (Cabin)*)

The first account you configure keeps the original entity and statistic names, so
upgrading from an earlier version changes nothing about your existing history or
Energy dashboard configuration.

## Data Update Frequency

BC Hydro publishes usage data once a day, usually in the morning (Pacific time).
By default the integration follows that **daily schedule**: from the *daily check
time* (8:00 by default) it checks for that day's data, retries hourly until it
shows up, then waits until the next morning. Most days that is a single check
and a single login.

You can change this under **Settings → Devices & services → BC Hydro → Configure**.
The first screen asks *when to check*; the second shows only the settings for
that choice:

- **Once a day, when BC Hydro publishes** (default) - set the time to start
  checking, and how often to check again while that day's data is missing
  (every 15 minutes to 3 hours, default every hour, or not until tomorrow).
  The screen shows when data has actually been arriving for your
  account over the last 30 days (typical, earliest and latest), so you can set
  the time just after it. The same figures are on the *Data arrival time*
  diagnostic sensor.
- **At a fixed interval, around the clock** - set how often to check (30-1440
  minutes).

Existing installations move to the daily schedule unless they had set an update
interval other than the 60-minute default.

## Known Limitations

### Data Availability Delay

BC Hydro's API has a **1.5-2 day delay** before consumption data becomes available. Data is initially marked as "INVALID" by BC Hydro until it has been validated/processed. This means:

- Yesterday's data may not appear until tomorrow or the day after
- The BC Hydro website may show provisional data that the API doesn't yet expose
- This is a BC Hydro limitation, not an issue with the integration

### Historical Data

- The integration can backfill up to 90 days of historical consumption data
- Statistics are stored in Home Assistant's long-term statistics database
- Data is available in the Energy Dashboard under "Grid consumption"

### Smart Meter Required

This integration requires a BC Hydro account with an active smart meter. Accounts without smart meters will not have hourly consumption data available.

## Troubleshooting

### Authentication Failures

If you receive authentication errors:

1. **Verify your credentials** - Make sure your username and password are correct
2. **Check the logs** - Enable debug logging (see below) to see detailed error messages

A failed login caused by BC Hydro itself (a network blip, a maintenance window,
its bot protection turning automated logins away) is retried on the next check
instead of breaking the integration. Reauthentication is only requested when BC
Hydro rejects the password several times in a row. If data stops arriving for
three hours or more, a notice appears under **Settings → System → Repairs** with
the last error; it clears itself once an update succeeds.

### Reauthentication Required

If you see a "Reauthentication Required" message:

1. Click the notification
2. Enter your current BC Hydro password
3. Click Submit

Your session will be automatically renewed.

### No Data Showing

- Check that your BC Hydro account has an active smart meter
- Verify that you can see consumption data when logging into BC Hydro's website
- Check the Home Assistant logs for any error messages

## Support

If you encounter issues:

1. Check the Home Assistant logs for error messages
2. Enable debug logging by adding this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.bchydro: debug
```

3. Open an issue on GitHub with the relevant log entries

## Privacy & Security

- Your BC Hydro credentials are stored securely in Home Assistant's credential storage
- Authentication cookies are stored encrypted in your Home Assistant configuration
- The integration only communicates with BC Hydro's official API endpoints
- No data is sent to any third parties

## Credits

Developed for the Home Assistant community.

## License

MIT License - see LICENSE file for details

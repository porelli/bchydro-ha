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

## Data Update Frequency

The integration updates data every 60 minutes by default. BC Hydro typically updates consumption data once per day.

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

The integration automatically re-authenticates when sessions expire, so you shouldn't need to manually reconfigure unless your password changes.

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

"""Constants for the BC Hydro integration."""

DOMAIN = "bchydro"

# API endpoints
BASE_URL = "https://app.bchydro.com"
LOGIN_PAGE_URL = f"{BASE_URL}/BCHCustomerPortal/web/login.html"
LOGIN_URL = f"{BASE_URL}/sso/UI/Login"
LOGIN_GOTO_URL = f"{BASE_URL}:443/BCHCustomerPortal/web/login.html"
ACCOUNT_PROFILE_URL = f"{BASE_URL}/evportlet/web/account-profile-data.html"
# Lists every account the login has access to, and switches the active one.
# A login with several accounts has no account selected after signing in, so the
# data endpoints answer with HTML instead of JSON until one is chosen.
ACCOUNT_LIST_URL = f"{BASE_URL}/BCHCustomerPortal/web/profileContractAccounts.html"
ACCOUNT_SELECT_URL = f"{BASE_URL}/BCHCustomerPortal/web/accountsOverview.html"
CONSUMPTION_DATA_URL = f"{BASE_URL}/evportlet/web/consumption-data.html"
GLOBAL_DATA_URL = f"{BASE_URL}/evportlet/web/global-data.html"

# Configuration
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Update schedule. "daily" checks from a time of day until that day's data
# arrives; "interval" polls every update_interval minutes.
CONF_UPDATE_MODE = "update_mode"
CONF_DAILY_CHECK_TIME = "daily_check_time"
# Minutes between checks while the day's data is missing; 0 = no retries
CONF_DAILY_RETRY = "daily_retry_minutes"
CONF_UPDATE_INTERVAL = "update_interval"
UPDATE_MODE_DAILY = "daily"
UPDATE_MODE_INTERVAL = "interval"
DEFAULT_DAILY_CHECK_TIME = "08:00:00"

# Update interval
UPDATE_INTERVAL_MINUTES = 60

# BC Hydro ends a login after under an hour without requests, and after about two
# hours regardless. A cheap request this often stretches each login to that limit,
# so the hourly refresh needs a fresh password login (each of which counts against
# BC Hydro's bot scoring) only about every two hours instead of every hour.
KEEPALIVE_INTERVAL_MINUTES = 20
# Keepalives only help when refreshes are closer together than a login lasts
KEEPALIVE_MAX_UPDATE_INTERVAL_MINUTES = 120

# Limit concurrent updates to avoid overwhelming the API
PARALLEL_UPDATES = 1

# Sensor types
SENSOR_CONSUMPTION_TO_DATE = "consumption_to_date"
SENSOR_COST_TO_DATE = "cost_to_date"
SENSOR_ESTIMATED_CONSUMPTION = "estimated_consumption"
SENSOR_ESTIMATED_COST = "estimated_cost"
SENSOR_YESTERDAY_CONSUMPTION = "yesterday_consumption"
SENSOR_YESTERDAY_COST = "yesterday_cost"
SENSOR_RATE_PLAN = "rate_plan"
SENSOR_BILLING_PERIOD_START = "billing_period_start"
SENSOR_DAYS_IN_BILLING_PERIOD = "days_in_billing_period"
SENSOR_BILLING_PERCENTAGE = "billing_percentage"
SENSOR_DATA_ARRIVAL_TIME = "data_arrival_time"

# Attributes
ATTR_ACCOUNT_ID = "account_id"
ATTR_ACCOUNT_NUMBER = "account_number"
ATTR_PROFILE_ID = "profile_id"
ATTR_RATE_CATEGORY = "rate_category"
ATTR_RATE_GROUP = "rate_group"
ATTR_ACCOUNT_TYPE = "account_type"
ATTR_BILLING_START = "billing_start"
ATTR_BILLING_END = "billing_end"
ATTR_DAYS_IN_BILLING_PERIOD = "days_in_billing_period"
ATTR_LAST_UPDATE = "last_update"
ATTR_DAILY_CONSUMPTION = "daily_consumption"

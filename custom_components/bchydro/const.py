"""Constants for the BC Hydro integration."""

DOMAIN = "bchydro"

# API endpoints
BASE_URL = "https://app.bchydro.com"
LOGIN_PAGE_URL = f"{BASE_URL}/BCHCustomerPortal/web/login.html"
LOGIN_URL = f"{BASE_URL}/sso/UI/Login"
LOGIN_GOTO_URL = f"{BASE_URL}:443/BCHCustomerPortal/web/login.html"
ACCOUNT_PROFILE_URL = f"{BASE_URL}/evportlet/web/account-profile-data.html"
CONSUMPTION_DATA_URL = f"{BASE_URL}/evportlet/web/consumption-data.html"
GLOBAL_DATA_URL = f"{BASE_URL}/evportlet/web/global-data.html"

# Configuration
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Update interval
UPDATE_INTERVAL_MINUTES = 60

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

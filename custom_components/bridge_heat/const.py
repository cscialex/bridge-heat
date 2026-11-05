"""Shared configuration; credentials must never be shipped in source code."""

DOMAIN = "bridge_heat"
PLATFORMS = ["sensor"]
UPLOAD_INTERVAL = 86400
SAMPLE_INTERVAL = 86400
POLL_INTERVAL = 60
API_BASE_URL = "https://bridgeheat.mssm.edu/api/v1"
URL = f"{API_BASE_URL}/env"
PENDING_UPLOADS_URL = f"{API_BASE_URL}/pending-uploads"
ACKNOWLEDGE_URL = f"{API_BASE_URL}/acknowledge-upload"

# Keep the existing header and endpoints, but obtain the key from each HA entry.
API_KEY = "api_key"
SHARE_LOCATION = "share_location"
TEMP = "Temperature"
HUMIDITY = "Humidity"
PRESSURE = "Pressure"
AQ = "Air Quality"
PERMS_TITLE = "Permissions"
PAGE_SIZE = 500
MAX_PAGES = 20
MAX_ATTRIBUTE_CHARS = 8192
RECORDER_SETTLE_SECONDS = 300
STATUS_SIGNAL = f"{DOMAIN}_status"

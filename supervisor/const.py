# maintainer: guoping.liu@3reality.com

"""Constants."""

VERSION = "v1.0.6"

VERSION_URL = "https://raw.githubusercontent.com/thirdreality/LinuxBox-Installer/refs/heads/main/version.json"
DOWNLOAD_BASE_URL = "https://github.com/thirdreality/LinuxBox-Installer/releases/download"

# GPIO chip and line numbers for LED control
LINUXBOX_LED_R_CHIP=1
LINUXBOX_LED_R_LINE=3

LINUXBOX_LED_G_CHIP=0
LINUXBOX_LED_G_LINE=4

LINUXBOX_LED_B_CHIP=0
LINUXBOX_LED_B_LINE=5

# GPIO chip and line numbers for button control
LINUXBOX_BUTTON_CHIP=0
LINUXBOX_BUTTON_LINE=26

DEVICE_MODEL_NAME="LinuxBox Dev Edition"
DEVICE_BUILD_NUMBER="20250626-0000"

# BLE GATT Server Configuration
# 'auto' - Automatically detect whether there is an external service
# 'external' - Force use of external C implemented btgatt-config-server
# 'internal' - Force use of internal Python implemented SupervisorGattServer
BLE_GATT_SERVER_MODE = "auto"

# External GATT server configuration
EXTERNAL_GATT_SERVICE_NAME = "btgatt-config.service"
EXTERNAL_GATT_BINARY_PATH = "/usr/local/bin/btgatt-config-server"

# GATT server timeout configuration (minutes)
GATT_SERVER_TIMEOUT_MINUTES = 3

# Backup storage configuration
# 'internal' - Use internal storage path
# 'external' - Use external storage path (default)
BACKUP_STORAGE_MODE = "external"

# Backup storage paths
BACKUP_INTERNAL_PATH = "/lib/thirdreality/backup"
BACKUP_EXTERNAL_PATH = "/mnt/R3Backup"

# HomeAssistant Token Mode Configuration
# 0 - auto: Prefer long_lived_token, fallback to oauth2
# 1 - longlived: Use long-lived access token only
# 2 - oauth2: Use OAuth2 authentication with username/password
TOKEN_MODE_AUTO = 0
TOKEN_MODE_LONGLIVED = 1
TOKEN_MODE_OAUTH2 = 2

# Default token mode (longlived)
TOKEN_MODE = TOKEN_MODE_LONGLIVED


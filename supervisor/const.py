# maintainer: guoping.liu@3reality.com

"""Constants."""

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
DEVICE_BUILD_NUMBER="20250619-0000"

# BLE GATT Server Configuration
# 'auto' - 自动检测是否存在外部服务
# 'external' - 强制使用外部C实现的btgatt-config-server
# 'internal' - 强制使用内部Python实现的SupervisorGattServer
BLE_GATT_SERVER_MODE = "auto"

# External GATT server configuration
EXTERNAL_GATT_SERVICE_NAME = "btgatt-config.service"
EXTERNAL_GATT_BINARY_PATH = "/usr/local/bin/btgatt-config-server"

# GATT server timeout configuration (minutes)
GATT_SERVER_TIMEOUT_MINUTES = 5


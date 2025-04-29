# Supervisor Python Package

This directory contains the main supervisor logic and all submodules.

- ble/: BLE GATT server and advertisement logic
- utils/: Utility functions (WiFi, system, etc.)
- ota/: OTA update logic
- cli.py: Command-line client
- http_server.py: HTTP server for supervisor
- hardware.py: GPIO and hardware abstraction
- network.py: Network monitoring logic
- const.py: Project-wide constants
- supervisor.py: Main entry point

Each subdirectory contains an __init__.py to ensure package importability.

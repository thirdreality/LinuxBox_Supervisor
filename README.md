# Supervisor

A Python-based service management system for monitoring and maintaining different service modes.

## Features

- Automatically detects current work mode based on running services:
  - HomeAssistant mode
  - Zigbee2MQTT mode
  - HomeKit mode
  - Idle mode
- In idle mode, automatically installs HomeAssistant components
- In HomeAssistant mode, checks for updates and notifies users
- Extensible framework for future service management

## Installation

1. Install the required dependencies:
```
pip install -r requirements.txt
```

2. Install the systemd service:
```
sudo cp supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable supervisor.service
sudo systemctl start supervisor.service
```

3. Check the status of the service:
```
sudo systemctl status supervisor.service
```

## Configuration

The service is designed to work out of the box with default settings. Logs are stored in `/var/log/supervisor.log`.

## Development

To contribute to this project, please follow these steps:

1. Clone the repository
2. Install development dependencies
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

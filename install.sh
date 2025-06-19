#!/bin/bash
# Installation script for Supervisor

set -e

echo "Installing Supervisor..."

# Create necessary directories
mkdir -p /usr/local/bin
mkdir -p /var/log

# Install dependencies
# pip3 install -r requirements.txt

# Copy all Supervisor source files (including submodules)
rm -rf /usr/local/lib/python3.11/dist-packages/supervisor > /dev/null 2>&1 || true
cp -r supervisor /usr/local/lib/python3.11/dist-packages/
cp bin/supervisor /usr/local/bin/
chmod +x /usr/local/bin/supervisor

cp bin/btgatt-config-server /usr/local/bin/
chmod +x /usr/local/bin/btgatt-config-server

# Install the service
cp supervisor.service /etc/systemd/system/
cp btgatt-config.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable supervisor.service
systemctl start supervisor.service

systemctl disable btgatt-config.service

echo "Supervisor has been installed successfully!"
echo "Check the service status with: systemctl status supervisor.service"
echo "View logs with: tail -f /var/log/supervisor.log"

#!/bin/bash
# Installation script for LinuxBox Supervisor
# This script installs the Supervisor service and its dependencies

set -e  # Exit on any error

# Color codes for better output formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function with timestamp
log_info() {
    echo -e "${GREEN}[INFO $(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}[WARN $(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"
}

log_error() {
    echo -e "${RED}[ERROR $(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"
}

log_step() {
    echo -e "${BLUE}[STEP $(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"
}

# Main installation process starts here
log_info "Starting LinuxBox Supervisor installation process..."
log_info "Installation script version: $(date '+%Y%m%d')"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root (use sudo)"
   exit 1
fi

log_info "Running as root user - permission check passed"

# Step 1: Create necessary directories
log_step "Step 1: Creating necessary directories..."
log_info "Creating /usr/local/bin directory..."
mkdir -p /usr/local/bin
log_info "Directory /usr/local/bin created/verified"

log_info "Creating /var/log directory..."
mkdir -p /var/log
log_info "Directory /var/log created/verified"

# Step 2: Install dependencies (commented out for now)
log_step "Step 2: Installing dependencies..."
log_warn "Python dependencies installation is currently disabled"
log_info "To install dependencies manually, run: pip3 install -r requirements.txt"
# pip3 install -r requirements.txt

# Step 3: Remove old installation if exists
log_step "Step 3: Cleaning up previous installation..."
log_info "Checking if supervisor.service is running..."
if systemctl is-active --quiet supervisor.service; then
    log_info "supervisor.service is running, stopping it now..."
    systemctl stop supervisor.service
    log_info "supervisor.service stopped"
else
    log_info "supervisor.service is not running"
fi
SUPERVISOR_LIB_PATH="/usr/local/lib/python3.11/dist-packages/supervisor"
if [ -d "$SUPERVISOR_LIB_PATH" ]; then
    log_info "Removing existing supervisor installation at $SUPERVISOR_LIB_PATH"
    rm -rf "$SUPERVISOR_LIB_PATH"
    log_info "Previous installation removed successfully"
else
    log_info "No previous installation found"
fi

# Step 4: Install Supervisor Python modules
log_step "Step 4: Installing Supervisor Python modules..."
log_info "Copying supervisor module to $SUPERVISOR_LIB_PATH"
cp -r supervisor /usr/local/lib/python3.11/dist-packages/
log_info "Supervisor Python modules installed successfully"

# Step 5: Install main supervisor binary
log_step "Step 5: Installing supervisor binary..."
SUPERVISOR_BIN="/usr/local/bin/supervisor"
log_info "Copying supervisor binary to $SUPERVISOR_BIN"
cp bin/supervisor "$SUPERVISOR_BIN"
chmod +x "$SUPERVISOR_BIN"
log_info "Supervisor binary installed and made executable"

# Step 6: Install BLE GATT config server
log_step "Step 6: Installing BLE GATT configuration server..."
BTGATT_BIN="/usr/local/bin/btgatt-config-server"
log_info "Copying btgatt-config-server binary to $BTGATT_BIN"
cp bin/btgatt-config-server "$BTGATT_BIN"
chmod +x "$BTGATT_BIN"
log_info "BLE GATT config server installed and made executable"

# Step 7: Install systemd services
log_step "Step 7: Installing systemd service files..."

# Install main supervisor service
SUPERVISOR_SERVICE="/etc/systemd/system/supervisor.service"
log_info "Installing supervisor.service to $SUPERVISOR_SERVICE"
cp supervisor.service "$SUPERVISOR_SERVICE"
log_info "Supervisor service file installed"

# Install BLE GATT config service
BTGATT_SERVICE="/etc/systemd/system/btgatt-config.service"
log_info "Installing btgatt-config.service to $BTGATT_SERVICE"
cp btgatt-config.service "$BTGATT_SERVICE"
log_info "BLE GATT config service file installed"

# Step 8: Configure systemd services
log_step "Step 8: Configuring systemd services..."

log_info "Reloading systemd daemon to recognize new services..."
systemctl daemon-reload
log_info "Systemd daemon reloaded successfully"

log_info "Enabling supervisor.service to start at boot..."
systemctl enable supervisor.service
log_info "Supervisor service enabled for auto-start"

log_info "Starting supervisor.service..."
if systemctl start supervisor.service; then
    log_info "Supervisor service started successfully"
else
    log_error "Failed to start supervisor service"
    log_info "Check service status with: systemctl status supervisor.service"
    exit 1
fi

log_info "Disabling btgatt-config.service (will be managed by supervisor)..."
systemctl disable btgatt-config.service 2>/dev/null || true
log_info "BLE GATT config service disabled (on-demand activation)"

# Step 9: Verify installation
log_step "Step 9: Verifying installation..."

log_info "Checking supervisor service status..."
if systemctl is-active --quiet supervisor.service; then
    log_info "✓ Supervisor service is running"
else
    log_warn "⚠ Supervisor service is not running"
fi

log_info "Checking installed files..."
if [ -f "$SUPERVISOR_BIN" ]; then
    log_info "✓ Supervisor binary found at $SUPERVISOR_BIN"
else
    log_error "✗ Supervisor binary not found"
fi

if [ -f "$BTGATT_BIN" ]; then
    log_info "✓ BLE GATT config server found at $BTGATT_BIN"
else
    log_error "✗ BLE GATT config server not found"
fi

if [ -d "$SUPERVISOR_LIB_PATH" ]; then
    log_info "✓ Supervisor Python modules found at $SUPERVISOR_LIB_PATH"
else
    log_error "✗ Supervisor Python modules not found"
fi

# Installation completed
log_info "=================================================="
log_info "LinuxBox Supervisor installation completed successfully!"
log_info "=================================================="
log_info ""
log_info "Useful commands:"
log_info "  • Check service status:    systemctl status supervisor.service"
log_info "  • View real-time logs:     journalctl -u supervisor.service -f"
log_info "  • Restart service:         systemctl restart supervisor.service"
log_info "  • Stop service:            systemctl stop supervisor.service"
log_info "  • Check BLE GATT status:   systemctl status btgatt-config.service"
log_info ""
log_info "Log files location:"
log_info "  • System logs:             journalctl -u supervisor.service"
log_info "  • Application logs:        /var/log/supervisor.log (if configured)"
log_info ""
log_info "Installation completed at: $(date)"

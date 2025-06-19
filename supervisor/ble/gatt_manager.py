# maintainer: guoping.liu@3reality.com
"""
BLE GATT Server Manager
Unified management of internal and external GATT servers
"""

import os
import subprocess
import threading
import time
import logging
from pathlib import Path
from ..const import (
    BLE_GATT_SERVER_MODE, 
    EXTERNAL_GATT_SERVICE_NAME, 
    EXTERNAL_GATT_BINARY_PATH,
    GATT_SERVER_TIMEOUT_MINUTES
)
from .gatt_server import SupervisorGattServer


class GattServerManager:
    """Unified GATT server manager"""
    
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.mode = self._determine_mode()
        self.gatt_server = None
        self.external_service_process = None
        self.timeout_timer = None
        self.is_provisioning = False
        
        self.logger.info(f"[GATT Manager] Initialized with mode: {self.mode}")

    def _determine_mode(self):
        """Determine which GATT server mode to use"""
        if BLE_GATT_SERVER_MODE == "external":
            return "external"
        elif BLE_GATT_SERVER_MODE == "internal":
            return "internal"
        else:  # auto mode
            # Check if external service exists
            if self._check_external_service_available():
                return "external"
            else:
                return "internal"

    def _check_external_service_available(self):
        """Check if external GATT service is available"""
        try:
            # Check if binary file exists
            if not Path(EXTERNAL_GATT_BINARY_PATH).exists():
                self.logger.debug(f"External binary not found: {EXTERNAL_GATT_BINARY_PATH}")
                return False
                
            # Check if systemd service exists
            result = subprocess.run(
                ['/bin/systemctl', 'list-unit-files', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            if EXTERNAL_GATT_SERVICE_NAME not in result.stdout:
                self.logger.debug(f"External service not found: {EXTERNAL_GATT_SERVICE_NAME}")
                return False
                
            self.logger.info("External GATT service is available")
            return True
        except Exception as e:
            self.logger.warning(f"Error checking external service: {e}")
            return False

    def start_provisioning_mode(self):
        """Start provisioning mode"""
        if self.is_provisioning:
            self.logger.warning("Provisioning mode already active")
            return True
            
        self.is_provisioning = True
        self.logger.info(f"[GATT Manager] Starting provisioning mode ({self.mode})")
        
        try:
            if self.mode == "external":
                return self._start_external_service()
            else:
                return self._start_internal_service()
        except Exception as e:
            self.logger.error(f"Failed to start provisioning mode: {e}")
            self.is_provisioning = False
            return False

    def stop_provisioning_mode(self):
        """Stop provisioning mode"""
        if not self.is_provisioning:
            return True
            
        self.logger.info(f"[GATT Manager] Stopping provisioning mode ({self.mode})")
        
        # Stop timeout timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
            
        try:
            if self.mode == "external":
                self._stop_external_service()
            else:
                self._stop_internal_service()
        except Exception as e:
            self.logger.error(f"Error stopping provisioning mode: {e}")
        finally:
            self.is_provisioning = False
            
        return True

    def _start_external_service(self):
        """Start external GATT service"""
        try:
            # Need to restart bluetooth service before starting external service (tentative)
            self.logger.info("Restarting bluetooth service before starting external GATT service...")
            
            # Restart bluetooth service
            restart_result = subprocess.run(
                ['/bin/systemctl', 'restart', 'bluetooth.service'],
                capture_output=True, text=True, timeout=15
            )
            if restart_result.returncode != 0:
                self.logger.warning(f"Bluetooth restart returned non-zero: {restart_result.stderr}")
            else:
                self.logger.info("Bluetooth service restarted successfully")
            
            # Wait for 1 second
            time.sleep(1)
            
            # Start external GATT service
            self.logger.info(f"Starting external GATT service: {EXTERNAL_GATT_SERVICE_NAME}")
            result = subprocess.run(
                ['/bin/systemctl', 'start', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                self.logger.error(f"Failed to start external service: {result.stderr}")
                return False
                
            self.logger.info("External GATT service started successfully")
            self._start_timeout_timer()
            return True
        except Exception as e:
            self.logger.error(f"Error starting external service: {e}")
            return False

    def _stop_external_service(self):
        """Stop external GATT service"""
        try:
            subprocess.run(
                ['/bin/systemctl', 'stop', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=10
            )
            self.logger.info("External GATT service stopped")
        except Exception as e:
            self.logger.error(f"Error stopping external service: {e}")

    def _start_internal_service(self):
        """Start internal GATT service"""
        try:
            if not self.gatt_server:
                self.gatt_server = SupervisorGattServer(self.supervisor)
                
            self.gatt_server.start()
            self.logger.info("Internal GATT server started successfully")
            self._start_timeout_timer()
            return True
        except Exception as e:
            self.logger.error(f"Error starting internal GATT server: {e}")
            return False

    def _stop_internal_service(self):
        """Stop internal GATT service"""
        try:
            if self.gatt_server:
                self.gatt_server.stop()
                self.gatt_server = None
            self.logger.info("Internal GATT server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping internal GATT server: {e}")

    def _start_timeout_timer(self):
        """Start timeout timer"""
        if self.timeout_timer:
            self.timeout_timer.cancel()
            
        timeout_seconds = GATT_SERVER_TIMEOUT_MINUTES * 60
        self.timeout_timer = threading.Timer(timeout_seconds, self._on_timeout)
        self.timeout_timer.start()
        self.logger.info(f"Started {GATT_SERVER_TIMEOUT_MINUTES} minute timeout timer")

    def _on_timeout(self):
        """Timeout callback"""
        self.logger.info("GATT server timeout reached, stopping provisioning mode")
        self.stop_provisioning_mode()

    def on_wifi_connected(self):
        """Callback when WiFi connection is successful"""
        if self.is_provisioning:
            self.logger.info("WiFi connected, stopping provisioning mode")
            self.stop_provisioning_mode()

    def startAdv(self):
        """Start BLE advertisement"""
        if self.mode == "internal" and self.gatt_server:
            return self.gatt_server.startAdv()
        return False

    def stopAdv(self):
        """Stop BLE advertisement"""  
        if self.mode == "internal" and self.gatt_server:
            return self.gatt_server.stopAdv()
        return False

    def cleanup(self):
        """Clean up resources"""
        self.stop_provisioning_mode() 
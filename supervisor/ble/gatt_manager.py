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
            self.logger.info("Provisioning mode already active, skipping start request")
            return True
            
        self.is_provisioning = True
        self.logger.info(f"[GATT Manager] Starting provisioning mode ({self.mode})")
        
        try:
            if self.mode == "external":
                success = self._start_external_service()
            else:
                success = self._start_internal_service()
                
            # Fix: If startup fails, immediately reset state
            if not success:
                self.is_provisioning = False
                self.logger.error("Failed to start provisioning mode, resetting state")
                
            return success
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
                
            # Set LED to off state when stopping
            from ..hardware import LedState
            if hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
                self.logger.info("Set LED to off state after stopping provisioning")
                
        except Exception as e:
            self.logger.error(f"Error stopping provisioning mode: {e}")
        finally:
            self.is_provisioning = False
            
        return True

    def _start_external_service(self):
        """Start external GATT service"""
        try:
            # Set LED to provisioning mode early
            from ..hardware import LedState
            if hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                self.logger.info("Set LED to provisioning mode")
            
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
                # Set LED to off state on failure
                if hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
                return False
                
            self.logger.info("External GATT service started successfully")
            self._start_timeout_timer()
            return True
        except Exception as e:
            self.logger.error(f"Error starting external service: {e}")
            # Set LED to off state on exception
            try:
                from ..hardware import LedState
                if hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
            except:
                pass
            return False

    def _stop_external_service(self):
        """Stop external GATT service"""
        try:
            # Try normal stop first
            result = subprocess.run(
                ['/bin/systemctl', 'stop', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                self.logger.info("External GATT service stopped successfully")
                return
            else:
                self.logger.warning(f"Normal stop failed: {result.stderr}")
                
        except Exception as e:
            self.logger.error(f"Error during normal stop: {e}")
        
        # If normal stop fails, try force kill
        try:
            self.logger.info("Attempting force kill of external GATT service...")
            kill_result = subprocess.run(
                ['/bin/systemctl', 'kill', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            if kill_result.returncode == 0:
                self.logger.info("External GATT service force killed successfully")
            else:
                self.logger.error(f"Force kill also failed: {kill_result.stderr}")
                
        except Exception as e:
            self.logger.error(f"Error during force kill: {e}")
            
        # Final verification - check if service is actually stopped
        try:
            status_result = subprocess.run(
                ['/bin/systemctl', 'is-active', EXTERNAL_GATT_SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            if status_result.returncode != 0:  # Service is not active
                self.logger.info("External GATT service confirmed stopped")
            else:
                self.logger.error("External GATT service may still be running!")
        except Exception as e:
            self.logger.warning(f"Could not verify service status: {e}")

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
        self.logger.info("[GATT Manager] GATT server timeout reached, stopping provisioning mode")
        try:
            self.stop_provisioning_mode()
        except Exception as e:
            self.logger.error(f"Error stopping provisioning mode on timeout: {e}")
            # Force reset state even if stop fails
            self.is_provisioning = False
            # Force stop timeout timer
            if self.timeout_timer:
                try:
                    self.timeout_timer.cancel()
                except:
                    pass
                self.timeout_timer = None
            # Try to set LED to off state as fallback
            try:
                from ..hardware import LedState
                if hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
            except:
                pass

    def on_wifi_connected(self):
        """Callback when WiFi connection is successful"""
        if self.is_provisioning:
            self.logger.info("[GATT Manager] WiFi connected, stopping provisioning mode")
            try:
                self.stop_provisioning_mode()
            except Exception as e:
                self.logger.error(f"Error stopping provisioning mode on WiFi connect: {e}")
                # Force reset state even if stop fails
                self.is_provisioning = False
                # Try to set LED to off state as fallback
                try:
                    from ..hardware import LedState
                    if hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
                except:
                    pass

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
        self.logger.info("[GATT Manager] Cleaning up resources...")
        try:
            self.stop_provisioning_mode()
        except Exception as e:
            self.logger.error(f"Error during normal cleanup: {e}")
            
        # Force cleanup even if normal stop fails
        try:
            # Force reset state
            self.is_provisioning = False
            
            # Force stop timeout timer
            if self.timeout_timer:
                try:
                    self.timeout_timer.cancel()
                except:
                    pass
                self.timeout_timer = None
            
            # Force cleanup internal server
            if self.gatt_server:
                try:
                    self.gatt_server.stop()
                except:
                    pass
                self.gatt_server = None
            
            # Force stop external service
            if self.mode == "external":
                try:
                    subprocess.run(
                        ['/bin/systemctl', 'kill', EXTERNAL_GATT_SERVICE_NAME],
                        capture_output=True, text=True, timeout=5
                    )
                except:
                    pass
            
            # Force LED off
            try:
                from ..hardware import LedState
                if hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.SYS_EVENT_OFF)
            except:
                pass
                
            self.logger.info("[GATT Manager] Force cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during force cleanup: {e}") 
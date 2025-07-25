# maintainer: guoping.liu@3reality.com
"""
WiFi provisioning control tool
Used to start and stop WiFi provisioning mode
"""

import sys
import os
import logging
import signal
import time

# Add the parent directory to the path to import the supervisor module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supervisor.const import BLE_GATT_SERVER_MODE, EXTERNAL_GATT_SERVICE_NAME
from supervisor.ble.gatt_manager import GattServerManager


class WiFiProvisionController:
    """WiFi provisioning controller"""
    
    def __init__(self):
        self.logger = self._setup_logging()
        self.gatt_manager = None
        self.running = False
    
    def _setup_logging(self):
        """Set up logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger("WiFiProvisionController")
    
    def start_provision_mode(self):
        """Start provisioning mode"""
        try:
            # Create a simplified supervisor object for testing
            class MockSupervisor:
                def __init__(self):
                    self.wifi_status = type('obj', (object,), {'ip_address': '', 'ssid': ''})()
                    
                def update_wifi_info(self, ip, ssid):
                    self.wifi_status.ip_address = ip
                    self.wifi_status.ssid = ssid
                    print(f"WiFi info updated: {ip} / {ssid}")
            
            mock_supervisor = MockSupervisor()
            self.gatt_manager = GattServerManager(mock_supervisor)
            
            self.logger.info("Starting WiFi provisioning mode...")
            if self.gatt_manager.start_provisioning_mode():
                self.logger.info(f"WiFi provisioning mode started successfully (mode: {self.gatt_manager.mode})")
                self.running = True
                return True
            else:
                self.logger.error("Failed to start WiFi provisioning mode")
                return False
        except Exception as e:
            self.logger.error(f"Error starting provisioning mode: {e}")
            return False
    
    def stop_provision_mode(self):
        """Stop provisioning mode"""
        if self.gatt_manager:
            self.logger.info("Stopping WiFi provisioning mode...")
            self.gatt_manager.stop_provisioning_mode()
            self.running = False
            self.logger.info("WiFi provisioning mode stopped")
    
    def run_interactive(self):
        """Interactive mode"""
        self.logger.info("WiFi Provision Controller - Interactive Mode")
        self.logger.info("Commands: start, stop, status, quit")
        
        while True:
            try:
                cmd = input("> ").strip().lower()
                
                if cmd == "start":
                    if not self.running:
                        self.start_provision_mode()
                    else:
                        print("Provision mode already running")
                        
                elif cmd == "stop":
                    if self.running:
                        self.stop_provision_mode()
                    else:
                        print("Provision mode not running")
                        
                elif cmd == "status":
                    if self.gatt_manager:
                        print(f"Mode: {self.gatt_manager.mode}")
                        print(f"Running: {self.running}")
                        print(f"Provisioning: {self.gatt_manager.is_provisioning}")
                    else:
                        print("Not initialized")
                        
                elif cmd in ["quit", "exit", "q"]:
                    if self.running:
                        self.stop_provision_mode()
                    break
                    
                else:
                    print("Unknown command. Use: start, stop, status, quit")
                    
            except KeyboardInterrupt:
                print("\nInterrupted by user")
                if self.running:
                    self.stop_provision_mode()
                break
            except Exception as e:
                self.logger.error(f"Error: {e}")


def main():
    """Main function"""
    controller = WiFiProvisionController()
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "start":
            success = controller.start_provision_mode()
            if success:
                print("WiFi provisioning mode started. Press Ctrl+C to stop.")
                try:
                    # Keep running until user interrupts
                    while controller.running:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nStopping...")
                finally:
                    controller.stop_provision_mode()
            sys.exit(0 if success else 1)
            
        elif command == "stop":
            controller.stop_provision_mode()
            sys.exit(0)
            
        elif command == "status":
            # Check external service status
            import subprocess
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', EXTERNAL_GATT_SERVICE_NAME],
                    capture_output=True, text=True
                )
                service_status = result.stdout.strip()
                print(f"External service ({EXTERNAL_GATT_SERVICE_NAME}): {service_status}")
            except Exception as e:
                print(f"Error checking service status: {e}")
            
            print(f"BLE GATT Server Mode: {BLE_GATT_SERVER_MODE}")
            sys.exit(0)
            
        else:
            print(f"Unknown command: {command}")
            print("Usage: python wifi_provision_control.py [start|stop|status]")
            sys.exit(1)
    else:
        # Interactive mode
        controller.run_interactive()


if __name__ == "__main__":
    main() 
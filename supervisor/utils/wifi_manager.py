# maintainer: guoping.liu@3reality.com

import subprocess
import os
import time
import logging
import shutil
from supervisor.hardware import LedState
import threading
from . import util

class WifiStatus:
    """
    Class to store WiFi connection status information
    """
    def __init__(self):
        self.connected = False
        self.ssid = ""
        self.ip_address = ""
        self.mac_address = ""
        self.error_message = ""
        self.logger = logging.getLogger(self.__class__.__name__)


class WifiManager:
    """
    Manager class for handling WiFi operations on the system
    """
    WIFI_INTERFACE = "wlan0"
    PROVISION_TIMEOUT_SECONDS = 300  # 5 minutes

    def __init__(self, supervisor):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.supervisor = supervisor
        self.provisioning_active = False
        self.provisioning_timer = None
        # Ensure NetworkManager is checked/started early, similar to existing init() logic
        # This init() is different from the user-callable init() method below.
        if not self._is_networkmanager_running():
            self.logger.warning("NetworkManager is not running during WifiManager instantiation, attempting to start it.")
            self._start_networkmanager()
            time.sleep(1) # Give it a moment
            if not self._is_networkmanager_running():
                self.logger.error("Failed to start NetworkManager during WifiManager instantiation.")
    
    @staticmethod
    def execute_command(command):
        """
        Execute a system command and return the result and status code
        
        Args:
            command: The command string to execute
            
        Returns:
            tuple: (result string, status code) - status code 0 indicates success
        """
        try:
            # Check if the command exists before running it
            # Check if the command exists before running it
            cmd_to_check = command.split()[0]
            if not shutil.which(cmd_to_check):
                logging.error(f"Command '{cmd_to_check}' not found in PATH.")
                return f"Command '{cmd_to_check}' not found", 127
                
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip(), 0
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip()
            if "NetworkManager is not running" in error_msg:
                logging.error(f"Command '{command.split()[0]}' failed with exit code {e.returncode}")
                logging.error(f"Error: NetworkManager is not running.")
            else:
                logging.error(f"Command failed: {command}, Error: {error_msg}")
            return error_msg, e.returncode
    
    def init(self):
        """
        Initialize the WiFi manager
        
        Returns:
            int: 0 for success, non-zero for failure
        """
        logging.info("Initializing WiFi manager")
        
        # Check if NetworkManager is running
        if not self._is_networkmanager_running():
            logging.warning("NetworkManager is not running, attempting to start it")
            self._start_networkmanager()
            
            # Wait for NetworkManager to start (up to 10 seconds)
            for _ in range(10):
                if self._is_networkmanager_running():
                    logging.info("NetworkManager started successfully")
                    break
                time.sleep(1)
            else:
                logging.error("Failed to start NetworkManager")
                return 1
                
        return 0
        
    def _is_networkmanager_running(self):
        """
        Check if NetworkManager service is running
        
        Returns:
            bool: True if running, False otherwise
        """
        command = "systemctl is-active NetworkManager"
        result, status = self.execute_command(command)
        return status == 0 and result == "active"
        
    def _start_networkmanager(self):
        """
        Attempt to start the NetworkManager service
        
        Returns:
            bool: True if successful, False otherwise
        """
        command = "systemctl start NetworkManager"
        _, status = self.execute_command(command)
        return status == 0
    
    def cleanup(self):
        """
        Clean up WiFi manager resources
        """
        logging.info("Cleaning up WiFi manager")
        # Nothing specific to clean up in this implementation
        if self.provisioning_timer:
            self.provisioning_timer.cancel()
            self.provisioning_timer = None
        self.logger.info("Cleaned up WifiManager resources.")


    def start_wifi_provision(self):
        """Starts the Wi-Fi provisioning mode."""
        if self.provisioning_active:
            self.logger.info("Wi-Fi provisioning is already active.")
            return True

        self.logger.info("Starting Wi-Fi provisioning mode...")
        if self.supervisor and hasattr(self.supervisor, 'led'):
            self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
        else:
            self.logger.warning("Supervisor instance or LED controller not available for SYS_WIFI_CONFIG_PENDING.")
        
        self.provisioning_active = True

        ha_service_name = "home-assistant.service"
        if util.is_service_running(ha_service_name):
            self.logger.info(f"Service '{ha_service_name}' is running, stopping it.")
            if not util.start_service(ha_service_name, False):
                self.logger.warning(f"Failed to stop '{ha_service_name}'.")
        else:
            self.logger.info(f"Service '{ha_service_name}' is not running.")

        if self.supervisor:
            self.supervisor.startAdv()
        else:
            self.logger.warning("Supervisor instance not available, cannot start BLE advertising.")

        if self.provisioning_timer:
            self.provisioning_timer.cancel()
        self.provisioning_timer = threading.Timer(self.PROVISION_TIMEOUT_SECONDS, self._provision_timeout_callback)
        self.provisioning_timer.daemon = True
        self.provisioning_timer.start()

        self.logger.info(f"Wi-Fi provisioning mode started. Timeout in {self.PROVISION_TIMEOUT_SECONDS} seconds.")
        return True

    def _provision_timeout_callback(self):
        self.logger.warning("Wi-Fi provisioning timed out.")
        self.stop_wifi_provision(timed_out=True)
        # Potentially signal timeout to supervisor/caller if needed


    def stop_wifi_provision(self, timed_out=False, called_after_success=False):
        self.logger.warning("Wi-Fi provisioning stopping...")
        """Stops the Wi-Fi provisioning mode."""
        if not self.provisioning_active:
            self.logger.info("Wi-Fi provisioning is not active, nothing to stop.")
            return

        self.logger.info(f"Stopping Wi-Fi provisioning mode... (Timed out: {timed_out}, Called after success: {called_after_success})")

        if self.provisioning_timer:
            self.provisioning_timer.cancel()
            self.provisioning_timer = None
        
        self.provisioning_active = False

        if self.supervisor:
            ip_address = self.get_wlan0_ip()
            if ip_address:
                self.logger.info(f"WLAN0 has IP address {ip_address}. Stopping BLE advertising.")
                self.supervisor.stopAdv()
            else:
                self.logger.info("WLAN0 has no IP address. BLE advertising will continue.")
        else:
            self.logger.warning("Supervisor instance not available, cannot control BLE advertising.")

        ha_service_name = "home-assistant.service"
        # If home-assistant.service is enabled, start it.
        _, status = self.execute_command(f"systemctl is-enabled {ha_service_name}")
        if status == 0:
            self.logger.info(f"Service '{ha_service_name}' is enabled, starting it.")
            if not util.start_service(ha_service_name, True):
                self.logger.warning(f"Failed to start '{ha_service_name}' in background.")
        else:
            # Service is not enabled, so we don't start it.
            self.logger.info(f"Service '{ha_service_name}' is not enabled, will not be started.")

        if timed_out:
            if self.supervisor and hasattr(self.supervisor, 'led'):
                self.supervisor.led.set_led_state(LedState.SYS_EVENT_OFF)
            else:
                self.logger.warning("Supervisor instance or LED controller not available for USER_EVENT_OFF on timeout.")
        elif not called_after_success: # Explicit stop, not after success and not a timeout
            if self.supervisor and hasattr(self.supervisor, 'led'):
                 self.supervisor.led.set_led_state(LedState.SYS_EVENT_OFF)
            else:
                self.logger.warning("Supervisor instance or LED controller not available for USER_EVENT_OFF.")
        # If called_after_success is True, LED was already set to SYS_WIFI_CONFIG_SUCCESS by configure(), so do nothing to LED here.
        
        self.logger.info(".")

    def get_wifi_provision_status(self):
        """Returns the current Wi-Fi provisioning status."""
        return self.provisioning_active
    
    def configure(self, ssid, password):
        """
        Configure WiFi connection
        
        Args:
            ssid: WiFi network name
            password: WiFi password, empty if no password required
            
        Returns:
            int: 0 for success, -1 for connection failure, -2 for timeout
        """
        logging.info(f"Configuring WiFi. SSID: {ssid}")
        if self.supervisor and hasattr(self.supervisor, 'led'):
            self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIGURING)
        else:
            self.logger.warning("Supervisor instance or LED controller not available for SYS_WIFI_CONFIGURING.")

        # Check if NetworkManager is running before attempting to use nmcli
        if not self._is_networkmanager_running():
            logging.error("NetworkManager is not running, attempting to start it")
            if not self._start_networkmanager():
                logging.error("Failed to start NetworkManager, cannot configure WiFi")
                if self.supervisor and hasattr(self.supervisor, 'led'):
                    self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                return -1
            time.sleep(2)
            
        command = f"nmcli device wifi list > /dev/null; nmcli device wifi connect '{ssid}'"
        if password:
            command += f" password '{password}'"
        
        _, status = self.execute_command(command)
        if status != 0:
            logging.error("Failed to connect to WiFi network, retry again ...")
            _, status = self.execute_command(command)
            if status != 0:
                logging.error("Failed to connect to WiFi network.")
                if self.supervisor and hasattr(self.supervisor, 'led'):
                    self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                return -1
        
        for _ in range(20): # 20 seconds timeout
            if self.check_wifi_connected():
                logging.info(f"Successfully connected to WiFi network: {ssid}")
                self.delete_other_connections(ssid)
                if self.supervisor and hasattr(self.supervisor, 'led'):
                    self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIG_SUCCESS)
                self.stop_wifi_provision(called_after_success=True)
                return 0
            time.sleep(1)
        
        logging.error("Timed out waiting for WiFi connection")
        if self.supervisor and hasattr(self.supervisor, 'led'):
            self.supervisor.led.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
        return -2

    def delete_other_connections(self, ssid):
        """
        Delete all WiFi connections except the one matching the given ssid.
        """
        logging.info(f"Deleting all WiFi connections except SSID: {ssid}")
        # List all connections with their names and uuids
        cmd = "nmcli -t -f name,uuid connection show"
        result, state = self.execute_command(cmd)
        if state != 0 or not result:
            logging.error("Failed to list WiFi connections.")
            return -1
        for line in result.splitlines():
            try:
                name, uuid = line.strip().split(":", 1)
                if name != ssid:
                    del_cmd = f"nmcli connection delete uuid {uuid}"
                    _, del_state = self.execute_command(del_cmd)
                    if del_state == 0:
                        logging.info(f"Deleted connection: {name} (UUID: {uuid})")
                    else:
                        logging.error(f"Failed to delete connection: {name} (UUID: {uuid})")
            except Exception as e:
                logging.error(f"Error parsing connection line '{line}': {e}")
        return 0

    def get_status(self):
        """
        Get the current WiFi connection status
        
        Returns:
            WifiStatus: Object containing WiFi connection information
        """
        logging.info(f"Get Wifi Status ...")
        status = WifiStatus()
        
        # Check if NetworkManager is running
        if not self._is_networkmanager_running():
            logging.warning("NetworkManager is not running, WiFi status may be inaccurate")
            status.error_message = "NetworkManager is not running"
            return status
            
        command = "nmcli -t -f active,ssid dev wifi | grep '^yes' | cut -d: -f2"
        result, state = self.execute_command(command)
        
        if state == 0 and result:
            status.connected = True
            status.ssid = result
        
        # Get IP address using a simpler command
        command = f"ip addr show {self.WIFI_INTERFACE} | grep -w inet | awk '{{print $2}}' | cut -d/ -f1"
        result, _ = self.execute_command(command)
        status.ip_address = result or "Unknown"

        command = f"cat /sys/class/net/{self.WIFI_INTERFACE}/address"
        result, _ = self.execute_command(command)
        status.mac_address = result or "Unknown"

        if not status.connected:
            status.error_message = "Not connected to any WiFi network"
        
        return status

    def delete_networks(self):
        """
        Delete all saved WiFi networks
        
        Returns:
            int: 0 for success, -1 for failure
        """
        logging.info("Deleting all saved WiFi networks")
        
        # Check if NetworkManager is running
        if not self._is_networkmanager_running():
            logging.error("NetworkManager is not running, attempting to start it")
            if not self._start_networkmanager():
                logging.error("Failed to start NetworkManager, cannot delete networks")
                return -1
            # Give NetworkManager time to initialize
            time.sleep(2)
            
        command = "nmcli -t -f uuid connection"
        result, state = self.execute_command(command)
        
        if state != 0 or not result:
            logging.error("Failed to list connections")
            return -1
        
        for uuid in result.splitlines():
            delete_cmd = f"nmcli connection delete uuid {uuid}"
            _, del_state = self.execute_command(delete_cmd)
            if del_state == 0:
                logging.info(f"Successfully deleted connection with UUID: {uuid}")
            else:
                logging.error(f"Failed to delete connection with UUID: {uuid}")
        
        return 0

    def execute_command_with_response(self, command):
        """
        Execute special commands and return response information
        
        Args:
            command: The name of the special command to execute
            
        Returns:
            tuple: (response message, status code) - status code 0 indicates success
        """
        special_commands = {
            "restart_wifi": "nmcli radio wifi off && sleep 1 && nmcli radio wifi on",
            "restart_device": "reboot",
            "factory_reset": "rm -rf /config/* && reboot",
        }
        
        # For nmcli commands, check if NetworkManager is running
        if command == "restart_wifi" and not self._is_networkmanager_running():
            logging.error("NetworkManager is not running, attempting to start it")
            if not self._start_networkmanager():
                logging.error("Failed to start NetworkManager, cannot restart WiFi")
                return "NetworkManager is not running and could not be started", -1
            # Give NetworkManager time to initialize
            time.sleep(2)

        if command in special_commands:
            cmd = special_commands[command]
            logging.info(f"Executing special command: {command}")
            
            if command == "factory_reset":
                # Execute factory reset command
                _, status = self.execute_command(cmd)
                if status == 0:
                    return "Factory reset initiated, device will reboot", 0
                else:
                    return "Factory reset failed", -1
            elif command == "restart_device":
                # Execute device restart command
                _, status = self.execute_command(cmd)
                if status == 0:
                    return "Device restart initiated", 0
                else:
                    return "Device restart failed", -1
            elif command == "restart_wifi":
                # Execute WiFi restart command
                _, status = self.execute_command(cmd)
                if status == 0:
                    return "WiFi restart completed", 0
                else:
                    return "WiFi restart failed", -1
        
        response = f"Unknown command: {command}"
        logging.error(response)
        return response, -1

    def check_wifi_connected(self):
        """
        Check if WiFi is connected
        
        Returns:
            bool: True if connected, False otherwise
        """
        # Check if NetworkManager is running
        if not self._is_networkmanager_running():
            logging.warning("NetworkManager is not running, cannot check WiFi connection status")
            return False
            
        command = "nmcli -t -f GENERAL.STATE device show wlan0"
        result, state = self.execute_command(command)
        return state == 0 and "(connected)" in result

    def get_wlan0_ip(self):
        """
        Get the IPv4 address of the wlan0 interface
        
        Returns:
            str or None: Returns the IP address, or None if not available
        """
        command = "ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1"
        result, status = self.execute_command(command)
        
        if status == 0 and result:
            return result
        return None

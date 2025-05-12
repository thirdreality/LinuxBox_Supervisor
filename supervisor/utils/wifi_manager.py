import subprocess
import os
import time
import logging
import shutil

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


class WifiManager:
    """
    Manager class for handling WiFi operations on the system
    """
    WIFI_INTERFACE = "wlan0"
    
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
            if command.startswith("nmcli") and not shutil.which("nmcli"):
                return "Command 'nmcli' not found", 127
                
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
        
        # Check if NetworkManager is running before attempting to use nmcli
        if not self._is_networkmanager_running():
            logging.error("NetworkManager is not running, attempting to start it")
            if not self._start_networkmanager():
                logging.error("Failed to start NetworkManager, cannot configure WiFi")
                return -1
            # Give NetworkManager time to initialize
            time.sleep(2)
            
        command = f"nmcli device wifi connect '{ssid}'"
        if password:
            command += f" password '{password}'"
        
        _, status = self.execute_command(command)
        if status != 0:
            logging.error("Failed to connect to WiFi network, retry again ...")

            command2 = f"nmcli device wifi list > /dev/null"
            _, status = self.execute_command(command2)  # Fixed: was using 'command' instead of 'command2'
            if status != 0:
                logging.error("Failed to connect to WiFi network.")
                return -1
        
        # Wait for the connection to be established
        for _ in range(20): # 20 seconds timeout
            if self.check_wifi_connected():
                logging.info(f"Successfully connected to WiFi network: {ssid}")
                return 0
            time.sleep(1)
        
        logging.error("Timed out waiting for WiFi connection")
        return -2

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

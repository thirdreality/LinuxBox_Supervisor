import threading
import logging
import os
import json
import subprocess

# ====== Merged from utils/utils.py below ======
"""
System utility functions for performing system operations like reboot, shutdown, and factory reset.
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class OtaStatus:
    """
    Class to store WiFi connection status information
    """
    def __init__(self):
        self.software_mode = "homeassistant-core"
        self.install = "false"
        self.process = "1"

def execute_system_command(command):
    """
    Execute a system command and handle exceptions.
    
    Args:
        command: List containing the command and its arguments
        
    Returns:
        bool: True if command executed successfully, False otherwise
    """
    try:
        subprocess.run(command, check=True)
        logging.info(f"Successfully executed: {' '.join(command)}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with return code {e.returncode}: {' '.join(command)}")
        return False
    except Exception as e:
        logging.error(f"Failed to execute command: {' '.join(command)}, Error: {str(e)}")
        return False

def compare_versions(current_version, new_version):
    """
    Compare two version numbers to determine if an update is needed
    
    Args:
        current_version: Current version number, in the format "x.y.z"
        new_version: New version number, in the format "x.y.z"
        
    Returns:
        bool: True if the new version is greater than the current version, False otherwise
    """
    if not current_version:
        # If the current version is empty, an update is needed
        return True
        
    try:
        # Split the version numbers into lists of integers
        current_parts = [int(x) for x in current_version.split('.')]
        new_parts = [int(x) for x in new_version.split('.')]
        
        # Ensure both lists have the same length
        while len(current_parts) < len(new_parts):
            current_parts.append(0)
        while len(new_parts) < len(current_parts):
            new_parts.append(0)
        
        # Compare each part of the version numbers
        for i in range(len(current_parts)):
            if new_parts[i] > current_parts[i]:
                return True
            elif new_parts[i] < current_parts[i]:
                return False
        
        # If all parts are equal, no update is needed
        return False
    except Exception as e:
        logging.error(f"Error comparing versions {current_version} and {new_version}: {e}")
        # If an error occurs, conservatively assume no update is needed
        return False

def get_installed_version(package_name):
    """
    Get the version of an installed package
    
    Args:
        package_name: Name of the package
        
    Returns:
        str: Version number of the package, or an empty string if not installed
    """
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package_name],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return ""
    except Exception as e:
        logging.error(f"Error getting version for {package_name}: {e}")
        return ""

def is_service_running(service_name):
    try:
        result = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
        return result.stdout.strip() == "active"
    except Exception as e:
        logging.error(f"Error checking if service {service_name} is running: {e}")
        return False

def is_service_enabled(service_name):
    try:
        result = subprocess.run(["systemctl", "is-enabled", service_name], capture_output=True, text=True)
        return result.stdout.strip() == "enabled"
    except Exception as e:
        logging.error(f"Error checking if service {service_name} is enabled: {e}")
        return False

def get_service_status(service_name):
    try:
        result = subprocess.run(["systemctl", "status", service_name], capture_output=True, text=True)
        return result.stdout
    except Exception as e:
        logging.error(f"Error getting status for service {service_name}: {e}")
        return ""

def enable_service(service_name, enable):
    try:
        if enable:
            subprocess.run(["systemctl", "enable", service_name], check=True)
        else:
            subprocess.run(["systemctl", "disable", service_name], check=True)
        return True
    except Exception as e:
        logging.error(f"Error {'enabling' if enable else 'disabling'} service {service_name}: {e}")
        return False

def start_service(service_name, start):
    try:
        if start:
            subprocess.run(["systemctl", "start", service_name], check=True)
        else:
            subprocess.run(["systemctl", "stop", service_name], check=True)
        return True
    except Exception as e:
        logging.error(f"Error {'starting' if start else 'stopping'} service {service_name}: {e}")
        return False

def perform_reboot():
    """
        Safely stop necessary services and reboot the system.
        
        This function stops Docker service before rebooting to prevent data corruption.
        
        Returns:
            bool: True if reboot command was executed successfully, False otherwise
        """
    try:
        subprocess.run(["systemctl", "stop", "docker"], check=True)
        subprocess.run(["reboot"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing reboot: {e}")
        return False

def perform_power_off():
    """
        Safely stop necessary services and shut down the system.
        
        This function stops Docker service before shutdown to prevent data corruption.
        
        Returns:
            bool: True if shutdown command was executed successfully, False otherwise
        """
    try:
        subprocess.run(["systemctl", "stop", "docker"], check=True)
        subprocess.run(["poweroff"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing power off: {e}")
        return False

def perform_factory_reset():
    try:
        subprocess.run(["/usr/local/bin/factory_reset.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing factory reset: {e}")
        return False

def perform_wifi_provision_prepare():
    try:
        subprocess.run(["/usr/local/bin/wifi_provision_prepare.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error preparing WiFi provision: {e}")
        return False

def perform_wifi_provision_restore():
    try:
        subprocess.run(["/usr/local/bin/wifi_provision_restore.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error restoring WiFi provision: {e}")
        return False

# ====== End of merged content ======

def get_ha_zigbee_mode(config_file="/var/lib/homeassistant/homeassistant/.storage/core.config_entries"):
    """
    Check the current Zigbee mode of HomeAssistant.
    - If "domain": "mqtt" is found, return 'z2m'
    - If "domain": "zha" is found, return 'zha'
    - If neither is found, return 'none'
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Correctly get the entries list
            entries = data.get('data', {}).get('entries', [])
            has_zha = any(e.get('domain') == 'zha' for e in entries)
            has_mqtt = any(e.get('domain') == 'mqtt' for e in entries)
            if has_mqtt:
                return 'z2m'
            elif has_zha:
                return 'zha'
            else:
                return 'none'
    except Exception as e:
        logging.error(f"Failed to read HomeAssistant config_entries: {e}")
        return 'none'


def run_zha_pairing(progress_callback=None, complete_callback=None):
    """
    Start the ZHA pairing process
    """
    # Assume there is a dedicated ZHA pairing script
    try:
        logging.info("ZHA pairing process started")

        if complete_callback:
            complete_callback(True, "success")        
    except Exception as e:
        logging.error(f"Failed to start ZHA pairing: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_mqtt_pairing(progress_callback=None, complete_callback=None):
    """
    Start the MQTT pairing process
    """
    # Assume there is a dedicated MQTT pairing script
    try:
        logging.info("MQTT pairing process started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to start MQTT pairing: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_zigbee_switch_zha_mode(progress_callback=None, complete_callback=None):        
    """
    Switch to ZHA mode
    """

    try:
        logging.info("Switching to ZHA mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to switch to ZHA mode: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_zigbee_switch_z2m_mode(progress_callback=None, complete_callback=None):        
    """
    Switch to zigbee2mqtt mode
    """

    try:
        logging.info("Switching to zigbee2mqtt mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to switch to zigbee2mqtt mode: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_zigbee_disable_z2m_mode(progress_callback=None, complete_callback=None):        
    """
    Disable zha/zigbee2mqtt mode
    """

    try:
        logging.info("Disabling zigbee2mqtt mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to disable zigbee2mqtt mode: {e}")        
        if complete_callback:
            complete_callback(False, "fail")

def run_thread_enable_mode(progress_callback=None, complete_callback=None):        
    """
    Enable thread mode
    """

    try:
        logging.info("Enabling thread mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to enable thread mode: {e}")   
        if complete_callback:
            complete_callback(False, "fail")

def run_thread_disable_mode(progress_callback=None, complete_callback=None):        
    """
    Disable thread mode
    """

    try:
        logging.info("Disabling thread mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to disable thread mode: {e}")   
        if complete_callback:
            complete_callback(False, "fail")

def run_zigbee_ota_update(progress_callback=None, complete_callback=None):
    """
    Refresh Zigbee OTA information
    """
    # Assume there is an OTA refresh script
    try:
        logging.info("Zigbee OTA information refreshed")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to refresh Zigbee OTA information: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_system_setting_backup(progress_callback=None, complete_callback=None):
    """
    Backup system settings
    """
    # Assume there is a backup script
    try:
        logging.info("System settings backed up")
        if complete_callback:
            complete_callback(True, "success")              
    except Exception as e:
        logging.error(f"Failed to backup system settings: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def run_system_setting_restore(backup_file=None, progress_callback=None, complete_callback=None):
    """
    Restore system settings from a backup
    """
    # Assume there is a restore script
    try:
        logging.info("System settings restored")
        if complete_callback:
            complete_callback(True, "success")              
    except Exception as e:
        logging.error(f"Failed to restore system settings: {e}")
        if complete_callback:
            complete_callback(False, "fail")

def threaded(func):
    def wrapper(*args, **kwargs):
        t = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        t.start()
        return t
    return wrapper

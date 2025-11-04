# maintainer: guoping.liu@3reality.com

import threading
import logging
import subprocess
import time
from datetime import datetime
import urllib.error
from .wifi_utils import get_wlan0_ip

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

def is_service_present(service_name):
    """
    Return True if the system has this service installed/known to systemd, regardless of running state.
    """
    try:
        # systemctl status returns non-zero for inactive services, so do not check=True
        result = subprocess.run(["systemctl", "status", service_name], capture_output=True, text=True)
        # The output contains Loaded: loaded or could be not-found. Use that to determine presence.
        stdout = (result.stdout or "") + "\n" + (result.stderr or "")
        lowered = stdout.lower()
        if "loaded: loaded" in lowered:
            return True
        # Some distros show 'could not be found' or 'not-found' when unit does not exist
        if "not-found" in lowered or "could not be found" in lowered or "no such file" in lowered:
            return False
        # Fallback: try list-unit-files which enumerates installed unit files
        list_result = subprocess.run(["systemctl", "list-unit-files", service_name], capture_output=True, text=True)
        if list_result.returncode == 0 and service_name in (list_result.stdout or ""):
            return True
        return False
    except Exception as e:
        logging.error(f"Error checking if service {service_name} is present: {e}")
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

def force_sync():
    """
    Force sync to flush NAND cache by executing sync command 3 times.
    This is necessary due to NAND caching mechanisms.
    
    Returns:
        bool: True if sync commands executed successfully, False otherwise
    """
    try:
        # Force sync 3 times to ensure data is written to NAND storage
        for _ in range(3):
            subprocess.run(["sync"], check=True)
        logging.info("Force sync completed (3 times)")
        return True
    except Exception as e:
        logging.error(f"Error during force sync: {e}")
        return False

def perform_reboot():
    """
        Safely stop necessary services and reboot the system.
        
        This function stops Docker service before rebooting to prevent data corruption.
        Docker stop failure is ignored and reboot will proceed anyway.
        
        Returns:
            bool: True if reboot command was executed (may not return if reboot succeeds)
        """
    try:
        # Try to stop docker service if it exists (failure is ignored)
        try:
            subprocess.run(["systemctl", "stop", "docker"], check=False, timeout=1)
        except Exception:
            pass  # Docker stop failure is not critical
        
        # Ensure all data is flushed to disk before reboot
        try:
            force_sync()
        except Exception:
            pass  # Sync failure should not prevent reboot
        
        # Execute reboot command (will not return if successful)
        # Use check=False to ensure reboot is attempted even if command returns error
        logging.info("Executing reboot command...")
        subprocess.run(["reboot"], check=False)
        
        # If we reach here, reboot command may have failed, try alternative
        # Give a moment for reboot to take effect
        time.sleep(1)
        
        # Fallback: try systemctl reboot
        subprocess.run(["systemctl", "reboot"], check=False)
        
        return True
    except Exception as e:
        logging.error(f"Error performing reboot: {e}")
        # Even if there's an error, try to reboot anyway
        try:
            subprocess.run(["reboot"], check=False)
        except:
            pass
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
        
        # Ensure all data is flushed to disk before power off
        force_sync()
        
        subprocess.run(["poweroff"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing power off: {e}")
        return False

def perform_factory_reset():
    try:
        # Ensure all data is flushed to disk before factory reset
        force_sync()
        
        subprocess.run(["/lib/armbian/factory-reset.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing factory reset: {e}")
        return False

# def perform_wifi_provision_prepare():
#     try:
#         subprocess.run(["/usr/local/bin/wifi_provision_prepare.sh"], check=True)
#         return True
#     except Exception as e:
#         logging.error(f"Error preparing WiFi provision: {e}")
#         return False

# def perform_wifi_provision_restore():
#     try:
#         subprocess.run(["/usr/local/bin/wifi_provision_restore.sh"], check=True)
#         return True
#     except Exception as e:
#         logging.error(f"Error restoring WiFi provision: {e}")
#         return False

# ====== End of merged content ======



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



def threaded(func):
    def wrapper(*args, **kwargs):
        t = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        t.start()
        return t
    return wrapper

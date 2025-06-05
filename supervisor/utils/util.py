# maintainer: guoping.liu@3reality.com

import threading
import logging
import os
import json
import subprocess
from datetime import datetime
import glob
import shutil
import tempfile

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
    Start the MQTT pairing process by enabling Zigbee joining via zigbee2mqtt.
    Checks if mosquitto and zigbee2mqtt services are running before attempting to pair.
    """
    services_to_check = ["mosquitto.service", "zigbee2mqtt.service"]
    all_services_active = True

    def _call_progress(percent, message):
        logging.info(f"zigbee2mqtt Pairing progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent)

    try:
        _call_progress(0, "Starting zigbee2mqtt pairing process (Zigbee permit join).")

        for i, service in enumerate(services_to_check):
            progress_step = 10 + (i * 10) # Progress from 10% to 30% for checks
            _call_progress(progress_step, f"Checking status of {service}.")
            try:
                # Use systemctl is-active --quiet to check service status
                # It exits with 0 if active, non-zero otherwise.
                subprocess.run(["systemctl", "is-active", "--quiet", service], check=True)
                logging.info(f"Service {service} is active.")
            except subprocess.CalledProcessError:
                logging.warning(f"Service {service} is not active. zigbee2mqtt pairing cannot proceed.")
                all_services_active = False
                _call_progress(100, f"Service {service} not active. zigbee2mqtt pairing aborted.")
                if complete_callback:
                    complete_callback(False, f"Service {service} not active")
                return
            except FileNotFoundError:
                logging.error(f"systemctl command not found. Cannot check service {service}.")
                all_services_active = False
                _call_progress(100, f"systemctl not found. Pairing aborted.")
                if complete_callback:
                    complete_callback(False, "systemctl not found")
                return

        if all_services_active:
            _call_progress(50, "All required services active. Attempting to enable Zigbee joining.")
            pairing_command = [
                "/usr/bin/mosquitto_pub",
                "-h", "localhost",
                "-t", "zigbee2mqtt/bridge/request/permit_join",
                "-m", '{"time": 254}',
                "-u", "thirdreality",
                "-P", "thirdreality"
            ]
            try:
                logging.info(f"Executing zigbee2mqtt pairing command: {' '.join(pairing_command)}")
                result = subprocess.run(pairing_command, check=True, capture_output=True, text=True)
                logging.info(f"zigbee2mqtt pairing enabled successfully via MQTT: {result.stdout.strip()}")
                _call_progress(100, "zigbee2mqtt pairing successfully enabled.")
                if complete_callback:
                    complete_callback(True, "success - zigbee2mqtt pairing enabled")
            except subprocess.CalledProcessError as e_cmd:
                logging.error(f"Failed to execute mosquitto_pub command. RC: {e_cmd.returncode}. Error: {e_cmd.stderr.strip()}")
                _call_progress(100, f"Failed to enable zigbee2mqtt pairing: {e_cmd.stderr.strip()}")
                if complete_callback:
                    complete_callback(False, f"Command failed: {e_cmd.stderr.strip()}")
            except FileNotFoundError:
                logging.error("mosquitto_pub command not found. Cannot enable pairing.")
                _call_progress(100, "mosquitto_pub not found.")
                if complete_callback:
                    complete_callback(False, "mosquitto_pub not found")
        # No else needed here as we return early if services are not active

    except Exception as e:
        logging.error(f"An unexpected error occurred during MQTT pairing: {e}")
        _call_progress(100, f"Unexpected error: {e}")
        if complete_callback:
            complete_callback(False, f"Unexpected error: {str(e)}")

def run_zigbee_switch_zha_mode(progress_callback=None, complete_callback=None):
    """
    Switch to zha mode
    """

    try:
        logging.info("Switching to zha mode started")
        if complete_callback:
            complete_callback(True, "success")         
    except Exception as e:
        logging.error(f"Failed to switch to zha mode: {e}")
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
    Backup system settings by stopping services, creating a tarball, managing backups, and restarting services.
    """
    services_to_manage = [
        "home-assistant.service",
        "matter-server.service",
        "otbr-agent.service",
        "zigbee2mqtt.service",
        "mosquitto.service",
        "openhab.service"
    ]
    backup_dirs_config = [
        ("/var/lib/thread", "thread_data"),
        ("/var/lib/homeassistant", "homeassistant_data"),
        ("/opt/zigbee2mqtt/data", "zigbee2mqtt_data"),
        ("/etc/mosquitto", "mosquitto_config")
    ]
    backup_base_path = "/lib/thirdreality/backup"
    max_backups = 5

    original_service_states = {}
    backup_archive_created = False

    def _call_progress(percent, message):
        logging.info(f"Backup progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent)

    try:
        _call_progress(0, "Starting system settings backup.")

        _call_progress(5, "Checking and stopping services.")
        current_progress = 5
        progress_per_service_stop = 25 / len(services_to_manage) if services_to_manage else 0

        for i, service in enumerate(services_to_manage):
            service_active = False
            try:
                result = subprocess.run(["systemctl", "is-active", "--quiet", service])
                service_active = result.returncode == 0
            except Exception as e:
                logging.warning(f"Could not determine status of service {service}: {e}. Assuming inactive.")
            
            original_service_states[service] = service_active
            if service_active:
                try:
                    logging.info(f"Service {service} is active. Stopping it.")
                    stop_result = subprocess.run(["systemctl", "stop", service], check=False, capture_output=True, text=True)
                    if stop_result.returncode == 0:
                        logging.info(f"Service {service} stopped successfully.")
                    else:
                        # systemctl stop returns 5 if service was not running, which is fine.
                        if stop_result.returncode == 5:
                             logging.info(f"Service {service} was already stopped or not found (rc=5).")
                        else:
                            logging.warning(f"Failed to stop service {service}. RC: {stop_result.returncode}. Error: {stop_result.stderr.strip()}. Proceeding.")
                except Exception as e:
                    logging.warning(f"Error stopping service {service}: {e}. Proceeding.")
            else:
                logging.info(f"Service {service} is not active.")
            current_progress += progress_per_service_stop
            _call_progress(int(current_progress), f"Processed service {service}.")

        _call_progress(30, "Preparing to create backup archive.")
        os.makedirs(backup_base_path, exist_ok=True)
        _call_progress(35, f"Ensured backup directory {backup_base_path} exists.")

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f"setting_{timestamp}.tar.gz"
        backup_filepath = os.path.join(backup_base_path, backup_filename)

        valid_backup_dirs = []
        for path, _ in backup_dirs_config:
            if os.path.exists(path):
                valid_backup_dirs.append(path)
            else:
                logging.warning(f"Backup source directory {path} does not exist. Skipping.")
        
        if not valid_backup_dirs:
            logging.error("No valid source directories found for backup. Aborting backup creation.")
            raise Exception("No valid source directories found for backup.")

        tar_command = ["tar", "-czf", backup_filepath] + valid_backup_dirs
        logging.info(f"Creating backup archive: {backup_filepath} from {valid_backup_dirs}")
        _call_progress(40, f"Creating tarball {backup_filename} from {len(valid_backup_dirs)} source(s).")
        
        tar_process_result = subprocess.run(tar_command, capture_output=True, text=True)

        if tar_process_result.returncode != 0:
            error_message = f"Tar command failed. RC: {tar_process_result.returncode}. Stderr: {tar_process_result.stderr.strip()}"
            logging.error(error_message)
            raise Exception(error_message)
        
        logging.info(f"Backup archive created successfully: {backup_filepath}")
        _call_progress(70, "Backup archive created.")
        backup_archive_created = True

        _call_progress(75, "Managing backup files (rotation).")
        backup_files = sorted(
            glob.glob(os.path.join(backup_base_path, "setting_*.tar.gz")),
            key=os.path.getmtime
        )
        
        if len(backup_files) > max_backups:
            files_to_delete_count = len(backup_files) - max_backups
            logging.info(f"Found {len(backup_files)} backups (max {max_backups}). Deleting {files_to_delete_count} oldest one(s).")
            for i in range(files_to_delete_count):
                file_to_delete = backup_files[i]
                try:
                    logging.info(f"Deleting old backup: {file_to_delete}")
                    os.remove(file_to_delete)
                except OSError as e:
                    logging.error(f"Failed to delete old backup {file_to_delete}: {e}")
            _call_progress(80, f"Deleted {files_to_delete_count} old backup(s).")
        else:
            logging.info(f"Found {len(backup_files)} backups. No rotation needed (max {max_backups}).")
        _call_progress(85, "Backup file management complete.")

        if complete_callback:
            complete_callback(True, "success")
        _call_progress(100, "System settings backup completed successfully.")

    except Exception as e:
        logging.error(f"System settings backup failed critically: {e}", exc_info=True)
        if complete_callback:
            complete_callback(False, str(e) if str(e) else "Unknown error during backup")
    finally:
        _call_progress(90, "Restoring services to their original states (if changed).")
        current_progress = 90
        progress_per_service_start = 10 / len(original_service_states) if original_service_states else 0

        for i, (service, was_active) in enumerate(original_service_states.items()):
            if was_active:
                try:
                    logging.info(f"Service {service} was originally active. Ensuring it is started.")
                    start_result = subprocess.run(["systemctl", "start", service], check=False, capture_output=True, text=True)
                    if start_result.returncode == 0:
                        logging.info(f"Service {service} started successfully.")
                    else:
                        logging.warning(f"Failed to start service {service}. RC: {start_result.returncode}. Error: {start_result.stderr.strip()}")
                except Exception as e_restart_other:
                    logging.error(f"Unexpected error restarting service {service}: {e_restart_other}")
            current_progress += progress_per_service_start
            _call_progress(int(current_progress), f"Processed service restoration for {service}.")
        
        logging.info("Service restoration phase complete.")


def run_system_setting_restore(backup_file=None, progress_callback=None, complete_callback=None):
    """
    Restore system settings from a backup file.
    """
    backup_base_path = "/lib/thirdreality/backup"
    services_to_manage = [
        "home-assistant.service", "matter-server.service", "otbr-agent.service",
        "zigbee2mqtt.service", "mosquitto.service", "openhab.service"
    ]
    # Define what directories are expected to be restored. Assumes tarball contains these paths relative to its root.
    # E.g., /var/lib/thread in the system is var/lib/thread inside the tarball.
    restore_target_system_paths = [
        "/var/lib/thread",
        "/var/lib/homeassistant/", # Trailing slash will be normalized
        "/opt/zigbee2mqtt/data",
        "/etc/mosquitto"
    ]
    original_service_states = {}

    def _call_progress(percent, message):
        logging.info(f"Restore progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent)

    try:
        _call_progress(0, "Starting system settings restore.")
        selected_backup_filepath = None

        if backup_file:
            _call_progress(5, f"Checking for specified backup file: {backup_file}")
            candidate_filepath = os.path.join(backup_base_path, backup_file)
            if os.path.isfile(candidate_filepath):
                selected_backup_filepath = candidate_filepath
                logging.info(f"Using specified backup file: {selected_backup_filepath}")
                _call_progress(10, f"Specified backup file found: {os.path.basename(selected_backup_filepath)}")
            else:
                logging.info(f"Specified backup file {candidate_filepath} not found. Concluding restore as per request.")
                _call_progress(100, "Specified backup file not found.")
                if complete_callback:
                    complete_callback(True, "success - specified backup file not found")
                return
        else:
            _call_progress(5, "No specific backup file provided. Scanning for existing backups.")
            backup_files = sorted(
                glob.glob(os.path.join(backup_base_path, "setting_*.tar.gz")),
                key=os.path.getmtime,
                reverse=True  # Get newest first
            )
            if backup_files:
                selected_backup_filepath = backup_files[0]
                logging.info(f"Using the latest backup file found: {selected_backup_filepath}")
                _call_progress(10, f"Selected latest backup for restore: {os.path.basename(selected_backup_filepath)}")
            else:
                logging.info(f"No backup file specified and no 'setting_*.tar.gz' files found in {backup_base_path}. Concluding restore as per request.")
                _call_progress(100, "No 'setting_*.tar.gz' backup files found to restore.")
                if complete_callback:
                    complete_callback(True, "success - no backup files found to restore")
                return
        
        # _call_progress(10, f"Selected backup for restore: {os.path.basename(selected_backup_filepath)}") # This line is now covered above

        with tempfile.TemporaryDirectory(prefix="restore_temp_") as temp_extraction_dir:
            _call_progress(15, f"Created temporary directory for extraction: {temp_extraction_dir}")
            logging.info(f"Extracting {selected_backup_filepath} to {temp_extraction_dir}")
            tar_extract_command = ["tar", "-xzf", selected_backup_filepath, "-C", temp_extraction_dir]
            extract_result = subprocess.run(tar_extract_command, capture_output=True, text=True)

            if extract_result.returncode != 0:
                error_msg = f"Failed to extract backup archive {selected_backup_filepath}. RC: {extract_result.returncode}. Stderr: {extract_result.stderr.strip()}"
                logging.error(error_msg)
                raise Exception(error_msg)
            _call_progress(30, "Backup archive extracted successfully.")

            _call_progress(35, "Checking and stopping services prior to restore.")
            current_progress_services = 35
            progress_per_service_stop = 15 / len(services_to_manage) if services_to_manage else 0

            for service in services_to_manage:
                service_active = False
                try:
                    result = subprocess.run(["systemctl", "is-active", "--quiet", service])
                    service_active = result.returncode == 0
                except Exception as e_stat:
                    logging.warning(f"Could not determine status of service {service}: {e_stat}. Assuming inactive.")
                original_service_states[service] = service_active
                if service_active:
                    try:
                        logging.info(f"Service {service} is active. Stopping it for restore.")
                        stop_result = subprocess.run(["systemctl", "stop", service], check=False, capture_output=True, text=True)
                        if stop_result.returncode == 0 or stop_result.returncode == 5: # 0=stopped, 5=not running
                            logging.info(f"Service {service} stopped or was not running.")
                        else:
                            logging.warning(f"Failed to stop service {service}. RC: {stop_result.returncode}. Error: {stop_result.stderr.strip()}. Proceeding with caution.")
                    except Exception as e_stop:
                        logging.warning(f"Error stopping service {service}: {e_stop}. Proceeding with caution.")
                current_progress_services += progress_per_service_stop
                _call_progress(int(current_progress_services), f"Processed service {service} for stopping.")
            _call_progress(50, "Service stopping phase complete.")

            _call_progress(55, "Starting data restoration from extracted backup.")
            current_progress_data = 55
            progress_per_dir_restore = 25 / len(restore_target_system_paths) if restore_target_system_paths else 0

            for target_sys_path_orig in restore_target_system_paths:
                target_sys_path = os.path.normpath(target_sys_path_orig)
                # Path inside tarball is relative to tar root, matching the absolute path structure
                source_in_temp = os.path.join(temp_extraction_dir, target_sys_path.lstrip(os.sep))

                _call_progress(int(current_progress_data), f"Restoring data for {target_sys_path}")
                if os.path.exists(source_in_temp):
                    logging.info(f"Source {source_in_temp} found in backup. Restoring to {target_sys_path}.")
                    try:
                        parent_of_target = os.path.dirname(target_sys_path)
                        if parent_of_target and not os.path.exists(parent_of_target):
                             os.makedirs(parent_of_target, exist_ok=True)

                        if os.path.exists(target_sys_path):
                            logging.info(f"Removing existing content at {target_sys_path} before restore.")
                            if os.path.isdir(target_sys_path):
                                shutil.rmtree(target_sys_path)
                            else:
                                os.remove(target_sys_path)
                        
                        if os.path.isdir(source_in_temp):
                            shutil.copytree(source_in_temp, target_sys_path, symlinks=True)
                        elif os.path.isfile(source_in_temp):
                            os.makedirs(os.path.dirname(target_sys_path), exist_ok=True) # Ensure target dir exists for file copy
                            shutil.copy2(source_in_temp, target_sys_path)
                        else:
                            logging.warning(f"Source {source_in_temp} is neither a file nor a directory. Skipping restore for {target_sys_path}.")
                            current_progress_data += progress_per_dir_restore # Still count progress
                            _call_progress(int(current_progress_data), f"Skipped non-file/dir source for {target_sys_path}.")
                            continue
                        logging.info(f"Successfully restored {target_sys_path}.")
                    except Exception as e_restore_item:
                        logging.error(f"Failed to restore {target_sys_path} from {source_in_temp}: {e_restore_item}", exc_info=True)
                        raise Exception(f"Critical error during restore of {target_sys_path}: {e_restore_item}")
                else:
                    logging.warning(f"Source path {source_in_temp} not found in extracted backup. Skipping restore for {target_sys_path}.")
                current_progress_data += progress_per_dir_restore
                _call_progress(int(current_progress_data), f"Finished processing restore for {target_sys_path}.")
            
            _call_progress(80, "Data restoration phase complete.")
            if complete_callback:
                complete_callback(True, "success")
            _call_progress(100, "System settings restore completed successfully.")

    except Exception as e:
        logging.error(f"System settings restore failed: {e}", exc_info=True)
        if complete_callback:
            complete_callback(False, str(e) if str(e) else "Unknown error during restore")
    finally:
        if original_service_states: # Only proceed if services were actually stopped
            _call_progress(85, "Restoring services to their original states (if changed).")
            current_progress_finally = 85
            progress_per_service_start = 15 / len(original_service_states) if original_service_states else 0 # Should not be 0 if original_service_states is true

            for service, was_active in original_service_states.items():
                if was_active:
                    try:
                        logging.info(f"Service {service} was originally active. Ensuring it is started post-restore.")
                        start_result = subprocess.run(["systemctl", "start", service], check=False, capture_output=True, text=True)
                        if start_result.returncode == 0:
                            logging.info(f"Service {service} started successfully.")
                        else:
                            logging.warning(f"Failed to restart service {service} post-restore. RC: {start_result.returncode}. Error: {start_result.stderr.strip()}")
                    except Exception as e_restart:
                        logging.error(f"Unexpected error restarting service {service} post-restore: {e_restart}")
                current_progress_finally += progress_per_service_start
                _call_progress(int(min(current_progress_finally,100)), f"Processed service restoration for {service}.")
            logging.info("Service restoration phase in 'finally' block complete.")
        else:
            # This case is hit if an early return occurred (e.g., no backup file found)
            # or if an error occurred before original_service_states was populated.
            logging.info("No services were modified or an early exit occurred; skipping service restoration progress in 'finally' block.")
        logging.info("Restore function 'finally' block finished execution.")


def threaded(func):
    def wrapper(*args, **kwargs):
        t = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        t.start()
        return t
    return wrapper

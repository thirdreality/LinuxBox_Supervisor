# maintainer: guoping.liu@3reality.com

import os
import time
import logging
import subprocess
import glob
import tempfile
import shutil
import json
from datetime import datetime
from .wifi_utils import get_current_wifi_info
import base64

from ..const import BACKUP_STORAGE_MODE, BACKUP_INTERNAL_PATH, BACKUP_EXTERNAL_PATH
from supervisor.sysinfo import SystemInfoUpdater

SERVICES_TO_MANAGE = [
    "home-assistant.service",
    "matter-server.service",
    "otbr-agent.service",
    "zigbee2mqtt.service",
    "mosquitto.service",
    "openhab.service"
]

BACKUP_DIRS_CONFIG = [
    ("/var/lib/thread", "thread_data"),
    ("/var/lib/homeassistant", "homeassistant_data"),
    ("/opt/zigbee2mqtt/data", "zigbee2mqtt_data"),
    ("/etc/mosquitto", "mosquitto_config")
]

# Configuration directory for restore records
RESTORE_RECORD_DIR = "/usr/lib/thirdreality/conf"

logger = logging.getLogger("Supervisor")

def _check_external_storage_available():
    """
    Check if external storage (USB) is mounted at /mnt
    """
    try:
        result = subprocess.run(["mount"], capture_output=True, text=True)
        if result.returncode == 0:
            # Check if there's any mount point under /mnt
            for line in result.stdout.splitlines():
                if "/mnt" in line:
                    return True
        return False
    except Exception as e:
        logging.error(f"Failed to check mount status: {e}")
        return False

def _get_backup_path():
    """
    Get backup path based on storage mode configuration
    """
    if BACKUP_STORAGE_MODE == "internal":
        return BACKUP_INTERNAL_PATH
    elif BACKUP_STORAGE_MODE == "external":
        if not _check_external_storage_available():
            raise Exception("External storage mode enabled but no USB device mounted at /mnt")
        return BACKUP_EXTERNAL_PATH
    else:
        raise Exception(f"Invalid backup storage mode: {BACKUP_STORAGE_MODE}")

def run_setting_backup(progress_callback=None, complete_callback=None):
    """
    Backup system settings by stopping services, creating a tarball, managing backups, and restarting services.
    """
    try:
        backup_base_path = _get_backup_path()
    except Exception as e:
        logging.error(f"Failed to determine backup path: {e}")
        if complete_callback:
            complete_callback(False, str(e))
        return
    
    max_backups = 5

    original_service_states = {}
    backup_archive_created = False

    def _call_progress(percent, message):
        logging.info(f"Backup progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent, message)

    try:
        _call_progress(0, f"Starting system settings backup using {BACKUP_STORAGE_MODE} storage.")

        _call_progress(5, "Checking and stopping services.")
        current_progress = 5
        progress_per_service_stop = 25 / len(SERVICES_TO_MANAGE) if SERVICES_TO_MANAGE else 0

        for i, service in enumerate(SERVICES_TO_MANAGE):
            service_active = False
            service_enabled = False
            try:
                # Check if service is active
                result = subprocess.run(["systemctl", "is-active", "--quiet", service])
                service_active = result.returncode == 0
                
                # Check if service is enabled
                result = subprocess.run(["systemctl", "is-enabled", "--quiet", service])
                service_enabled = result.returncode == 0
            except Exception as e:
                logging.warning(f"Could not determine status of service {service}: {e}. Assuming inactive and disabled.")
            
            # Store both active and enabled status
            original_service_states[service] = {
                "active": service_active,
                "enabled": service_enabled
            }
            
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
        
        # For external storage, create backup directory if it doesn't exist
        if BACKUP_STORAGE_MODE == "external" and not os.path.exists(backup_base_path):
            try:
                os.makedirs(backup_base_path, exist_ok=True)
                logging.info(f"Created external backup directory: {backup_base_path}")
            except Exception as e:
                error_msg = f"Failed to create external backup directory {backup_base_path}: {e}"
                logging.error(error_msg)
                raise Exception(error_msg)
        else:
            os.makedirs(backup_base_path, exist_ok=True)
        
        _call_progress(35, f"Ensured backup directory {backup_base_path} exists.")

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f"setting_{timestamp}.tar.gz"
        backup_filepath = os.path.join(backup_base_path, backup_filename)
        
        valid_backup_dirs = []
        for path, _ in BACKUP_DIRS_CONFIG:
            if os.path.exists(path):
                valid_backup_dirs.append(path)
            else:
                logging.warning(f"Backup source directory {path} does not exist. Skipping.")
        
        if not valid_backup_dirs:
            logging.error("No valid source directories found for backup. Aborting backup creation.")
            raise Exception("No valid source directories found for backup.")

        # --- 新的打包流程 ---
        import tempfile
        _call_progress(37, "Preparing temporary directory for backup.")
        with tempfile.TemporaryDirectory(prefix="setting_backup_") as temp_backup_dir:
            # 1. 拷贝所有需要备份的目录到临时目录
            for src_path, name in BACKUP_DIRS_CONFIG:
                if os.path.exists(src_path):
                    dest_path = os.path.join(temp_backup_dir, name)
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path, symlinks=True)
                    elif os.path.isfile(src_path):
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        shutil.copy2(src_path, dest_path)
            # 2. 写入service_states.json
            service_states_path = os.path.join(temp_backup_dir, "service_states.json")
            with open(service_states_path, 'w') as f:
                json.dump(original_service_states, f, indent=2)
            # 收集并写入network_states.json
            network_states = None
            try:
                ssid, psk = get_current_wifi_info()
                if ssid and psk:
                    encrypted_psk = base64.b64encode(psk.encode()).decode()
                    network_states = {"ssid": ssid, "psk": encrypted_psk}
                    network_states_path = os.path.join(temp_backup_dir, "network_states.json")
                    with open(network_states_path, 'w') as f:
                        json.dump(network_states, f, indent=2)
                    logger.info(f"Network states saved: ssid={ssid}")
                else:
                    logger.info("No WiFi connection info found, skipping network_states.json backup.")
            except Exception as e:
                logger.warning(f"Failed to collect network states: {e}")
            _call_progress(40, "All data copied to temp dir, creating tarball...")
            # 3. 打包整个临时目录内容
            tar_command = ["tar", "-czf", backup_filepath, "-C", temp_backup_dir, "."]
            tar_process_result = subprocess.run(tar_command, capture_output=True, text=True)
            if tar_process_result.returncode != 0:
                error_message = f"Tar command failed. RC: {tar_process_result.returncode}. Stderr: {tar_process_result.stderr.strip()}"
                logging.error(error_message)
                raise Exception(error_message)
            logging.info(f"Backup archive created successfully: {backup_filepath}")
            # Force sync to flush NAND cache
            force_sync()
            logging.info("Force sync executed after backup archive creation.")
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

    except Exception as e:
        logging.error(f"System settings backup failed critically: {e}", exc_info=True)
        if complete_callback:
            complete_callback(False, str(e) if str(e) else "Unknown error during backup")
    finally:
        # Clean up any remaining temporary service state file
        try:
            temp_service_state_file = os.path.join(backup_base_path, "service_states.json")
            if os.path.exists(temp_service_state_file):
                os.remove(temp_service_state_file)
                logging.info(f"Cleaned up temporary file: {temp_service_state_file}")
        except Exception as cleanup_error:
            logging.warning(f"Failed to clean up temporary files: {cleanup_error}")
        _call_progress(90, "Restoring services to their original states (if changed).")
        current_progress = 90
        progress_per_service_start = 10 / len(original_service_states) if original_service_states else 0

        for i, (service, was_active) in enumerate(original_service_states.items()):
            if was_active["active"]:
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
        
        # Force sync to flush NAND cache after successful backup
        force_sync()
        logging.info("Force sync executed after successful backup completion.")
        
        # Call completion callback and final progress only after all work is done
        if complete_callback:
            complete_callback(True, "success")
        _call_progress(100, "System settings backup completed successfully.")


def _get_restore_record_path(backup_filename):
    """
    Generate restore record file path based on backup filename
    """
    # Extract timestamp from backup filename (setting_YYYYMMDDHHMMSS.tar.gz)
    if backup_filename.startswith("setting_") and backup_filename.endswith(".tar.gz"):
        timestamp = backup_filename.replace("setting_", "").replace(".tar.gz", "")
        record_filename = f"restore_record_{timestamp}.json"
    else:
        # Fallback: use the full filename as identifier
        safe_filename = backup_filename.replace(".", "_").replace("-", "_")
        record_filename = f"restore_record_{safe_filename}.json"
    
    return os.path.join(RESTORE_RECORD_DIR, record_filename)

def _check_restore_record_exists(backup_filename):
    """
    Check if restore record exists for the given backup file
    """
    record_path = _get_restore_record_path(backup_filename)
    return os.path.exists(record_path)

def _create_restore_record(backup_filename, success=True):
    """
    Create restore record file after successful restore
    """
    try:
        # Ensure record directory exists
        os.makedirs(RESTORE_RECORD_DIR, exist_ok=True)
        
        record_path = _get_restore_record_path(backup_filename)
        record_data = {
            "backup_filename": backup_filename,
            "restore_timestamp": datetime.now().isoformat(),
            "restore_success": success,
            "restore_storage_mode": BACKUP_STORAGE_MODE
        }
        
        with open(record_path, 'w') as f:
            json.dump(record_data, f, indent=2)
        
        logging.info(f"Restore record created: {record_path}")
        return True
    except Exception as e:
        logging.error(f"Failed to create restore record: {e}")
        return False

def run_setting_restore(backup_file=None, progress_callback=None, complete_callback=None):
    """
    Restore system settings from a backup file.
    """
    
    try:
        backup_base_path = _get_backup_path()
    except Exception as e:
        logging.error(f"Failed to determine backup path: {e}")
        if complete_callback:
            complete_callback(False, str(e))
        return
    original_service_states = {}
    backup_service_states = {}

    def _call_progress(percent, message):
        logging.info(f"Restore progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent, message)

    try:
        _call_progress(0, f"Starting system settings restore using {BACKUP_STORAGE_MODE} storage.")
        
        # Check if backup directory exists for external storage
        if BACKUP_STORAGE_MODE == "external" and not os.path.exists(backup_base_path):
            error_msg = f"External backup directory {backup_base_path} does not exist"
            logging.error(error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            _call_progress(100, "External backup directory not found.")
            return
        
        selected_backup_filepath = None

        if backup_file:
            # Convert timestamp to full filename format
            full_backup_filename = f"setting_{backup_file}.tar.gz"
            _call_progress(5, f"Checking for specified backup file: {full_backup_filename} (from timestamp: {backup_file})")
            candidate_filepath = os.path.join(backup_base_path, full_backup_filename)
            if os.path.isfile(candidate_filepath):
                selected_backup_filepath = candidate_filepath
                logging.info(f"Using specified backup file: {selected_backup_filepath}")
                _call_progress(10, f"Specified backup file found: {full_backup_filename}")
            else:
                error_msg = f"Specified backup file {full_backup_filename} not found in backup directory {backup_base_path}"
                logging.error(error_msg)
                if complete_callback:
                    complete_callback(False, error_msg)
                _call_progress(100, "Specified backup file not found.")
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
                if BACKUP_STORAGE_MODE == "external":
                    error_msg = f"No backup files found in external storage directory {backup_base_path}"
                    logging.error(error_msg)
                    if complete_callback:
                        complete_callback(False, error_msg)
                    _call_progress(100, "No backup files found in external storage.")
                    return
                else:
                    logging.info(f"No backup file specified and no 'setting_*.tar.gz' files found in {backup_base_path}. Concluding restore as per request.")
                    if complete_callback:
                        complete_callback(True, "success - no backup files found to restore")
                    _call_progress(100, "No 'setting_*.tar.gz' backup files found to restore.")
                    return
        
        # Check if restore record exists for the selected backup file
        backup_filename = os.path.basename(selected_backup_filepath)
        if _check_restore_record_exists(backup_filename):
            error_msg = f"Restore record already exists for backup file {backup_filename}. This backup has already been restored. Restore operation cancelled."
            logging.error(error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            _call_progress(100, "Restore cancelled - backup already restored.")
            return

        _call_progress(12, f"No restore record found for {backup_filename}, proceeding with restore")

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

            # Try to read service states from backup
            # Service state file is stored at the top level of the backup as service_states.json
            service_state_file = os.path.join(temp_extraction_dir, "service_states.json")
            if os.path.exists(service_state_file):
                try:
                    backup_service_states = json.load(open(service_state_file, 'r'))
                    logging.info(f"Loaded service states from backup: {backup_service_states}")
                    _call_progress(32, "Service states loaded from backup.")
                except Exception as e:
                    logging.warning(f"Failed to load service states from backup: {e}. Will use current service states for restore.")
                    backup_service_states = {}
            else:
                logging.info("No service state file found in backup. Will use current service states for restore.")
                backup_service_states = {}

            _call_progress(35, "Stopping all services prior to restore.")
            current_progress_services = 35
            progress_per_service_stop = 15 / len(SERVICES_TO_MANAGE) if SERVICES_TO_MANAGE else 0

            for service in SERVICES_TO_MANAGE:
                service_active = False
                service_enabled = False
                try:
                    result = subprocess.run(["systemctl", "is-active", "--quiet", service])
                    service_active = result.returncode == 0
                    
                    result = subprocess.run(["systemctl", "is-enabled", "--quiet", service])
                    service_enabled = result.returncode == 0
                except Exception as e_stat:
                    logging.warning(f"Could not determine status of service {service}: {e_stat}. Assuming inactive and disabled.")
                original_service_states[service] = {
                    "active": service_active,
                    "enabled": service_enabled
                }
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
            progress_per_dir_restore = 25 / len(BACKUP_DIRS_CONFIG) if BACKUP_DIRS_CONFIG else 0

            for target_sys_path, mapped_name in BACKUP_DIRS_CONFIG:
                target_sys_path = os.path.normpath(target_sys_path)
                source_in_temp = os.path.join(temp_extraction_dir, mapped_name)
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
                            os.makedirs(os.path.dirname(target_sys_path), exist_ok=True)
                            shutil.copy2(source_in_temp, target_sys_path)
                        else:
                            logging.warning(f"Source {source_in_temp} is neither a file nor a directory. Skipping restore for {target_sys_path}.")
                            current_progress_data += progress_per_dir_restore
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
            # Force sync to flush NAND cache after data restoration
            force_sync()
            logging.info("Force sync executed after data restoration.")
            # 恢复网络连接
            try:
                network_states_path = os.path.join(temp_extraction_dir, "network_states.json")
                network_states = None
                if os.path.exists(network_states_path):
                    with open(network_states_path, 'r') as f:
                        network_states = json.load(f)
                if network_states:
                    ssid = network_states.get("ssid")
                    encrypted_psk = network_states.get("psk")
                    if ssid and encrypted_psk:
                        psk = base64.b64decode(encrypted_psk.encode()).decode()
                        # 检查当前连接
                        current_ssid, _ = get_current_wifi_info()
                        if current_ssid == ssid:
                            logger.info("Current SSID matches saved SSID, skipping network restore.")
                        else:
                            cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", psk]
                            result = subprocess.run(cmd, capture_output=True, text=True)
                            if result.returncode == 0:
                                logger.info(f"Successfully restored network connection to {ssid}")
                            else:
                                logger.warning(f"Failed to restore network connection: {result.stderr}")
                    else:
                        logger.info("network_states.json missing ssid or psk, skipping network restore.")
                else:
                    logger.info("No network_states.json found, skipping network restore.")
            except Exception as e:
                logger.warning(f"Failed to restore network connection: {e}")

    except Exception as e:
        logging.error(f"System settings restore failed: {e}", exc_info=True)
        if complete_callback:
            complete_callback(False, str(e) if str(e) else "Unknown error during restore")
    finally:
        if original_service_states: # Only proceed if services were actually stopped
            _call_progress(85, "Restoring services based on backup service states.")
            current_progress_finally = 85
            
            # Use backup service states if available, otherwise fall back to original states
            service_states_to_restore = backup_service_states if backup_service_states else original_service_states
            progress_per_service_start = 15 / len(service_states_to_restore) if service_states_to_restore else 0

            for service, service_state in service_states_to_restore.items():
                # Handle both old format (boolean) and new format (dict) for backward compatibility
                if isinstance(service_state, bool):
                    # Old format: only active status was stored
                    should_be_active = service_state
                    should_be_enabled = None  # Unknown
                else:
                    # New format: both active and enabled status
                    should_be_active = service_state.get("active", False)
                    should_be_enabled = service_state.get("enabled", None)
                
                # Restore enabled status if available
                if should_be_enabled is not None:
                    try:
                        if should_be_enabled:
                            logging.info(f"Service {service} should be enabled according to backup. Enabling it.")
                            enable_result = subprocess.run(["systemctl", "enable", service], check=False, capture_output=True, text=True)
                            if enable_result.returncode == 0:
                                logging.info(f"Service {service} enabled successfully.")
                            else:
                                logging.warning(f"Failed to enable service {service} post-restore. RC: {enable_result.returncode}. Error: {enable_result.stderr.strip()}")
                        else:
                            logging.info(f"Service {service} should be disabled according to backup. Disabling it.")
                            disable_result = subprocess.run(["systemctl", "disable", service], check=False, capture_output=True, text=True)
                            if disable_result.returncode == 0:
                                logging.info(f"Service {service} disabled successfully.")
                            else:
                                logging.warning(f"Failed to disable service {service} post-restore. RC: {disable_result.returncode}. Error: {disable_result.stderr.strip()}")
                    except Exception as e_enable:
                        logging.error(f"Unexpected error managing enabled status for service {service} post-restore: {e_enable}")
                
                # Restore active status
                if should_be_active:
                    try:
                        logging.info(f"Service {service} should be active according to backup. Starting it.")
                        start_result = subprocess.run(["systemctl", "start", service], check=False, capture_output=True, text=True)
                        if start_result.returncode == 0:
                            logging.info(f"Service {service} started successfully.")
                        else:
                            logging.warning(f"Failed to start service {service} post-restore. RC: {start_result.returncode}. Error: {start_result.stderr.strip()}")
                    except Exception as e_restart:
                        logging.error(f"Unexpected error starting service {service} post-restore: {e_restart}")
                else:
                    logging.info(f"Service {service} should remain inactive according to backup. Leaving it stopped.")
                current_progress_finally += progress_per_service_start
                _call_progress(int(min(current_progress_finally,100)), f"Processed service restoration for {service}.")
            
            if backup_service_states:
                logging.info("Service restoration based on backup service states complete.")
            else:
                logging.info("Service restoration based on current service states complete (no backup states found).")
            
            # Create restore record after successful restore
            backup_filename = os.path.basename(selected_backup_filepath)
            _create_restore_record(backup_filename, True)
            
            # Force sync to flush NAND cache after successful restore
            force_sync()
            logging.info("Force sync executed after successful restore completion.")
            
            # Call completion callback and final progress only after all work is done
            if complete_callback:
                complete_callback(True, "success")
            _call_progress(100, "System settings restore completed successfully.")
        else:
            # This case is hit if an early return occurred (e.g., no backup file found)
            # or if an error occurred before original_service_states was populated.
            logging.info("No services were modified or an early exit occurred; skipping service restoration progress in 'finally' block.")
        logging.info("Restore function 'finally' block finished execution.")

def run_setting_updated(supervisor=None, progress_callback=None, complete_callback=None):
    """
    Clear version information for HomeAssistantInfo and OpenHabInfo when software is updated.
    """
    try:
        if progress_callback:
            progress_callback(0, "Starting version information cleanup...")
        
        if not supervisor or not hasattr(supervisor, 'system_info'):
            error_msg = "Cannot access supervisor system_info"
            logger.error(error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            return
        
        if progress_callback:
            progress_callback(20, "Clearing HomeAssistant version information...")
        
        # Clear HomeAssistant version information
        if hasattr(supervisor.system_info, 'hainfo'):
            ha_info = supervisor.system_info.hainfo
            ha_info.config = ""
            ha_info.python = ""
            ha_info.core = ""
            ha_info.otbr = ""
            ha_info.z2m = ""
            # Reset installation status
            ha_info.installed = False
            ha_info.enabled = False
            logger.info("HomeAssistant version information cleared")
        
        if progress_callback:
            progress_callback(60, "Clearing OpenHAB version information...")
        
        # Clear OpenHAB version information
        if hasattr(supervisor.system_info, 'openhabinfo'):
            openhab_info = supervisor.system_info.openhabinfo
            openhab_info.version = ""
            # Reset installation status
            openhab_info.installed = False
            openhab_info.enabled = False
            logger.info("OpenHAB version information cleared")
        
        if progress_callback:
            progress_callback(90, "Version information cleanup completed...")
        
        logger.info("Software update notification processed - all version information cleared")
        
        # 调用SystemInfoUpdater更新软件状态和LED
        if supervisor and hasattr(supervisor, 'sysinfo_update'):
            supervisor.sysinfo_update.update_software_status_and_led()
        else:
            logger.warning("Supervisor missing sysinfo_update, cannot update software status and LED.")
        
        if complete_callback:
            complete_callback(True, "Version information cleared successfully")
        if progress_callback:
            progress_callback(100, "Software update notification completed successfully")
        
    except Exception as e:
        error_msg = f"Error during version information cleanup: {e}"
        logger.error(error_msg, exc_info=True)
        if complete_callback:
            complete_callback(False, error_msg)

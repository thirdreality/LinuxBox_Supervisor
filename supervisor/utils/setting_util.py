# maintainer: guoping.liu@3reality.com

import os
import time
import logging
import subprocess
import glob
import tempfile
import shutil
import json
import fnmatch
from datetime import datetime
from .wifi_utils import get_current_wifi_info
import base64
from .util import force_sync

from ..const import BACKUP_STORAGE_MODE, BACKUP_INTERNAL_PATH, BACKUP_EXTERNAL_PATH
from supervisor.sysinfo import SystemInfoUpdater
import sqlite3

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

# Files and directories to exclude from backup (patterns)
BACKUP_EXCLUDE_PATTERNS = [
    "*.log",           # All log files
    "*.log.*",         # Rotated log files (e.g. .log.1, .log.gz)
    "*.tmp",           # Temporary files
    "*.cache",         # Cache files
    "cache/",          # Cache directories
    "logs/",           # Log directories
    "log/",            # Log directories (single log folder)
    "temp/",           # Temporary directories
    "tmp/",            # Temporary directories
    "__pycache__/",    # Python cache directories
    "*.pyc",           # Python compiled files
    "*.pid",           # Process ID files
    "*.sock",          # Socket files
    "*.db-wal",        # SQLite WAL files
    "*.db-shm",        # SQLite SHM files
    "*.backup",        # Backup files
    "*.bak"            # Backup files
]

# Configuration directory for restore records
RESTORE_RECORD_DIR = "/usr/lib/thirdreality/conf"

logger = logging.getLogger("Supervisor")

def _should_exclude_file(file_path, exclude_patterns):
    """
    Check if a file should be excluded from backup
    """
    file_name = os.path.basename(file_path)
    rel_path = file_path
    
    for pattern in exclude_patterns:
        # Check filename match
        if fnmatch.fnmatch(file_name, pattern):
            return True
        # Check relative path match
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Check if path contains directory pattern
        if pattern.endswith('/') and ('/' + pattern.rstrip('/') + '/') in ('/' + rel_path + '/'):
            return True
    
    return False

def _clean_directory_for_backup(source_dir, dest_dir, exclude_patterns):
    """
    Copy directory contents to destination while excluding unwanted files
    Returns the number of cleaned files and their size
    """
    cleaned_files_count = 0
    cleaned_size = 0
    
    if not os.path.exists(source_dir):
        return cleaned_files_count, cleaned_size
    
    for root, dirs, files in os.walk(source_dir):
        # Calculate path relative to source directory
        rel_root = os.path.relpath(root, source_dir)
        if rel_root == '.':
            rel_root = ''
        
        # Check if directories should be excluded
        dirs_to_remove = []
        for dir_name in dirs:
            rel_dir_path = os.path.join(rel_root, dir_name) if rel_root else dir_name
            if _should_exclude_file(rel_dir_path + '/', exclude_patterns):
                dirs_to_remove.append(dir_name)
                logger.info(f"Excluding directory from backup: {rel_dir_path}/")
        
        # Remove excluded directories from dirs list so os.walk won't traverse them
        for dir_name in dirs_to_remove:
            dirs.remove(dir_name)
        
        # Create target directory structure
        target_root = os.path.join(dest_dir, rel_root) if rel_root else dest_dir
        if not os.path.exists(target_root):
            os.makedirs(target_root, exist_ok=True)
        
        # Process files
        for file_name in files:
            rel_file_path = os.path.join(rel_root, file_name) if rel_root else file_name
            source_file = os.path.join(root, file_name)
            
            if _should_exclude_file(rel_file_path, exclude_patterns):
                # Calculate size of excluded files
                try:
                    file_size = os.path.getsize(source_file)
                    cleaned_size += file_size
                    cleaned_files_count += 1
                    logger.info(f"Excluding file from backup: {rel_file_path} ({file_size} bytes)")
                except OSError:
                    pass
            else:
                # Copy file
                target_file = os.path.join(target_root, file_name)
                try:
                    shutil.copy2(source_file, target_file)
                except Exception as e:
                    logger.warning(f"Failed to copy file {source_file} to {target_file}: {e}")
    
    return cleaned_files_count, cleaned_size

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

def run_setting_update_z2m_mqtt(config: dict, progress_callback=None, complete_callback=None):
    """
    更新 Zigbee2MQTT 的 MQTT 连接配置。

    期望的 config 字段：
      - base_topic
      - server (例如 mqtt://localhost:1883)
      - user
      - password
      - client_id

    步骤：校验参数 → 停止 zigbee2mqtt → 备份并写入 /opt/zigbee2mqtt/data/configuration.yaml → 同步 → 启动 zigbee2mqtt
    """
    def _call_progress(percent: int, message: str):
        try:
            if progress_callback:
                progress_callback(percent, message)
        except Exception:
            pass

    try:
        _call_progress(0, "Validating parameters")

        required_keys = ["base_topic", "server", "user", "password", "client_id"]
        missing = [k for k in required_keys if not config.get(k)]
        if missing:
            msg = f"Missing required fields: {','.join(missing)}"
            logger.error(msg)
            if complete_callback:
                complete_callback(False, msg)
            return

        z2m_data_path = "/opt/zigbee2mqtt/data"
        config_path = os.path.join(z2m_data_path, "configuration.yaml")

        # 停止服务
        _call_progress(10, "Stopping zigbee2mqtt service...")
        try:
            subprocess.run(["systemctl", "stop", "zigbee2mqtt.service"], check=False)
        except Exception as e:
            logger.warning(f"Failed to stop zigbee2mqtt: {e}")

        # 确保目录存在
        _call_progress(20, "Preparing configuration directory")
        try:
            os.makedirs(z2m_data_path, exist_ok=True)
        except Exception as e:
            msg = f"Failed to create {z2m_data_path}: {e}"
            logger.error(msg)
            if complete_callback:
                complete_callback(False, msg)
            return

        # 备份旧配置
        _call_progress(30, "Backing up existing configuration if present")
        try:
            if os.path.exists(config_path):
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                shutil.copy2(config_path, f"{config_path}.{ts}.bak")
        except Exception as e:
            logger.warning(f"Failed to backup existing configuration: {e}")

        # 准备基础模板
        default_template = "/lib/thirdreality/conf/configuration_blz.yaml.default"
        if not os.path.exists(config_path):
            _call_progress(40, "Installing default configuration as base")
            try:
                if os.path.exists(default_template):
                    shutil.copy2(default_template, config_path)
                else:
                    # 如果默认模板不存在，至少创建一个最小文件
                    with open(config_path, "w") as f:
                        f.write("version: 4\n")
            except Exception as e:
                msg = f"Failed to install default configuration: {e}"
                logger.error(msg)
                if complete_callback:
                    complete_callback(False, msg)
                return

        # 写入：仅替换 mqtt 块和 homeassistant.enabled
        _call_progress(50, "Updating mqtt section and homeassistant.enabled")
        try:
            with open(config_path, "r") as f:
                lines = f.read().splitlines()

            def replace_block(lines, header_key, kv_map):
                n = len(lines)
                i = 0
                found = False
                header_index = -1
                indent = ""
                while i < n:
                    line = lines[i]
                    stripped = line.lstrip()
                    if stripped.startswith(f"{header_key}:") and (len(stripped) == len(header_key)+1):
                        found = True
                        header_index = i
                        indent = line[:len(line) - len(stripped)]
                        break
                    i += 1

                if not found:
                    # 追加一个新块到文件末尾，保持一个空行分隔
                    if lines and lines[-1].strip() != "":
                        lines.append("")
                    lines.append(f"{header_key}:")
                    indent = ""
                    header_index = len(lines) - 1
                    # 在末尾插入键值
                    for k, v in kv_map.items():
                        lines.append(f"  {k}: {v}")
                    return lines

                # 找到块边界：直到遇到非空行且缩进小于等于 header 的同级或更小缩进且不是空白
                j = header_index + 1
                block_lines = []
                while j < n:
                    lj = lines[j]
                    if lj.strip() == "":
                        block_lines.append(lj)
                        j += 1
                        continue
                    # 如果缩进小于等于 header 的缩进，说明块结束
                    if len(lj) - len(lj.lstrip()) <= len(indent):
                        break
                    block_lines.append(lj)
                    j += 1

                # 将块解析为字典形式（仅处理一级键: 值）
                existing = {}
                kv_indent = indent + "  "
                new_block = []
                keys_to_write = set(kv_map.keys())

                # 先遍历原有行，替换我们关心的键，保留其他行
                for bl in block_lines:
                    if bl.strip() == "":
                        new_block.append(bl)
                        continue
                    if not bl.startswith(kv_indent):
                        new_block.append(bl)
                        continue
                    content = bl[len(kv_indent):]
                    if ":" in content:
                        k = content.split(":", 1)[0].strip()
                        if k in kv_map:
                            new_block.append(f"{kv_indent}{k}: {kv_map[k]}")
                            if k in keys_to_write:
                                keys_to_write.remove(k)
                        else:
                            new_block.append(bl)
                    else:
                        new_block.append(bl)

                # 对缺失的键进行追加
                for k in keys_to_write:
                    new_block.append(f"{kv_indent}{k}: {kv_map[k]}")

                # 重组
                return lines[:header_index+1] + new_block + lines[j:]

            # 更新 mqtt 块
            lines = replace_block(
                lines,
                "mqtt",
                {
                    "base_topic": config['base_topic'],
                    "server": config['server'],
                    "user": config['user'],
                    "password": config['password'],
                    "client_id": config['client_id'],
                }
            )

            # 更新 homeassistant.enabled
            enable_ha = "localhost" in config['server']
            lines = replace_block(
                lines,
                "homeassistant",
                {"enabled": "true" if enable_ha else "false"}
            )

            with open(config_path, "w") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            msg = f"Failed to update configuration: {e}"
            logger.error(msg, exc_info=True)
            if complete_callback:
                complete_callback(False, msg)
            return

        # 同步到存储
        _call_progress(70, "Syncing data to storage")
        try:
            force_sync()
        except Exception:
            pass

        # 启动服务
        _call_progress(90, "Starting zigbee2mqtt service...")
        try:
            subprocess.run(["systemctl", "enable", "zigbee2mqtt.service"], check=False)
            subprocess.run(["systemctl", "start", "zigbee2mqtt.service"], check=False)
        except Exception as e:
            logger.warning(f"Failed to start zigbee2mqtt: {e}")

        _call_progress(100, "Zigbee2MQTT MQTT configuration updated")
        if complete_callback:
            complete_callback(True, "success")
    except Exception as e:
        msg = f"Unexpected error during z2m-mqtt update: {e}"
        logger.error(msg, exc_info=True)
        if complete_callback:
            complete_callback(False, msg)
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

        # Before copying data, shrink Home Assistant SQLite DB if too large
        try:
            ha_db_path = "/var/lib/homeassistant/homeassistant/home-assistant_v2.db"
            size_threshold_bytes = 10 * 1024 * 1024  # 10MB
            if os.path.exists(ha_db_path):
                db_size = os.path.getsize(ha_db_path)
                if db_size > size_threshold_bytes:
                    logging.warning(f"Home Assistant DB size {db_size/1024/1024:.2f}MB exceeds 10MB. Purging DB before backup...")
                    _call_progress(32, "Purging Home Assistant database before backup...")
                    try:
                        # Connect and cleanup
                        conn = sqlite3.connect(ha_db_path, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        conn.execute("PRAGMA busy_timeout=30000;")
                        cur = conn.cursor()
                        # Purge all rows as requested
                        cur.execute("DELETE FROM events;")
                        cur.execute("DELETE FROM states;")
                        cur.execute("DELETE FROM statistics;")
                        cur.execute("DELETE FROM statistics_short_term;")
                        conn.commit()
                        # Checkpoint WAL to reduce file size before VACUUM
                        try:
                            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                        except Exception:
                            pass
                        conn.commit()
                        conn.close()
                        # VACUUM may need free space; try normal first
                        try:
                            _call_progress(34, "VACUUM Home Assistant database...")
                            conn2 = sqlite3.connect(ha_db_path, timeout=60)
                            conn2.execute("VACUUM;")
                            conn2.close()
                        except Exception as e_vac:
                            logging.warning(f"VACUUM failed: {e_vac}")
                        logging.info("Home Assistant database cleanup finished.")
                    except Exception as e_db:
                        logging.warning(f"Failed to cleanup Home Assistant DB before backup: {e_db}")
                else:
                    logging.info("Home Assistant DB under threshold; skipping cleanup.")
        except Exception as e:
            logging.warning(f"Error during pre-backup DB cleanup stage: {e}")
        
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

        # --- New packaging process (with file cleanup) ---
        import tempfile
        _call_progress(37, "Preparing temporary directory for backup.")
        
        total_cleaned_files = 0
        total_cleaned_size = 0
        
        with tempfile.TemporaryDirectory(prefix="setting_backup_") as temp_backup_dir:
            # 1. Copy all directories to be backed up to temp directory (excluding unwanted files)
            for src_path, name in BACKUP_DIRS_CONFIG:
                if os.path.exists(src_path):
                    dest_path = os.path.join(temp_backup_dir, name)
                    if os.path.isdir(src_path):
                        # Use new cleanup function to copy directory
                        cleaned_count, cleaned_size = _clean_directory_for_backup(src_path, dest_path, BACKUP_EXCLUDE_PATTERNS)
                        total_cleaned_files += cleaned_count
                        total_cleaned_size += cleaned_size
                        logger.info(f"Directory {src_path} copied to backup with {cleaned_count} files excluded ({cleaned_size} bytes saved)")
                    elif os.path.isfile(src_path):
                        # Check if single file should be excluded
                        if not _should_exclude_file(os.path.basename(src_path), BACKUP_EXCLUDE_PATTERNS):
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            shutil.copy2(src_path, dest_path)
                        else:
                            file_size = os.path.getsize(src_path)
                            total_cleaned_files += 1
                            total_cleaned_size += file_size
                            logger.info(f"File {src_path} excluded from backup ({file_size} bytes saved)")
            
            if total_cleaned_files > 0:
                logger.info(f"Backup cleanup summary: {total_cleaned_files} files excluded, {total_cleaned_size / 1024 / 1024:.2f} MB saved")
                _call_progress(39, f"File cleanup completed: {total_cleaned_files} files excluded, {total_cleaned_size / 1024 / 1024:.2f} MB saved")
            # 2. Write service_states.json
            service_states_path = os.path.join(temp_backup_dir, "service_states.json")
            with open(service_states_path, 'w') as f:
                json.dump(original_service_states, f, indent=2)
            # Collect and write network_states.json
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
            # 3. Package entire temporary directory contents
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
        
        # Create restore record for the backup to prevent auto-restore
        if backup_archive_created:
            backup_filename = os.path.basename(backup_filepath)
            _create_restore_record(backup_filename, True)
            logging.info(f"Restore record created for backup {backup_filename} to prevent auto-restore")
        
        # Call completion callback and final progress only after all work is done
        if complete_callback:
            complete_callback(True, "success")
        _call_progress(100, "System settings backup completed successfully.")


def run_setting_local_backup(progress_callback=None, complete_callback=None):
    """
    Run backup forcing internal storage (local) with minimal code duplication by
    temporarily overriding BACKUP_STORAGE_MODE within this module's scope.
    """
    global BACKUP_STORAGE_MODE
    prev_mode = BACKUP_STORAGE_MODE
    try:
        BACKUP_STORAGE_MODE = "internal"
        return run_setting_backup(progress_callback=progress_callback, complete_callback=complete_callback)
    finally:
        BACKUP_STORAGE_MODE = prev_mode

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

            logging.info("Update zigbee information ...")
            if os.path.exists("/srv/homeassistant/bin/home_assistant_zigbee_fix.sh"):
                if os.path.exists("/var/lib/homeassistant/zha.conf"):
                    os.remove("/var/lib/homeassistant/zha.conf")
                subprocess.run(["/srv/homeassistant/bin/home_assistant_zigbee_fix.sh"], capture_output=True, text=True)
            else:
                logging.warning("No homeassistant zigbee fix.sh found, skipping zigbee default config update.")

            logging.info("Force sync executed after data restoration.")
            force_sync()
            # Restore network connection
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
                        # Check current connection
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
                # Special handling: mosquitto must be enabled and started regardless of backup/original state
                if service == "mosquitto.service":
                    try:
                        logging.info("Forcing mosquitto.service to be enabled after restore.")
                        enable_result = subprocess.run(["systemctl", "enable", service], check=False, capture_output=True, text=True)
                        if enable_result.returncode == 0:
                            logging.info("mosquitto.service enabled successfully (post-restore).")
                        else:
                            logging.warning(f"Failed to enable mosquitto.service post-restore. RC: {enable_result.returncode}. Error: {enable_result.stderr.strip()}")
                    except Exception as e_m_enable:
                        logging.warning(f"Exception enabling mosquitto.service post-restore: {e_m_enable}")

                    try:
                        logging.info("Starting mosquitto.service after restore.")
                        start_result = subprocess.run(["systemctl", "start", service], check=False, capture_output=True, text=True)
                        if start_result.returncode == 0:
                            logging.info("mosquitto.service started successfully (post-restore).")
                        else:
                            logging.warning(f"Failed to start mosquitto.service post-restore. RC: {start_result.returncode}. Error: {start_result.stderr.strip()}")
                    except Exception as e_m_start:
                        logging.warning(f"Exception starting mosquitto.service post-restore: {e_m_start}")

                    current_progress_finally += progress_per_service_start
                    _call_progress(int(min(current_progress_finally,100)), f"Processed service restoration for {service} (forced enable/start).")
                    continue
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

def run_setting_local_restore(backup_file=None, progress_callback=None, complete_callback=None):
    """
    Run restore forcing internal storage (local) with minimal code duplication by
    temporarily overriding BACKUP_STORAGE_MODE within this module's scope.
    """
    global BACKUP_STORAGE_MODE
    prev_mode = BACKUP_STORAGE_MODE
    try:
        BACKUP_STORAGE_MODE = "internal"
        return run_setting_restore(backup_file=backup_file, progress_callback=progress_callback, complete_callback=complete_callback)
    finally:
        BACKUP_STORAGE_MODE = prev_mode

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
        
        # Call SystemInfoUpdater to update software status and LED
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

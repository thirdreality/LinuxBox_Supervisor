# maintainer: guoping.liu@3reality.com

import subprocess
import logging
import os
import json
import uuid
from datetime import datetime, timezone
import shutil
import urllib.request
import urllib.error

from supervisor.hardware import LedState
import threading
import time
from supervisor.token_manager import TokenManager
from .util import force_sync
from supervisor.ptest.blz_test import get_blz_info

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def _call_progress(progress_callback, percent, message):
    """Helper to log progress and call the callback if it exists."""
    logging.info(f"Progress ({percent}%): {message}")
    if progress_callback:
        progress_callback(percent, message)

class ZigbeePairingState:
    def __init__(self):
        self._lock = threading.Lock()
        self._is_pairing = False

    def is_pairing(self):
        with self._lock:
            return self._is_pairing

    def set_pairing(self, status: bool):
        with self._lock:
            self._is_pairing = status

pairing_state = ZigbeePairingState()

class ConfigError(Exception):
    """Custom exception for configuration errors."""
    pass

def _service_exists(service_name):
    """Check if a systemd service unit file exists."""
    try:
        # systemctl cat returns 0 if service unit exists, non-zero otherwise
        result = subprocess.run(["systemctl", "cat", service_name], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            logging.debug(f"Service unit '{service_name}' exists.")
            return True
        else:
            logging.warning(f"Service unit '{service_name}' does not exist or systemctl cat failed: {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        logging.error("systemctl command not found. Cannot check service existence.")
        # Depending on desired strictness, could raise an error here
        return False # Assume service doesn't exist if systemctl is missing
    except Exception as e:
        logging.error(f"Error checking existence of service '{service_name}': {e}")
        return False

def _check_if_z2m_configured():
    """Check if Home Assistant is already configured for Zigbee2MQTT mode."""
    config_entries_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.config_entries")
    if not os.path.exists(config_entries_path):
        logging.warning(f"{config_entries_path} not found. Cannot determine Z2M configuration status.")
        return False # Cannot confirm if file doesn't exist

    try:
        with open(config_entries_path, 'r') as f:
            config_data = json.load(f)
    except Exception as e:
        logging.error(f"Error reading {config_entries_path} for Z2M status check: {e}")
        return False # Error reading, assume not configured or indeterminate

    if 'data' not in config_data or 'entries' not in config_data['data']:
        logging.warning("Invalid format in config_entries file for Z2M status check.")
        return False

    entries = config_data['data']['entries']
    mqtt_is_configured_and_active = False
    zha_is_configured_and_active = False

    for entry in entries:
        domain = entry.get('domain')
        disabled_by = entry.get('disabled_by') # null or absent means not disabled

        if domain == 'mqtt' and disabled_by is None:
            # This is a basic check. A more specific check might look at entry data if available.
            mqtt_is_configured_and_active = True
        elif domain == 'zha' and disabled_by is None:
            zha_is_configured_and_active = True
            # If ZHA is active, it's definitely not exclusively Z2M mode for Zigbee.
            # We might still have MQTT for other purposes, but ZHA implies Zigbee is handled by ZHA.
            break
    
    # Z2M mode implies MQTT is active for Zigbee and ZHA is not active for Zigbee.
    is_z2m = mqtt_is_configured_and_active and not zha_is_configured_and_active
    logging.info(f"Z2M configuration status check: MQTT active = {mqtt_is_configured_and_active}, ZHA active = {zha_is_configured_and_active}. Is Z2M = {is_z2m}")
    return is_z2m

# Base path
BASE_PATH = "/var/lib/homeassistant"

def _get_info_from_zha_conf():
    zha_conf_path = os.path.join(BASE_PATH, "zha.conf")
    
    if not os.path.exists(zha_conf_path):
        raise ConfigError(f"Error: {zha_conf_path} does not exist")
    
    ieee = None
    radio_type = "zigate"  # Default to zigate if not specified
    
    try:
        with open(zha_conf_path, 'r') as f:
            for line in f:
                if "Device IEEE:" in line:
                    ieee = line.split("Device IEEE:")[1].strip()
                elif "Radio Type:" in line:
                    radio_type = line.split("Radio Type:")[1].strip()
    except Exception as e:
        raise ConfigError(f"Error reading {zha_conf_path}: {e}")
    
    if not ieee:
        raise ConfigError("Error: Could not find Device IEEE in zha.conf")
    
    print(f"Found IEEE: {ieee}, Radio Type: {radio_type}")
    return ieee, radio_type

def find_zigbee_coordinator(devices):
    """Find Zigbee Coordinator device(s) by connection, via_device_id, and identifier."""
    coordinators = []
    for device in devices:
        has_zigbee_connection = any(
            conn[0] == "zigbee"
            for conn in device.get("connections", [])
        )
        is_root_device = device.get("via_device_id") is None
        has_zha_identifier = any(
            identifier[0] == "zha"
            for identifier in device.get("identifiers", [])
        )
        if has_zigbee_connection and is_root_device and has_zha_identifier:
            coordinators.append(device)
    return coordinators

def _update_zha_config_entries(radio_type="zigate"):
    config_entries_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.config_entries")
    mqtt_entry_id = None
    
    if not os.path.exists(config_entries_path):
        raise ConfigError(f"Error: {config_entries_path} does not exist")
    
    try:
        with open(config_entries_path, 'r') as f:
            config_data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Error reading {config_entries_path}: {e}")
    
    # Check if entries exist
    if 'data' not in config_data or 'entries' not in config_data['data']:
        raise ConfigError("Error: Invalid format in config_entries file")
    
    entries = config_data['data']['entries']
    new_entries = []
    
    # Iterate through entries, remove mqtt related configs, keep others
    for entry in entries:
        if entry.get('domain') == 'mqtt':
            mqtt_entry_id = entry.get('entry_id')
            print(f"Removing MQTT configuration with entry_id: {mqtt_entry_id}")
            continue  # Skip this entry, equivalent to deletion
        new_entries.append(entry)
    
    # Check if ZHA configuration exists
    has_zha = False
    zha_entry_id = None
    for entry in new_entries:
        if entry.get('domain') == 'zha':
            has_zha = True
            zha_entry_id = entry.get('entry_id')
            print(f"ZHA configuration already exists with entry_id: {zha_entry_id}")
            break
    
    # If no ZHA configuration exists, add one
    if not has_zha:
        zha_entry_id = f"01{uuid.uuid4().hex.upper()[:24]}"
        now = datetime.now(timezone.utc).isoformat()
        if radio_type == "blz":
            zha_entry = {
                "created_at": now,
                "data": {
                    "device": {
                        "baudrate": 2000000,
                        "flow_control": None,
                        "path": "/dev/ttyAML3"
                    },
                    "radio_type": "blz"
                },
                "disabled_by": None,
                "discovery_keys": {},
                "domain": "zha",
                "entry_id": zha_entry_id,
                "minor_version": 1,
                "modified_at": now,
                "options": {},
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "source": "user",
                "subentries": [],
                "title": "/dev/ttyAML3",
                "unique_id": None,
                "version": 4
            }
        else:
            zha_entry = {
                "created_at": now,
                "data": {
                    "device": {
                        "baudrate": 115200,
                        "flow_control": None,
                        "path": "/dev/ttyAML3"
                    },
                    "radio_type": radio_type
                },
                "disabled_by": None,
                "discovery_keys": {},
                "domain": "zha",
                "entry_id": zha_entry_id,
                "minor_version": 1,
                "modified_at": now,
                "options": {},
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "source": "user",
                "subentries": [],
                "title": "/dev/ttyAML3",
                "unique_id": None,
                "version": 4
            }
        new_entries.append(zha_entry)
        print(f"Added ZHA configuration with entry_id: {zha_entry_id}")
    
    # Update entries
    config_data['data']['entries'] = new_entries
    
    # Write back to file
    try:
        with open(config_entries_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        print(f"Updated {config_entries_path}")
        # Force sync to flush NAND cache
        force_sync()
    except Exception as e:
        raise ConfigError(f"Error writing to {config_entries_path}: {e}")
    
    return mqtt_entry_id, zha_entry_id

def _update_zha_device_registry(mqtt_entry_id, zha_entry_id, ieee, radio_type="zigate"):
    device_registry_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.device_registry")
    print(f"_update_zha_device_registry zha_entry_id: {zha_entry_id}, mqtt_entry_id: {mqtt_entry_id}")

    if not os.path.exists(device_registry_path):
        raise ConfigError(f"Error: {device_registry_path} does not exist")
    
    try:
        with open(device_registry_path, 'r') as f:
            device_data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Error reading {device_registry_path}: {e}")
    
    # Check if devices exist
    if 'data' not in device_data or 'devices' not in device_data['data']:
        raise ConfigError("Error: Invalid format in device_registry file")
    
    devices = device_data['data']['devices']
    new_devices = []
    
    # Iterate through devices, remove those related to mqtt_entry_id or Zigbee2MQTT
    for device in devices:
        # Remove devices linked to MQTT entry_id
        if mqtt_entry_id and 'config_entries' in device:
            if mqtt_entry_id in device['config_entries']:
                print(f"Removing device linked to MQTT entry_id: [{device.get('name', 'Unknown device')} ]")
                continue
        
        # Remove devices with manufacturer "Zigbee2MQTT"
        if device.get('manufacturer') == 'Zigbee2MQTT':
            print(f"Removing Zigbee2MQTT device: [{device.get('name', 'Unknown device')} ]")
            continue
            
        new_devices.append(device)
    
    # 查找coordinator
    coordinators = find_zigbee_coordinator(new_devices)
    has_zha_coordinator = len(coordinators) > 0
    
    # 如果没有coordinator，添加
    if not has_zha_coordinator:
        now = datetime.now(timezone.utc).isoformat()
        if radio_type == "blz":
            blz_device = {
                "area_id": None,
                "config_entries": [zha_entry_id],
                "config_entries_subentries": {zha_entry_id: [None]},
                "configuration_url": None,
                "connections": [["zigbee", ieee]],
                "created_at": now,
                "disabled_by": None,
                "entry_type": None,
                "hw_version": None,
                "id": f"{uuid.uuid4().hex}",
                "identifiers": [["zha", ieee]],
                "labels": [],
                "manufacturer": "Bouffalo Lab",
                "model": "BL706",
                "model_id": None,
                "modified_at": now,
                "name_by_user": None,
                "name": "Bouffalo Lab BL706",
                "primary_config_entry": zha_entry_id,
                "serial_number": None,
                "sw_version": "0x00000000",
                "via_device_id": None
            }
            new_devices.append(blz_device)
            print(f"Added BLZ coordinator device with ZHA entry_id: {zha_entry_id}")
        else:
            zigate_device = {
                "area_id": None,
                "config_entries": [zha_entry_id],
                "config_entries_subentries": {zha_entry_id: [None]},
                "configuration_url": None,
                "connections": [["zigbee", ieee]],
                "created_at": now,
                "disabled_by": None,
                "entry_type": None,
                "hw_version": None,
                "id": f"{uuid.uuid4().hex}",
                "identifiers": [["zha", ieee]],
                "labels": [],
                "manufacturer": "ZiGate",
                "model": "ZiGate USB-TTL",
                "model_id": None,
                "modified_at": now,
                "name_by_user": None,
                "name": "ZiGate ZiGate USB-TTL",
                "primary_config_entry": zha_entry_id,
                "serial_number": None,
                "sw_version": "3.21",
                "via_device_id": None
            }
            new_devices.append(zigate_device)
            print(f"Added ZiGate device with ZHA entry_id: {zha_entry_id}")
    
    # Update devices
    device_data['data']['devices'] = new_devices
    device_data['data']['deleted_devices'] = []
    
    # Write back to file
    try:
        with open(device_registry_path, 'w') as f:
            json.dump(device_data, f, indent=2)
        print(f"Updated {device_registry_path}")
        # Force sync to flush NAND cache
        force_sync()
    except Exception as e:
        raise ConfigError(f"Error writing to {device_registry_path}: {e}")

def _update_zha_entity_registry():
    """Update the entity registry to remove all MQTT platform entities"""
    entity_registry_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.entity_registry")
    
    if not os.path.exists(entity_registry_path):
        print(f"Warning: {entity_registry_path} does not exist, skipping entity registry update")
        return
    
    try:
        with open(entity_registry_path, 'r') as f:
            entity_data = json.load(f)
    except Exception as e:
        print(f"Warning: Error reading {entity_registry_path}: {e}, skipping entity registry update")
        return
    
    # Check if entities exist
    if 'data' not in entity_data or 'entities' not in entity_data['data']:
        print("Warning: Invalid format in entity_registry file, skipping entity registry update")
        return
    
    entities = entity_data['data']['entities']
    new_entities = []
    removed_count = 0
    
    # Filter out all entities with platform="mqtt"
    for entity in entities:
        if entity.get('platform') == 'mqtt':
            removed_count += 1
            continue  # Skip this entity (remove it)
        new_entities.append(entity)
    
    if removed_count > 0:
        # Update entities
        entity_data['data']['entities'] = new_entities
        
        entity_data['data']['deleted_entities'] = []
        
        # Write back to file
        try:
            with open(entity_registry_path, 'w') as f:
                json.dump(entity_data, f, indent=2)
            print(f"Updated {entity_registry_path}: removed {removed_count} MQTT entities")
        except Exception as e:
            print(f"Warning: Error writing to {entity_registry_path}: {e}")

def _update_zigbee2mqtt_config_entries():
    """Update config entries to remove ZHA and ensure MQTT is configured"""
    config_entries_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.config_entries")
    zha_entry_id = None
    mqtt_entry_id = None
    
    if not os.path.exists(config_entries_path):
        raise ConfigError(f"Error: {config_entries_path} does not exist")
    
    try:
        with open(config_entries_path, 'r') as f:
            config_data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Error reading {config_entries_path}: {e}")
    
    # Check if entries exist
    if 'data' not in config_data or 'entries' not in config_data['data']:
        raise ConfigError("Error: Invalid format in config_entries file")
    
    entries = config_data['data']['entries']
    new_entries = []
    
    # First pass: Find ZHA and MQTT entries
    for entry in entries:
        if entry.get('domain') == 'zha':
            zha_entry_id = entry.get('entry_id')
            print(f"Found ZHA configuration with entry_id: {zha_entry_id}, will remove it")
            continue  # Skip this entry (remove it)
        elif entry.get('domain') == 'mqtt':
            mqtt_entry_id = entry.get('entry_id')
            print(f"Found MQTT configuration with entry_id: {mqtt_entry_id}")
        new_entries.append(entry)
    
    # If no MQTT configuration exists, add one
    if not mqtt_entry_id:
        # Generate a new entry_id
        # Format similar to: 01JWJ3XGKNCN35YTRYE0W9MCQE
        mqtt_entry_id = f"01{uuid.uuid4().hex.upper()[:24]}"
        now = datetime.now(timezone.utc).isoformat()
        mqtt_entry = {
            "created_at": now,
            "data": {
                "broker": "localhost",
                "password": "thirdreality",
                "port": 1883,
                "username": "thirdreality"
            },
            "disabled_by": None,
            "discovery_keys": {},
            "domain": "mqtt",
            "entry_id": mqtt_entry_id,
            "minor_version": 2,
            "modified_at": now,
            "options": {},
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "source": "user",
            "subentries": [],
            "title": "localhost",
            "unique_id": None,
            "version": 1
        }
        new_entries.append(mqtt_entry)
        print(f"Added MQTT configuration with entry_id: {mqtt_entry_id}")
    
    # Update entries
    config_data['data']['entries'] = new_entries
    
    # Write back to file
    try:
        with open(config_entries_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        print(f"Updated {config_entries_path}")
    except Exception as e:
        raise ConfigError(f"Error writing to {config_entries_path}: {e}")
    
    return zha_entry_id, mqtt_entry_id

def _update_zigbee2mqtt_device_registry(zha_entry_id, mqtt_entry_id):
    """Update device registry to remove ZHA devices and add Zigbee2MQTT Bridge if needed"""
    print(f"_update_zigbee2mqtt_device_registry zha_entry_id: {zha_entry_id}, mqtt_entry_id: {mqtt_entry_id}")
    device_registry_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.device_registry")
    
    if not os.path.exists(device_registry_path):
        raise ConfigError(f"Error: {device_registry_path} does not exist")
    
    try:
        with open(device_registry_path, 'r') as f:
            device_data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Error reading {device_registry_path}: {e}")
    
    # Check if devices exist
    if 'data' not in device_data or 'devices' not in device_data['data']:
        raise ConfigError("Error: Invalid format in device_registry file")
    
    devices = device_data['data']['devices']
    new_devices = []
    
    # Remove devices linked to ZHA entry_id if it exists
    # "manufacturer":"Zigbee2MQTT"
    has_z2m_bridge = False
    bridge_device_to_keep = None
    
    for device in devices:
        # Check if this is the Zigbee2MQTT Bridge
        if device.get('name') == "Zigbee2MQTT Bridge":
            if has_z2m_bridge:
                # This is a duplicate bridge, skip it
                print(f"Removing duplicate Zigbee2MQTT Bridge device: {device.get('id')}")
                continue
            else:
                # This is the first bridge we found, keep it
                has_z2m_bridge = True
                print("Found Zigbee2MQTT Bridge in registry")
                # Update the bridge to use the current MQTT entry_id
                device['config_entries'] = [mqtt_entry_id]
                device['config_entries_subentries'] = {mqtt_entry_id: [None]}
                device['primary_config_entry'] = mqtt_entry_id
                device['modified_at'] = datetime.now(timezone.utc).isoformat()
                print("Updated Zigbee2MQTT Bridge with current MQTT entry_id")
                bridge_device_to_keep = device
                new_devices.append(device)
                continue
            
        # Skip devices linked to ZHA if zha_entry_id exists
        if zha_entry_id and 'config_entries' in device:
            if zha_entry_id in device['config_entries']:
                print(f"Removing device linked to ZHA: [ {device.get('name', 'Unknown device')} ]")
                continue
        new_devices.append(device)
 
    # Update devices
    device_data['data']['devices'] = new_devices
    
    device_data['data']['deleted_devices'] = []

    # Write back to file
    try:
        with open(device_registry_path, 'w') as f:
            json.dump(device_data, f, indent=2)
        print(f"Updated {device_registry_path}")
    except Exception as e:
        raise ConfigError(f"Error writing to {device_registry_path}: {e}")

def _update_zigbee2mqtt_entity_registry():
    """Update entity registry to remove ZHA platform entities"""
    entity_registry_path = os.path.join(BASE_PATH, "homeassistant/.storage/core.entity_registry")
    
    if not os.path.exists(entity_registry_path):
        print(f"Warning: {entity_registry_path} does not exist, skipping entity registry update")
        return
    
    try:
        with open(entity_registry_path, 'r') as f:
            entity_data = json.load(f)
    except Exception as e:
        print(f"Warning: Error reading {entity_registry_path}: {e}")
        return
    
    # Check if entities exist
    if 'data' not in entity_data or 'entities' not in entity_data['data']:
        print("Warning: Invalid format in entity_registry file, skipping entity registry update")
        return
    
    entities = entity_data['data']['entities']
    new_entities = []
    removed_count = 0
    
    # Remove ZHA platform entities
    for entity in entities:
        if entity.get('platform') == 'zha':
            removed_count += 1
            continue
        new_entities.append(entity)
    
    if removed_count > 0:
        print(f"Removed {removed_count} ZHA platform entities from entity registry")
    
    # Update entities
    entity_data['data']['entities'] = new_entities
    
    entity_data['data']['deleted_entities'] = []
    # Write back to file
    try:
        with open(entity_registry_path, 'w') as f:
            json.dump(entity_data, f, indent=2)
        print(f"Updated {entity_registry_path}")
    except Exception as e:
        print(f"Warning: Error writing to {entity_registry_path}: {e}")

def _reset_zigbee2mqtt_configuration():
    """
    根据zha.conf的radio_type选择不同的zigbee2mqtt配置模板，拷贝到目标目录，并sync三次。
    """
    import shutil
    zha_conf_path = "/var/lib/homeassistant/zha.conf"
    conf_dir = "/lib/thirdreality/conf"
    z2m_data_path = "/opt/zigbee2mqtt/data"
    config_file = None
    radio_type = None
    try:
        if os.path.exists(zha_conf_path):
            # 读取最后一个有效的Radio Type
            with open(zha_conf_path, 'r') as f:
                lines = f.readlines()
            for line in reversed(lines):
                if "Radio Type:" in line:
                    radio_type = line.split("Radio Type:")[-1].strip()
                    break
        if radio_type == "blz":
            config_file = os.path.join(conf_dir, "configuration_blz.yaml.default")
            logging.info("Detected Radio Type: blz, using configuration_blz.yaml.default")
        elif radio_type == "zigate":
            config_file = os.path.join(conf_dir, "configuration_zigate.yaml.default")
            logging.info("Detected Radio Type: zigate, using configuration_zigate.yaml.default")
        else:
            config_file = os.path.join(conf_dir, "configuration_blz.yaml.default")
            if radio_type:
                logging.info(f"Unknown Radio Type: {radio_type}, defaulting to configuration_blz.yaml.default")
            else:
                logging.info("zha.conf not found or no Radio Type, defaulting to configuration_blz.yaml.default")
        if not os.path.exists(config_file):
            logging.warning(f"WARNING: Configuration file not found: {config_file}")
            return
        if not os.path.exists(z2m_data_path):
            os.makedirs(z2m_data_path, exist_ok=True)
            logging.info(f"Created {z2m_data_path} directory")
        dest_file = os.path.join(z2m_data_path, "configuration.yaml")
        shutil.copy2(config_file, dest_file)
        logging.info(f"Installed zigbee2mqtt configuration from {config_file} to {dest_file}")
        # Force sync to flush NAND cache
        force_sync()
    except Exception as e:
        logging.warning(f"Error resetting zigbee2mqtt configuration: {e}")

def _reset_blz_hardware():
    """
    如果脚本/srv/homeassistant/bin/home_assistant_blz_reset.sh存在，则执行该脚本。不输出任何日志。
    """
    logging.info("Resetting blz hardware...")
    script_path = "/srv/homeassistant/bin/home_assistant_blz_reset.sh"
    if os.path.exists(script_path):
        try:
            subprocess.run([script_path], check=False)
        except Exception:
            pass


def _restart_dongle():
    """
    Restart Zigbee dongle by resetting GPIO pins.
    Zigbee reset: DB_RSTN1/GPIOZ_1
    Zigbee boot: DB_BOOT1/GPIOZ_3
    """
    logging.info("Restarting Zigbee dongle...")
    try:
        # Reset Zigbee module GPIOZ_1/GPIOZ_3
        subprocess.run(["gpioset", "0", "3=1"], check=True)
        time.sleep(0.2)
        subprocess.run(["gpioset", "0", "3=0"], check=True)
        time.sleep(0.2)
        subprocess.run(["gpioset", "0", "1=1"], check=True)
        time.sleep(0.2)
        subprocess.run(["gpioset", "0", "1=0"], check=True)
        time.sleep(0.2)
        subprocess.run(["gpioset", "0", "1=1"], check=True)
        logging.info("Zigbee dongle restart completed successfully")
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Error executing Zigbee gpioset command: {e}"
        logging.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Error restarting Zigbee dongle: {e}"
        logging.error(error_msg)
        raise RuntimeError(error_msg)

def run_zigbee_switch_zha_mode(progress_callback=None, complete_callback=None):
    """
    Switch to ZHA mode.
    Manages Home Assistant service, updates configurations, and handles conflicting services.
    """
    logging.info("Attempting to switch to ZHA mode...")
    ha_service_was_running = False

    try:
        _call_progress(progress_callback, 5, "Fetching Zigbee device info...")
        try:
            ieee, radio_type = _get_info_from_zha_conf()
            if not ieee:
                logging.error("Could not find Device IEEE in zha.conf. Aborting switch to ZHA mode.")
                if complete_callback:
                    complete_callback(False, "no_ieee_found")
                return
            logging.info(f"Successfully fetched IEEE: {ieee} and Radio Type: {radio_type}")
        except ConfigError as e:
            logging.error(f"Configuration error while fetching Zigbee info: {e}")
            if complete_callback:
                complete_callback(False, f"config_error_fetch_info: {e}")
            return
        except Exception as e:
            logging.error(f"Unexpected error while fetching Zigbee info: {e}", exc_info=True)
            if complete_callback:
                complete_callback(False, f"unexpected_error_fetch_info: {e}")
            return

        _call_progress(progress_callback, 10, "Checking Home Assistant service status...")
        try:
            status_check = subprocess.run(["systemctl", "is-active", "home-assistant.service"], capture_output=True, text=True, check=False, timeout=15)
            if status_check.stdout.strip() == "active":
                ha_service_was_running = True
                logging.info("Home Assistant service is running. Stopping it temporarily.")
                _call_progress(progress_callback, 15, "Stopping Home Assistant service...")
                subprocess.run(["systemctl", "stop", "home-assistant.service"], check=True, timeout=60)
                logging.info("Home Assistant service stopped.")
            else:
                logging.info("Home Assistant service is not running or status unknown.")
        except subprocess.TimeoutExpired as e:
            logging.error(f"Timeout checking or stopping Home Assistant service: {e}")
            if complete_callback:
                complete_callback(False, f"ha_service_timeout: {e}")
            return
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to stop Home Assistant service: {e}")
            if complete_callback:
                complete_callback(False, f"ha_service_stop_error: {e}")
            return
        except FileNotFoundError:
            logging.error("systemctl command not found. Cannot manage Home Assistant service.")
            if complete_callback:
                complete_callback(False, "systemctl_not_found")
            return

        _call_progress(progress_callback, 20, "Updating ZHA config entries...")
        mqtt_entry_id, zha_entry_id = _update_zha_config_entries(radio_type)
        logging.info(f"ZHA config entries updated. MQTT Entry ID: {mqtt_entry_id}, ZHA Entry ID: {zha_entry_id}")

        _call_progress(progress_callback, 40, "Updating ZHA device registry...")
        _update_zha_device_registry(mqtt_entry_id, zha_entry_id, ieee, radio_type)
        logging.info("ZHA device registry updated.")

        _call_progress(progress_callback, 60, "Updating ZHA entity registry...")
        _update_zha_entity_registry()
        logging.info("ZHA entity registry updated.")

        _call_progress(progress_callback, 80, "Stopping and disabling conflicting services (zigbee2mqtt, mosquitto)...")
        services_to_manage = [("zigbee2mqtt.service", "Zigbee2MQTT"), ("mosquitto.service", "Mosquitto")]
        all_services_managed_successfully = True
        for service_file, service_name in services_to_manage:
            try:
                logging.info(f"Disabling {service_name} ({service_file})...")
                subprocess.run(["systemctl", "disable", service_file], check=True)
                logging.info(f"Stopping {service_name} ({service_file})...")
                subprocess.run(["systemctl", "stop", service_file], check=True)
                logging.info(f"{service_name} ({service_file}) disabled and stopped.")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logging.warning(f"Error managing {service_name} ({service_file}): {e}. Continuing...")
                all_services_managed_successfully = False
            except FileNotFoundError:
                logging.warning(f"systemctl not found. Cannot manage {service_name} ({service_file}).")
                all_services_managed_successfully = False
                break
        
        if not all_services_managed_successfully:
            logging.warning("One or more conflicting services could not be fully managed. Check logs.")

        _call_progress(progress_callback, 90, "Cleaning up Zigbee2MQTT data and resetting configuration...")
        z2m_data_path = "/opt/zigbee2mqtt/data"
        files_to_delete = [
            os.path.join(z2m_data_path, "database.db"),
            os.path.join(z2m_data_path, "state.json")
        ]
        dir_to_delete = os.path.join(z2m_data_path, "log")
        config_src = "/lib/thirdreality/conf/configuration.yaml.default"
        config_dest = os.path.join(z2m_data_path, "configuration.yaml")

        for f_path in files_to_delete:
            try:
                if os.path.exists(f_path):
                    os.remove(f_path)
                    logging.info(f"Successfully deleted Zigbee2MQTT file: {f_path}")
                    # 如果是database.db且radio_type为blz，执行reset
                    if f_path.endswith("database.db"):
                        try:
                            _, radio_type = _get_info_from_zha_conf()
                        except Exception:
                            radio_type = None
                        if radio_type == "blz":
                            _reset_blz_hardware()
                else:
                    logging.info(f"Zigbee2MQTT file not found, skipping deletion: {f_path}")
            except OSError as e:
                logging.warning(f"Error deleting Zigbee2MQTT file {f_path}: {e}")

        try:
            if os.path.exists(dir_to_delete):
                shutil.rmtree(dir_to_delete)
                logging.info(f"Successfully deleted Zigbee2MQTT directory: {dir_to_delete}")
            else:
                logging.info(f"Zigbee2MQTT directory not found, skipping deletion: {dir_to_delete}")
        except OSError as e:
            logging.warning(f"Error deleting Zigbee2MQTT directory {dir_to_delete}: {e}")

        try:
            if not os.path.exists(z2m_data_path):
                os.makedirs(z2m_data_path, exist_ok=True)
                logging.info(f"Created Zigbee2MQTT data directory: {z2m_data_path} as it did not exist.")

            if os.path.exists(config_src):
                shutil.copy2(config_src, config_dest)
                logging.info(f"Successfully copied default Zigbee2MQTT configuration to {config_dest}")
            else:
                logging.warning(f"Default Zigbee2MQTT configuration source file not found, cannot copy: {config_src}")
        except OSError as e:
            logging.warning(f"Error copying default Zigbee2MQTT configuration from {config_src} to {config_dest}: {e}")
        except Exception as e:
            logging.warning(f"Unexpected error during Zigbee2MQTT configuration reset: {e}")

        _call_progress(progress_callback, 100, "Successfully switched to ZHA mode.")
        logging.info("Successfully switched to ZHA mode.")
        if complete_callback:
            complete_callback(True, "success")

    except ConfigError as e:
        logging.error(f"Configuration error during ZHA mode switch: {e}")
        _call_progress(progress_callback, 100, f"Failed: Configuration error - {e}")
        if complete_callback:
            complete_callback(False, f"config_error: {e}")
    except subprocess.TimeoutExpired as e:
        logging.error(f"A system command timed out during ZHA mode switch: {e}")
        _call_progress(progress_callback, 100, f"Failed: System command timeout - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_timeout: {e}")
    except subprocess.CalledProcessError as e:
        logging.error(f"System command failed during ZHA mode switch: {e.cmd} returned {e.returncode}")
        _call_progress(progress_callback, 100, f"Failed: System command error - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_failed: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during ZHA mode switch: {e}", exc_info=True)
        _call_progress(progress_callback, 100, f"Failed: Unexpected error - {e}")
        if complete_callback:
            complete_callback(False, f"unexpected_error: {e}")
    finally:
        if ha_service_was_running:
            logging.info("Restoring Home Assistant service state as it was running before...")
            try:
                subprocess.run(["systemctl", "start", "home-assistant.service"], check=True, timeout=60)
                logging.info("Home Assistant service started successfully.")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logging.error(f"CRITICAL: Failed to restart Home Assistant service: {e}. Manual intervention may be required.")
            except FileNotFoundError:
                logging.error("CRITICAL: systemctl not found. Cannot restart Home Assistant service. Manual intervention may be required.")
        # Force sync to flush NAND cache
        try:
            force_sync()
            logging.info("Force sync executed after ZHA mode switch.")
        except Exception as e:
            logging.error(f"Force sync failed after ZHA mode switch: {e}")

def run_zigbee_switch_z2m_mode(progress_callback=None, complete_callback=None):
    """
    Switch to Zigbee2MQTT (Z2M) mode.
    Manages Home Assistant service, checks/updates configurations, and handles Z2M services.
    """
    logging.info("Attempting to switch to Zigbee2MQTT mode...")
    ha_service_was_running = False

    try:
        _call_progress(progress_callback, 5, "Checking prerequisite services (Mosquitto, Zigbee2MQTT)...")
        required_services = ["mosquitto.service", "zigbee2mqtt.service"]
        for service_name in required_services:
            if not _service_exists(service_name):
                error_msg = f"Prerequisite service '{service_name}' not found. Cannot switch to Z2M mode."
                logging.error(error_msg)
                _call_progress(progress_callback, 100, f"Failed: {error_msg}")
                if complete_callback:
                    complete_callback(False, f"prerequisite_service_missing: {service_name}")
                return
        logging.info("Prerequisite services found.")

        _call_progress(progress_callback, 10, "Checking Home Assistant service status...")
        try:
            status_check = subprocess.run(["systemctl", "is-active", "home-assistant.service"], capture_output=True, text=True, check=False)
            if status_check.stdout.strip() == "active":
                ha_service_was_running = True
                logging.info("Home Assistant service is running. Stopping it temporarily.")
                _call_progress(progress_callback, 15, "Stopping Home Assistant service...")
                subprocess.run(["systemctl", "stop", "home-assistant.service"], check=True)
                logging.info("Home Assistant service stopped.")
            else:
                logging.info("Home Assistant service is not running or status unknown.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to stop Home Assistant service: {e}")
            _call_progress(progress_callback, 100, f"Failed: HA service stop error - {e}")
            if complete_callback:
                complete_callback(False, f"ha_service_stop_error: {e}")
            return
        except FileNotFoundError:
            logging.error("systemctl command not found. Cannot manage Home Assistant service.")
            _call_progress(progress_callback, 100, "Failed: systemctl not found")
            if complete_callback:
                complete_callback(False, "systemctl_not_found")
            return

        _call_progress(progress_callback, 20, "Checking if already in Z2M mode...")
        if _check_if_z2m_configured():
            logging.info("System is already configured for Zigbee2MQTT mode. Skipping configuration updates.")
            _call_progress(progress_callback, 60, "Already in Z2M mode. Ensuring services are active.")
        else:
            _call_progress(progress_callback, 25, "Not in Z2M mode or status unclear. Proceeding with configuration updates.")
            logging.info("Updating Home Assistant configurations for Z2M mode...")
            
            _call_progress(progress_callback, 30, "Updating Z2M config entries...")
            zha_entry_id, mqtt_entry_id = _update_zigbee2mqtt_config_entries()
            logging.info(f"Z2M config entries updated. MQTT Entry ID: {mqtt_entry_id}, ZHA Entry ID targeted for removal: {zha_entry_id}")
            # Force sync to flush NAND cache
            force_sync()

            _call_progress(progress_callback, 50, "Updating Z2M device registry...")
            _update_zigbee2mqtt_device_registry(zha_entry_id, mqtt_entry_id)
            logging.info("Z2M device registry updated.")
            # Force sync to flush NAND cache
            force_sync()

            _call_progress(progress_callback, 60, "Updating Z2M entity registry...")
            _update_zigbee2mqtt_entity_registry()
            logging.info("Z2M entity registry updated.")
            # Force sync to flush NAND cache
            force_sync()

            # 新增：重置configuration.yaml
            _reset_zigbee2mqtt_configuration()

        _call_progress(progress_callback, 70, "Process zigbee dongle ...")
        # Delete HomeAssistant zigbee database file before final success
        zigbee_db_path = "/var/lib/homeassistant/homeassistant/zigbee.db"
        try:
            if os.path.exists(zigbee_db_path):
                os.remove(zigbee_db_path)
                logging.info(f"Successfully deleted HomeAssistant zigbee database: {zigbee_db_path}")
                # radio_type判断，复用_get_info_from_zha_conf
                try:
                    _, radio_type = _get_info_from_zha_conf()
                except Exception:
                    radio_type = None
                if radio_type == "blz":
                    _reset_blz_hardware()
                    _restart_dongle()
            else:
                logging.info(f"HomeAssistant zigbee database not found, skipping deletion: {zigbee_db_path}")
        except OSError as e:
            logging.warning(f"Error deleting HomeAssistant zigbee database {zigbee_db_path}: {e}")

        _call_progress(progress_callback, 80, "Starting and enabling Z2M services (Mosquitto, Zigbee2MQTT)...")
        all_services_managed_successfully = True
        for service_file, service_name in [("mosquitto.service", "Mosquitto"), ("zigbee2mqtt.service", "Zigbee2MQTT")]:
            try:
                logging.info(f"Enabling {service_name} ({service_file})...")
                subprocess.run(["systemctl", "enable", service_file], check=True)
                logging.info(f"Starting {service_name} ({service_file})...")
                subprocess.run(["systemctl", "start", service_file], check=True)
                logging.info(f"{service_name} ({service_file}) enabled and started.")
            except subprocess.CalledProcessError as e:
                logging.error(f"Error managing {service_name} ({service_file}): {e}. This might affect Z2M functionality.")
                all_services_managed_successfully = False # Mark that not all services were perfectly managed
            except FileNotFoundError:
                logging.error(f"systemctl not found. Cannot manage {service_name} ({service_file}).")
                all_services_managed_successfully = False
                break # If systemctl is gone, no point trying further services
        
        if not all_services_managed_successfully:
            logging.warning("One or more Z2M services could not be properly started/enabled. Check logs.")
            # Decide if this is a partial success or failure for complete_callback
            # For now, continue and report overall success if other steps passed.

        _call_progress(progress_callback, 100, "Successfully switched to Z2M mode (or confirmed existing Z2M mode).")
        logging.info("Successfully switched to Zigbee2MQTT mode.")
        if complete_callback:
            complete_callback(True, "success_z2m_mode_set_or_confirmed")

    except ConfigError as e:
        logging.error(f"Configuration error during Z2M mode switch: {e}")
        _call_progress(progress_callback, 100, f"Failed: Configuration error - {e}")
        if complete_callback:
            complete_callback(False, f"config_error_z2m: {e}")
    except subprocess.CalledProcessError as e:
        logging.error(f"System command failed during Z2M mode switch: {e.cmd} returned {e.returncode} with output: {e.output} and stderr: {e.stderr}")
        _call_progress(progress_callback, 100, f"Failed: System command error - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_failed_z2m: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during Z2M mode switch: {e}", exc_info=True)
        _call_progress(progress_callback, 100, f"Failed: Unexpected error - {e}")
        if complete_callback:
            complete_callback(False, f"unexpected_error_z2m: {e}")
    finally:
        if ha_service_was_running:
            logging.info("Restoring Home Assistant service state as it was running before...")
            try:
                subprocess.run(["systemctl", "start", "home-assistant.service"], check=True)
                logging.info("Home Assistant service started successfully.")
            except subprocess.CalledProcessError as e:
                logging.error(f"CRITICAL: Failed to restart Home Assistant service: {e}. Manual intervention may be required.")
            except FileNotFoundError:
                logging.error("CRITICAL: systemctl not found. Cannot restart Home Assistant service. Manual intervention may be required.")
        # Force sync to flush NAND cache
        try:
            force_sync()
            logging.info("Force sync executed after Z2M mode switch.")
        except Exception as e:
            logging.error(f"Force sync failed after Z2M mode switch: {e}")

def get_ha_zigbee_mode(config_file="/var/lib/homeassistant/homeassistant/.storage/core.config_entries"):
    """
    Check the current Zigbee mode of HomeAssistant.
    - If "domain": "mqtt" is found, return 'z2m' 
    - If "domain": "zha" is found, return 'zha'
    - If neither is found, return 'none'
    """
    attempts = 0
    max_attempts = 3
    retry_delay_seconds = 0.5

    while attempts < max_attempts:
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
        except (FileNotFoundError, IOError, json.JSONDecodeError) as e:
            attempts += 1
            logging.warning(f"Attempt {attempts}/{max_attempts} failed to read/parse {config_file}: {e}")
            if attempts < max_attempts:
                time.sleep(retry_delay_seconds)
            else:
                logging.error(f"All {max_attempts} attempts to read/parse {config_file} failed.")
                return 'none'
        except Exception as e: # Catch any other unexpected errors
            logging.error(f"An unexpected error occurred while reading HomeAssistant config_entries: {e}")
            return 'none'
    return 'none' # Should be unreachable if logic is correct, but as a fallback

def _start_pairing_led_timer(led_controller, duration):
    """Starts a timer in a daemon thread to manage the LED state for pairing."""
    if not led_controller:
        pairing_state.set_pairing(False) # Ensure state is reset even without LED
        return

    def timer_task():
        logging.info(f"Zigbee pairing LED timer started for {duration} seconds.")
        time.sleep(duration)
        # Check if the state is still pairing before turning it off
        led_controller.set_led_state(LedState.SYS_DEVICE_PAIRED)
        pairing_state.set_pairing(False)
        logging.info("Zigbee pairing timer finished and state reset.")

    timer_thread = threading.Thread(target=timer_task, daemon=True)
    timer_thread.start()

def run_mqtt_pairing(progress_callback=None, led_controller=None) -> bool:
    """ 
    Start the MQTT pairing process by enabling Zigbee joining via zigbee2mqtt.
    """
    services_to_check = ["mosquitto.service", "zigbee2mqtt.service"]
    all_services_active = True

    try:
        _call_progress(progress_callback, 0, "Starting zigbee2mqtt pairing process (Zigbee permit join).")

        for i, service in enumerate(services_to_check):
            progress_step = 10 + (i * 10) # Progress from 10% to 30% for checks
            _call_progress(progress_callback, progress_step, f"Checking status of {service}.")
            try:
                subprocess.run(["systemctl", "is-active", "--quiet", service], check=True)
                logging.info(f"Service {service} is active.")
            except subprocess.CalledProcessError:
                logging.warning(f"Service {service} is not active. zigbee2mqtt pairing cannot proceed.")
                all_services_active = False
                _call_progress(progress_callback, 100, f"Service {service} not active. zigbee2mqtt pairing aborted.")
                return False
            except FileNotFoundError:
                logging.error(f"systemctl command not found. Cannot check service {service}.")
                all_services_active = False
                _call_progress(progress_callback, 100, f"systemctl not found. Pairing aborted.")
                return False

        if all_services_active:
            _call_progress(progress_callback, 50, "All required services active. Attempting to enable Zigbee joining.")
            pairing_command = [
                "/usr/bin/mosquitto_pub",
                "-h", "localhost",
                "-t", "zigbee2mqtt/bridge/request/permit_join",
                "-m", f'{{"time": {PERMIT_JOIN_DURATION}}}',
                "-u", "thirdreality",
                "-P", "thirdreality"
            ]
            logging.info(f"Executing zigbee2mqtt pairing command: {' '.join(pairing_command)}")
            result = subprocess.run(pairing_command, check=True, capture_output=True, text=True)
            logging.info(f"zigbee2mqtt pairing enabled successfully via MQTT: {result.stdout.strip()}")
            _call_progress(progress_callback, 100, "zigbee2mqtt pairing successfully enabled.")
            return True

    except subprocess.CalledProcessError as e:
        err_msg = f"Failed to enable zigbee2mqtt pairing: {e.stderr.strip()}"
        logging.error(err_msg)
        _call_progress(progress_callback, 100, err_msg)
        return False
    except Exception as e:
        err_msg = f"An unexpected error occurred during MQTT pairing: {str(e)}"
        logging.error(err_msg)
        _call_progress(progress_callback, 100, err_msg)
        return False

def run_zha_pairing(progress_callback=None, led_controller=None) -> bool:
    """
    Start the ZHA pairing process by calling the Home Assistant API.
    Uses TokenManager to get the Bearer token.
    """
    token_manager = TokenManager()
    bearer_token = None

    def _call_progress(percent, message):
        logging.info(f"ZHA Pairing progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent, message)

    try:
        _call_progress(10, "Starting ZHA pairing process.")

        # 1. Get Bearer token using TokenManager
        _call_progress(20, "Getting token from TokenManager.")
        bearer_token = token_manager.get_access_token()
        
        if not bearer_token:
            err_msg = "Failed to get Bearer token from TokenManager."
            logging.error(err_msg)
            _call_progress(100, err_msg)
            return False
        _call_progress(25, "Bearer token retrieved successfully.")

        # 2. Prepare and send the request
        _call_progress(40, "Preparing ZHA pairing request.")
        url = "http://localhost:8123/api/services/zha/permit"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }
        data = {"duration": PERMIT_JOIN_DURATION}

        try:
            _call_progress(60, f"Sending request to {url}.")
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'), method='POST')
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.getcode()
                response_content = response.read().decode('utf-8')
                if 200 <= status_code < 300:
                    success_msg = f"ZHA pairing successfully initiated. Response: {response_content}"
                    logging.info(success_msg)
                    _call_progress(100, success_msg)
                    return True
                else:
                    err_msg = f"Failed to initiate ZHA pairing. Status: {status_code}, Response: {response_content}"
                    logging.error(err_msg)
                    _call_progress(100, err_msg)
                    return False
        except urllib.error.HTTPError as e:
            status_code = e.code
            response_content = "No content (HTTPError)"
            try:
                response_content = e.read().decode('utf-8')
            except Exception:
                pass
            err_msg = f"ZHA pairing request failed (HTTPError). Status: {status_code}, Response: {response_content}"
            logging.error(err_msg)
            _call_progress(95, f"Error: {err_msg}")
            return False
        except urllib.error.URLError as e:
            err_msg = f"ZHA pairing request failed (URLError): {e.reason}"
            logging.error(err_msg)
            _call_progress(95, f"Error: {err_msg}")
            return False
    except Exception as e: # Catch other exceptions like issues with token file, IP, etc.

        err_msg = f"ZHA pairing request failed: {e}"
        logging.error(err_msg, exc_info=True)
        _call_progress(95, f"Error: {err_msg}")
        return False

def run_zigbee_pairing(progress_callback=None, complete_callback=None, led_controller=None):
    """ 
    Start the Zigbee pairing process based on the current HA Zigbee mode.
    """ 
    if pairing_state.is_pairing():
        logging.warning("Pairing is already in progress.")
        if complete_callback:
            complete_callback(False, "Pairing is already in progress.")
        return

    pairing_state.set_pairing(True)
    if led_controller:
        led_controller.set_led_state(LedState.SYS_DEVICE_PAIRING)

    pairing_initiated_successfully = False
    try:
        _call_progress(progress_callback, 10, "Determining Zigbee mode...")
        mode = get_ha_zigbee_mode()
        logging.info(f"Current Zigbee mode is: {mode}")

        if mode == 'zha':
            _call_progress(progress_callback, 20, "Starting ZHA pairing process...")
            pairing_initiated_successfully = run_zha_pairing(progress_callback, led_controller)
        elif mode == 'z2m':
            _call_progress(progress_callback, 20, "Starting Zigbee2MQTT pairing process...")
            pairing_initiated_successfully = run_mqtt_pairing(progress_callback, led_controller)
        else:
            error_msg = "Zigbee pairing failed: No valid Zigbee integration (ZHA or Z2M) is active."
            logging.error(error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
        
        if pairing_initiated_successfully:
            _start_pairing_led_timer(led_controller, PERMIT_JOIN_DURATION)
            if complete_callback:
                complete_callback(True, "Pairing process initiated.")
        else:
            if complete_callback:
                # Check if a more specific message was already sent by the sub-function
                # This avoids sending a generic failure message if a specific one is available.
                if mode in ['zha', 'z2m']:
                    # The sub-functions now handle their own failure callbacks via progress
                    pass
                else:
                    complete_callback(False, "Failed to initiate pairing command.")

    finally:
        if not pairing_initiated_successfully:
            pairing_state.set_pairing(False)
            if led_controller:
                led_controller.set_led_state(LedState.SYS_DEVICE_PAIRED)
            logging.info("Pairing initiation failed, state reset.")

PERMIT_JOIN_DURATION = 254  # Unified permit join duration (seconds)

def _check_service_running(service_name):
    """Check if a systemd service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name], 
            capture_output=True, 
            text=True, 
            check=False
        )
        return result.stdout.strip() == "active"
    except Exception as e:
        logging.error(f"Error checking service status for {service_name}: {e}")
        return False

def get_zigbee_info():
    """
    Get Zigbee information and return as JSON string.
    
    Returns:
        str: JSON string containing Zigbee information
    """
    try:
        # First determine the mode
        mode = get_ha_zigbee_mode()
        
        # Check service status
        ha_running = _check_service_running("home-assistant.service")
        z2m_running = _check_service_running("zigbee2mqtt.service")
        
        result = {
            "mode": mode,
            "services": {
                "home_assistant": ha_running,
                "zigbee2mqtt": z2m_running
            }
        }
        
        # If both services are not running, try to get BL702 info directly
        if not ha_running and not z2m_running:
            logging.info("Both Home Assistant and Zigbee2MQTT services are not running, trying BL702 direct communication...")
            try:
                blz_info = get_blz_info(verbose=False)
                if blz_info:
                    result["blz_info"] = {}
                    
                    # Add IEEE address
                    if 'IEEE' in blz_info:
                        result["blz_info"]["IEEE"] = blz_info['IEEE']
                    
                    # Add application version
                    if 'version' in blz_info:
                        result["blz_info"]["version"] = blz_info['version']
                    
                    # Add stack version
                    if 'stack_version' in blz_info:
                        result["blz_info"]["stack_version"] = blz_info['stack_version']
                    
                    # Add network parameters
                    if 'network_parameters' in blz_info:
                        result["blz_info"]["network_parameters"] = blz_info['network_parameters']
                    
                    logging.info("Successfully retrieved BL702 information")
                else:
                    result["blz_info"] = None
                    result["error"] = "Failed to get BL702 information"
                    logging.warning("Failed to get BL702 information")
            except Exception as e:
                result["blz_info"] = None
                result["error"] = f"BL702 communication error: {str(e)}"
                logging.error(f"BL702 communication failed: {e}")
        
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "mode": "unknown",
            "error": f"Failed to get Zigbee info: {str(e)}"
        }
        logging.error(f"Error in get_zigbee_info: {e}")
        return json.dumps(error_result, ensure_ascii=False, indent=2)

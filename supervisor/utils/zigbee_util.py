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
from .wifi_utils import get_wlan0_ip


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

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
        # Generate a truly new entry_id
        # Format similar to: 01JWJ0ZAEC9C8YN1BVYW4SFW3G
        zha_entry_id = f"01{uuid.uuid4().hex.upper()[:24]}"
        now = datetime.now(timezone.utc).isoformat()
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
    except Exception as e:
        raise ConfigError(f"Error writing to {config_entries_path}: {e}")
    
    return mqtt_entry_id, zha_entry_id

def _update_zha_device_registry(mqtt_entry_id, zha_entry_id, ieee):
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
    
    # Iterate through devices, remove those related to mqtt_entry_id
    for device in devices:
        if mqtt_entry_id and 'config_entries' in device:
            # If the device's config_entries contains mqtt_entry_id, remove the device
            if mqtt_entry_id in device['config_entries']:
                print(f"Removing device linked to MQTT: [{device.get('name', 'Unknown device')}]")
                continue
        new_devices.append(device)
    else:
        new_devices = devices
    
    # Check if ZiGate USB-TTL device already exists
    has_zigate = False
    for device in new_devices:
        if device.get('model') == 'ZiGate USB-TTL' and device.get('manufacturer') == 'ZiGate':
            has_zigate = True
            print("ZiGate device already exists in registry")
            break
    
    # If no ZiGate device exists, add one
    if not has_zigate:
        now = datetime.now(timezone.utc).isoformat()
        # Use the zha_entry_id obtained from update_config_entries
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
            "id": "af41f395068280b4b3c76734dd1444f3",
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
    
    # 更新devices
    device_data['data']['devices'] = new_devices

    device_data['data']['deleted_devices'] = []
    
    # Write back to file
    try:
        with open(device_registry_path, 'w') as f:
            json.dump(device_data, f, indent=2)
        print(f"Updated {device_registry_path}")
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
    else:
        print("No MQTT entities found in entity registry, no changes made")


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
    for device in devices:
        # Check if this is the Zigbee2MQTT Bridge
        if device.get('name') == "Zigbee2MQTT Bridge":
            has_z2m_bridge = True
            print("Zigbee2MQTT Bridge already exists in registry")
            # Update the bridge to use the current MQTT entry_id
            if device.get('config_entries') and mqtt_entry_id not in device.get('config_entries', []):
                device['config_entries'] = [mqtt_entry_id]
                device['config_entries_subentries'] = {mqtt_entry_id: [None]}
                device['primary_config_entry'] = mqtt_entry_id
                device['modified_at'] = datetime.now(timezone.utc).isoformat()
                print("Updated Zigbee2MQTT Bridge with current MQTT entry_id")
            new_devices.append(device)
            continue
            
        # Skip devices linked to ZHA if zha_entry_id exists
        if zha_entry_id and 'config_entries' in device:
            if zha_entry_id in device['config_entries']:
                print(f"Removing device linked to ZHA: [ {device.get('name', 'Unknown device')} ]")
                continue
        new_devices.append(device)
    
    # If Zigbee2MQTT Bridge doesn't exist, add it
    if not has_z2m_bridge:
        now = datetime.now(timezone.utc).isoformat()
        bridge_device = {
            "area_id": None,
            "config_entries": [mqtt_entry_id],
            "config_entries_subentries": {mqtt_entry_id: [None]},
            "configuration_url": None,
            "connections": [],
            "created_at": now,
            "disabled_by": None,
            "entry_type": None,
            "hw_version": "zigate 321",
            "id": f"{uuid.uuid4().hex}",
            "identifiers": [["mqtt", "zigbee2mqtt_bridge_0x1c784ba0ffca0000"]],
            "labels": [],
            "manufacturer": "Zigbee2MQTT",
            "model": "Bridge",
            "model_id": None,
            "modified_at": now,
            "name_by_user": None,
            "name": "Zigbee2MQTT Bridge",
            "primary_config_entry": mqtt_entry_id,
            "serial_number": None,
            "sw_version": "2.3.0",
            "via_device_id": None
        }
        new_devices.append(bridge_device)
        print("Added Zigbee2MQTT Bridge device")
    
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

def run_zigbee_switch_zha_mode(progress_callback=None, complete_callback=None):
    """
    Switch to ZHA mode.
    Manages Home Assistant service, updates configurations, and handles conflicting services.
    """
    logging.info("Attempting to switch to ZHA mode...")
    ha_service_was_running = False

    def _call_progress(percentage, message):
        logging.info(f"Progress: {percentage}% - {message}")
        if progress_callback:
            try:
                progress_callback(percentage, message)
            except Exception as cb_e:
                logging.warning(f"Error in progress_callback: {cb_e}")

    try:
        _call_progress(5, "Fetching Zigbee device info...")
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

        _call_progress(10, "Checking Home Assistant service status...")
        try:
            status_check = subprocess.run(["systemctl", "is-active", "home-assistant.service"], capture_output=True, text=True, check=False, timeout=15)
            if status_check.stdout.strip() == "active":
                ha_service_was_running = True
                logging.info("Home Assistant service is running. Stopping it temporarily.")
                _call_progress(15, "Stopping Home Assistant service...")
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

        _call_progress(20, "Updating ZHA config entries...")
        mqtt_entry_id, zha_entry_id = _update_zha_config_entries(radio_type)
        logging.info(f"ZHA config entries updated. MQTT Entry ID: {mqtt_entry_id}, ZHA Entry ID: {zha_entry_id}")

        _call_progress(40, "Updating ZHA device registry...")
        _update_zha_device_registry(mqtt_entry_id, zha_entry_id, ieee)
        logging.info("ZHA device registry updated.")

        _call_progress(60, "Updating ZHA entity registry...")
        _update_zha_entity_registry()
        logging.info("ZHA entity registry updated.")

        _call_progress(80, "Stopping and disabling conflicting services (zigbee2mqtt, mosquitto)...")
        services_to_manage = [("zigbee2mqtt.service", "Zigbee2MQTT"), ("mosquitto.service", "Mosquitto")]
        all_services_managed_successfully = True
        for service_file, service_name in services_to_manage:
            try:
                logging.info(f"Stopping {service_name} ({service_file})...")
                subprocess.run(["systemctl", "stop", service_file], check=False, timeout=60)
                logging.info(f"Disabling {service_name} ({service_file})...")
                subprocess.run(["systemctl", "disable", service_file], check=False, timeout=60)
                logging.info(f"{service_name} ({service_file}) stopped and disabled.")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logging.warning(f"Error managing {service_name} ({service_file}): {e}. Continuing...")
                all_services_managed_successfully = False
            except FileNotFoundError:
                logging.warning(f"systemctl not found. Cannot manage {service_name} ({service_file}).")
                all_services_managed_successfully = False
                break
        
        if not all_services_managed_successfully:
            logging.warning("One or more conflicting services could not be fully managed. Check logs.")

        _call_progress(90, "Cleaning up Zigbee2MQTT data and resetting configuration...")
        z2m_data_path = "/opt/zigbee2mqtt/data"
        # ... (rest of the file operations remain the same) ...
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

        _call_progress(100, "Successfully switched to ZHA mode.")
        logging.info("Successfully switched to ZHA mode.")
        if complete_callback:
            complete_callback(True, "success")

    except ConfigError as e:
        logging.error(f"Configuration error during ZHA mode switch: {e}")
        _call_progress(100, f"Failed: Configuration error - {e}")
        if complete_callback:
            complete_callback(False, f"config_error: {e}")
    except subprocess.TimeoutExpired as e:
        logging.error(f"A system command timed out during ZHA mode switch: {e}")
        _call_progress(100, f"Failed: System command timeout - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_timeout: {e}")
    except subprocess.CalledProcessError as e:
        logging.error(f"System command failed during ZHA mode switch: {e.cmd} returned {e.returncode}")
        _call_progress(100, f"Failed: System command error - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_failed: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during ZHA mode switch: {e}", exc_info=True)
        _call_progress(100, f"Failed: Unexpected error - {e}")
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

def run_zigbee_switch_z2m_mode(progress_callback=None, complete_callback=None):
    """
    Switch to Zigbee2MQTT (Z2M) mode.
    Manages Home Assistant service, checks/updates configurations, and handles Z2M services.
    """
    logging.info("Attempting to switch to Zigbee2MQTT mode...")
    ha_service_was_running = False

    def _call_progress(percentage, message):
        logging.info(f"Progress: {percentage}% - {message}")
        if progress_callback:
            try:
                progress_callback(percentage, message)
            except Exception as cb_e:
                logging.warning(f"Error in progress_callback: {cb_e}")

    try:
        _call_progress(5, "Checking prerequisite services (Mosquitto, Zigbee2MQTT)...")
        required_services = ["mosquitto.service", "zigbee2mqtt.service"]
        for service_name in required_services:
            if not _service_exists(service_name):
                error_msg = f"Prerequisite service '{service_name}' not found. Cannot switch to Z2M mode."
                logging.error(error_msg)
                _call_progress(100, f"Failed: {error_msg}")
                if complete_callback:
                    complete_callback(False, f"prerequisite_service_missing: {service_name}")
                return
        logging.info("Prerequisite services found.")

        _call_progress(10, "Checking Home Assistant service status...")
        try:
            status_check = subprocess.run(["systemctl", "is-active", "home-assistant.service"], capture_output=True, text=True, check=False)
            if status_check.stdout.strip() == "active":
                ha_service_was_running = True
                logging.info("Home Assistant service is running. Stopping it temporarily.")
                _call_progress(15, "Stopping Home Assistant service...")
                subprocess.run(["systemctl", "stop", "home-assistant.service"], check=True)
                logging.info("Home Assistant service stopped.")
            else:
                logging.info("Home Assistant service is not running or status unknown.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to stop Home Assistant service: {e}")
            _call_progress(100, f"Failed: HA service stop error - {e}")
            if complete_callback:
                complete_callback(False, f"ha_service_stop_error: {e}")
            return
        except FileNotFoundError:
            logging.error("systemctl command not found. Cannot manage Home Assistant service.")
            _call_progress(100, "Failed: systemctl not found")
            if complete_callback:
                complete_callback(False, "systemctl_not_found")
            return

        _call_progress(20, "Checking if already in Z2M mode...")
        if _check_if_z2m_configured():
            logging.info("System is already configured for Zigbee2MQTT mode. Skipping configuration updates.")
            _call_progress(70, "Already in Z2M mode. Ensuring services are active.")
        else:
            _call_progress(25, "Not in Z2M mode or status unclear. Proceeding with configuration updates.")
            logging.info("Updating Home Assistant configurations for Z2M mode...")
            
            _call_progress(30, "Updating Z2M config entries...")
            # _update_zigbee2mqtt_config_entries does not take radio_type
            mqtt_entry_id, zha_entry_id = _update_zigbee2mqtt_config_entries()
            logging.info(f"Z2M config entries updated. MQTT Entry ID: {mqtt_entry_id}, ZHA Entry ID targeted for removal: {zha_entry_id}")

            _call_progress(50, "Updating Z2M device registry...")
            # _update_zigbee2mqtt_device_registry does not take ieee
            _update_zigbee2mqtt_device_registry(mqtt_entry_id, zha_entry_id)
            logging.info("Z2M device registry updated.")

            _call_progress(70, "Updating Z2M entity registry...")
            _update_zigbee2mqtt_entity_registry()
            logging.info("Z2M entity registry updated.")

        _call_progress(80, "Starting and enabling Z2M services (Mosquitto, Zigbee2MQTT)...")
        all_services_managed_successfully = True
        for service_file, service_name in [("mosquitto.service", "Mosquitto"), ("zigbee2mqtt.service", "Zigbee2MQTT")]:
            try:
                logging.info(f"Starting {service_name} ({service_file})...")
                subprocess.run(["systemctl", "start", service_file], check=True)
                logging.info(f"Enabling {service_name} ({service_file})...")
                subprocess.run(["systemctl", "enable", service_file], check=True)
                logging.info(f"{service_name} ({service_file}) started and enabled.")
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

        _call_progress(100, "Successfully switched to Z2M mode (or confirmed existing Z2M mode).")
        logging.info("Successfully switched to Zigbee2MQTT mode.")
        if complete_callback:
            complete_callback(True, "success_z2m_mode_set_or_confirmed")

    except ConfigError as e:
        logging.error(f"Configuration error during Z2M mode switch: {e}")
        _call_progress(100, f"Failed: Configuration error - {e}")
        if complete_callback:
            complete_callback(False, f"config_error_z2m: {e}")
    except subprocess.CalledProcessError as e:
        logging.error(f"System command failed during Z2M mode switch: {e.cmd} returned {e.returncode} with output: {e.output} and stderr: {e.stderr}")
        _call_progress(100, f"Failed: System command error - {e}")
        if complete_callback:
            complete_callback(False, f"system_command_failed_z2m: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during Z2M mode switch: {e}", exc_info=True)
        _call_progress(100, f"Failed: Unexpected error - {e}")
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


def run_zha_pairing(progress_callback=None, complete_callback=None):
    """
    Start the ZHA pairing process by calling the Home Assistant API.
    Reads Bearer token from /etc/automation-robot.conf.
    """
    token_file = "/etc/automation-robot.conf"
    bearer_token = None

    def _call_progress(percent, message):
        logging.info(f"ZHA Pairing progress ({percent}%): {message}")
        if progress_callback:
            progress_callback(percent, message)

    try:
        _call_progress(0, "Starting ZHA pairing process.")

        # 1. Read Bearer token
        _call_progress(10, f"Reading Bearer token from {token_file}.")
        if not os.path.exists(token_file):
            err_msg = f"Token file not found: {token_file}"
            logging.error(err_msg)
            if complete_callback:
                complete_callback(False, err_msg)
            return
        with open(token_file, "r", encoding="utf-8") as f:
            bearer_token = f.read().strip()
        
        if not bearer_token:
            err_msg = f"Bearer token is empty in {token_file}."
            logging.error(err_msg)
            if complete_callback:
                complete_callback(False, err_msg)
            return
        _call_progress(25, "Bearer token read successfully.")

        # 2. Get local IP address
        _call_progress(40, "Fetching wlan0 IP address.")
        local_ip = get_wlan0_ip()
        if not local_ip:
            err_msg = "Failed to determine wlan0 IP address."
            logging.error(err_msg)
            if complete_callback:
                complete_callback(False, err_msg)
            return
        _call_progress(50, f"wlan0 IP address: {local_ip}.")

        # 3. Prepare and send the request
        api_url = f"http://{local_ip}:8123/api/services/zha/permit"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "User-Agent": "CascadeAI/1.0",
            "Accept": "*/*",
            "Host": f"{local_ip}:8123",
            "Connection": "keep-alive"
        }
        data = {
            "duration": 254
        }

        _call_progress(60, f"Sending ZHA permit join request to {api_url}.")
        
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(api_url, data=json_data, headers=headers, method='POST')
        
        try:
            with urllib.request.urlopen(req, timeout=10) as http_response:
                response_content = http_response.read().decode('utf-8')
                status_code = http_response.getcode()
                _call_progress(90, f"Received response: {status_code}.")

                if 200 <= status_code < 300:
                    success_msg = f"ZHA pairing successfully initiated. Response: {response_content}"
                    logging.info(success_msg)
                    if complete_callback:
                        complete_callback(True, success_msg)
                else:
                    err_msg = f"Failed to initiate ZHA pairing. Status: {status_code}, Response: {response_content}"
                    logging.error(err_msg)
                    if complete_callback:
                        complete_callback(False, err_msg)
        except urllib.error.HTTPError as e:
            status_code = e.code
            response_content = "No content (HTTPError)"
            try:
                response_content = e.read().decode('utf-8')
            except Exception:
                pass # Keep default error content
            err_msg = f"ZHA pairing request failed (HTTPError). Status: {status_code}, Response: {response_content}"
            logging.error(err_msg)
            _call_progress(95, f"Error: {err_msg}")
            if complete_callback:
                complete_callback(False, err_msg)
        except urllib.error.URLError as e:
            err_msg = f"ZHA pairing request failed (URLError): {e.reason}"
            logging.error(err_msg)
            _call_progress(95, f"Error: {err_msg}")
            if complete_callback:
                complete_callback(False, err_msg)
    except Exception as e: # Catch other exceptions like issues with token file, IP, etc.

        err_msg = f"ZHA pairing request failed: {e}"
        logging.error(err_msg)
        _call_progress(95, f"Error: {err_msg}")
        if complete_callback:
            complete_callback(False, err_msg)
    except Exception as e:
        err_msg = f"An unexpected error occurred during ZHA pairing: {e}"
        logging.error(err_msg, exc_info=True)
        _call_progress(95, f"Error: {err_msg}")
        if complete_callback:
            complete_callback(False, err_msg)

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
            progress_callback(percent, message)

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

def run_zigbee_pairing(progress_callback=None, complete_callback=None):
    """
    Start the Zigbee pairing process based on the current HA Zigbee mode.
    """
    try:
        if progress_callback:
            progress_callback(10, "Determining Zigbee mode...")
        
        mode = get_ha_zigbee_mode()
        logging.info(f"Current Zigbee mode is: {mode}")

        if mode == 'zha':
            if progress_callback:
                progress_callback(20, "Starting ZHA pairing process...")
            # run_zha_pairing will handle the rest of the callbacks
            run_zha_pairing(progress_callback, complete_callback)
        elif mode == 'z2m':
            if progress_callback:
                progress_callback(20, "Starting Zigbee2MQTT pairing process...")
            # run_mqtt_pairing will handle the rest of the callbacks
            run_mqtt_pairing(progress_callback, complete_callback)
        else:
            error_msg = "Zigbee pairing failed: No valid Zigbee integration (ZHA or Z2M) is active."
            logging.error(error_msg)
            if complete_callback:
                complete_callback(False, error_msg)

    except Exception as e:
        error_msg = f"An unexpected error occurred during Zigbee pairing: {e}"
        logging.error(error_msg, exc_info=True)
        if complete_callback:
            complete_callback(False, error_msg)
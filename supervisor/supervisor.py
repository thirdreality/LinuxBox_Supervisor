#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# maintainer: guoping.liu@3reality.com

import os
import time
import logging
import signal
import json
import sys
import subprocess
import threading
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .network import NetworkMonitor
from .hardware import GpioButton, GpioLed, LedState,GpioHwController
from .utils.wifi_manager import WifiStatus, WifiManager
from .ota.ota_server import SupervisorOTAServer
from .task import TaskManager
from .utils import util
from .utils.wifi_utils import get_wlan0_mac, get_wlan0_ip, get_current_wifi_info

from .ble.gatt_server import SupervisorGattServer
from .ble.gatt_manager import GattServerManager
from .http_server import SupervisorHTTPServer  
from .proxy import SupervisorProxy
from .cli import SupervisorClient
from .sysinfo import SystemInfoUpdater, SystemInfo, OpenHabInfo
from supervisor.utils.zigbee_util import get_ha_zigbee_mode
from .const import VERSION, DEVICE_BUILD_NUMBER
from .zero_manager import ZeroconfManager
from .storage_manager import StorageManager
try:
    import yaml
except Exception:
    yaml = None
try:
    from supervisor.utils.zigbee_util import get_zigbee_info
except ImportError:
    # Fallback if get_zigbee_info is not available
    def get_zigbee_info():
        return '{"error": "get_zigbee_info function not available"}'

try:
    from supervisor.utils.thread_util import get_thread_info
except ImportError:
    # Fallback if get_thread_info is not available
    def get_thread_info():
        return '{"error": "get_thread_info function not available"}'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/var/log/supervisor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Supervisor")

class Supervisor:
    #zigbee2mqtt, homekitbridge, homeassistant-core
    _worker_mode="homeassistant-core"

    def __init__(self):
        # Hardware control
        self.hwinit = GpioHwController(self)
        self.led = GpioLed(self)
        self.button = GpioButton(self)        

        # LED state and running state
        self.state_lock = threading.Lock()
        self.running = threading.Event()
        self.running.set()
        self.stop_event = threading.Event()
        
        self.wifi_status = WifiStatus()
        self.ota_status = util.OtaStatus()
        self.system_info = SystemInfo()

        self.wifi_manager = WifiManager(self)
        self.wifi_manager.init()

        self.task_manager = TaskManager(self)
        self.task_manager.init()

        self.proxy = SupervisorProxy(self)
        self.http_server = None

        # Initialize SystemInfoUpdater before GATT manager to ensure device name is set
        self.sysinfo_update = SystemInfoUpdater(self)
        
        self.gatt_manager = GattServerManager(self)
        # self.gatt_server = None  # No longer needed

        self.network_monitor = NetworkMonitor(self)
        # self.ota_server = SupervisorOTAServer(self)  # Temporarily disabled OTA server

        # boot up time
        self.start_time = time.time()

        # Zeroconf manager
        self.zeroconf_manager = ZeroconfManager(
            service_type="_linuxbox._tcp.local.",
            service_name_template="HUB-{mac}._linuxbox._tcp.local.",
            service_port=8086,
            properties={"version": VERSION, "build": DEVICE_BUILD_NUMBER}
        )
        
        # Storage manager
        self.storage_manager = StorageManager(self)
        
        # Status reporter
        self._status_report_thread = None
    

    def set_led_state(self, state):
        """Set LED state with safety checks"""
        if self.led and hasattr(self.led, 'set_led_state'):
            self.led.set_led_state(state)
        else:
            logger.warning("LED controller not available or missing set_led_state method")

    def clear_led_state(self, state):
        """Clear LED state with safety checks"""
        if self.led and hasattr(self.led, 'clear_led_state'):
            self.led.clear_led_state(state)
        else:
            logger.warning("LED controller not available or missing clear_led_state method")

    def toggle_led_critical_red(self):
        """Toggle the critical red LED state (red-yellow alternating flash)"""
        if self.led and hasattr(self.led, 'toggle_critical_red'):
            self.led.toggle_critical_red()
            logger.info("LED critical red toggle executed")
            return "LED critical red toggled"
        else:
            logger.error("LED toggle function not available")
            return "LED toggle function not available"

    def set_ota_command(self, cmd):
        logger.info(f"OTA Command: param={cmd}")
        cmd_lower = cmd.strip().lower() if isinstance(cmd, str) else ""
        
        if cmd_lower == "bridge":
            try:
                # Start bridge OTA upgrade task
                started = self.task_manager.start_ota_bridge_upgrade()
                if started:
                    logger.info("Bridge OTA upgrade task started")
                    return "Bridge OTA upgrade started"
                else:
                    return "Another OTA task is already running"
            except Exception as e:
                logger.error(f"Failed to start bridge OTA upgrade: {e}")
                return f"Failed to start bridge OTA upgrade: {e}"
        elif cmd_lower == "z2m":
            try:
                # Start z2m OTA upgrade task
                started = self.task_manager.start_ota_z2m_upgrade()
                if started:
                    logger.info("Z2M OTA upgrade task started")
                    return "Z2M OTA upgrade started"
                else:
                    return "Another OTA task is already running"
            except Exception as e:
                logger.error(f"Failed to start z2m OTA upgrade: {e}")
                return f"Failed to start z2m OTA upgrade: {e}"
        else:
            return f"Unknown OTA command: {cmd}. Supported: bridge, z2m"

    #### Asynchronous commands 

    def set_zigbee_command(self, cmd):
        logger.info(f"zigbee Command: param={cmd}")
        cmd_lower = cmd.strip().lower() if isinstance(cmd, str) else ""
        
        if cmd_lower == "zha":
            try:
                self.task_manager.start_zigbee_switch_zha_mode()
                logger.info("Zigbee device started pairing")
                return "Zigbee device started pairing"
            except Exception as e:
                logger.error(f"Zigbee pairing start failed: {e}")
                return f"Zigbee pairing start failed: {e}"
        elif cmd_lower == "z2m":
            try:
                self.task_manager.start_zigbee_switch_z2m_mode()
                logger.info("Zigbee device started pairing")
                return "Zigbee device started pairing"
            except Exception as e:
                logger.error(f"Zigbee pairing start failed: {e}")
                return f"Zigbee pairing start failed: {e}"
        elif cmd_lower == "info":
            # Query zigbee information
            return get_zigbee_info()
        elif cmd_lower == "reset":
            try:
                # Use GPIO to reset Zigbee chip
                subprocess.run(["gpioset", "0", "3=1"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "3=0"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "1=1"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "1=0"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "1=1"], check=True)
                logger.info("Zigbee reset sequence executed via GPIO")
                return "Zigbee reset OK"
            except Exception as e:
                logger.error(f"Zigbee reset failed: {e}")
                return f"Zigbee reset failed: {e}"
        elif cmd_lower == "scan":
            try:
                self.task_manager.start_zigbee_pairing(led_controller=self.led)
                logger.info("Zigbee device started pairing")
                return "Zigbee device started pairing"
            except Exception as e:
                logger.error(f"Zigbee pairing start failed: {e}")
                return f"Zigbee pairing start failed: {e}"
        elif cmd_lower == "stop_scan":
            try:
                self.task_manager.start_zigbee_stop_pairing(led_controller=self.led)
                logger.info("Zigbee pairing stop requested")
                return "Zigbee pairing stop requested"
            except Exception as e:
                logger.error(f"Zigbee pairing stop failed: {e}")
                return f"Zigbee pairing stop failed: {e}"                
        elif cmd_lower == "update":
            try:
                #self.task_manager.start_zigbee_ota()
                logger.info("Zigbee device started pairing")
                return "Zigbee device started pairing"
            except Exception as e:
                logger.error(f"Zigbee pairing start failed: {e}")
                return f"Zigbee pairing start failed: {e}"
        elif cmd_lower.startswith("channel_"):
            # Handle ZHA channel switching: channel_11, channel_12, etc.
            try:
                channel_str = cmd_lower.replace("channel_", "")
                channel = int(channel_str)
                if 11 <= channel <= 26:
                    self.task_manager.start_zha_channel_switch(channel)
                    logger.info(f"ZHA channel switch to {channel} started")
                    return f"ZHA channel switch to {channel} started"
                else:
                    return f"Invalid ZHA channel: {channel}. Must be between 11-26"
            except ValueError:
                return f"Invalid ZHA channel format: {cmd}"
            except Exception as e:
                logger.error(f"ZHA channel switch failed: {e}")
                return f"ZHA channel switch failed: {e}"
        elif cmd_lower == "firmware_update":
            try:
                self.task_manager.start_zha_firmware_update_notification()
                logger.info("ZHA firmware update notification started")
                return "ZHA firmware update notification started"
            except Exception as e:
                logger.error(f"ZHA firmware update notification failed: {e}")
                return f"ZHA firmware update notification failed: {e}"
        else:
            logger.warning(f"Unknown Zigbee command: {cmd}")
            return f"Unknown Zigbee command: {cmd}"

            
    def set_thread_command(self, cmd):
        logger.info(f"thread Command: param={cmd}")
        
        if cmd.lower() == "enabled":
            # Thread support is always enabled
            logger.info("Thread support is always enabled")
            return "Thread support enabled"
        elif cmd.lower() == "disabled":
            # Thread support is always enabled, cannot be disabled
            logger.info("Thread support is always enabled, cannot be disabled")
            return "Thread support is always enabled"
        elif cmd.lower() == "info":
            # Get Thread information
            return get_thread_info()
        elif cmd.lower() == "enable":
            try:
                self.task_manager.start_thread_mode_enable()
                logger.info("Thread support enabled")
                return "Thread support enabled"
            except Exception as e:
                logger.error(f"Thread support enable fail: {e}")
                return f"Thread support enable fail: {e}" 
        elif cmd.lower() == "disable":
            # Disable Thread support
            try:
                self.task_manager.start_thread_mode_disable()
                logger.info("Thread support disabled")
                return "Thread support disabled"
            except Exception as e:
                logger.error(f"Thread support disable fail: {e}")
                return f"Thread support disable fail: {e}"
        elif cmd.lower() == "reset":
            try:
                # Use GPIO to reset Thread chip
                subprocess.run(["gpioset", "0", "29=1"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "29=0"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "27=1"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "27=0"], check=True)
                time.sleep(0.2)
                subprocess.run(["gpioset", "0", "27=1"], check=True)
                time.sleep(0.5)
                logger.info("Thread reset sequence executed via GPIO")
                return "Thread reset OK"
            except Exception as e:
                logger.error(f"Thread reset failed: {e}")
                return f"Thread reset failed: {e}"
        elif cmd.lower().startswith("channel_"):
            # Handle Thread channel switching: channel_11, channel_12, etc.
            try:
                channel_str = cmd.lower().replace("channel_", "")
                channel = int(channel_str)
                if 11 <= channel <= 26:
                    self.task_manager.start_thread_channel_switch(channel)
                    logger.info(f"Thread channel switch to {channel} started")
                    return f"Thread channel switch to {channel} started"
                else:
                    return f"Invalid Thread channel: {channel}. Must be between 11-26"
            except ValueError:
                return f"Invalid Thread channel format: {cmd}"
            except Exception as e:
                logger.error(f"Thread channel switch failed: {e}")
                return f"Thread channel switch failed: {e}"


    def set_setting_command(self, cmd):
        logger.info(f"setting Command: param={cmd}")
        
        # If the command is enable, enable Thread support
        if cmd.lower() == "backup":
            try:
                self.task_manager.start_setting_backup()
                logger.info("Setting backup finish")
                return "Setting backup finish"
            except Exception as e:
                logger.error(f"Setting backup fail: {e}")
                return f"Setting backup fail: {e}"
        elif cmd.lower() == "restore":
            try:
                self.task_manager.start_setting_restore()
                logger.info("Setting restore finish")
                return "Setting restore finish"
            except Exception as e:
                logger.error(f"Setting restore fail: {e}")
                return f"Setting restore fail: {e}"
        elif cmd.lower() == "local_backup":
            try:
                self.task_manager.start_setting_local_backup()
                logger.info("Setting local backup finish")
                return "Setting local backup finish"
            except Exception as e:
                logger.error(f"Setting local backup fail: {e}")
                return f"Setting local backup fail: {e}"
        elif cmd.lower() == "local_restore":
            try:
                self.task_manager.start_setting_local_restore()
                logger.info("Setting local restore finish")
                return "Setting local restore finish"
            except Exception as e:
                logger.error(f"Setting local restore fail: {e}")
                return f"Setting local restore fail: {e}"
        elif cmd.lower() == "updated":
            try:
                self.task_manager.start_setting_updated()
                logger.info("Setting updated finish")
                return "Setting updated finish"
            except Exception as e:
                logger.error(f"Setting updated fail: {e}")
                return f"Setting updated fail: {e}"
        elif cmd.lower() == "z2m-mqtt":
            try:
                default_config = {
                    "base_topic": "zigbee2mqtt",
                    "server": "mqtt://hm.3reality.co:1883",
                    "user": "thirdreality",
                    "password": "shushi0705",
                    "client_id": "my_id_9527",
                }
                self.task_manager.start_setting_update_z2m_mqtt(default_config)
                logger.info("Setting z2m-mqtt update task started")
                return "Setting z2m-mqtt update task started"
            except Exception as e:
                logger.error(f"Setting z2m-mqtt fail: {e}")
                return f"Setting z2m-mqtt fail: {e}"
        elif cmd.lower() == "wifi_notify":
            try:
                threading.Timer(1, self.finish_wifi_provision).start()
                logger.info("WiFi provision mode exited by external notify")
                return "WiFi provision mode exited"
            except Exception as e:
                logger.error(f"WiFi provision exit fail: {e}")
                return f"WiFi provision exit fail: {e}"

    def start_zigbee_pairing(self):
        """Starts the Zigbee pairing process via the TaskManager."""
        try:
            self.task_manager.start_zigbee_pairing(led_controller=self.led)
            logger.info("Zigbee pairing process started by button.")
            return True
        except Exception as e:
            logger.error(f"Failed to start Zigbee pairing by button: {e}")
            return False

    def start_zigbee_switch_zha(self) -> bool:
        """
        Starts the process of switching the Zigbee integration to ZHA mode.

        Returns:
            bool: True if the process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_zigbee_switch_zha_mode()
            logger.info("Successfully started Zigbee switch to ZHA mode.")
            return True
        except Exception as e:
            logger.error(f"Failed to start Zigbee switch to ZHA mode: {e}")
            return False

    def start_zigbee_switch_z2m(self) -> bool:
        """
        Starts the process of switching the Zigbee integration to Z2M mode.

        Returns:
            bool: True if the process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_zigbee_switch_z2m_mode()
            logger.info("Successfully started Zigbee switch to Z2M mode.")
            return True
        except Exception as e:
            logger.error(f"Failed to start Zigbee switch to Z2M mode: {e}")
            return False

    def start_zigbee_channel_switch(self, channel: int):
        """
        Starts the process of switching Zigbee channel based on current mode (ZHA or Z2M).
        Args:
            channel: Channel number to switch to (11, 12, ..., 26)
        Returns:
            (success: bool, message: str)
        """
        try:
            zigbee_mode = get_ha_zigbee_mode()
            if zigbee_mode == 'zha':
                self.task_manager.start_zha_channel_switch(channel)
                return True, f"Channel has been switched to {channel}"
            elif zigbee_mode == 'z2m':
                self.task_manager.start_z2m_channel_switch(channel)
                return True, f"Channel has been switched to {channel}, related services need to be restarted, please wait"
            else:
                return False, "Cannot switch: Zigbee mode not detected"
        except Exception as e:
            return False, f"Switch failed: {e}"

    def start_thread_channel_switch(self, channel: int):
        """
        Starts the process of switching Thread channel.
        Args:
            channel: Channel number to switch to (11, 12, ..., 26)
        Returns:
            (success: bool, message: str)
        """
        try:
            self.task_manager.start_thread_channel_switch(channel)
            return True, f"Channel will take 5 minutes to switch to {channel}, please wait"
        except Exception as e:
            return False, f"Switch failed: {e}"

    def start_setting_backup(self) -> bool:
        """
        Starts the setting backup process.

        Returns:
            bool: True if the backup process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_setting_backup()
            logger.info("Setting backup process started successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to start setting backup process: {e}")
            return False

    def start_setting_restore(self, backup_file=None) -> bool:
        """
        Starts the setting restore process.

        Args:
            backup_file: Optional backup file timestamp to restore from

        Returns:
            bool: True if the restore process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_setting_restore(backup_file)
            logger.info("Setting restore process started successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to start setting restore process: {e}")
            return False

    def start_setting_updated(self) -> bool:
        """
        Starts the setting updated process to clear version information.

        Returns:
            bool: True if the updated process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_setting_updated()
            logger.info("Setting updated process started successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to start setting updated process: {e}")
            return False

    def get_led_state(self):
        # Get the LED state from the GpioLed instance
        return self.led.get_led_state()
        
    def isThreadSupported(self):
        return self.system_info.support_thread

    def isZigbeeSupported(self):
        return self.system_info.support_zigbee


    def onNetworkFirstConnected(self):
        logger.info("## Supervisor: Network onNetworkFirstConnected() ...")
        try:
            logger.info(f"Attempting to start Zeroconf with IP: {self.wifi_status.ip_address}")
            success = self.zeroconf_manager.start(self.wifi_status.ip_address)
            if success:
                logger.info("Zeroconf started successfully")
            else:
                logger.warning("Zeroconf start returned False")
        except Exception as e:
            logger.error(f"Failed to start Zeroconf on first connect: {e}", exc_info=True)
        # Network is up, safe to launch status reporting
        # self._start_status_reporter()

    def onNetworkDisconnect(self):
        logger.info("## Supervisor: Network onNetworkDisconnect() ...")
        try:
            self.zeroconf_manager.stop()
        except Exception as e:
            logger.warning(f"Failed to stop Zeroconf on disconnect: {e}")

    def onNetworkConnected(self):
        logger.info("## Supervisor: Network onNetworkConnected() ...")
        # Use update_ip method which handles retry logic internally
        self.zeroconf_manager.update_ip(self.wifi_status.ip_address)

    def update_wifi_info(self, ip_address, ssid):
        """Update WiFi information cache"""
        prev_ip = self.wifi_status.ip_address
        ip_changed = prev_ip != ip_address

        # Update WiFi status information
        if ip_changed:
            self.wifi_status.ip_address = ip_address
            self.wifi_status.ssid = ssid

        # Only trigger when transitioning from no IP to having an IP
        if (not prev_ip or prev_ip in ["", "0.0.0.0"]) and (ip_address and ip_address not in ["", "0.0.0.0"]):
            logger.debug(f"WiFi connected with IP {ip_address}, notifying GATT manager")
            self.gatt_manager.on_wifi_connected()

        return True   

    def update_system_uptime(self):
        """Update system uptime"""
        if 'uptime' in self.system_info:
            self.system_info['uptime'] = int(time.time() - self.start_time)

    def _start_http_server(self):
        """Start HTTP server"""
        if not self.http_server:
            try:
                self.http_server = SupervisorHTTPServer(self, port=8086)
                self.http_server.start()
                logger.info("HTTP server started")
                return True
            except Exception as e:
                logger.error(f"Failed to start HTTP server: {e}")
                return False
        return True

    def _stop_http_server(self):
        if not self.http_server:
            return True
        try:
            self.http_server.stop()
            self.http_server = None
            logger.info("HTTP server stopped")
            return True
        except Exception as e:
            logger.error(f"Failed to stop HTTP server: {e}")
            return False

    def _start_gatt_server(self):
        """Initialize GATT manager, but do not automatically start the service"""
        # GATT manager is already initialized in __init__, here just to confirm the state
        if self.gatt_manager:
            logger.info("GATT manager initialized, ready for provisioning mode")
            return True
        return False

    def _stop_gatt_server(self):
        """Stop GATT server"""
        if self.gatt_manager:
            try:
                self.gatt_manager.cleanup()
                logger.info("GATT manager stopped")
                return True
            except Exception as e:
                logger.error(f"Failed to stop GATT manager: {e}")
                return False
        return True

    def on_system_ready_check_wifi_provision(self):
        logger.info("System is ready, checking auto wifi provision...")
        self.task_manager.start_auto_wifi_provision()

    def perform_reboot(self):
        logging.info("Performing reboot...")
        util.perform_reboot()

    # -------------------------
    # Status report (every 2h)
    # -------------------------
    def _read_z2m_mqtt_host(self):
        """
        Read mqtt.server field from /opt/zigbee2mqtt/data/configuration.yaml, return hostname; return None on failure
        """
        config_path = "/opt/zigbee2mqtt/data/configuration.yaml"
        try:
            if not os.path.exists(config_path):
                logger.info("z2m configuration not found at /opt/zigbee2mqtt/data/configuration.yaml")
                return None
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Prefer using yaml parsing
            if yaml:
                try:
                    data = yaml.safe_load(content) or {}
                    mqtt_cfg = data.get("mqtt") or {}
                    server = mqtt_cfg.get("server") or ""
                except Exception:
                    server = ""
            else:
                # Simple parsing fallback: find lines starting with 'server:'
                server = ""
                for line in content.splitlines():
                    line_stripped = line.strip()
                    if line_stripped.lower().startswith("server:"):
                        # server: mqtt://host:port
                        parts = line_stripped.split(":", 1)
                        if len(parts) == 2:
                            server = parts[1].strip()
                        break
            if not server:
                logger.info("z2m configuration found but mqtt.server is empty")
                return None
            # Allow server format like mqtt://host:port or tcp:// or ws://
            parsed = urlparse(server)
            hostname = parsed.hostname
            # If parsing fails (e.g., directly written as host:port), do a fallback
            if not hostname:
                # Remove possible prefix
                s = server
                if "://" in s:
                    s = s.split("://", 1)[1]
                hostname = s.split("/")[0].split(":")[0].strip()
            return hostname or None
        except Exception as e:
            logger.warning(f"Failed to read z2m configuration: {e}")
            return None

    def _get_cpu_load_15min(self):
        """Get CPU load 15min only"""
        try:
            with open('/proc/loadavg', 'r') as f:
                load = f.read().strip().split()
                return float(load[2])  # load_15min
        except Exception as e:
            logger.error(f"Error getting CPU load: {e}")
            return 0.0
    
    def _get_memory_usage(self):
        """Get memory usage in format: usedMB/totalMB"""
        try:
            with open('/proc/meminfo', 'r') as f:
                mem_info = {}
                for line in f:
                    if 'MemTotal' in line or 'MemFree' in line:
                        key, value = line.split(':', 1)
                        value = value.strip().split()[0]  # Remove unit, keep only number
                        mem_info[key.strip()] = int(value)
                
                if 'MemTotal' in mem_info and 'MemFree' in mem_info:
                    total_kb = mem_info['MemTotal']
                    free_kb = mem_info['MemFree']
                    # Convert to MB (round to nearest integer)
                    total_mb = round(total_kb / 1024)
                    free_mb = round(free_kb / 1024)
                    return f"{free_mb}MB/{total_mb}MB"
                else:
                    return ""
        except Exception as e:
            logger.error(f"Error getting memory usage: {e}")
            return ""

    def _build_status_payload(self):
        """
        Build status report JSON payload
        """
        sys_info = self.system_info
        # Try to fill in IP and SSID before building
        try:
            if not self.wifi_status.ip_address:
                ip = get_wlan0_ip()
                if ip:
                    self.wifi_status.ip_address = ip
            if not self.wifi_status.ssid:
                ssid, _ = get_current_wifi_info()
                if ssid:
                    self.wifi_status.ssid = ssid
        except Exception:
            pass
        
        # Calculate uptime (seconds)
        uptime_seconds = int(time.time() - self.start_time)
        
        # Get CPU load (only use load_15min)
        cpu_load_15min = self._get_cpu_load_15min()
        
        # Get memory usage (format: usedMB/totalMB)
        memory_str = self._get_memory_usage()
        
        # Get storage information (using http_server.py format)
        storage = sys_info.storage_space if isinstance(sys_info.storage_space, dict) else {"available": "", "total": ""}
        storage_str = f"{storage.get('available', '')}/{storage.get('total', '')}" if storage.get('available') and storage.get('total') else ""
        
        # Get installed services list, filter out "hab" (never officially installed)
        try:
            if hasattr(sys_info, 'installed_services'):
                services_list = [s for s in sys_info.installed_services ]
                services_str = ",".join(services_list) if services_list else ""
            else:
                services_str = ""
        except Exception:
            services_str = ""
        
        payload = {
            "Model ID": sys_info.model_id,
            "Device Name": sys_info.name,
            "Version": sys_info.version,
            "uptime": uptime_seconds,
            "SSID": self.wifi_status.ssid or "",
            "Ip Address": self.wifi_status.ip_address or "",
            "Mac Address": (get_wlan0_mac() or "").lower(),
            "Services": services_str,
            "Memory": memory_str,
            "Storage": storage_str,
            "cpu": cpu_load_15min,
        }
        return payload

    def _post_status(self, host):
        """
        Send status report; use https
        """
        payload = self._build_status_payload()
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "LinuxBoxSupervisor/1.0",
            "Host": host,
        }
        paths = [f"https://{host}/api/hub/v1/status/report"]
        last_err = None
        for url in paths:
            try:
                logger.info(f"Posting status to {url}")
                try:
                    logger.info(f"Status payload: {json.dumps(payload, ensure_ascii=False)}")
                except Exception:
                    logger.info("Status payload: <failed to serialize>")
                req = Request(url=url, data=body, headers=headers, method="POST")
                with urlopen(req, timeout=10) as resp:
                    resp_body = resp.read().decode("utf-8", errors="ignore")
                    return resp.status, resp_body
            except Exception as e:
                # If HTTPError, try to read response body for debugging
                try:
                    if isinstance(e, HTTPError) and getattr(e, "read", None):
                        err_body = e.read().decode("utf-8", errors="ignore")
                        # Treat HTTP 4xx/5xx with business JSON as "normal response" for upper layer parsing, not as error
                        logger.info(f"Post to {url} returned HTTP {e.code}, body={err_body}")
                        return e.code, err_body
                    else:
                        logger.warning(f"Post to {url} failed: {e}")
                except Exception:
                    logger.warning(f"Post to {url} failed: {e}")
                last_err = e
                continue
        raise last_err if last_err else RuntimeError("Unknown request failure")

    def _handle_pending_commands(self, resp_json: dict):
        """
        Handle pendingCommands in response, execute reboot if it contains reboot command
        """
        try:
            data = resp_json.get("data") or {}
            pending = data.get("pendingCommands") or []
            for item in pending:
                cmd = (item.get("command") or "").strip().lower()
                if cmd == "reboot":
                    logger.warning("Received pending command: reboot, executing reboot")
                    # Give a little delay to return HTTP response
                    threading.Timer(3.0, self.perform_reboot).start()
        except Exception as e:
            logger.warning(f"Handle pendingCommands failed: {e}")

    def _status_report_loop(self):
        """
        Loop every 2 hours: check configuration, determine host, report if not localhost
        """
        logger.info("Status report thread started")
        # Give network initialization wait time on first startup, wait up to 60 seconds, get IP/SSID in advance
        try:
            logger.info("Status reporter initial delay: waiting up to 60s for network info...")
            waited = 0
            while self.running.is_set() and waited < 60:
                ip_now = self.wifi_status.ip_address
                if ip_now and ip_now not in ("", "0.0.0.0"):
                    break
                time.sleep(1)
                waited += 1
            # Do one more query
            if not self.wifi_status.ip_address:
                ip = get_wlan0_ip()
                if ip:
                    self.wifi_status.ip_address = ip
            if not self.wifi_status.ssid:
                ssid, _ = get_current_wifi_info()
                if ssid:
                    self.wifi_status.ssid = ssid
            logger.info(f"Status reporter initial check finished: ip={self.wifi_status.ip_address or ''}, ssid={self.wifi_status.ssid or ''}")
        except Exception as e:
            logger.debug(f"Initial network wait failed: {e}")
            
        while self.running.is_set():
            try:
                host = self._read_z2m_mqtt_host()
                
                # If host is localhost-like, wait in 30-second cycles until host is no longer localhost
                while host and host in ("localhost", "127.0.0.1", "::1") and self.running.is_set():
                    logger.info(f"mqtt host is local ({host}), waiting for non-localhost host (30s intervals)...")
                    # Wait 30 seconds (10 times 3 seconds)
                    for _ in range(10):  # 10 times * 3 seconds = 30 seconds
                        if not self.running.is_set():
                            break
                        time.sleep(3)  # Wait 3 seconds each time, convenient for service exit
                    
                    if not self.running.is_set():
                        break
                    
                    # Re-read host
                    host = self._read_z2m_mqtt_host()
                    # if host and host not in ("localhost", "127.0.0.1", "::1"):
                    #     logger.info(f"mqtt host changed to non-localhost: {host}, proceeding with status report")
                    #     break
                    # # If still localhost, while loop condition still satisfied, will automatically continue next 30-second wait cycle

                if not self.running.is_set():
                    break                    
                
                if host and host not in ("localhost", "127.0.0.1", "::1"):
                    try:
                        status, body = self._post_status(host)
                        logger.info(f"Status reported to {host}, status={status}")
                        # Parse response and handle pendingCommands
                        try:
                            resp_json = json.loads(body)
                            self._handle_pending_commands(resp_json)
                        except Exception as e:
                            logger.debug(f"Parse response failed: {e}")
                    except Exception as e:
                        logger.warning(f"Report status to {host} failed: {e}")
                else:
                    if host:
                        logger.info(f"Skip status report: mqtt host is local ({host}), reporting disabled for localhost")
                    else:
                        logger.info("Skip status report: z2m configuration not found or mqtt.server empty")
            except Exception as e:
                logger.warning(f"Status report iteration error: {e}")
            logger.info("Next status report in 2 hours")
            # 2-hour interval, use 3-second intervals for convenient service exit
            for _ in range(2400):  # 2400 * 3s = 7200s = 2h
                if not self.running.is_set():
                    break
                time.sleep(3)
        logger.info("Status report thread stopped")

    def _start_status_reporter(self):
        """
        Start daemon thread if configuration file exists; do not start if it doesn't exist
        """
        try:
            if self._status_report_thread and self._status_report_thread.is_alive():
                return
            # Only start if configuration file exists
            if not os.path.exists("/opt/zigbee2mqtt/data/configuration.yaml"):
                logger.info("z2m configuration not found, status reporter not started")
                return
            # Only start status report thread when zigbee2mqtt.service is enabled
            try:
                result = subprocess.run(
                    ["systemctl", "is-enabled", "zigbee2mqtt.service"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                enabled_status = (result.stdout or "").strip()
                if result.returncode != 0 or enabled_status not in ("enabled", "enabled-runtime"):
                    logger.info(
                        f"zigbee2mqtt.service is not enabled (status='{enabled_status}'), "
                        "status reporter not started"
                    )
                    return
            except Exception as se:
                logger.warning(f"Failed to check zigbee2mqtt.service enabled status: {se}, status reporter not started")
                return
            
            self._status_report_thread = threading.Thread(target=self._status_report_loop, daemon=True)
            self._status_report_thread.start()
            logger.info("Status reporter started")
        except Exception as e:
            logger.warning(f"Failed to start status reporter: {e}")

    def perform_factory_reset(self):
        logging.info("Performing factory reset...")
        self.set_led_state(LedState.USER_EVENT_OFF)
        self.set_led_state(LedState.FACTORY_RESET)
        util.perform_factory_reset()

    def perform_power_off(self):
        logging.info("Performing power off...")
        # Here you can start a script
        util.perform_power_off()
    
    @util.threaded
    def perform_wifi_provision(self, progress_callback=None, complete_callback=None):
        """Execute WiFi provisioning"""
        logging.info("Initiating wifi provision...")
        try:
            # Start GATT provisioning mode
            if not self.gatt_manager.start_provisioning_mode():
                raise Exception("Failed to start GATT provisioning mode")
                
            if progress_callback:
                progress_callback(50, "WiFi provision GATT server started...")
                
            # Here you can add other provisioning logic
            
            if complete_callback:
                complete_callback(True, "WiFi provision mode activated")
        except Exception as e:
            logging.error(f"WiFi provision failed: {e}")
            if complete_callback:
                complete_callback(False, str(e))

    @util.threaded
    def finish_wifi_provision(self):
        """Finish WiFi provisioning"""
        logging.info("Finishing wifi provision...")
        self.gatt_manager.stop_provisioning_mode()

    def _signal_handler(self, sig, frame):
        logging.info("Signal received, stopping...")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up resources...")
        self.running.clear()

        # Stop Zeroconf service
        if hasattr(self, 'zeroconf_manager') and self.zeroconf_manager:
            try:
                self.zeroconf_manager.stop()
                logger.info("Zeroconf service stopped during cleanup")
            except Exception as e:
                logger.warning(f"Failed to stop Zeroconf during cleanup: {e}")

        # Stop HTTP server
        self._stop_http_server()
        # Stop GATT server
        self._stop_gatt_server()
        # Stop OTA server
        # try:
        #     if hasattr(self, 'ota_server') and self.ota_server:
        #         self.ota_server.stop()
        # except Exception as e:
        #     logger.warning(f"Failed to stop OTA server during cleanup: {e}")
        # Stop WiFi manager
        if hasattr(self, 'wifi_manager') and self.wifi_manager:
            self.wifi_manager.cleanup()

        if self.network_monitor:
            self.network_monitor.stop()

        self.task_manager.cleanup()

        try:
            self.led.off()  # Ensure LED is off
        except:
            pass

        if self.proxy:
            self.proxy.stop()
            self.proxy = None
        
        # Stop status reporter
        try:
            if self._status_report_thread and self._status_report_thread.is_alive():
                # Thread ends based on self.running, do not join and block here
                logger.info("Status reporter stopping...")
        except Exception:
            pass
        
        # Stop storage manager
        if hasattr(self, 'storage_manager') and self.storage_manager:
            try:
                self.storage_manager.stop()
            except Exception as e:
                logger.warning(f"Failed to stop storage manager: {e}")
        
        logger.info("Cleanup finished.")

    @util.threaded
    def start_zigbee_switch_zha(self):
        logger.info("Starting zigbee switch to zha mode...")
        self.task_manager.start_zigbee_switch_zha_mode()

    @util.threaded
    def start_zigbee_switch_z2m(self):
        logger.info("Starting zigbee switch to z2m mode...")
        self.task_manager.start_zigbee_switch_z2m_mode()

    def run(self):
        """Main run function"""
        logger.info("Starting supervisor...")

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.led.start()
        self.hwinit.initialize_pin()

        self.button.start()
        self.network_monitor.start()

        self._start_http_server()
        self._start_gatt_server()

        self.sysinfo_update.start()

        # Start OTA server
        # try:
        #     if self.ota_server:
        #         self.ota_server.start()
        # except Exception as e:
        #     logger.error(f"Failed to start OTA server: {e}")

        self.led.set_led_off_state()
        logger.info("[LED]Switch to other mode...")

        # Start storage manager (start storage space management service, start last)
        try:
            if self.storage_manager:
                self.storage_manager.start()
        except Exception as e:
            logger.error(f"Failed to start storage manager: {e}")

        self.proxy.run()


def main():
    import argparse
    from .commands import get_registry, execute_command, show_version
    
    registry = get_registry()
    available_commands = registry.list_commands()
    
    parser = argparse.ArgumentParser(
        description="Supervisor Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available commands: {', '.join(available_commands)}"
    )
    parser.add_argument('--version', '-v', action='store_true', help='Show version and exit')
    parser.add_argument(
        'command', 
        nargs='?', 
        default='daemon', 
        choices=available_commands,
        help="Command to run"
    )
    parser.add_argument('arg', nargs='?', default=None, help="Argument for command (e.g., color for led)")
    args = parser.parse_args()

    # Handle --version early and exit
    if getattr(args, 'version', False):
        show_version()
        sys.exit(0)

    # Execute command using the command registry
    exit_code = execute_command(args.command, args.arg)
    sys.exit(exit_code)               

if __name__ == "__main__":
    main()

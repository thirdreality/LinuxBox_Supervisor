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

from .network import NetworkMonitor
from .hardware import GpioButton, GpioLed, LedState,GpioHwController
from .utils.wifi_manager import WifiStatus, WifiManager
from .ota.ota_server import SupervisorOTAServer
from .task import TaskManager
from .utils import util

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
        # 硬件控制
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
        # self.ota_server = SupervisorOTAServer(self)  # 临时屏蔽OTA服务器

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
    

    def set_led_state(self, state):
        # Forward the LED state to the GpioLed instance
        self.led.set_led_state(state)

    def clear_led_state(self, state):
        # Forward the LED state clearing to the GpioLed instance
        self.led.clear_led_state(state)

    def set_ota_command(self, cmd):
        logger.info(f"OTA Command: param={cmd}")

    #### 异步命令 

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
            # 查询zigbee信息
            return get_zigbee_info()
        elif cmd_lower == "reset":
            try:
                # 使用 GPIO 让 Zigbee 芯片重启
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
                # 使用 GPIO 让 Thread 芯片重启
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
                return True, f"channel已经切换到{channel}"
            elif zigbee_mode == 'z2m':
                self.task_manager.start_z2m_channel_switch(channel)
                return True, f"channel已经切换到{channel}, 需要重新启动相关服务，请稍候"
            else:
                return False, "无法切换：未检测到Zigbee模式"
        except Exception as e:
            return False, f"切换失败: {e}"

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
            return True, f"channel需要5分钟切换到{channel}，请稍候"
        except Exception as e:
            return False, f"切换失败: {e}"

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

        # 只有从无IP到有IP的跃迁才触发
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

        # Start storage manager (启动存储空间管理服务，在最后启动)
        try:
            if self.storage_manager:
                self.storage_manager.start()
        except Exception as e:
            logger.error(f"Failed to start storage manager: {e}")

        self.proxy.run()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor Service")
    parser.add_argument('--version', '-v', action='store_true', help='Show version and exit')
    parser.add_argument('command', nargs='?', default='daemon', choices=['daemon', 'led', 'zigbee','thread','setting','ptest'], help="Command to run: daemon | led <color> | zigbee <parameter> | thread <parameter> | setting <parameter> | ptest <mode>")
    parser.add_argument('arg', nargs='?', default=None, help="Argument for command (e.g., color for led)")
    args = parser.parse_args()

    # Handle --version early and exit
    if getattr(args, 'version', False):
        print(f"Supervisor {VERSION} ({DEVICE_BUILD_NUMBER})")
        sys.exit(0)

    if args.command == 'daemon':
        supervisor = Supervisor()
        try:
            supervisor.run()
        except KeyboardInterrupt:
            supervisor.cleanup()
            logger.info("Supervisor terminated by user")
        except Exception as e:
            logger.error(f"Unhandled exception: {e}")
            supervisor.cleanup()
    elif args.command == 'led':
        value = args.arg
        print(f"[Main]input led arg: {value}")
        if value is None:
            print("Usage: supervisor.py led <arg>")
            print("Supported: on|off|clear, colors [red|blue|yellow|green|white|cyan|magenta|purple], states [reboot|startup|factory_reset|sys_normal_operation|...]")
            sys.exit(1)
        try:
            client = SupervisorClient()
            # Pass-through; server端负责解析 enable/disable/clear/颜色/状态
            resp = client.send_command("led", value, "Led command")
            if resp:
                print(resp)
        except Exception as e:
            print(f"Error sending LED command: {e}")
            sys.exit(1)
    elif args.command in ['ota', 'zigbee', 'thread', 'setting', 'ptest']:
        param = args.arg
        if param is None:
            print(f"Usage: supervisor.py {args.command} <parameter>")
            if args.command == 'ptest':
                print("Available modes: start")
            sys.exit(1)
            
        try:
            client = SupervisorClient()
            # Directly use the send_command method for simplicity and flexibility
            response = client.send_command(args.command, param, f"{args.command} command")
            
            if response is None:
                print(f"Error: Failed to send {args.command} command")
                sys.exit(1)
                
            # For ptest, the response handling is done in the client
            if args.command != 'ptest':
                # Special handling for info commands - display JSON response
                if param == 'info':
                    try:
                        # Try to parse and pretty print JSON response
                        json_data = json.loads(response)
                        print(json.dumps(json_data, indent=2, ensure_ascii=False))
                    except (json.JSONDecodeError, TypeError):
                        # If not valid JSON, print as is
                        print(response)
                else:
                    print(f"{args.command.capitalize()} command sent successfully: {param}")
        except Exception as e:
            print(f"Error sending {args.command} command: {e}")
            sys.exit(1)               

if __name__ == "__main__":
    main()

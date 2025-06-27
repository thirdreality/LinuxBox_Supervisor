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
        # self.ota_server = SupervisorOTAServer(self)

        # boot up time
        self.start_time = time.time()
    

    def set_led_state(self, state):
        # Forward the LED state to the GpioLed instance
        self.led.set_led_state(state)

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
            return get_ha_zigbee_mode()
        elif cmd_lower == "scan":
            try:
                self.task_manager.start_zigbee_pairing(led_controller=self.led)
                logger.info("Zigbee device started pairing")
                return "Zigbee device started pairing"
            except Exception as e:
                logger.error(f"Zigbee pairing start failed: {e}")
                return f"Zigbee pairing start failed: {e}"
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
        elif cmd.lower() == "updated":
            try:
                self.task_manager.start_setting_updated()
                logger.info("Setting updated finish")
                return "Setting updated finish"
            except Exception as e:
                logger.error(f"Setting updated fail: {e}")
                return f"Setting updated fail: {e}"
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
        logger.info("checking Network onNetworkFirstConnected() ...")

    def onNetworkDisconnect(self):
        logger.info("checking Network onNetworkDisconnect() ...")

    def onNetworkConnected(self):
        logger.info("checking Network onNetworkConnected() ...")

    def update_wifi_info(self, ip_address, ssid):
        """Update WiFi information cache"""
        prev_ip = self.wifi_status.ip_address
        ip_changed = prev_ip != ip_address

        # Update WiFi status information
        if ip_changed:
            logger.info(f"Update wifi info: {ip_address}")
            self.wifi_status.ip_address = ip_address
            self.wifi_status.ssid = ssid
            logger.debug("WiFi info updated")

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

        # Stop HTTP server
        self._stop_http_server()
        # Stop GATT server
        self._stop_gatt_server()
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

        self.led.set_led_off_state()
        logger.info("[LED]Switch to other mode...")

        self.proxy.run()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor Service")
    parser.add_argument('command', nargs='?', default='daemon', choices=['daemon', 'led', 'zigbee','thread','setting'], help="Command to run: daemon | led <color> | zigbee <parameter> | thread <parameter> | setting <parameter>")
    parser.add_argument('arg', nargs='?', default=None, help="Argument for command (e.g., color for led)")
    args = parser.parse_args()

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
        color = args.arg
        print(f"[Main]input color: {color}")
        if color is None:
            print("Usage: supervisor.py led <color>")
            print("Support colors: [mqtt_paring|mqtt_pared|mqtt_error|mqtt_normal|reboot|power_off|normal|network_error|network_lost|startu]")
            sys.exit(1)
        try:
            # Support simplified color name to USER_EVENT mapping
            user_event_map = {
                'red': LedState.USER_EVENT_RED,
                'blue': LedState.USER_EVENT_BLUE,
                'yellow': LedState.USER_EVENT_YELLOW,
                'green': LedState.USER_EVENT_GREEN,
                'white': LedState.USER_EVENT_WHITE,
                'off': LedState.USER_EVENT_OFF
            }
            led_state = getattr(LedState, color.upper(), None)
            if led_state is None:
                led_state = user_event_map.get(color.lower())
            if led_state is None:
                print(f"[Main]Unknown color: {color}, support [mqtt_paring|mqtt_pared|mqtt_error|mqtt_normal|reboot|power_off|normal|network_error|network_lost|startup|red|blue|yellow|green|white|off]")
                sys.exit(1)
            client = SupervisorClient()
            client.send_command("led", color.upper(), "Led command")
            #client.set_led_state(color.upper())
            print(f"LED set to {color}")
        except Exception as e:
            print(f"Error setting LED color: {e}")
            sys.exit(1)
    elif args.command in ['ota', 'zigbee', 'thread', 'setting']:
        param = args.arg
        if param is None:
            print(f"Usage: supervisor.py {args.command} <parameter>")
            sys.exit(1)
            
        try:
            client = SupervisorClient()
            # Directly use the_send_command method for simplicity and flexibility
            response = client.send_command(args.command, param, f"{args.command} command")
            
            if response is None:
                print(f"Error: Failed to send {args.command} command")
                sys.exit(1)
                
            print(f"{args.command.capitalize()} command sent successfully: {param}")
        except Exception as e:
            print(f"Error sending {args.command} command: {e}")
            sys.exit(1)               

if __name__ == "__main__":
    main()

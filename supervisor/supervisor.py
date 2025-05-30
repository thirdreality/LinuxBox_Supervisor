#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading
import time
import logging
import signal
import socket
import tempfile
import platform
import json
import sys
import subprocess

from .network import NetworkMonitor
from .hardware import GpioButton, GpioLed, LedState,GpioHwController
from .utils.wifi_manager import WifiStatus, WifiManager
from .ota.ota_server import SupervisorOTAServer

from .ble.gattserver import SupervisorGattServer
from .http_server import SupervisorHTTPServer  
from .proxy import SupervisorProxy
from .cli import SupervisorClient
from .sysinfo import SystemInfoUpdater, SystemInfo, HomeAssistantInfo, Zigbee2mqttInfo, homekitbridgeInfo

from .utils import utils
from .utils.utils import OtaStatus

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
        self.ota_status = OtaStatus()
        self.system_info = SystemInfo()

        self.wifi_manager = WifiManager()
        self.wifi_manager.init()

        self.proxy = SupervisorProxy(self)
        self.http_server = None
        self.gatt_server = None

        self.network_monitor = NetworkMonitor(self)
        self.sysinfo_update = SystemInfoUpdater(self)
        self.ota_server = SupervisorOTAServer(self)

        # boot up time
        self.start_time = time.time()
        
        # Flag to indicate if home-assistant needs to be resumed
        self.ha_resume_need = False

    def set_led_state(self, state):
        # Forward the LED state to the GpioLed instance
        self.led.set_led_state(state)

    def set_ota_command(self, cmd):
        logger.info(f"OTA Command: param={cmd}")

    def set_zigbee_command(self, cmd):
        logger.info(f"zigbee Command: param={cmd}")

    def set_thread_command(self, cmd):
        logger.info(f"thread Command: param={cmd}")
        
        # 如果命令是enable，则启用Thread支持
        if cmd.lower() == "enable":
            logger.info("Enabling Thread support")
            self.enableThreadSupported()
            return "Thread support enabled"
        elif cmd.lower() == "disable":
            logger.info("Disabling Thread support")
            self.disableThreadSupported()
            return "Thread support disabled"

    def get_led_state(self):
        # Get the LED state from the GpioLed instance
        return self.led.get_led_state()
        
    def isThreadSupported(self):
        return self.system_info.support_thread

    def isZigbeeSupported(self):
        return self._support_zigbee

    def enableThreadSupported(self):
        self.system_info.support_thread = True

    def disableThreadSupported(self):
        self.system_info.support_thread = False

    def enableZigbeeSupported(self):
        self._support_zigbee = True

    def _is_tmp_mounted(self):
        return os.system("mountpoint -q /tmp") == 0
    
    def onNetworkFirstConnected(self):
        logger.info("checking Network onNetworkFirstConnected() ...")

    def onNetworkDisconnect(self):
        logger.info("checking Network onNetworkDisconnect() ...")

    def onNetworkConnected(self):
        logger.info("checking Network onNetworkConnected() ...")
        

    def check_ha_resume(self):
        # Check if we need to resume home-assistant
        if self.ha_resume_need:
            logger.info("Resuming home-assistant service...")
            # Start home-assistant in a separate thread
            def start_ha_service():
                logger.info("Starting home-assistant service...")
                utils.execute_system_command(["systemctl", "start", "home-assistant"])
                logger.info("home-assistant service started")
                # Reset the flag
                self.ha_resume_need = False
            
            # Create and start the thread
            ha_thread = threading.Thread(target=start_ha_service)
            ha_thread.daemon = True
            ha_thread.start()


    def update_wifi_info(self, ip_address, ssid):
        """更新WiFi信息缓存"""
        logger.info(f"Update wifi info: {ip_address}")
        self.wifi_status.ip_address = ip_address
        self.wifi_status.ssid = ssid
        if self.gatt_server:
            if ip_address == "":
                self.gatt_server.updateAdv("0.0.0.0")
            else:
                self.gatt_server.updateAdv(ip_address)
        else:
            logger.info("gatt_server not initialized, skipping updateAdv operation")
        return True

    def update_system_uptime(self):
        """更新系统运行时间"""
        if 'uptime' in self.system_info:
            self.system_info['uptime'] = int(time.time() - self.start_time)

    def _start_http_server(self):
        """启动HTTP服务器"""
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
        if not self.gatt_server:
            # Check if bluetooth service is running
            def check_bluetooth_service():
                try:
                    import subprocess
                    result = subprocess.run(['systemctl', 'is-active', 'bluetooth'], capture_output=True, text=True)
                    return result.stdout.strip() == 'active'
                except Exception as e:
                    logger.error(f"Error checking bluetooth service: {e}")
                    return False
            
            # Function to be run in a separate thread to monitor bluetooth service
            def bluetooth_monitor_thread():
                max_retries = 30
                retry_delay = 3  # seconds
                
                for attempt in range(max_retries):
                    try:
                        # Check if bluetooth service is active
                        if not check_bluetooth_service():
                            logger.warning(f"Bluetooth service not active yet, waiting {retry_delay} seconds (attempt {attempt+1}/{max_retries})")
                            time.sleep(retry_delay)
                            continue
                        
                        # Try to start the GATT server
                        self.gatt_server = SupervisorGattServer(self)
                        self.gatt_server.start()
                        logger.info("GATT server started successfully")
                        return
                    except Exception as e:
                        if "org.freedesktop.DBus.Error.ServiceUnknown" in str(e):
                            logger.warning(f"DBus service not ready yet, retrying in {retry_delay} seconds (attempt {attempt+1}/{max_retries})")
                            time.sleep(retry_delay)
                        else:
                            logger.error(f"Failed to start GATT server: {e}")
                            return
                
                logger.error(f"Failed to start GATT server after {max_retries} attempts")
            
            # Start the bluetooth monitor in a separate thread
            bluetooth_thread = threading.Thread(target=bluetooth_monitor_thread, daemon=True)
            bluetooth_thread.start()
            logger.info("Started bluetooth service monitor thread for GATT server")
            return True
        return True

    def _stop_gatt_server(self):
        if not self.gatt_server:
            return True
        try:
            self.gatt_server.stop()
            self.gatt_server = None
            logger.info("GATT server stopped")
            return True
        except Exception as e:
            logger.error(f"Failed to stop GATT server: {e}")
            return False

    def perform_reboot(self):
        logging.info("Performing reboot...")
        utils.perform_reboot()

    def perform_factory_reset(self):
        logging.info("Performing factory reset...")

        self.set_led_state(LedState.FACTORY_RESET)
        utils.perform_factory_reset()

    def perform_power_off(self):
        logging.info("Performing power off...")
        # 这里可以启动一个脚本
        utils.perform_power_off()
    
    def perform_wifi_provision_prepare(self):
        logging.info("Performing prepare wifi provision...")
        
        # Check if home-assistant is running using subprocess directly to get output
        try:
            result = subprocess.run(["systemctl", "is-active", "home-assistant"], 
                                   capture_output=True, text=True, check=False)
            if result.stdout.strip() == "active":
                # Set the flag to resume home-assistant later
                self.ha_resume_need = True
                logging.info("home-assistant is running, will resume after network connected")
        except Exception as e:
            logging.error(f"Error checking home-assistant status: {e}")
        
        # Start thread to stop home-assistant
        thread = threading.Thread(target=utils.perform_wifi_provision_prepare)
        thread.daemon = True
        thread.start()

    def _signal_handler(self, sig, frame):
        logging.info("Signal received, stopping...")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        """清理资源"""
        logger.info("Cleaning up resources...")
        self.running.clear()

        # 停止HTTP服务器
        self._stop_http_server()
        # 停止GATT服务器
        self._stop_gatt_server()
        # 停止WiFi管理器    
        self.wifi_manager.cleanup()

        self.network_monitor.stop()

        try:
            self.led.off()  # 确保LED关闭
        except:
            pass

        if self.proxy:
            self.proxy.stop()
            self.proxy = None

    def run(self):
        """主运行函数"""
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

        self.proxy.run()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor Service")
    parser.add_argument('command', nargs='?', default='daemon', choices=['daemon', 'led', 'ota', 'zigbee','thread','sysinfo'], help="Command to run: daemon | led <color> | sysinfo")
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
        if color is None:
            print("Usage: supervisor.py led <color>")
            print("Support colors: mqtt_paring|mqtt_pared|mqtt_error|mqtt_normal|reboot|power_off|normal|network_error|network_lost|startup")
            sys.exit(1)
        try:
            led_state = getattr(LedState, color.upper(), None)
            if led_state is None:
                print(f"Unknown color: {color}, support [mqtt_paring|mqtt_pared|mqtt_error|mqtt_normal|reboot|power_off|normal|network_error|network_lost|startup")
                sys.exit(1)
            client = SupervisorClient()
            client.send_command("led", color.upper(), "Led command")
            #client.set_led_state(color.upper())
            print(f"LED set to {color}")
        except Exception as e:
            print(f"Error setting LED color: {e}")
            sys.exit(1)
    # Handle command types that follow the same pattern (ota, zigbee, thread)
    elif args.command in ['ota', 'zigbee', 'thread']:
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
    elif args.command == 'sysinfo':
        print(json.dumps(supervisor.system_info, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

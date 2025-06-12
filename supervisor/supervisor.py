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

from .ble.gattserver import SupervisorGattServer
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
        self.gatt_server = None

        self.network_monitor = NetworkMonitor(self)
        self.sysinfo_update = SystemInfoUpdater(self)
        self.ota_server = SupervisorOTAServer(self)

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
                logger.info("Zigbee设备开始配对")
                return "Zigbee设备开始配对"
            except Exception as e:
                logger.error(f"Zigbee配对启动失败: {e}")
                return f"Zigbee配对启动失败: {e}"
        elif cmd_lower == "z2m":
            try:
                self.task_manager.start_zigbee_switch_z2m_mode()
                logger.info("Zigbee设备开始配对")
                return "Zigbee设备开始配对"
            except Exception as e:
                logger.error(f"Zigbee配对启动失败: {e}")
                return f"Zigbee配对启动失败: {e}"
        elif cmd_lower == "info":
            # 查询zigbee信息
            zigbee_util.get_ha_zigbee_mode()
        elif cmd_lower == "scan":
            try:
                self.task_manager.start_zigbee_pairing(led_controller=self.led)
                logger.info("Zigbee设备开始配对")
                return "Zigbee设备开始配对"
            except Exception as e:
                logger.error(f"Zigbee配对启动失败: {e}")
                return f"Zigbee配对启动失败: {e}"
        elif cmd_lower == "update":
            try:
                self.task_manager.start_zigbee_ota()
                logger.info("Zigbee设备开始配对")
                return "Zigbee设备开始配对"
            except Exception as e:
                logger.error(f"Zigbee配对启动失败: {e}")
                return f"Zigbee配对启动失败: {e}"
        else:
            logger.warning(f"未知的Zigbee命令: {cmd}")
            return f"未知的Zigbee命令: {cmd}"

            
    def set_thread_command(self, cmd):
        logger.info(f"thread Command: param={cmd}")
        
        if cmd.lower() == "enabled":
            # 用作不同模块之间状态同步
            logger.info("Set Enabled Thread state")
            self.system_info.support_thread = True
            return "Thread support enabled"
        elif cmd.lower() == "disabled":
            # 用作不同模块之间状态同步
            logger.info("Set Disabled Thread state")
            self.system_info.support_thread = False
            return "Thread support disabled"
        elif cmd.lower() == "enable":
            try:
                self.task_manager.start_thread_mode_enable()
                logger.info("Thread support enabled")
                return "Thread support enabled"
            except Exception as e:
                logger.error(f"Thread support enable fail: {e}")
                return f"Thread support enable fail: {e}" 
        elif cmd.lower() == "disable":
            # 关闭Thread支持
            try:
                self.task_manager.start_thread_mode_disable()
                logger.info("Thread support disabled")
                return "Thread support disabled"
            except Exception as e:
                logger.error(f"Thread support disable fail: {e}")
                return f"Thread support disable fail: {e}"


    def set_setting_command(self, cmd):
        logger.info(f"setting Command: param={cmd}")
        
        # 如果命令是enable，则启用Thread支持
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

    def start_setting_restore(self) -> bool:
        """
        Starts the setting restore process.

        Returns:
            bool: True if the restore process started successfully, False otherwise.
        """
        try:
            self.task_manager.start_setting_restore()
            logger.info("Setting restore process started successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to start setting restore process: {e}")
            return False



    def get_led_state(self):
        # Get the LED state from the GpioLed instance
        return self.led.get_led_state()
        
    def isThreadSupported(self):
        return self.system_info.support_thread

    def isZigbeeSupported(self):
        return self._support_zigbee


    def onNetworkFirstConnected(self):
        logger.info("checking Network onNetworkFirstConnected() ...")

    def onNetworkDisconnect(self):
        logger.info("checking Network onNetworkDisconnect() ...")

    def onNetworkConnected(self):
        logger.info("checking Network onNetworkConnected() ...")

    # def check_ha_resume(self):
    #     # Check if we need to resume home-assistant
    #     if self.ha_resume_need:
    #         logger.info("Resuming home-assistant service...")
    #         # Start home-assistant in a separate thread
    #         @util.threaded
    #         def start_ha_service():
    #             logger.info("Starting home-assistant service...")
    #             util.execute_system_command(["systemctl", "start", "home-assistant"])
    #             logger.info("home-assistant service started")
    #             # Reset the flag
    #             self.ha_resume_need = False
            
    #         # Start the thread
    #         start_ha_service()

    def update_wifi_info(self, ip_address, ssid):
        """更新WiFi信息缓存"""
        # 只有在IP地址发生变化时才更新
        if self.wifi_status.ip_address != ip_address:
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
            @util.threaded
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
                            logger.error(f"Failed to start GATT server: {e} (Type: {type(e)}, Args: {e.args})")
                            return
                
                logger.error(f"Failed to start GATT server after {max_retries} attempts")
            
            # Start the bluetooth monitor
            bluetooth_monitor_thread()
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
        util.perform_reboot()

    def perform_factory_reset(self):
        logging.info("Performing factory reset...")

        self.set_led_state(LedState.FACTORY_RESET)
        util.perform_factory_reset()

    def perform_power_off(self):
        logging.info("Performing power off...")
        # 这里可以启动一个脚本
        util.perform_power_off()
    
    @util.threaded
    def perform_wifi_provision(self):
        logging.info("Initiating wifi provision...")
        self.wifi_manager.start_wifi_provision()

    @util.threaded
    def finish_wifi_provision(self):
        logging.info("Initiating finish wifi provision...")
        self.wifi_manager.stop_wifi_provision()

    def startAdv(self):
        logging.info("Supervisor: Starting BLE Advertisement...")
        if self.gatt_server:
            self.gatt_server.startAdv()
            return True
        else:
            logging.warning("Supervisor: GATT server not initialized, cannot start advertisement.")
            return False

    def stopAdv(self):
        logging.info("Supervisor: Stopping BLE Advertisement...")
        if self.gatt_server:
            self.gatt_server.stopAdv()
            return True
        else:
            logging.warning("Supervisor: GATT server not initialized, cannot stop advertisement.")
            return False

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
        if hasattr(self, 'wifi_manager') and self.wifi_manager:
            self.wifi_manager.cleanup()

        if self.network_monitor:
            self.network_monitor.stop()

        self.task_manager.cleanup()

        try:
            self.led.off()  # 确保LED关闭
        except:
            pass

        if self.proxy:
            self.proxy.stop()
            self.proxy = None
        
        logger.info("Cleanup finished.")

    @util.threaded
    def start_zigbee_switch_zha(self):
        self.logger.info("Starting zigbee switch to zha mode...")
        self.task_manager.start_zigbee_switch_zha_mode()

    @util.threaded
    def start_zigbee_switch_z2m(self):
        self.task_manager.start_zigbee_switch_z2m_mode()

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

        self.led.set_led_off_state()
        logger.info("[LED]Switch to other mode...")

        self.task_manager.start_auto_wifi_provision()

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
            # 支持简化颜色名到USER_EVENT映射
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

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

from .network import NetworkMonitor
from .hardware import GpioButton, GpioLed, LedState,GpioHwController
from .utils.wifi_manager import WifiStatus, WifiManager
from .ota.ota_server import SupervisorOTAServer


from .ble.gattserver import SupervisorGattServer
from .http_server import SupervisorHTTPServer  
from .proxy import SupervisorProxy
from .cli import SupervisorClient

from .utils.utils import (
    execute_system_command,
    perform_reboot,
    perform_power_off    
)

from .utils.utils import SystemInfo, OtaStatus


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
    _support_zigbee=True
    _support_thread=False
    #zigbee2mqtt, homekitbridge, homeassistant-core
    _worker_mode="homeassistant-core"

    def __init__(self):
        # 硬件控制
        self.hwinit = GpioHwController(self)
        self.led = GpioLed(self)
        self.button = GpioButton(self)        

        # LED state and running state
        self.current_led_state = LedState.STARTUP
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
        self.ota_server = SupervisorOTAServer(self)

        
        # boot up time
        self.start_time = time.time()

    def set_led_state(self, state):
        with self.state_lock:
            # 最高优先级：如果新状态是 REBOOT 或 POWER_OFF，则无条件地设置状态
            if state in [LedState.REBOOT, LedState.POWER_OFF]:
                self.current_led_state = state
            else:
                # 如果当前状态是 PARING 且新状态是 PARED 或 NORMAL，则转换为 NORMAL
                if self.current_led_state == LedState.MQTT_PARING and state == LedState.MQTT_PARED:
                    self.current_led_state = LedState.NORMAL
                elif self.current_led_state == LedState.MQTT_ERROR and state == LedState.MQTT_NORMAL:
                    self.current_led_state = LedState.NORMAL                    
                # 否则，如果当前状态不是 REBOOT, POWER_OFF, 或 PARING，则更新状态
                elif self.current_led_state not in [LedState.REBOOT, LedState.POWER_OFF, LedState.MQTT_PARING, LedState.MQTT_ERROR]:
                    self.current_led_state = state

    def get_led_state(self):
        with self.state_lock:
            return self.current_led_state
        
    def isThreadSupported(self):
        return self._support_thread

    def isZigbeeSupported(self):
        return self._support_zigbee

    def enableThreadSupported(self):
        self._support_thread = True

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

    def _handle_socket_command(self, data, conn):
        """处理来自Socket的命令"""
        try:
            command = data.decode('utf-8').strip()
            logger.info(f"Received command: {command}")
            
            # 示例命令处理
            if command == "status":
                state = self.get_led_state().value
                conn.sendall(f"LED State: {state}".encode('utf-8'))
            elif command == "reboot":
                conn.sendall(b"Rebooting...")
                self.perform_reboot()
            elif command == "factory_reset":
                conn.sendall(b"Factory resetting...")
                self.perform_factory_reset()
            elif command == "wifi_status":
                self.update_wifi_info()
                conn.sendall(str(self.wifi_info).encode('utf-8'))
            else:
                conn.sendall(b"Unknown command")
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            try:
                conn.sendall(f"Error: {str(e)}".encode('utf-8'))
            except:
                pass
        finally:
            try:
                conn.close()
            except:
                pass

    def update_wifi_info(self):
        """更新WiFi信息缓存"""
        return True

    def configure_wifi(self, ssid, password):
        """配置WiFi连接"""
        if not ssid:
            return False
            
        try:
            logger.info(f"Configuring WiFi: SSID={ssid}")
            # 这里实现WiFi配置逻辑，例如写入wpa_supplicant.conf
            # 或者使用NetworkManager等工具
            
            # 示例: 使用wpa_cli (需要根据实际环境调整)
            # commands = [
            #    f'wpa_cli -i wlan0 add_network',
            #    f'wpa_cli -i wlan0 set_network 0 ssid \\"{ssid}\\"',
            #    f'wpa_cli -i wlan0 set_network 0 psk \\"{password}\\"',
            #    f'wpa_cli -i wlan0 enable_network 0',
            #    f'wpa_cli -i wlan0 save_config'
            # ]
            # for cmd in commands:
            #    subprocess.check_call(cmd, shell=True)
            
            # 触发网络状态更新 - 实际环境中可能需要延迟
            time.sleep(5)  # 等待WiFi连接
            self.update_wifi_info()
            
            # 如果连接成功，更新LED状态
            if self.wifi_info['connected']:
                self.set_led_state(LedState.NORMAL)
                return True
            else:
                self.set_led_state(LedState.NETWORK_ERROR)
                return False
        except Exception as e:
            logger.error(f"WiFi configuration failed: {e}")
            self.wifi_info['error_message'] = str(e)
            self.set_led_state(LedState.NETWORK_ERROR)
            return False

    def update_system_uptime(self):
        """更新系统运行时间"""
        if 'uptime' in self.system_info:
            self.system_info['uptime'] = int(time.time() - self.start_time)

    def _start_http_server(self):
        """启动HTTP服务器"""
        if not self.http_server:
            try:

                # 创建并启动HTTP服务器
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
            try:
                # 创建并启动GATT服务器
                self.gatt_server = SupervisorGattServer(self)
                self.gatt_server.start()
                logger.info("GATT server started")
                return True
            except Exception as e:
                logger.error(f"Failed to start GATT server: {e}")
                return False
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
        self.set_led_state(LedState.REBOOT)
        perform_reboot()

    def perform_factory_reset(self):
        logging.info("Performing factory reset...")
        self.set_led_state(LedState.REBOOT)
        # 这里可以添加清除配置的代码
        perform_reboot()

    def perform_power_off(self):
        logging.info("Performing power off...")
        self.set_led_state(LedState.POWER_OFF)
        # 这里可以启动一个脚本
        perform_power_off()
    
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

        # 关闭硬件
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
        if self._support_thread == False:
            execute_system_command(["systemctl", "disable", "otbr-agent"])

        self.button.start()
        self.network_monitor.start()

        self._start_http_server()
        self._start_gatt_server()

        self.proxy.run()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor Service")
    parser.add_argument('command', nargs='?', default='daemon', choices=['daemon', 'led', 'sysinfo'], help="Command to run: daemon | led <color> | sysinfo")
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
            client.set_led_state(color.upper())
            print(f"LED set to {color}")
        except Exception as e:
            print(f"Error setting LED color: {e}")
            sys.exit(1)
    elif args.command == 'sysinfo':
        print(json.dumps(supervisor.system_info, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

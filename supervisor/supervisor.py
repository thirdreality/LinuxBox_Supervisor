#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading
import time
import logging

import socket
import tempfile
import platform

from hardware import GpioButton, GpioLed, LedState,GpioHwController
from utils.wifi_manager import WifiStatus, WifiManager
from ble.gattserver import SupervisorGattServer

from utils.utils import (
    execute_system_command,
    perform_reboot,
    perform_power_off,
    get_mac_address,
    get_ip_address,
    get_wifi_ssid,
    is_wifi_connected
)


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
    SOCKET_PATH = "/tmp/led_socket"

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
        self.wifi_manager = WifiManager()
        self.wifi_manager.init()

        # initial 
        self.server = None
        self.server_thread = None
        self.http_server = SupervisorHTTPServer(self)
        self.gatt_server = SupervisorGattServer(self)
        
        # boot up time
        self.start_time = time.time()
                
        # system information
        self.system_info = self._get_system_info()

    def _get_system_info(self):
        """获取系统信息并缓存"""
        try:
            return {
                "model": "LinuxBox",
                "version": "1.0.0",
                "hostname": socket.gethostname(),
                "platform": platform.system(),
                "platform_version": platform.version(),
                "architecture": platform.machine(),
                "processor": platform.processor()
            }
        except Exception as e:
            logger.error(f"Error getting system info: {e}")
            return {
                "model": "LinuxBox",
                "version": "1.0.0",
                "hostname": "unknown",
                "uptime": 0
            }

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

    def _is_tmp_mounted(self):
        return os.system("mountpoint -q /tmp") == 0
    
    def onNetworkFirstConnected(self):
        print("checking Network ...")

    def onNetworkDisconnect(self):
        print("checking Network ...")

    def onNetworkConnected(self):
        print("checking Network ...")

    def _ensure_tmp_ready(self, timeout=60, interval=1):
        start_time = time.time()
        
        while not self._is_tmp_mounted():
            time.sleep(interval)

        print("/tmp is mounted")
        successful_check = False

        time.sleep(5)
        print("checking /tmp ...")

        while time.time() - start_time < timeout:
            try:
                if os.path.exists("/tmp") and os.access("/tmp", os.W_OK):
                    fd, temp_path = tempfile.mkstemp(dir='/tmp')
                    try:
                        os.write(fd, b'Test Write')
                        os.fsync(fd)  # Ensure data is flushed to disk
                        successful_check = True
                    finally:
                        os.close(fd)
                        os.remove(temp_path)
                    if successful_check:
                        print("/tmp check: OK")
                        return True
            except OSError as e:
                print(f"OS error while checking /tmp: {e}")
            
            time.sleep(interval)

        return False

    def _setup_socket(self):
        self._ensure_tmp_ready()
        time.sleep(1)

        if os.path.exists(self.SOCKET_PATH):
            os.remove(self.SOCKET_PATH)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.SOCKET_PATH)
        self.server.listen(1)
        self.server.settimeout(1.0)
    
    def _socket_thread(self):
        """运行Socket服务器线程"""
        logger.info("Starting socket server...")
        
        while self.running.is_set():
            try:
                conn, _ = self.server.accept()
                data = conn.recv(1024)
                if data:
                    self._handle_socket_command(data, conn)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running.is_set():
                    logger.error(f"Socket error: {e}")
                    time.sleep(1)

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
        try:
            connected = is_wifi_connected()
            self.wifi_info.update({
                'connected': connected,
                'ssid': get_wifi_ssid() if connected else '',
                'ip_address': get_ip_address() if connected else '',
                'error_message': '' if connected else 'WiFi not connected'
            })
            return True
        except Exception as e:
            logger.error(f"Error updating WiFi info: {e}")
            self.wifi_info['error_message'] = str(e)
            return False

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

    def start_http_server(self):
        """启动HTTP服务器"""
        if not self.http_server:
            try:
                # 导入HTTP服务器类
                from http_server import SupervisorHTTPServer
                
                # 创建并启动HTTP服务器
                self.http_server = SupervisorHTTPServer(self, port=8086)
                self.http_server.start()
                logger.info("HTTP server started")
                return True
            except Exception as e:
                logger.error(f"Failed to start HTTP server: {e}")
                return False
        return True

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
    
    def cleanup(self):
        """清理资源"""
        logger.info("Cleaning up resources...")
        self.running.clear()

        # 停止HTTP服务器
        if self.http_server:
            self.http_server.stop()
            self.http_server = None
        
        # 关闭Socket
        if self.server:
            try:
                self.server.close()
                if os.path.exists(self.SOCKET_PATH):
                    os.remove(self.SOCKET_PATH)
            except Exception as e:
                logger.error(f"Error closing socket: {e}")
        
        self.wifi_manager.cleanup()

        # 关闭硬件
        try:
            self.led.off()  # 确保LED关闭
        except:
            pass

    def run(self):
        """主运行函数"""
        logger.info("Starting supervisor...")
        self.led.run()

        self.hwinit()
        if self._support_thread == False:
            execute_system_command(["systemctl", "disable", "otbr-agent"])

        self.button.run() 

import sys

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor Service")
    parser.add_argument('command', nargs='?', default='daemon', choices=['daemon', 'led', 'sysinfo'], help="Command to run: daemon | led <color> | sysinfo")
    parser.add_argument('arg', nargs='?', default=None, help="Argument for command (e.g., color for led)")
    args = parser.parse_args()

    supervisor = Supervisor()

    if args.command == 'daemon':
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
            sys.exit(1)
        # Set LED color (assuming LedState uses color names)
        try:
            led_state = getattr(LedState, color.upper(), None)
            if led_state is None:
                print(f"Unknown color: {color}")
                sys.exit(1)
            supervisor.set_led_state(led_state)
            print(f"LED set to {color}")
        except Exception as e:
            print(f"Error setting LED color: {e}")
            sys.exit(1)
    elif args.command == 'sysinfo':
        import json
        print(json.dumps(supervisor.system_info, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()


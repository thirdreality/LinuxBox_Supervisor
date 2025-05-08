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
        self.sysinfo_update = SystemInfoUpdater(self)
        self.ota_server = SupervisorOTAServer(self)

        # boot up time
        self.start_time = time.time()

    def set_led_state(self, state):
        with self.state_lock:
            # 最高优先级：如果新状态是 REBOOT 或 POWER_OFF，则无条件地设置状态
            if state in [LedState.REBOOT, LedState.POWER_OFF, LedState.FACTORY_RESET]:
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
        return self.system_info.support_thread

    def isZigbeeSupported(self):
        return self._support_zigbee

    def enableThreadSupported(self):
        self.system_info.support_thread = True

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


    def update_wifi_info(self, ip_address, ssid):
        """更新WiFi信息缓存"""
        logger.info(f"Update wifi info: {ip_address}")
        self.wifi_status.ip_address = ip_address
        self.wifi_status.ssid = ssid
        if ip_address == "":
            self.gatt_server.updateAdv("0.0.0.0")
        else:
            self.gatt_server.updateAdv(ip_address)
        return True

    def configure_wifi(self, ssid, password):
        """配置WiFi连接"""
        if not ssid:
            return False
            
        try:
            logger.info(f"Configuring WiFi: SSID={ssid}")
            
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
        utils.perform_reboot()

    def perform_factory_reset(self):
        logging.info("Performing factory reset...")
        # 这里可以添加清除配置的代码
        utils.perform_factory_reset()

    def perform_power_off(self):
        logging.info("Performing power off...")
        # 这里可以启动一个脚本
        utils.perform_power_off()
    
    def perform_wifi_provision_prepare(self):
        logging.info("Performing prepare wifi provision...")
        utils.perform_wifi_provision_prepare()

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
        if self.system_info.support_thread == False:
            utils.execute_system_command(["systemctl", "disable", "otbr-agent"])

        self.button.start()
        self.network_monitor.start()

        self._start_http_server()
        self._start_gatt_server()

        self.sysinfo_update.start()

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

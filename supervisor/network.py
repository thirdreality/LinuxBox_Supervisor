# maintainer: guoping.liu@thirdreality.com

"""Network Monitor for HubV3/LinuxBox"""

import os
import time
import logging
import threading

from hardware import LedState
from wifi_utils.utils import (
    get_wlan0_mac,
    is_interface_existing,
    is_network_connected,
    has_active_connection,
    get_wlan0_ip,
    get_active_connection_name
)

class NetworkMonitor:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.network_thread = None
    
    def network_monitor_task(self):
        self.logger.info("Starting Network monitor...")

        check_interval = 2
        time.sleep(check_interval)
        check_interval = 1
        disconnect_count = 0
        firstConnected=False

        mac_address = None

        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            if is_interface_existing("wlan0"):
                if mac_address == None:
                    """获取Mac地址并缓存"""
                    mac_address = get_wlan0_mac()
                    self.supervisor.wifi_status.mac_address = mac_address
        
                if is_network_connected():
                    self.supervisor.set_led_state(LedState.NORMAL)

                    self.supervisor.wifi_status.connected = True
                    disconnect_count = 0

                    if firstConnected == False:
                        """获取SSID/IP并缓存"""
                        firstConnected=True
                        self.supervisor.wifi_status.ip_address = get_wlan0_ip()
                        self.supervisor.wifi_status.ssid = get_active_connection_name()
                        self.supervisor.onNetworkFirstConnected()
                    else:
                        """wifi掉线并快速恢复"""
                        self.supervisor.onNetworkConnected()

                    check_interval = 3 
                else:
                    self.supervisor.wifi_status.connected = False

                    check_interval = 1
                    disconnect_count = disconnect_count + 1

                    if disconnect_count > 5:
                        """清除缓存"""
                        firstConnected=False
                        self.supervisor.wifi_status.ip_address = ""
                        self.supervisor.wifi_status.ssid = ""
                        
                    if has_active_connection():
                        self.supervisor.set_led_state(LedState.NETWORK_ERROR)
                    else:
                        self.supervisor.set_led_state(LedState.NETWORK_LOST)
            else:
                self.supervisor.set_led_state(LedState.STARTUP)          

            time.sleep(check_interval)
    
    def run(self):
        """启动线程， 并且维护WIFI状态"""
        self.network_thread = threading.Thread(target=self.network_monitor_task, daemon=True)
        self.network_thread.start()
        self.logger.info("Network monitor started")
        
    def stop(self):
        """使用supervisor.running关闭，这里做做样子"""
        self.logger.info("Network monitor stopped")

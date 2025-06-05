# maintainer: guoping.liu@3reality.com

"""Network Monitor for HubV3/LinuxBox"""

import os
import time
import logging
import threading
import dbus
import dbus.mainloop.glib
import traceback

try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

from .hardware import LedState
from .utils.wifi_utils import (
    get_wlan0_mac,
    is_interface_existing,
    is_network_connected,
    has_active_connection,
    get_wlan0_ip,
    get_active_connection_name
)

# NetworkManager D-Bus接口定义
NM_DBUS_SERVICE = "org.freedesktop.NetworkManager"
NM_DBUS_PATH = "/org/freedesktop/NetworkManager"
NM_DBUS_INTERFACE = "org.freedesktop.NetworkManager"
NM_DBUS_INTERFACE_DEVICE = "org.freedesktop.NetworkManager.Device"
NM_DBUS_INTERFACE_DEVICE_WIRELESS = "org.freedesktop.NetworkManager.Device.Wireless"
NM_DBUS_INTERFACE_CONNECTION_ACTIVE = "org.freedesktop.NetworkManager.Connection.Active"

# NetworkManager设备状态
NM_DEVICE_STATE_UNKNOWN = 0
NM_DEVICE_STATE_UNMANAGED = 10
NM_DEVICE_STATE_UNAVAILABLE = 20
NM_DEVICE_STATE_DISCONNECTED = 30
NM_DEVICE_STATE_PREPARE = 40
NM_DEVICE_STATE_CONFIG = 50
NM_DEVICE_STATE_NEED_AUTH = 60
NM_DEVICE_STATE_IP_CONFIG = 70
NM_DEVICE_STATE_IP_CHECK = 80
NM_DEVICE_STATE_SECONDARIES = 90
NM_DEVICE_STATE_ACTIVATED = 100
NM_DEVICE_STATE_DEACTIVATING = 110
NM_DEVICE_STATE_FAILED = 120

class NetworkMonitor:
    """基于事件的网络监控器，使用NetworkManager D-Bus接口监听网络状态变化"""
    
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.mainloop = None
        self.mainloop_thread = None
        self.bus = None
        self.nm_proxy = None
        self.wlan0_device_path = None
        self.wlan0_proxy = None
        
        # 状态跟踪
        self.mac_address = None
        self.first_connected = False
        self.connected = False
        self.disconnect_count = 0
        self.check_timer_id = None
        self._lock = threading.RLock()  # 保护状态更新
    
    def _init_dbus(self):
        """初始化D-Bus连接和NetworkManager代理"""
        try:
            self.logger.debug("Initializing NetworkManager D-Bus connection")
            
            # 初始化D-Bus主循环
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            
            # 获取NetworkManager代理
            self.nm_proxy = self.bus.get_object(NM_DBUS_SERVICE, NM_DBUS_PATH)
            nm_interface = dbus.Interface(self.nm_proxy, NM_DBUS_INTERFACE)
            
            # 查找wlan0设备
            devices = nm_interface.GetDevices()
            self.logger.debug(f"Found {len(devices)} NetworkManager devices")
            
            for device_path in devices:
                device_proxy = self.bus.get_object(NM_DBUS_SERVICE, device_path)
                device_props = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
                device_iface = device_props.Get(NM_DBUS_INTERFACE_DEVICE, "Interface")
                
                self.logger.debug(f"Found device: {device_iface} at {device_path}")
                
                if device_iface == "wlan0":
                    self.wlan0_device_path = device_path
                    self.wlan0_proxy = device_proxy
                    self.logger.info(f"Successfully found wlan0 device at {device_path}")
                    break
            
            if not self.wlan0_proxy:
                self.logger.warning("wlan0 interface not found in NetworkManager")
                return False
                
            # 设置信号处理器
            self._setup_signal_handlers()
            self.logger.info("NetworkManager D-Bus initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error initializing NetworkManager D-Bus: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def _setup_signal_handlers(self):
        """设置NetworkManager信号处理器"""
        try:
            # 监听设备状态变化
            if self.wlan0_proxy:
                self.bus.add_signal_receiver(
                    self._handle_device_state_changed,
                    dbus_interface=NM_DBUS_INTERFACE_DEVICE,
                    signal_name="StateChanged",
                    path=self.wlan0_device_path
                )
                self.logger.info(f"Registered device state change handler for {self.wlan0_device_path}")
            
            # 监听整体网络状态变化
            self.bus.add_signal_receiver(
                self._handle_nm_state_changed,
                dbus_interface=NM_DBUS_INTERFACE,
                signal_name="StateChanged"
            )
            self.logger.info("Registered NetworkManager state change handler")
            
            # 监听活动连接属性变化
            self.bus.add_signal_receiver(
                self._handle_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                arg0=NM_DBUS_INTERFACE_CONNECTION_ACTIVE
            )
            self.logger.info("Registered connection properties change handler")
            
            self.logger.info("NetworkManager signal handlers registered successfully")
        except Exception as e:
            self.logger.error(f"Error setting up signal handlers: {e}")
            self.logger.error(traceback.format_exc())
    
    def _handle_device_state_changed(self, new_state, old_state, reason):
        """处理设备状态变化信号"""
        with self._lock:
            self.logger.info(f"Network device state changed: {old_state} -> {new_state} (reason: {reason})")
            
            # 获取MAC地址（如果尚未获取）
            if self.mac_address is None and is_interface_existing("wlan0"):
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address
                self.logger.info(f"Cached MAC address: {self.mac_address}")
            
            # 处理连接状态
            if new_state == NM_DEVICE_STATE_ACTIVATED:
                self._handle_connection_established()
            elif old_state == NM_DEVICE_STATE_ACTIVATED and new_state != NM_DEVICE_STATE_ACTIVATED:
                self._schedule_disconnect_check()
            
            # 更新LED状态
            self._update_led_state(new_state)
    
    def _handle_nm_state_changed(self, state):
        """处理NetworkManager整体状态变化"""
        self.logger.info(f"NetworkManager state changed to: {state}")
        # 可以根据需要处理全局网络状态变化
    
    def _handle_properties_changed(self, interface_name, changed_properties, invalidated_properties):
        """处理活动连接属性变化"""
        if "Ip4Config" in changed_properties:
            # IP配置已更改，可能需要更新IP地址
            self._update_connection_info()
    
    def _handle_connection_established(self):
        """处理网络连接建立"""
        with self._lock:
            if self.supervisor:
                self.supervisor.set_led_state(LedState.NORMAL)
                
                if hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.connected = True
                
                self.disconnect_count = 0
                
                # 更新连接信息
                ip_address = get_wlan0_ip()
                ssid = get_active_connection_name()
                self.supervisor.update_wifi_info(ip_address, ssid)
                
                if not self.first_connected:
                    self.first_connected = True
                    self.connected = True
                    self.logger.info(f"First network connection established: {ssid} ({ip_address})")
                    self.supervisor.onNetworkFirstConnected()
                elif not self.connected:
                    self.connected = True
                    self.logger.info(f"Network connection re-established: {ssid} ({ip_address})")
                    self.supervisor.onNetworkConnected()
    
    def _schedule_disconnect_check(self):
        """安排断开连接检查"""
        with self._lock:
            # 取消之前的定时器（如果有）
            if self.check_timer_id is not None:
                GObject.source_remove(self.check_timer_id)
            
            # 设置新的定时器，5秒后检查断开状态
            self.check_timer_id = GObject.timeout_add(5000, self._check_disconnect_status)
            self.logger.debug("Scheduled disconnect check in 5 seconds")
    
    def _check_disconnect_status(self):
        """检查断开连接状态"""
        with self._lock:
            self.check_timer_id = None
            self.logger.debug("Checking network disconnect status")
            
            if not is_network_connected():
                self.disconnect_count += 1
                self.logger.debug(f"Network disconnected, count: {self.disconnect_count}")
                
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.connected = False
                
                if self.disconnect_count > 5 and self.connected:
                    self.connected = False
                    self.logger.info("Network disconnected for too long, clearing cache")
                    
                    if self.supervisor:
                        self.supervisor.update_wifi_info("", "")
                        self.supervisor.onNetworkDisconnect()
                
                # 更新LED状态
                if self.supervisor:
                    if has_active_connection():
                        self.supervisor.set_led_state(LedState.NETWORK_ERROR)
                        self.logger.debug("Set LED state to NETWORK_ERROR")
                    else:
                        self.supervisor.set_led_state(LedState.NETWORK_LOST)
                        self.logger.debug("Set LED state to NETWORK_LOST")
                
                # 继续检查，每秒一次
                self.check_timer_id = GObject.timeout_add(1000, self._check_disconnect_status)
                self.logger.debug("Scheduled next disconnect check in 1 second")
                return False  # 不再调用当前回调
            else:
                # 网络已恢复连接
                self.logger.debug("Network connection restored")
                self._handle_connection_established()
                return False  # 不再调用当前回调
    
    def _update_connection_info(self):
        """更新连接信息"""
        if self.supervisor and is_network_connected():
            ip_address = get_wlan0_ip()
            ssid = get_active_connection_name()
            self.supervisor.update_wifi_info(ip_address, ssid)
            self.logger.debug(f"Updated connection info: {ssid} ({ip_address})")
    
    def _update_led_state(self, device_state):
        """根据设备状态更新LED状态"""
        if not self.supervisor:
            return
            
        if device_state == NM_DEVICE_STATE_ACTIVATED:
            self.supervisor.set_led_state(LedState.NORMAL)
        elif device_state in [NM_DEVICE_STATE_PREPARE, NM_DEVICE_STATE_CONFIG, NM_DEVICE_STATE_NEED_AUTH, 
                             NM_DEVICE_STATE_IP_CONFIG, NM_DEVICE_STATE_IP_CHECK, NM_DEVICE_STATE_SECONDARIES]:
            # 连接过程中
            self.supervisor.set_led_state(LedState.NETWORK_ERROR)
        elif device_state == NM_DEVICE_STATE_DISCONNECTED:
            self.supervisor.set_led_state(LedState.NETWORK_LOST)
        elif device_state == NM_DEVICE_STATE_UNAVAILABLE:
            self.supervisor.set_led_state(LedState.STARTUP)
        elif device_state == NM_DEVICE_STATE_FAILED:
            self.supervisor.set_led_state(LedState.NETWORK_ERROR)
    
    def _run_mainloop(self):
        """在单独的线程中运行GLib主循环"""
        try:
            self.logger.info("Starting NetworkMonitor mainloop")
            self.mainloop.run()
            self.logger.info("NetworkMonitor mainloop exited")
        except Exception as e:
            self.logger.error(f"Error in NetworkMonitor mainloop: {e}")
            self.logger.error(traceback.format_exc())
    
    def _initial_check(self):
        """执行一次性的初始网络检查"""
        try:
            self.logger.info("Performing initial network check")
            
            # 检查wlan0接口是否存在
            wlan0_exists = is_interface_existing("wlan0")
            self.logger.info(f"wlan0 interface exists: {wlan0_exists}")
            
            if not wlan0_exists:
                if self.supervisor:
                    self.supervisor.set_led_state(LedState.STARTUP)
                return False  # 不再调用此回调
            
            # 获取MAC地址
            if self.mac_address is None:
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address
                self.logger.info(f"Cached MAC address: {self.mac_address}")
            
            # 检查网络连接状态
            is_connected = is_network_connected()
            self.logger.info(f"Initial network connection status: {is_connected}")
            
            if is_connected:
                self._handle_connection_established()
            else:
                self._check_disconnect_status()
        except Exception as e:
            self.logger.error(f"Error in initial network check: {e}")
            self.logger.error(traceback.format_exc())
        
        return False  # 一次性检查，不再调用
    
    def _periodic_check(self):
        """定期检查网络状态（作为备用机制）"""
        try:
            self.logger.debug("Periodic network check started")
            
            # 检查wlan0接口是否存在
            wlan0_exists = is_interface_existing("wlan0")
            self.logger.debug(f"wlan0 interface exists: {wlan0_exists}")
            
            if not wlan0_exists:
                if self.supervisor:
                    self.supervisor.set_led_state(LedState.STARTUP)
                return True  # 继续定期检查
            
            # 获取MAC地址（如果尚未获取）
            if self.mac_address is None:
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address
                self.logger.debug(f"Cached MAC address: {self.mac_address}")
            
            # 检查网络连接状态
            is_connected = is_network_connected()
            self.logger.debug(f"Network connection status: {is_connected}")
            
            if is_connected:
                self._handle_connection_established()
            else:
                self._check_disconnect_status()
        except Exception as e:
            self.logger.error(f"Error in periodic network check: {e}")
            self.logger.error(traceback.format_exc())
        
        return True  # 继续定期检查
    
    def start(self):
        """启动基于事件的网络监控"""
        try:
            # 初始化GLib主循环
            self.mainloop = GObject.MainLoop()
            
            # 初始化D-Bus连接
            if not self._init_dbus():
                self.logger.warning("Failed to initialize D-Bus, falling back to periodic checks")
                # 设置备用的定期检查（每10秒）
                GObject.timeout_add(10000, self._periodic_check)
            
            # 立即执行一次初始检查
            GObject.idle_add(self._initial_check)
            
            # 在单独的线程中启动主循环
            self.mainloop_thread = threading.Thread(target=self._run_mainloop, daemon=True)
            self.mainloop_thread.start()
            
            self.logger.info("Network monitor started (event-based)")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start network monitor: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def stop(self):
        """停止网络监控"""
        try:
            if self.mainloop and self.mainloop.is_running():
                self.mainloop.quit()
            
            if self.check_timer_id is not None:
                GObject.source_remove(self.check_timer_id)
                self.check_timer_id = None
                
            self.logger.info("Network monitor stopped")
        except Exception as e:
            self.logger.error(f"Error stopping network monitor: {e}")
            self.logger.error(traceback.format_exc())

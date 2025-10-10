# maintainer: guoping.liu@3reality.com

"""Network Monitor for HubV3/LinuxBox"""

import os
import subprocess
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
    has_saved_connection,
    get_wlan0_ip,
    get_active_connection_name
)

# NetworkManager D-Bus interface definition
NM_DBUS_SERVICE = "org.freedesktop.NetworkManager"
NM_DBUS_PATH = "/org/freedesktop/NetworkManager"
NM_DBUS_INTERFACE = "org.freedesktop.NetworkManager"
NM_DBUS_INTERFACE_DEVICE = "org.freedesktop.NetworkManager.Device"
NM_DBUS_INTERFACE_DEVICE_WIRELESS = "org.freedesktop.NetworkManager.Device.Wireless"
NM_DBUS_INTERFACE_CONNECTION_ACTIVE = "org.freedesktop.NetworkManager.Connection.Active"

# NetworkManager device state
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
    """Event-based network monitor, using NetworkManager D-Bus interface to listen for network state changes"""
    
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
        self.last_ip = None
        self.last_ssid = None
        self.first_connected = False
        self.connected = False
        self.last_connected = True
        self.disconnect_tick = 0
        self.check_timer_id = None
        self._lock = threading.RLock()  # Protect state update
    
    def _init_dbus(self):
        """Initialize D-Bus connection and NetworkManager proxy"""
        try:
            self.logger.info("NetworkMonitor: Initializing NetworkManager D-Bus connection")
            
            # 初始化D-Bus主循环
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            
            # 获取NetworkManager代理
            self.nm_proxy = self.bus.get_object(NM_DBUS_SERVICE, NM_DBUS_PATH)
            nm_interface = dbus.Interface(self.nm_proxy, NM_DBUS_INTERFACE)
            
            # 查找wlan0设备
            devices = nm_interface.GetDevices()
            self.logger.debug(f"NetworkMonitor: Found {len(devices)} NetworkManager devices")
            
            for device_path in devices:
                device_proxy = self.bus.get_object(NM_DBUS_SERVICE, device_path)
                device_props = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
                device_iface = device_props.Get(NM_DBUS_INTERFACE_DEVICE, "Interface")
                
                self.logger.debug(f"NetworkMonitor: Found device: {device_iface} at {device_path}")
                
                if device_iface == "wlan0":
                    self.wlan0_device_path = device_path
                    self.wlan0_proxy = device_proxy
                    self.logger.info(f"NetworkMonitor: Successfully found wlan0 device at {device_path}")
                    break
            
            if not self.wlan0_proxy:
                self.logger.warning("NetworkMonitor: wlan0 interface not found in NetworkManager")
                return False
                
            # 设置信号处理器
            self._setup_signal_handlers()
            self.logger.info("NetworkMonitor: D-Bus initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"NetworkMonitor: Error initializing NetworkManager D-Bus: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def _setup_signal_handlers(self):
        """Set NetworkManager signal handlers"""
        try:
            # Listen for device state changes
            if self.wlan0_proxy:
                self.bus.add_signal_receiver(
                    self._handle_device_state_changed,
                    dbus_interface=NM_DBUS_INTERFACE_DEVICE,
                    signal_name="StateChanged",
                    path=self.wlan0_device_path
                )
                self.logger.info(f"Registered device state change handler for {self.wlan0_device_path}")
            
            # Listen for overall network state changes
            self.bus.add_signal_receiver(
                self._handle_nm_state_changed,
                dbus_interface=NM_DBUS_INTERFACE,
                signal_name="StateChanged"
            )
            self.logger.info("Registered NetworkManager state change handler")
            
            # Listen for active connection property changes
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
        """Handle device state change signal"""
        with self._lock:
            self.logger.info(f"NetworkMonitor: [SIGNAL-DEVICE]Network device state changed: {old_state} -> {new_state} (reason: {reason})")
            
            # Get MAC address (if not already obtained)
            if self.mac_address is None and is_interface_existing("wlan0"):
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address
                self.logger.info(f"Cached MAC address: {self.mac_address}")
            
            # Handle connection status
            if new_state == NM_DEVICE_STATE_ACTIVATED:
                self._handle_connection_established()
            elif old_state == NM_DEVICE_STATE_ACTIVATED and new_state != NM_DEVICE_STATE_ACTIVATED:
                self._schedule_disconnect_check()
            if new_state == NM_DEVICE_STATE_DISCONNECTED:
                self.logger.info("[SIGNAL-DEVICE]Network disconnected.")
                if self.supervisor:
                    self.supervisor.update_wifi_info("", "")
                    self.supervisor.onNetworkDisconnect()
            
            # Update LED state
            self._update_led_state(new_state)
    
    def _handle_nm_state_changed(self, state):
        """Handle NetworkManager overall state changes"""
        self.logger.info(f"NetworkMonitor: [SIGNAL-NM]NetworkManager state changed to: {state}")
        # Can handle global network state changes as needed
    
    def _handle_properties_changed(self, interface_name, changed_properties, invalidated_properties):
        """Handle active connection property changes"""
        if "Ip4Config" in changed_properties:
            # IP configuration has changed, may need to update IP address
            self.logger.info("NetworkMonitor: [SIGNAL-PROP]PropertiesChanged: Ip4Config changed.")
            self._update_connection_info()
    
    def _handle_connection_established(self):
        """Handle network connection establishment"""
        with self._lock:
            if self.supervisor:
                self.supervisor.clear_led_state(LedState.SYS_OFFLINE)
                self.supervisor.set_led_state(LedState.SYS_NORMAL_OPERATION)
                
                if hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.connected = True
                
                self.disconnect_tick = 0
                
                # Update connection info
                current_ip = get_wlan0_ip()
                current_ssid = get_active_connection_name()

                if (current_ip != self.last_ip or current_ssid != self.last_ssid):
                    self.supervisor.update_wifi_info(current_ip, current_ssid)
                    self.last_ip = current_ip
                    self.last_ssid = current_ssid                
                    
                if not self.first_connected:
                    self.first_connected = True
                    self.connected = True
                    self.logger.info(f"First network connection established: {current_ssid} ({current_ip})")
                    self.supervisor.onNetworkFirstConnected()
                elif not self.connected:
                    self.connected = True
                    self.logger.info(f"NetworkMonitor: wireless network wlan0 is: {current_ssid} ({current_ip})")
                    self.supervisor.onNetworkConnected()
                #else:
                #    self.logger.info(f"NetworkMonitor: wireless network wlan0 is connected..") 
                #self.logger.info(f"Network connected: SSID='{ssid}', IP='{ip_address}'")
        
    def _handle_disconnect_status(self):
        """Check disconnect status"""
        with self._lock:
            self.check_timer_id = None
            self.disconnect_tick += 1
            self.logger.info(f"Network disconnected: ({self.disconnect_tick})")
            if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                self.supervisor.wifi_status.connected = False

            self.connected = False
            # if self.disconnect_tick > 5 and self.connected:
            #     self.connected = False
            #     if self.supervisor:
            #         self.supervisor.update_wifi_info("", "")
            #         self.supervisor.onNetworkDisconnect()
                    
            if self.supervisor:
                if has_saved_connection():
                    self.supervisor.set_led_state(LedState.SYS_OFFLINE)
                    # 14400 seconds， 4 Hours,  10 seconds = 1 tick, 1440 ticks = 4 hours
                    # 1800 seconds， 30 minutes, 10 seconds = 1 tick, 180 ticks = 30 minutes
                    if self.disconnect_tick == 180:
                        try:
                            script_path = "/lib/thirdreality/resetupwifi.sh"
                            if os.path.exists(script_path):
                                self.logger.warning(f"[!]Network disconnected: ({self.disconnect_tick}), triggering async {script_path}")
                                def _run_resetupwifi():
                                    try:
                                        # Run asynchronously and detach outputs so failures won't affect main flow
                                        with open(os.devnull, 'wb') as devnull:
                                            subprocess.Popen([script_path], stdout=devnull, stderr=devnull)
                                    except Exception as e:
                                        self.logger.warning(f"resetupwifi.sh execution failed: {e}")
                                threading.Thread(target=_run_resetupwifi, daemon=True).start()
                            else:
                                self.logger.info(f"resetupwifi.sh not found at {script_path}, skipping 30-minute action")
                        except Exception as e:
                            self.logger.warning(f"Error scheduling 30-minute resetupwifi action: {e}")
                    elif self.disconnect_tick == 1440:
                        #reboot the device
                        self.logger.warning(f"Network disconnected: ({self.disconnect_tick}), rebooting the device")
                        self.supervisor.set_led_state(LedState.REBOOT)
                        self.disconnect_tick = 0
                        self.supervisor.perform_reboot()                    
                else:
                    self.supervisor.set_led_state(LedState.SYS_OFFLINE)
            return False

    
    def _schedule_disconnect_check(self):
        """Schedule disconnect check"""
        with self._lock:
            if self.check_timer_id is not None:
                GObject.source_remove(self.check_timer_id)
            self.check_timer_id = GObject.timeout_add(5000, self._check_connection_status)

    def _check_connection_status(self):
        """Check connection status"""
        if is_network_connected():
            self._handle_connection_established()
        else:
            self._handle_disconnect_status()
                    
    def _update_connection_info(self):
        """Update connection info"""
        if self.supervisor and is_network_connected():
            ip_address = get_wlan0_ip()
            ssid = get_active_connection_name()
            self.supervisor.update_wifi_info(ip_address, ssid)
    
    def _update_led_state(self, device_state):
        """Update LED state based on device state"""
        if not self.supervisor:
            return
        
        if device_state == NM_DEVICE_STATE_ACTIVATED:
            self.supervisor.clear_led_state(LedState.SYS_OFFLINE)
        elif device_state in [NM_DEVICE_STATE_PREPARE, NM_DEVICE_STATE_CONFIG, NM_DEVICE_STATE_NEED_AUTH, 
                             NM_DEVICE_STATE_IP_CONFIG, NM_DEVICE_STATE_IP_CHECK, NM_DEVICE_STATE_SECONDARIES]:
            # During connection process
            self.supervisor.set_led_state(LedState.SYS_OFFLINE)
        elif device_state == NM_DEVICE_STATE_DISCONNECTED:
            self.supervisor.set_led_state(LedState.SYS_OFFLINE)
        elif device_state == NM_DEVICE_STATE_UNAVAILABLE:
            self.supervisor.set_led_state(LedState.SYS_OFFLINE)  # Or consider SYS_ERROR_CONDITION if unavailability is an error
        elif device_state == NM_DEVICE_STATE_FAILED:
            self.supervisor.set_led_state(LedState.SYS_OFFLINE)
    
    def _run_mainloop(self):
        """Run GLib main loop in separate thread"""
        try:
            self.logger.info("Starting NetworkMonitor mainloop")
            self.mainloop.run()
            self.logger.info("NetworkMonitor mainloop exited")
        except Exception as e:
            self.logger.error(f"Error in NetworkMonitor mainloop: {e}")
            self.logger.error(traceback.format_exc())
    
    def _initial_check(self):
        """Perform one-time initial network check"""
        try:
            self.logger.info("Performing initial network check")
            
            # Check if wlan0 interface exists
            wlan0_exists = is_interface_existing("wlan0")
            self.logger.info(f"wlan0 interface exists: {wlan0_exists}")
            
            if not wlan0_exists:
                if self.supervisor:
                    self.supervisor.set_led_state(LedState.STARTUP)
                return False  # No longer call this callback
            
            # Get MAC address
            if self.mac_address is None:
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address
                self.logger.info(f"Cached MAC address: {self.mac_address}")
            
            # Check network connection status
            is_connected = is_network_connected()
            self.logger.info(f"Initial network connection status: {is_connected}")

            self.last_connected = None
            
            if is_connected:
                self._handle_connection_established()
            else:
                self._handle_disconnect_status()
        except Exception as e:
            self.logger.error(f"Error in initial network check: {e}")
            self.logger.error(traceback.format_exc())
        
        return False  # One-time check, no longer call this callback
    
    def _periodic_check(self):
        """Periodic network status check (as backup mechanism)"""
        try:
            
            wlan0_exists = is_interface_existing("wlan0")
            if not wlan0_exists:
                if self.supervisor:
                    self.supervisor.set_led_state(LedState.STARTUP)
                return True  # Continue periodic checks

            if self.mac_address is None:
                self.mac_address = get_wlan0_mac()
                if self.supervisor and hasattr(self.supervisor, 'wifi_status'):
                    self.supervisor.wifi_status.mac_address = self.mac_address

            is_connected = is_network_connected()

            if is_connected != self.last_connected:
                if is_connected:
                    self.logger.info("Performing periodic network check: connected")
                else:
                    self.logger.warning("Performing periodic network check: disconnected")

            if is_connected:
                self._handle_connection_established()
            else:
                self._handle_disconnect_status()

            self.last_connected = is_connected                
        except Exception as e:
            self.logger.error(f"Error in periodic network check: {e}")
            self.logger.error(traceback.format_exc())
        return True  # Continue periodic checks
    
    def start(self):
        """Start event-based network monitoring"""
        try:
            # Initialize GLib main loop
            self.mainloop = GObject.MainLoop()
            
            # Initialize D-Bus connection
            if not self._init_dbus():
                self.logger.warning("Failed to initialize D-Bus, falling back to periodic checks")
                # Set backup periodic check (every 5 seconds for more responsive monitoring)
                GObject.timeout_add(5000, self._periodic_check)
            else:
                # Even with D-Bus events, add periodic check as backup (every 10 seconds)
                self.logger.info("D-Bus initialized successfully, adding backup periodic check")
                GObject.timeout_add(10000, self._periodic_check)
            
            # Perform immediate initial check
            GObject.idle_add(self._initial_check)
            
            # Start main loop in separate thread
            self.mainloop_thread = threading.Thread(target=self._run_mainloop, daemon=True)
            self.mainloop_thread.start()
            
            self.logger.info("NetworkMonitor started.")
        except Exception as e:
            self.logger.error(f"Failed to start network monitor: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def stop(self):
        """Stop network monitoring"""
        try:
            if self.mainloop and self.mainloop.is_running():
                self.mainloop.quit()
            
            if self.check_timer_id is not None:
                GObject.source_remove(self.check_timer_id)
                self.check_timer_id = None
                
            self.logger.info("NetworkMonitor stopped.")
        except Exception as e:
            self.logger.error(f"Error stopping network monitor: {e}")
            self.logger.error(traceback.format_exc())



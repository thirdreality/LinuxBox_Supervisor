# maintainer: guoping.liu@3reality.com

#!/usr/bin/python3

"""Copyright (c) 2019, Douglas Otwell

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import dbus
import dbus.mainloop.glib
import threading
import time
import json
import logging


try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

from .advertisement import Advertisement
from .service import Application, Service, Characteristic, Descriptor
from ..utils.wifi_manager import WifiManager
from ..utils.wifi_utils import get_wlan0_ip

#define HUBV3_CONFIG_SERVICE_UUID "6e400000-0000-4e98-8024-bc5b71e0893e"

# Configure wifi using JSON commands: write
#define HUBV3_WIFI_CONFIG_CHAR_UUID "6e400002-0000-4e98-8024-bc5b71e0893e"

GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
NOTIFY_TIMEOUT = 5000

class SupervisorGattServer:
    """
    BLE GATT server manager for Supervisor BLE modules.
    Handles BLE Advertisement, Application, and Services lifecycle.
    Optimized for embedded platforms with limited resources.
    """
    
    def __init__(self, supervisor):
        """Initialize GATT server"""
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.app = None
        self.adv = None
        self.manager_service = None
        self.running = False
        self.mainloop = None
        self.mainloop_thread = None
        
        # Add thread lock to protect advertisement operations
        self._adv_lock = threading.RLock()
        self._adv_registered_externally = False # Track if adv is active due to external call
        
        # Add timeout control
        self.timeout_timer = None
        self.timeout_minutes = 5
        
        self.logger.debug("Initialized GATT server for embedded platform")

    def start_with_timeout(self, timeout_minutes=5):
        """Start GATT server with timeout"""
        self.timeout_minutes = timeout_minutes
        
        if not self.start():
            return False
            
        # Start timeout timer
        timeout_seconds = timeout_minutes * 60
        self.timeout_timer = threading.Timer(timeout_seconds, self._on_timeout)
        self.timeout_timer.start()
        self.logger.info(f"GATT server started with {timeout_minutes} minute timeout")
        return True

    def _on_timeout(self):
        """Timeout callback"""
        self.logger.info("GATT server timeout reached, stopping...")
        self.stop()

    def start(self):
        if self.running:
            return
        
        try:
            # Initialize D-Bus main loop
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.mainloop = GObject.MainLoop()
            
            # Initialize BLE Advertisement
            self.adv = LinuxBoxAdvertisement(self.supervisor, 0)
            
            # Initialize BLE Application and Service
            self.app = Application()
            self.app.add_device_property_callback(my_callback)

            self.manager_service = LinuxBoxManagerService(0, self.supervisor)
            self.app.add_service(self.manager_service)
            
            # Register Application first, then Advertisement
            # This order can be important for some BlueZ implementations
            self.logger.info("[BLE] Registering GATT application...")
            self.app.register()
            
            # Small delay to ensure application is registered before advertisement
            time.sleep(0.5)
            
            # Start BLE advertisement
            self.adv.register()
            self.logger.info("[BLE] Advertisement registered on server start.")
            
            # Start the main loop in a separate thread
            self.mainloop_thread = threading.Thread(target=self._run_mainloop)
            self.mainloop_thread.daemon = True
            self.mainloop_thread.start()
            
            self.running = True
            self.logger.info("[BLE] GATT server started.")
        except Exception as e:
            self.logger.error(f"Failed to start GATT server: {e}")
            self.stop()

    def _run_mainloop(self):
        """Run the GLib main loop in a separate thread"""
        try:
            self.logger.info("[BLE] Starting GATT server main loop")
            self.mainloop.run()
            self.logger.info("[BLE] GATT server main loop exited")
        except Exception as e:
            self.logger.error(f"Error in GATT server main loop: {e}")

    def stop(self):
        # Stop timeout timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
            
        if not self.running:
            return
        
        # Unregister Advertisement
        if self.adv:
            try:
                self.adv.unregister()
            except Exception as e:
                self.logger.error(f"Error unregistering advertisement: {e}")
            self.adv = None
        
        # Stop Application mainloop
        if self.mainloop and self.mainloop.is_running():
            try:
                GObject.idle_add(self.mainloop.quit)
            except Exception as e:
                self.logger.error(f"Error stopping mainloop: {e}")
        
        # Wait for the mainloop thread to finish
        if self.mainloop_thread and self.mainloop_thread.is_alive():
            try:
                self.mainloop_thread.join(timeout=2)
            except Exception as e:
                self.logger.error(f"Error joining mainloop thread: {e}")
        
        self.app = None
        self.manager_service = None
        self.mainloop = None
        self.mainloop_thread = None
        self.running = False
        self.logger.info("[BLE] GATT server stopped.")

def my_callback(interface, path, is_connected):
    logger = logging.getLogger("Supervisor")
    if is_connected:
        logger.info(f"[BLE] Device connected: {interface}:{path}")
    else:
        logger.info(f"[BLE] Device disconnected: {interface}:{path}")    

class LinuxBoxAdvertisement(Advertisement):
    def __init__(self, supervisor, index):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        Advertisement.__init__(self, index, "peripheral")
        
        # Get device name from SystemInfo (set by SystemInfoUpdater)
        device_name = getattr(self.supervisor.system_info, 'name', "3RHUB-EMB")
        if device_name and device_name != "3RHUB-XXXX":
            self.add_local_name(device_name)
            self.logger.info(f"[BLE] Using device name from SystemInfo: '{device_name}'")
        else:
            # Fallback if SystemInfo name is not set or is default
            self.add_local_name("3RHUB-EMB")
            self.logger.info(f"[BLE] Using fallback local name: '3RHUB-EMB'")

        # Add only main service UUID
        self.add_service_uuid("6e400000-0000-4e98-8024-bc5b71e0893e")
        
        # Do not include TX power, keep advertisement simple
        self.include_tx_power = False
        self.logger.info("[BLE] Simplified advertisement for embedded platform")

class LinuxBoxManagerService(Service):
    _LINUXBOX_SVC_UUID = "6e400000-0000-4e98-8024-bc5b71e0893e"

    def __init__(self, index, supervisor=None):
        # Initialize the service with the UUID and set primary=True to make it discoverable
        Service.__init__(self, index, self._LINUXBOX_SVC_UUID, True)
        self.supervisor = supervisor
        # Add the WiFi configuration characteristic
        self.add_characteristic(WIFIConfigCharacteristic(self))
        logging.getLogger("Supervisor").info(f"[BLE] Initialized LinuxBox Manager Service with UUID: {self._LINUXBOX_SVC_UUID}")

class WIFIConfigCharacteristic(Characteristic):
    _CHARACTERISTIC_UUID = "6e400001-0000-4e98-8024-bc5b71e0893e"
    # Parameters optimized for MTU=23
    _MAX_CHUNK_SIZE = 18  # 23-3(protocol overhead)-2(safety margin) 
    _CHUNK_DELAY = 0.05   # 50ms delay is sufficient
    _MAX_RETRIES = 3      # Reduce retry count to avoid excessive retries

    def __init__(self, service):
        self.logger = logging.getLogger("Supervisor")
        self._notifying = False
        self._notification_ready = False  # Track if notification is fully ready
        self._notification_lock = threading.Lock()  # Lock for thread safety
        self.service = service
        # Using notify mode
        Characteristic.__init__(
                self, self._CHARACTERISTIC_UUID,
                ["notify", "write"], service)
        self.add_descriptor(WIFIConfigDescriptor(self))
        
        # Record using notify mode (was indicate)
        self._use_indicate = False # Set to False for notify
        self.logger.info("Using notify mode with MTU=23 optimization")

    def WriteValue(self, value, options):
        #self.logger.info(f"Write Value: {value}")

        command_str = "".join(chr(byte) for byte in value)
        process_thread = threading.Thread(target=self._process_command_and_notify, args=(command_str,))
        process_thread.daemon = True  # Make thread daemon so it doesn't block program exit
        process_thread.start()    

    def _process_command_and_notify(self, command):
        self.logger.info(f"_process_command_and_notify: {command}")

        # Default values
        ret = False  # Default result
        ip_address = ""
        ssid = ""
        password = ""
        restore = False
        result = ""

        # Process all commands as JSON
        # Try to parse as JSON for WiFi configuration commands
        try:
            config = json.loads(command)
            if "ssid" in config:
                ssid = config["ssid"]
                password = config.get("password", "")
                restore = config.get("restore", False)
                
                # Get wifi_manager from supervisor if available
                if hasattr(self.service, 'supervisor') and self.service.supervisor and hasattr(self.service.supervisor, 'wifi_manager'):
                    wifi_manager = self.service.supervisor.wifi_manager
                    ret = wifi_manager.configure(ssid, password)
                    self.logger.info(f"WiFi configuration result: {ret}")
                    if ret == 0:
                        time.sleep(3)  # Wait for WiFi connection
                        ip_address = get_wlan0_ip() or ""
                        self.logger.info(f"WiFi IP address: {ip_address}")
                        self.service.supervisor.update_wifi_info(ip_address, ssid)  
                        #self.service.supervisor.check_ha_resume()
                else:
                    self.logger.info("WiFi manager not initialized")

            # Format the response for WiFi configuration
            result = json.dumps({"connected": ret==0, "ip_address": ip_address})
        except json.JSONDecodeError:
            self.logger.error(f"Invalid command format: {command}")
            result = json.dumps({"error": "Invalid command format"})

        self.logger.info(f"Sending result: {result}")

        # Send notification with retry mechanism
        self.send_response_notification(result)

        if restore:
            self.logger.info(f"Restore previous state ...")
            utils.perform_wifi_provision_restore()            

    def _send_notification_value(self, value):
        """
        Sends the actual notification value via D-Bus PropertiesChanged.
        BlueZ will handle sending this as a notification if the client subscribed.
        """
        self.logger.debug(f"Sending indication with {len(value)} bytes")
        
        # Send indication via PropertiesChanged
        # BlueZ will handle sending this as a notification if the client subscribed
        try:
            self.PropertiesChanged(
                GATT_CHRC_IFACE,
                {"Value": dbus.Array(value, signature='y')},
                []
            )
            self.logger.debug("Notification value sent via PropertiesChanged")
            return True
        except Exception as e:
            self.logger.warning(f"Failed to send notification value: {e}")
            raise

    def send_response_notification(self, result):
        """Optimized fixed MTU=23 notification sending"""
        if not self._notifying:
            self.logger.warning("Cannot send notification: notifications not enabled")
            return False

        value = [dbus.Byte(c) for c in result.encode('utf-8')]
        
        # Force fragmentation, each 18 bytes (optimized for MTU=23)
        chunks = [value[i:i+self._MAX_CHUNK_SIZE] 
                 for i in range(0, len(value), self._MAX_CHUNK_SIZE)]
        
        self.logger.info(f"Sending {len(chunks)} chunks of {self._MAX_CHUNK_SIZE} bytes (MTU=23)")
        
        for i, chunk in enumerate(chunks):
            success = False
            for retry in range(self._MAX_RETRIES):
                try:
                    with self._notification_lock:
                        self._send_notification_value(chunk)
                        success = True
                        break
                except Exception as e:
                    self.logger.warning(f"Chunk {i+1} retry {retry+1} failed: {e}")
                    time.sleep(0.02 * (retry + 1))
            
            if not success:
                self.logger.error(f"Failed to send chunk {i+1}")
                return False
                
            # Inter-chunk delay
            if i < len(chunks) - 1:
                time.sleep(self._CHUNK_DELAY)
        
        return True


    def StartNotify(self):
        """
        Called by BlueZ when the client subscribes to notifications.
        """
        with self._notification_lock:
            if self._notifying:
                self.logger.info("Client already subscribed to notifications.")
                return
                
            self._notifying = True
            self._notification_ready = True  # Set ready immediately for embedded platform
            self.logger.info("Client subscribed to notifications (MTU=23 optimization enabled)")
            # Send a test notification to confirm the channel is working
            # This helps some clients establish the notification flow correctly.
            # try:
            #     test_value = [dbus.Byte(c) for c in "NOTIFY_READY".encode('utf-8')]
            #     self._send_notification_value(test_value)
            #     self.logger.info("Sent 'NOTIFY_READY' test notification.")
            # except Exception as e:
            #     self.logger.warning(f"Failed to send 'NOTIFY_READY' test notification: {e}")

    def StopNotify(self):
        """
        Called by BlueZ when the client unsubscribes from notifications.
        """
        with self._notification_lock:
            if not self._notifying:
                return
                
            # Reset status flags as client has unsubscribed
            self._notifying = False
            self._notification_ready = False
            self.logger.info("Client unsubscribed from notifications. Stopping notification channel.")


class WIFIConfigDescriptor(Descriptor):
    _DESCRIPTOR_UUID = "2901"
    _DESCRIPTOR_VALUE = "Wifi config"

    def __init__(self, characteristic):
        Descriptor.__init__(
                self, self._DESCRIPTOR_UUID,
                ["read"],
                characteristic)

    def ReadValue(self, options):
        value = []
        desc = self._DESCRIPTOR_VALUE
        print(desc)

        for c in desc:
            value.append(dbus.Byte(c.encode()))

        return value

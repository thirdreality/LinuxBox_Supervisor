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
import json
import logging

try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

from .advertisement import Advertisement
from .service import Application, Service, Characteristic, Descriptor
from ..utils.wifi_manager import WifiManager
from ..utils.wifi_utils import get_wlan0_mac_for_localname

#define HUBV3_CONFIG_SERVICE_UUID "6e400000-0000-4e98-8024-bc5b71e0893e"

#配置wifi，使用json指令：写
#define HUBV3_WIFI_CONFIG_CHAR_UUID "6e400002-0000-4e98-8024-bc5b71e0893e"

GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
NOTIFY_TIMEOUT = 5000

class SupervisorGattServer:
    """
    BLE GATT server manager for Supervisor BLE modules.
    Handles BLE Advertisement, Application, and Services lifecycle.
    """
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.app = None
        self.adv = None
        self.manager_service = None
        self.running = False
        self.mainloop = None
        self.mainloop_thread = None

    def updateAdv(self, ip_address):
        try:
            # Add IP address to advertisement
            ip_bytes = [int(part) for part in ip_address.split('.')]
            self.logger.info(f"Adding device IP address to advertisement: {ip_address} -> {ip_bytes}")
            if self.adv:
                self.adv.add_manufacturer_data(0x0133, ip_bytes)
        except Exception as e:
            self.logger.error(f"Error setting up network services: {e}")

    def start(self):
        if self.running:
            return
        
        try:
            # Initialize D-Bus main loop
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.mainloop = GObject.MainLoop()
            
            # Initialize BLE Advertisement
            self.adv = LinuxBoxAdvertisement(0)
            
            # Initialize BLE Application and Service
            self.app = Application()
            self.app.add_device_property_callback(my_callback)

            self.manager_service = LinuxBoxManagerService(0)
            self.app.add_service(self.manager_service)
            
            # Register Advertisement and Application
            self.adv.register()
            self.app.register()
            
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

def my_callback(interface, changed, invalidated, path):
    logger = logging.getLogger("Supervisor")
    logger.info(f"Custom BLE event: {interface} {changed} {path}")

class LinuxBoxAdvertisement(Advertisement):
    def __init__(self, index):
        Advertisement.__init__(self, index, "peripheral")
        mac_str = get_wlan0_mac_for_localname()
        if mac_str:
            self.add_local_name(f"3RHUB-{mac_str}")
        else:
            self.add_local_name("3RHUB-XXXXXXXX")
        self.include_tx_power = True

class LinuxBoxManagerService(Service):
    _LINUXBOX_SVC_UUID = "6e400000-0000-4e98-8024-bc5b71e0893e"

    def __init__(self, index):
        self.farenheit = True

        Service.__init__(self, index, self._LINUXBOX_SVC_UUID, True)
        self.add_characteristic(WifiStatusCharacteristic(self))
        self.add_characteristic(WIFIConfigCharacteristic(self))

    def is_farenheit(self):
        return self.farenheit

    def set_farenheit(self, farenheit):
        self.farenheit = farenheit

class WifiStatusCharacteristic(Characteristic):
    _CHARACTERISTIC_UUID = "6e400001-0000-4e98-8024-bc5b71e0893e"

    def __init__(self, service):
        self._notifying = False

        Characteristic.__init__(
                self, self._CHARACTERISTIC_UUID,
                ["notify", "write"], service)
        self.add_descriptor(WifiStatusDescriptor(self))
    
    def WriteValue(self, value, options):
        self.logger.info(f"Write Value: {value}")

        command_str = "".join(chr(byte) for byte in value)
        process_thread = threading.Thread(target=self._process_command_and_notify, args=(command_str,))
        process_thread.start()        

    def _process_command_and_notify(self, command):
        self.logger.info(f"_process_command_and_notify: {command}")
        
        # Generate a proper result using wifi_manager
        result = ""
        if command == "GET_STATUS" and wifi_manager:
            status = wifi_manager.get_status()
            result = json.dumps({
                "connected": status.connected,
                "ssid": status.ssid,
                "ip_address": status.ip_address,
                "mac_address": status.mac_address,
                "error_message": status.error_message
            })

            if result:
                self.logger.info(f"_process_command_and_notify result: {result}")
        
        if self._notifying:
            # Ensure result is not empty to prevent D-Bus signature error
            if not result:
                result = "{}"
                
            # Convert the result to a byte array and emit the signal
            value = [dbus.Byte(c) for c in result.encode('utf-8')]
            self.PropertiesChanged(
                GATT_CHRC_IFACE,
                {"Value": dbus.Array(value, signature='y')},
                []
            )

    def StartNotify(self):
        if self._notifying:
            return
        self._notifying = True
        self.logger.info("Starting Notification")

    def StopNotify(self):
        self._notifying = False
        self.logger.info("Stopping Notification")

class WifiStatusDescriptor(Descriptor):
    WIFI_STATUS_DESCRIPTOR_UUID = "2901"
    WIFI_STATUS_DESCRIPTOR_VALUE = "Wifi Status"

    def __init__(self, characteristic):
        Descriptor.__init__(
                self, self.WIFI_STATUS_DESCRIPTOR_UUID,
                ["read"],
                characteristic)

    def ReadValue(self, options):
        value = []
        desc = self.WIFI_STATUS_DESCRIPTOR_VALUE

        for c in desc:
            value.append(dbus.Byte(c.encode()))

        return value

class WIFIConfigCharacteristic(Characteristic):
    _CHARACTERISTIC_UUID = "6e400002-0000-4e98-8024-bc5b71e0893e"

    def __init__(self, service):
        Characteristic.__init__(
                self, self._CHARACTERISTIC_UUID,
                ["read", "write"], service)
        self.add_descriptor(WIFIConfigDescriptor(self))

    def WriteValue(self, value, options):
        try:
            data = bytes(value).decode('utf-8')
            config = json.loads(data)
            
            if "ssid" in config:
                ssid = config["ssid"]
                password = config.get("password", "")
                
                if wifi_manager:
                    result = wifi_manager.configure(ssid, password)
                    self.logger.info(f"WiFi configuration result: {result}")
            else:
                self.logger.info("WiFi manager not initialized")
        except Exception as e:
            self.logger.error(f"Error processing WiFi config: {e}")
        

    def ReadValue(self, options):
        value = []

        if self.service.is_farenheit(): val = "F"
        else: val = "C"
        value.append(dbus.Byte(val.encode()))

        return value

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

        for c in desc:
            value.append(dbus.Byte(c.encode()))

        return value

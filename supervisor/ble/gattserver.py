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
import threading
import json

from advertisement import Advertisement
from service import Application, Service, Characteristic, Descriptor
from wifi_manager import WifiManager

#define HUBV3_CONFIG_SERVICE_UUID "6e400000-0000-4e98-8024-bc5b71e0893e"

#查看wifi状态: 使用json指令：读
#define HUBV3_WIFI_STATUS_CHAR_UUID "6e400001-0000-4e98-8024-bc5b71e0893e"

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
        self.app = None
        self.adv = None
        self.manager_service = None
        self.running = False

    def updateAdv(self, ip_address):
        try:
            # Add IP address to advertisement
            ip_bytes = [int(part) for part in ip_address.split('.')]
            print(f"Adding device IP address to advertisement: {ip_address} -> {ip_bytes}")
            if self.adv:
                self.adv.add_manufacturer_data(0x0133, ip_bytes)
        except Exception as e:
            print(f"Error setting up network services: {e}")

    def start(self):
        if self.running:
            return
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
        self.running = True
        print("[BLE] GATT server started.")

    def stop(self):
        if not self.running:
            return
        # Unregister Advertisement and stop Application mainloop
        if self.adv:
            self.adv.unregister()
            self.adv = None
        if self.app:
            self.app.quit()
            self.app = None
        self.manager_service = None
        self.running = False
        print("[BLE] GATT server stopped.")

def my_callback(interface, changed, invalidated, path):
    print("Custom BLE event:", interface, changed, path)


from utils.wifi_utils import get_wlan0_mac_for_localname

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

# -----------------------------------------------------------------------------
class WifiStatusCharacteristic(Characteristic):
    _CHARACTERISTIC_UUID = "6e400001-0000-4e98-8024-bc5b71e0893e"

    def __init__(self, service):
        self._notifying = False

        Characteristic.__init__(
                self, self._CHARACTERISTIC_UUID,
                ["notify", "write"], service)
        self.add_descriptor(WifiStatusDescriptor(self))
    
    def WriteValue(self, value, options):
        print(f"Write Value: {value}")

        command_str = "".join(chr(byte) for byte in value)
        process_thread = threading.Thread(target=self._process_command_and_notify, args=(command_str,))
        process_thread.start()        

    def _process_command_and_notify(self, command):
        print(f"_process_command_and_notify: {command}")
        
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
                print(f"_process_command_and_notify result: {result}")
        
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
        print("Starting Notification")

    def StopNotify(self):
        self._notifying = False
        print("Stopping Notification")

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

# -----------------------------------------------------------------------------

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
                    print(f"WiFi configuration result: {result}")
            else:
                print("WiFi manager not initialized")
        except Exception as e:
            print(f"Error processing WiFi config: {e}")
        

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

# -----------------------------------------------------------------------------



# def signal_handler(signum, frame):
#     global adv, app, wifi_manager, monitor_stop_flag, http_server_running
#     print(f"Received signal {signum}, exiting gracefully...")
#     wifi_manager.cleanup()
    
#     # 停止网络监控线程
#     if monitor_stop_flag:
#         print("Stopping network monitor...")
#         monitor_stop_flag.set()
    
#     # 停止HTTP服务器（如果正在运行）
#     if http_server_running:
#         print("Stopping HTTP server...")
#         http_server.stop_server()
#         http_server_running = False
    
#     # 停止BLE广告
#     adv.unregister()
#     app.quit()
#     sys.exit(0)

# def main():
#     global adv, app, wifi_manager, monitor_stop_flag, http_server_running
    
#     # 初始化WiFi管理器
#     wifi_manager = WifiManager()
#     wifi_manager.init()
    
#     # 启动GATT服务器
#     app = Application()
#     app.add_service(LinuxBoxManagerService(0))
#     app.register()

#     adv = LinuxBoxAdvertisement(0)
    
#     # 直接获取wlan0的IP地址
#     ip_address = wifi_manager.get_wlan0_ip()
    
#     # 如果有IP地址，我们假设网络已连接，启动HTTP服务器
#     if ip_address:
#         try:
#             # 添加IP地址到广告中
#             ip_bytes = [int(part) for part in ip_address.split('.')]
#             print(f"Adding device IP address to advertisement: {ip_address} -> {ip_bytes}")
#             adv.add_manufacturer_data(0x0133, ip_bytes)
            
#             # 启动HTTP服务器
#             print("Network detected, starting HTTP server...")
#             if not http_server_running and http_server.init(wifi_manager):
#                 http_server_running = True
#                 print(f"HTTP server started on port 8086, available at http://{ip_address}:8086/")
#             else:
#                 print("Failed to start HTTP server")
#         except Exception as e:
#             print(f"Error setting up network services: {e}")
#     else:
#         print("No IPv4 address found for wlan0, advertisement will not include IP address")
#         print("HTTP server will not be started")
    
#     # 注册广告
#     adv.register()

#     # 设置信号处理函数，用于干净退出
#     signal.signal(signal.SIGTERM, signal_handler)
#     signal.signal(signal.SIGINT, signal_handler)

#     # 创建停止事件
#     monitor_stop_flag = threading.Event()
    
#     # 启动网络监控线程，动态管理HTTP服务器
#     monitor_thread = threading.Thread(target=monitor_network, args=(adv, monitor_stop_flag))
#     monitor_thread.daemon = True
#     monitor_thread.start()

#     try:
#         app.run()
#     except KeyboardInterrupt:
#         print("Keyboard interrupt received")
#         if monitor_stop_flag:
#             monitor_stop_flag.set()
#         wifi_manager.cleanup()
#         if http_server_running:
#             http_server.stop_server()
#             http_server_running = False
#         adv.unregister()
#         app.quit()

# def monitor_network(adv, stop_flag):
#     """监控网络状态并动态管理HTTP服务器"""
#     global wifi_manager, http_server_running
    
#     previous_ip = None
    
#     while not stop_flag.is_set():
#         try:
#             # 获取当前IP地址
#             current_ip = wifi_manager.get_wlan0_ip()
            
#             if current_ip != previous_ip:
#                 print(f"Network change detected. Previous IP: {previous_ip}, Current IP: {current_ip}")
                
#                 # 更新广告数据
#                 if current_ip:
#                     try:
#                         # 将IP地址添加到广告中
#                         ip_bytes = [int(part) for part in current_ip.split('.')]
#                         adv.add_manufacturer_data(0x0133, ip_bytes)
#                         print(f"Updated advertisement with IP: {current_ip}")
                        
#                         # 如果HTTP服务器未运行，启动它
#                         if not http_server_running:
#                             print("Starting HTTP server...")
#                             if http_server.init(wifi_manager):
#                                 http_server_running = True
#                                 print(f"HTTP server started on port 8086")
#                     except Exception as e:
#                         print(f"Error updating network services: {e}")
#                 else:
#                     # 如果没有IP但HTTP服务器正在运行，停止它
#                     if http_server_running:
#                         print("Network disconnected, stopping HTTP server...")
#                         http_server.stop_server()
#                         http_server_running = False
                
#                 previous_ip = current_ip
            
#             # 每10秒检查一次网络状态
#             time.sleep(10)
#         except Exception as e:
#             print(f"Error in network monitor: {e}")
#             time.sleep(30)  # 出错后稍微等待长一些

#     print("Network monitor thread stopped")
#     stop_flag.set()

# if __name__ == "__main__":
#     main()

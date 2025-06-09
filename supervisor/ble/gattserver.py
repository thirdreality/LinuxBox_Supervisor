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
import hashlib
import base64
import struct

try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

from .advertisement import Advertisement
from .service import Application, Service, Characteristic, Descriptor
from ..utils.wifi_manager import WifiManager
from ..utils.wifi_utils import get_wlan0_mac_for_localname,get_wlan0_ip

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
    # 加密密钥 (16字节，用于AES加密)
    ENCRYPTION_KEY = b"ThirdRealityKey"
    
    def __init__(self, supervisor):
        """初始化GATT服务器"""
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.app = None
        self.adv = None
        self.manager_service = None
        self.running = False
        self.mainloop = None
        self.mainloop_thread = None
        self._adv_lock = threading.Lock()
        self._previous_ip_address = None
        self._last_update_time = None  # 上次更新时间
        self.logger.debug("Initialized GATT server with update tracking")
        self.mainloop_thread = None
        self._previous_ip_address = "0.0.0.0"
        
        # 添加线程锁，保护广告更新操作
        self._adv_lock = threading.RLock()

    def _encrypt_ip_address(self, ip_address):
        """
        加密IP地址，返回加密后的字节数组
        使用简单的可逆加密方法，适合资源受限的BLE设备
        """
        try:
            # 将IP地址转换为字节列表
            ip_bytes = [int(part) for part in ip_address.split('.')]
            if len(ip_bytes) != 4:
                raise ValueError(f"Invalid IP address format: {ip_address}")
                
            # 使用密钥生成一个简单的XOR掩码
            key_hash = hashlib.md5(self.ENCRYPTION_KEY).digest()[:4]  # 取MD5的前4个字节作为掩码
            
            # 对IP地址进行XOR加密
            encrypted_bytes = []
            for i in range(4):
                encrypted_bytes.append(ip_bytes[i] ^ key_hash[i])
                
            # 添加校验和 (简单的字节求和)
            checksum = sum(encrypted_bytes) & 0xFF
            encrypted_bytes.append(checksum)
            
            self.logger.debug(f"Encrypted IP {ip_address} to bytes: {encrypted_bytes}")
            return encrypted_bytes  # 返回加密后的字节数组，包含校验和
        except Exception as e:
            self.logger.error(f"Error encrypting IP address: {e}")
            # 返回全0作为错误情况，包括校验和字节
            error_bytes = [0, 0, 0, 0, 0]  # 4个IP字节加1个校验和字节
            return error_bytes

    def updateAdv(self, ip_address=None):
        """
        更新BLE广告中的IP地址信息
        使用线程安全机制、IP地址加密和防抖机制
        """
        # 使用线程锁保护整个更新过程
        with self._adv_lock:
            # 防抖机制：如果上次更新时间太近，直接返回
            current_time = time.time()
            if self._last_update_time and \
               current_time - self._last_update_time < 2:  # 至少2秒间隔
                self.logger.debug(f"Debounced updateAdv call (interval < 2s)")
                return
                
            self.logger.info(f"[BLE] Updating advertisement with IP address: {ip_address}")
            try:
                # 如果没有提供 IP 地址，检查当前连接状态
                if ip_address is None:
                    # 从 supervisor 获取当前 WiFi 状态
                    ip_address = self.supervisor.wifi_status.ip_address
                    
                # 如果IP地址没有变化，直接返回
                if self._previous_ip_address == ip_address:
                    self.logger.debug(f"IP address unchanged ({ip_address})")
                    return
                    
                # 准备制造商数据
                manufacturer_data = [0x00, 0x00, 0x00, 0x00]  # 默认空IP地址
                
                # 如果有IP地址，则加密后放入广告数据
                if ip_address:
                    encrypted_ip = self._encrypt_ip_address(ip_address)
                    self.logger.debug(f"Encrypted IP {ip_address} to bytes: {encrypted_ip}")
                    manufacturer_data = encrypted_ip
                
                # 更新制造商数据 - 使用厂商代码 0x0059 (89)
                self.adv.add_manufacturer_data(0x0133, manufacturer_data)
                self.logger.info(f"Added IP address to advertisement: {ip_address} -> {manufacturer_data}")
                
                # 注册广告使更改生效
                self.adv.register()
                
                # 保存当前IP地址和更新时间
                self._previous_ip_address = ip_address
                self._last_update_time = current_time
            except Exception as e:
                self.logger.error(f"Error updating advertisement with IP address: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

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
            
            # Add initial manufacturer data
            ip_bytes = [0, 0, 0, 0]
            self.adv.add_manufacturer_data(0x0133, ip_bytes)
            
            # Register Application first, then Advertisement
            # This order can be important for some BlueZ implementations
            self.logger.info("[BLE] Registering GATT application...")
            self.app.register()
            
            # Small delay to ensure application is registered before advertisement
            time.sleep(0.5)
            
            self.logger.info("[BLE] Registering BLE advertisement...")
            self.adv.register()
            
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
        mac_str = get_wlan0_mac_for_localname()
        if mac_str:
            self.logger.info(f"[BLE] Adding local name: '3RHUB-{mac_str}'")
            self.add_local_name(f"3RHUB-{mac_str}")
            self.supervisor.system_info.name = f"3RHUB-{mac_str}"
        else:
            self.logger.error(f"[BLE] Adding local name: '3RHUB-XXXXXXXX'")
            self.add_local_name("3RHUB-XXXXXXXX")
            self.supervisor.system_info.name = "3RHUB-XXXXXXXX"

        self.add_service_uuid("6e400000-0000-4e98-8024-bc5b71e0893e")

        # Do not add service UUID to advertisement - this appears to be causing issues with BlueZ
        # The service will still be discoverable through the GATT service discovery process
        self.logger.info(f"[BLE] Service UUID will be discoverable through GATT service discovery")
        # We'll focus on making the GATT service work properly instead of adding it to the advertisement
        self.include_tx_power = True

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
    _MAX_RETRIES = 3  # Maximum number of notification retries
    _RETRY_DELAY = 0.5  # Delay between retries in seconds
    _NOTIFICATION_SETUP_DELAY = 0.2  # Delay after notification is enabled before sending data

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
        self.logger.info("Using notify mode")

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
                        time.sleep(3)  # 等待WiFi连接
                        ip_address = get_wlan0_ip() or ""
                        self.logger.info(f"WiFi IP address: {ip_address}")
                        self.service.supervisor.update_wifi_info(ip_address, ssid)  
                        self.service.supervisor.check_ha_resume()
                else:
                    self.logger.info("WiFi manager not initialized")

            # Format the response for WiFi configuration
            result = json.dumps({"connected": ret==0, "ip_address": ip_address})
        except json.JSONDecodeError:
            self.logger.error(f"Invalid command format: {command}")
            result = json.dumps({"error": "Invalid command format"})

        self.logger.info(f"Sending result: {result}")

        # Send notification with retry mechanism
        self._send_notification_with_retry(result)

        if restore:
            self.logger.info(f"Restore previous state ...")
            utils.perform_wifi_provision_restore()            

    def SendIndication(self, value):
        """
        发送带确认的indication数据
        这是对BlueZ DBus API的扩展，专门用于处理indication模式
        """
        self.logger.debug(f"Sending indication with {len(value)} bytes")
        
        # 使用PropertiesChanged发送indication
        # BlueZ会根据特性的配置决定是使用notify还是indicate
        try:
            self.PropertiesChanged(
                GATT_CHRC_IFACE,
                {"Value": dbus.Array(value, signature='y')},
                []
            )
            self.logger.debug("Indication sent via PropertiesChanged")
            return True
        except Exception as e:
            self.logger.warning(f"Failed to send indication: {e}")
            raise

    def _send_notification_with_retry(self, result):
        """使用indicate模式发送数据，带重试机制确保传输可靠性"""
        if not self._notifying:
            self.logger.warning("Cannot send indication: indications not enabled")
            return False
        
        # 等待indication通道完全就绪
        retry_count = 0
        success = False
        
        # 将结果转换为字节数组用于indication
        value = [dbus.Byte(c) for c in result.encode('utf-8')]
        
        # 大数据包分片处理，提高传输成功率
        max_chunk_size = 100  # 限制每次发送大小
        if len(value) > max_chunk_size:
            self.logger.info(f"Large indication detected ({len(value)} bytes), splitting into chunks")
            chunks = [value[i:i+max_chunk_size] for i in range(0, len(value), max_chunk_size)]
            
            # 发送分片
            for i, chunk in enumerate(chunks):
                chunk_header = f"CHUNK:{i+1}/{len(chunks)}:"  # 添加分片头部
                if i == 0:
                    # 第一个分片包含分片信息
                    header_bytes = [dbus.Byte(c) for c in chunk_header.encode('utf-8')]
                    chunk_with_header = header_bytes + chunk
                    success = self._send_single_indication(chunk_with_header)
                else:
                    # 后续分片只发送数据
                    success = self._send_single_indication(chunk)
                
                if not success:
                    self.logger.error(f"Failed to send chunk {i+1}/{len(chunks)}")
                    return False
                    
                # 分片之间添加延迟
                time.sleep(0.3)
            
            return True
        else:
            # 正常发送单个数据包
            return self._send_single_indication(value)
        
    def _send_single_indication(self, value):
        """发送单个数据包并处理重试"""
        retry_count = 0
        success = False
        max_retries = 3  # indicate模式下可以使用更少的重试次数
        retry_delay = 0.5  # 重试间隔
        
        while retry_count < max_retries and not success:
            try:
                with self._notification_lock:
                    # 重试时添加日志和延迟
                    if retry_count > 0:
                        self.logger.info(f"Retry #{retry_count} sending indication ({len(value)} bytes)")
                        time.sleep(retry_delay * retry_count)  # 每次重试增加等待时间
                    
                    # 发送前添加小延迟
                    time.sleep(0.2)
                    
                    # 使用SendIndication方法发送数据
                    self.SendIndication(value)
                    
                    # 发送后添加小延迟
                    time.sleep(0.2)
                    success = True
                    self.logger.info(f"Indication sent successfully ({len(value)} bytes)")
            except Exception as e:
                self.logger.error(f"Failed to send indication: {e}")
                retry_count += 1
        
        if not success:
            self.logger.error(f"Failed to send indication after {max_retries} retries")
        
        return success

    def StartNotify(self):
        """
        BlueZ GATT特性接口标准方法，用于启动通知/指示通道
        即使在indicate模式下也需要保留此方法
        """
        with self._notification_lock:
            if self._notifying:
                return
                
            self._notifying = True
            self._notification_ready = False
            self.logger.info("Starting Indication Channel")
            
            # 延迟设置通知就绪标志
            def set_ready():
                self._notification_ready = True
                self.logger.info("Indication channel ready")
                
                # 发送测试indication确保通道已建立
                try:
                    test_value = [dbus.Byte(c) for c in b'READY']
                    self.SendIndication(test_value)
                except Exception as e:
                    self.logger.warning(f"Failed to send test indication: {e}")
                return False
            
            # 延迟设置通知就绪标志
            GObject.timeout_add(int(self._NOTIFICATION_SETUP_DELAY * 1000), set_ready)

    def StopNotify(self):
        """
        BlueZ GATT特性接口标准方法，用于停止通知/指示通道
        即使在indicate模式下也需要保留此方法
        """
        with self._notification_lock:
            if not self._notifying:
                return
                
            # 尝试发送空指示以正确关闭通道
            try:
                empty_value = [dbus.Byte(c) for c in b'']
                self.SendIndication(empty_value)
            except Exception as e:
                self.logger.warning(f"Error sending empty indication: {e}")
            
            # 重置状态标志
            self._notifying = False
            self._notification_ready = False
            self.logger.info("Indication channel stopped")


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

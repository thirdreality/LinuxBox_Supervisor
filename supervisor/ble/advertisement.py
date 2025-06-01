import dbus
import dbus.service
import logging
import threading
import time

from .bletools import BleTools

BLUEZ_SERVICE_NAME = "org.bluez"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"


class Advertisement(dbus.service.Object):
    PATH_BASE = "/org/bluez/example/advertisement"
    
    # 广告刷新配置
    MAX_RETRY_COUNT = 3
    RETRY_DELAY = 2.0  # 秒

    def __init__(self, index, advertising_type):
        self.logger = logging.getLogger("Supervisor")
        self.path = self.PATH_BASE + str(index)
        self.bus = BleTools.get_bus()
        self.ad_type = advertising_type
        self.local_name = None
        self.service_uuids = None
        self.solicit_uuids = None
        self.manufacturer_data = None
        self.service_data = None
        self.include_tx_power = None
        self.is_registered = False
        self.is_registering = False
        
        # 添加线程安全锁
        self._lock = threading.RLock()
        self._data_changed = False  # 标记数据是否已更改
        
        dbus.service.Object.__init__(self, self.bus, self.path)

    def get_properties(self):
        with self._lock:
            properties = dict()
            properties["Type"] = self.ad_type

            if self.local_name is not None:
                properties["LocalName"] = dbus.String(self.local_name)

            if self.service_uuids is not None:
                properties["ServiceUUIDs"] = dbus.Array(self.service_uuids,
                                                        signature='s')
            if self.solicit_uuids is not None:
                properties["SolicitUUIDs"] = dbus.Array(self.solicit_uuids,
                                                        signature='s')
            if self.manufacturer_data is not None:
                properties["ManufacturerData"] = dbus.Dictionary(
                    self.manufacturer_data, signature='qv')

            if self.service_data is not None:
                properties["ServiceData"] = dbus.Dictionary(self.service_data,
                                                            signature='sv')
            if self.include_tx_power is not None:
                properties["IncludeTxPower"] = dbus.Boolean(self.include_tx_power)

            return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service_uuid(self, uuid):
        with self._lock:
            if not self.service_uuids:
                self.service_uuids = []
            self.service_uuids.append(uuid)
            self._data_changed = True

    def add_solicit_uuid(self, uuid):
        with self._lock:
            if not self.solicit_uuids:
                self.solicit_uuids = []
            self.solicit_uuids.append(uuid)
            self._data_changed = True

    def add_manufacturer_data(self, manuf_code, data):
        """
        Add or update manufacturer data for the given manufacturer code.
        If the key already exists, it will be overwritten (dynamic update supported).
        """
        with self._lock:
            if not self.manufacturer_data:
                self.manufacturer_data = dbus.Dictionary({}, signature="qv")
            # Overwrite the value if manuf_code already exists
            self.manufacturer_data[manuf_code] = dbus.Array(data, signature="y")
            self._data_changed = True
            self.logger.debug(f"Updated manufacturer data for code {manuf_code}")

    def add_service_data(self, uuid, data):
        with self._lock:
            if not self.service_data:
                self.service_data = dbus.Dictionary({}, signature="sv")
            self.service_data[uuid] = dbus.Array(data, signature="y")
            self._data_changed = True

    def add_local_name(self, name):
        with self._lock:
            if not self.local_name:
                self.local_name = ""
            self.local_name = dbus.String(name)
            self._data_changed = True

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature="s",
                         out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE,
                         in_signature='',
                         out_signature='')
    def Release(self):
        self.logger.info(f"{self.path}: Released!")

    def register_ad_callback(self):
        with self._lock:
            self.is_registered = True
            self.is_registering = False
            self._data_changed = False  # 注册成功后重置数据变更标志
        self.logger.info("GATT advertisement registered successfully")

    def register_ad_error_callback(self, error):
        with self._lock:
            self.is_registered = False
            self.is_registering = False
        self.logger.error(f"Failed to register GATT advertisement: {error}")

    def register(self):
        """注册广告，如果已注册则跳过"""
        with self._lock:
            # 如果正在注册或已注册且数据未变更，则跳过
            if self.is_registering:
                self.logger.info(f"Advertisement {self.get_path()} is already being registered, skipping")
                return
                
            if self.is_registered and not self._data_changed:
                self.logger.debug(f"Advertisement {self.get_path()} is already registered and data unchanged, skipping")
                return
                
            # 如果已注册但数据已变更，先注销再重新注册
            if self.is_registered and self._data_changed:
                self.logger.info(f"Advertisement data changed, re-registering {self.get_path()}")
                # 先解锁，避免死锁
                self._lock.release()
                try:
                    self.unregister()
                finally:
                    # 重新获取锁
                    self._lock.acquire()
            
            self.is_registering = True
            
        # 在锁外执行DBus调用，避免潜在的死锁
        try:
            bus = BleTools.get_bus()
            adapter = BleTools.find_adapter(bus)

            ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                    LE_ADVERTISING_MANAGER_IFACE)
            ad_manager.RegisterAdvertisement(self.get_path(), {},
                                        reply_handler=self.register_ad_callback,
                                        error_handler=self.register_ad_error_callback)
            self.logger.info(f"Advertisement registration request sent for {self.get_path()}")
        except Exception as e:
            # 发生异常时重置状态
            with self._lock:
                self.is_registering = False
            self.logger.error(f"Error registering advertisement: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def unregister(self):
        """注销广告，并添加重试机制"""
        with self._lock:
            # 如果广告未注册，跳过注销
            if not self.is_registered:
                self.logger.info(f"Advertisement {self.get_path()} is not registered, skipping unregister")
                return
        
        # 在锁外执行DBus调用，避免潜在的死锁
        retry_count = 0
        while retry_count < self.MAX_RETRY_COUNT:
            try:
                bus = BleTools.get_bus()
                adapter = BleTools.find_adapter(bus)
                ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                        LE_ADVERTISING_MANAGER_IFACE)
                
                # 注销广告
                ad_manager.UnregisterAdvertisement(self.get_path())
                
                # 注销成功，更新状态
                with self._lock:
                    self.is_registered = False
                    
                self.logger.info(f"Advertisement {self.get_path()} successfully unregistered")
                return
            except dbus.DBusException as e:
                retry_count += 1
                if retry_count >= self.MAX_RETRY_COUNT:
                    self.logger.error(f"Failed to unregister advertisement after {self.MAX_RETRY_COUNT} attempts: {e}")
                    # 如果多次注销失败，强制设置为未注册状态
                    with self._lock:
                        self.is_registered = False
                    return
                else:
                    self.logger.warning(f"Unregister attempt {retry_count} failed: {e}, retrying...")
                    time.sleep(self.RETRY_DELAY)
            except Exception as e:
                self.logger.error(f"Unexpected error unregistering advertisement: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                # 如果发生意外异常，重置状态
                with self._lock:
                    self.is_registered = False
                return

import dbus
import dbus.service
import logging

from .bletools import BleTools

BLUEZ_SERVICE_NAME = "org.bluez"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"


class Advertisement(dbus.service.Object):
    PATH_BASE = "/org/bluez/example/advertisement"

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
        dbus.service.Object.__init__(self, self.bus, self.path)

    def get_properties(self):
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

        if self.local_name is not None:
            properties["LocalName"] = dbus.String(self.local_name)

        return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service_uuid(self, uuid):
        if not self.service_uuids:
            self.service_uuids = []
        self.service_uuids.append(uuid)

    def add_solicit_uuid(self, uuid):
        if not self.solicit_uuids:
            self.solicit_uuids = []
        self.solicit_uuids.append(uuid)

    def add_manufacturer_data(self, manuf_code, data):
        """
        Add or update manufacturer data for the given manufacturer code.
        If the key already exists, it will be overwritten (dynamic update supported).
        """
        if not self.manufacturer_data:
            self.manufacturer_data = dbus.Dictionary({}, signature="qv")
        # Overwrite the value if manuf_code already exists
        self.manufacturer_data[manuf_code] = dbus.Array(data, signature="y")

    def add_service_data(self, uuid, data):
        if not self.service_data:
            self.service_data = dbus.Dictionary({}, signature="sv")
        self.service_data[uuid] = dbus.Array(data, signature="y")

    def add_local_name(self, name):
        if not self.local_name:
            self.local_name = ""
        self.local_name = dbus.String(name)

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
        self.is_registered = True
        self.is_registering = False
        self.logger.info("GATT advertisement registered")

    def register_ad_error_callback(self, error):
        self.is_registered = False
        self.is_registering = False
        self.logger.info(f"Failed to register GATT advertisement: {error}")

    def register(self):
        self.logger.info(f"Advertisement register ...")
        # Skip registration if already registered
        if self.is_registered or self.is_registering:
            self.logger.info(f"Advertisement {self.get_path()} is already registering/registered, skipping register")
            return
        
        self.is_registering = True
        bus = BleTools.get_bus()
        adapter = BleTools.find_adapter(bus)

        ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                LE_ADVERTISING_MANAGER_IFACE)
        ad_manager.RegisterAdvertisement(self.get_path(), {},
                                     reply_handler=self.register_ad_callback,
                                     error_handler=self.register_ad_error_callback)

    def unregister(self):
        self.logger.info(f"Advertisement unregister ...")
        # Only attempt to unregister if the advertisement is registered
        if not self.is_registered:
            self.logger.info(f"Advertisement {self.get_path()} is not registered, skipping unregister")
            return
            
        bus = BleTools.get_bus()
        adapter = BleTools.find_adapter(bus)

        ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                LE_ADVERTISING_MANAGER_IFACE)
    
        # Unregister the advertisement
        try:
            ad_manager.UnregisterAdvertisement(self.get_path())
            self.is_registered = False
            self.logger.info(f"Advertisement {self.get_path()} successfully unregistered")
        except dbus.DBusException as e:
            self.logger.error(f"Failed to unregister advertisement: {e}")

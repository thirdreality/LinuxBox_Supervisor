import dbus
import dbus.mainloop.glib
import logging
import dbus.exceptions
try:
  from gi.repository import GObject
except ImportError:
    import gobject as GObject
from .bletools import BleTools

BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
DBUS_OM_IFACE =      "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE =    "org.freedesktop.DBus.Properties"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE =    "org.bluez.GattCharacteristic1"
GATT_DESC_IFACE =    "org.bluez.GattDescriptor1"

class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"

class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"

class NotPermittedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotPermitted"

class Application(dbus.service.Object):
    def __init__(self):
        self.logger = logging.getLogger("Supervisor")
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.mainloop = GObject.MainLoop()
        self.bus = BleTools.get_bus()
        self.path = "/"
        self.services = []
        self.next_index = 0
        dbus.service.Object.__init__(self, self.bus, self.path)

        # Callback list for device property changes
        self.device_property_callbacks = []

        # Listen for device connection and disconnection events
        def device_property_changed(interface, changed, invalidated, path):
            # 只关注 org.bluez.Device1 接口的 Connected 属性变化
            if interface == "org.bluez.Device1" and "Connected" in changed:
                self.logger.info(f"[BLE] Device property changed: {interface}, {changed}, {invalidated}, {path}")
                if changed["Connected"]:
                    logger.info(f"[BLE] Device connected: {path}")
                else:
                    logger.info(f"[BLE] Device disconnected: {path}")
            
                for cb in self.device_property_callbacks:
                    try:
                        cb(interface, changed, invalidated, path)
                    except Exception as e:
                        logger.error(f"[BLE] Callback error: {e}")                    

        self.bus.add_signal_receiver(
            device_property_changed,
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path_keyword="path"
        )

    def add_device_property_callback(self, callback):
        """Register a callback for device property changes."""
        self.device_property_callbacks.append(callback)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature = "a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}

        for service in self.services:
            response[service.get_path()] = service.get_properties()
            chrcs = service.get_characteristics()
            for chrc in chrcs:
                response[chrc.get_path()] = chrc.get_properties()
                descs = chrc.get_descriptors()
                for desc in descs:
                    response[desc.get_path()] = desc.get_properties()

        return response

    def register_app_callback(self):
        self.logger.info("GATT application registered")

    def register_app_error_callback(self, error):
        self.logger.error(f"Failed to register application: {error}")

    def register(self):
        adapter = BleTools.find_adapter(self.bus)

        service_manager = dbus.Interface(
                self.bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                GATT_MANAGER_IFACE)

        service_manager.RegisterApplication(self.get_path(), {},
                reply_handler=self.register_app_callback,
                error_handler=self.register_app_error_callback)

    def run(self):
        self.mainloop.run()

    def quit(self):
        self.logger.info("GATT application terminated")
        self.mainloop.quit()

class Service(dbus.service.Object):
    PATH_BASE = "/org/bluez/example/service"

    def __init__(self, index, uuid, primary):
        self.bus = BleTools.get_bus()
        self.path = self.PATH_BASE + str(index)
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        self.next_index = 0
        dbus.service.Object.__init__(self, self.bus, self.path)

    def get_properties(self):
        return {
                GATT_SERVICE_IFACE: {
                        'UUID': self.uuid,
                        'Primary': self.primary,
                        'Characteristics': dbus.Array(
                                self.get_characteristic_paths(),
                                signature='o')
                }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    def get_characteristic_paths(self):
        result = []
        for chrc in self.characteristics:
            result.append(chrc.get_path())
        return result

    def get_characteristics(self):
        return self.characteristics

    def get_bus(self):
        return self.bus

    def get_next_index(self):
        idx = self.next_index
        self.next_index += 1

        return idx

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[GATT_SERVICE_IFACE]

class Characteristic(dbus.service.Object):
    """
    org.bluez.GattCharacteristic1 interface implementation
    """
    def __init__(self, uuid, flags, service):
        index = service.get_next_index()
        self.path = service.path + '/char' + str(index)
        self.bus = service.get_bus()
        self.uuid = uuid
        self.service = service
        self.flags = flags
        self.descriptors = []
        self.next_index = 0
        dbus.service.Object.__init__(self, self.bus, self.path)

    def get_properties(self):
        return {
                GATT_CHRC_IFACE: {
                        'Service': self.service.get_path(),
                        'UUID': self.uuid,
                        'Flags': self.flags,
                        'Descriptors': dbus.Array(
                                self.get_descriptor_paths(),
                                signature='o')
                }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_descriptor(self, descriptor):
        self.descriptors.append(descriptor)

    def get_descriptor_paths(self):
        result = []
        for desc in self.descriptors:
            result.append(desc.get_path())
        return result

    def get_descriptors(self):
        return self.descriptors

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE,
                        in_signature='a{sv}',
                        out_signature='ay')
    def ReadValue(self, options):
        print('Default ReadValue called, returning error')
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}')
    def WriteValue(self, value, options):
        print('Default WriteValue called, returning error')
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        print('Default StartNotify called, returning error')
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        print('Default StopNotify called, returning error')
        raise NotSupportedException()

    @dbus.service.signal(DBUS_PROP_IFACE,
                         signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def get_bus(self):
        bus = self.bus

        return bus

    def get_next_index(self):
        idx = self.next_index
        self.next_index += 1

        return idx

    def add_timeout(self, timeout, callback):
        GObject.timeout_add(timeout, callback)


class Descriptor(dbus.service.Object):
    def __init__(self, uuid, flags, characteristic):
        index = characteristic.get_next_index()
        self.path = characteristic.path + '/desc' + str(index)
        self.uuid = uuid
        self.flags = flags
        self.chrc = characteristic
        self.bus = characteristic.get_bus()
        dbus.service.Object.__init__(self, self.bus, self.path)

    def get_properties(self):
        return {
                GATT_DESC_IFACE: {
                        'Characteristic': self.chrc.get_path(),
                        'UUID': self.uuid,
                        'Flags': self.flags,
                }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE,
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_DESC_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[GATT_DESC_IFACE]

    @dbus.service.method(GATT_DESC_IFACE,
                        in_signature='a{sv}',
                        out_signature='ay')
    def ReadValue(self, options):
        self.logger.info('Default ReadValue called, returning error')
        raise NotSupportedException()

    @dbus.service.method(GATT_DESC_IFACE, in_signature='aya{sv}')
    def WriteValue(self, value, options):
        self.logger.info('Default WriteValue called, returning error')
        raise NotSupportedException()


class CharacteristicUserDescriptionDescriptor(Descriptor):
    CUD_UUID = '2901'

    def __init__(self, bus, index, characteristic):
        self.writable = 'writable-auxiliaries' in characteristic.flags
        self.value = array.array('B', b'This is a characteristic for testing')
        self.value = self.value.tolist()
        Descriptor.__init__(
                self, bus, index,
                self.CUD_UUID,
                ['read', 'write'],
                characteristic)

    def ReadValue(self, options):
        return self.value

    def WriteValue(self, value, options):
        if not self.writable:
            raise NotPermittedException()
        self.value = value

# maintainer: guoping.liu@thirdreality.com

"""Button & LED for HubV3/LinuxBox"""

import os
import time
import logging
import threading

from enum import Enum
from .const import LINUXBOX_LED_R_PIN
from .const import LINUXBOX_LED_G_PIN
from .const import LINUXBOX_LED_B_PIN
from .const import LINUXBOX_BUTTON_PIN

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# -----------------------------------------------------------------------------
class LedState(Enum):
    REBOOT = "reboot"
    POWER_OFF = "power_off"
    NORMAL = "normal"
    NETWORK_ERROR = "network_error"
    NETWORK_LOST = "network_lost"
    STARTUP = "startup"
    MQTT_PARING = "mqtt_paring"
    MQTT_PARED = "mqtt_pared"
    MQTT_ERROR = "mqtt_error"
    MQTT_NORMAL = "mqtt_normal"

# -----------------------------------------------------------------------------
class SysFSGPIO:
    BASE_PATH = "/sys/class/gpio"

    @staticmethod
    def export_pin(pin):
        if not os.path.exists(f"{SysFSGPIO.BASE_PATH}/gpio{pin}"):
            try:
                with open(f"{SysFSGPIO.BASE_PATH}/export", "w") as f:
                    f.write(str(pin))
                time.sleep(0.1)
            except IOError as e:
                logging.error(f"Exporting GPIO pin {pin} failed: {e}")

    @staticmethod
    def write_value(pin, value):
        try:
            with open(f"{SysFSGPIO.BASE_PATH}/gpio{pin}/value", "w") as f:
                f.write(str(value))
        except IOError as e:
            logging.error(f"Writing to GPIO pin {pin} failed: {e}")

    @staticmethod
    def read_value(pin):
        try:
            with open(f"{SysFSGPIO.BASE_PATH}/gpio{pin}/value", "r") as f:
                return f.read().strip()
        except IOError as e:
            logging.error(f"Reading from GPIO pin {pin} failed: {e}")
            return None

    @staticmethod
    def set_direction(pin, direction):
        try:
            with open(f"{SysFSGPIO.BASE_PATH}/gpio{pin}/direction", "w") as f:
                f.write(direction)
        except IOError as e:
            logging.error(f"Setting direction for GPIO pin {pin} failed: {e}")

# -----------------------------------------------------------------------------
class GpioLed:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        
        self.pins = {
            'RED': LINUXBOX_LED_R_PIN,
            'GREEN': LINUXBOX_LED_G_PIN,
            'BLUE': LINUXBOX_LED_B_PIN
        }
        self._initialize_pins()
        
        # Thread control
        self.led_thread = None

    def _initialize_pins(self):
        for pin in self.pins.values():
            SysFSGPIO.export_pin(pin)
            SysFSGPIO.set_direction(pin, "out")

    def set_color(self, red, green, blue):
        pin_states = {
            self.pins['RED']: red,
            self.pins['GREEN']: green,
            self.pins['BLUE']: blue
        }
        for pin, state in pin_states.items():
            SysFSGPIO.write_value(pin, 1 if state else 0)

    def off(self): self.set_color(False, False, False)
    def red(self): self.set_color(True, False, False)
    def green(self): self.set_color(False, True, False)
    def blue(self): self.set_color(False, False, True)
    def yellow(self): self.set_color(True, True, False)
    def purple(self): self.set_color(True, False, True)
    def cyan(self): self.set_color(False, True, True)
    def white(self): self.set_color(True, True, True)
    
    def led_control_task(self):
        """LED control thread"""
        blink_counter = 0
        self.logger.info("Starting LED controller...")
        
        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            state = self.supervisor.get_led_state()
            blink_counter = (blink_counter + 1) % 2

            if state == LedState.REBOOT:
                self.red()
            elif state == LedState.POWER_OFF:
                self.yellow()
            elif state == LedState.NORMAL:
                self.blue()
            elif state == LedState.MQTT_NORMAL:
                self.blue()                
            elif state == LedState.NETWORK_ERROR:
                if blink_counter == 0:
                    self.yellow()
                else:
                    self.off()
            elif state == LedState.MQTT_ERROR:
                if blink_counter == 0:
                    self.blue()
                else:
                    self.off()                    
            elif state == LedState.NETWORK_LOST:
                if blink_counter == 0:
                    self.yellow()
                else:
                    self.off()
            elif state == LedState.STARTUP:
                if blink_counter == 0:
                    self.white()
                else:
                    self.off()
            elif state == LedState.MQTT_PARING:
                if blink_counter == 0:
                    self.green()
                else:
                    self.off()

            time.sleep(0.5)
    
    def start(self):
        """Start LED control thread"""
        self.led_thread = threading.Thread(target=self.led_control_task, daemon=True)
        self.led_thread.start()
        self.logger.info("LED controller started")
        
    def stop(self):
        """Stop LED controller"""
        self.off()  # Turn off LED
        self.logger.info("LED controller stopped")

# -----------------------------------------------------------------------------

class GpioButton:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        
        self.BUTTON_PIN = LINUXBOX_BUTTON_PIN
        self._initialize_pin()
        
        # Thread control
        self.button_thread = None

    def _initialize_pin(self):
        SysFSGPIO.export_pin(self.BUTTON_PIN)
        SysFSGPIO.set_direction(self.BUTTON_PIN, "in")

    def is_pressed(self):
        return SysFSGPIO.read_value(self.BUTTON_PIN) == "1"
    
    def button_control_task(self):
        """Button monitoring thread"""
        press_start, reboot_triggered, power_off_triggered = None, False, False
        self.logger.info("Starting button monitor...")

        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            if self.is_pressed():
                if press_start is None: 
                    press_start = time.time()
                press_duration = time.time() - press_start

                if press_duration >= 15:
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.REBOOT)
                    reboot_triggered, power_off_triggered = True, False
                elif press_duration >= 5:
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.POWER_OFF)
                    power_off_triggered = True

            else:
                if press_start:
                    if power_off_triggered and self.supervisor and hasattr(self.supervisor, 'perform_power_off'):
                        self.supervisor.perform_power_off()
                    elif reboot_triggered and self.supervisor and hasattr(self.supervisor, 'perform_factory_reset'):
                        self.supervisor.perform_factory_reset()
                press_start, reboot_triggered, power_off_triggered = None, False, False

            time.sleep(0.5)
    
    def start(self):
        """Start button monitoring thread"""
        self.button_thread = threading.Thread(target=self.button_control_task, daemon=True)
        self.button_thread.start()
        self.logger.info("Button monitor started")
        
    def stop(self):
        """Stop button monitoring"""
        self.logger.info("Button monitor stopped")

# -----------------------------------------------------------------------------

# zigbee and thread device bootup and thread chip check.
class GpioHwController:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.thread_conf_path = "/var/lib/homeassistant/thread.conf"

    
    # chip 0: gpiochip426
    # refer to pinctrl-meson-axg.c
    def initialize_pin(self):
        """
        Initialize GPIO pins for Zigbee and Thread modules
        """
        # Initialize GPIO pins for Zigbee and Thread modules
        for pin in (427, 429, 453, 455):
            SysFSGPIO.export_pin(pin)
            SysFSGPIO.set_direction(pin, "out")

        self.logger.info("Reset Zigbee module GPIOZ_1/GPIOZ_3...")
        # Zigbee reset: DB_RSTN1/GPIOZ_1
        # Zigbee boot: DB_BOOT1/GPIOZ_3
        SysFSGPIO.write_value(429, 0)
        time.sleep(0.5)
        SysFSGPIO.write_value(427, 1)
        time.sleep(0.5)
        SysFSGPIO.write_value(427, 0)
        time.sleep(0.5)
        SysFSGPIO.write_value(427, 1)
        time.sleep(0.5)

        self.logger.info("Reset Thread module GPIOA_1/GPIOA_3 ...")
        # Thread reset: DB_RSTN2/GPIOA_1
        # Thread boot: DB_BOOT2/GPIOA_3 
        SysFSGPIO.write_value(455, 0)
        time.sleep(0.5)
        SysFSGPIO.write_value(453, 1)
        time.sleep(0.5)
        SysFSGPIO.write_value(453, 0)
        time.sleep(0.5)
        SysFSGPIO.write_value(453, 1)
        time.sleep(0.5)
        
        # Check if thread.conf exists and contains device information
        thread_device_already_detected = False
        if os.path.exists(self.thread_conf_path):
            try:
                with open(self.thread_conf_path, 'r') as f:
                    content = f.read()
                    if "/dev/ttyAML" in content:
                        self.logger.info(f"Thread device previously detected in {self.thread_conf_path}, skipping device detection")
                        thread_device_already_detected = True
                        self.supervisor.enableThreadSupported()
                    else:
                        self.logger.info(f"Thread configuration file exists but no device detected previously")
            except Exception as e:
                self.logger.error(f"Error reading Thread configuration file: {e}")
        else:
            # Check if Thread device is connected to /dev/ttyAML6
            thread_device_detected = self.check_thread_device()
            
            # Create thread.conf file directory if it doesn't exist
            try:
                os.makedirs(os.path.dirname(self.thread_conf_path), exist_ok=True)
                
                # Create the thread.conf file
                with open(self.thread_conf_path, 'w') as f:
                    f.write(f"# Thread configuration created at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    if thread_device_detected:
                        # Write device path if device is detected
                        f.write("/dev/ttyAML6\n")
                        self.logger.info(f"Created Thread configuration file with device path at {self.thread_conf_path}")
                        self.supervisor.enableThreadSupported()
                    else:
                        # Create empty file if no device is detected
                        self.logger.info(f"Created empty Thread configuration file at {self.thread_conf_path}")
            except Exception as e:
                self.logger.error(f"Failed to create Thread configuration file: {e}")

    def check_thread_device(self):
        """
        Check if a Thread device is connected to /dev/ttyAML6
        Returns True if device is detected, False otherwise
        """
        try:
            # Check if the device file exists
            if not os.path.exists("/dev/ttyAML6"):
                self.logger.info("Thread device port /dev/ttyAML6 does not exist")
                return False
                
            # Try to open the serial port and read data
            import serial
            try:
                # Open the serial port with a short timeout
                ser = serial.Serial("/dev/ttyAML6", 115200, timeout=3)
                
                # Try to read some data
                data = ser.read(100)
                
                # Close the port
                ser.close()
                
                # Check if we received any data
                if len(data) > 0:
                    self.logger.info(f"Thread device detected on /dev/ttyAML6, received data: {data}")
                    return True
                else:
                    self.logger.info("Thread device port /dev/ttyAML6 opened successfully, but no data received")
                    # Still return True since we could open the port
                    return False
                
            except serial.SerialException as e:
                self.logger.info(f"No Thread device detected on /dev/ttyAML6: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Error checking Thread device: {e}")
            return False

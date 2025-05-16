# maintainer: guoping.liu@thirdreality.com

"""Button & LED for HubV3/LinuxBox"""

import os
import time
import logging
import threading
import subprocess

from enum import Enum
from .const import LINUXBOX_LED_R_CHIP, LINUXBOX_LED_R_LINE
from .const import LINUXBOX_LED_G_CHIP, LINUXBOX_LED_G_LINE
from .const import LINUXBOX_LED_B_CHIP, LINUXBOX_LED_B_LINE
from .const import LINUXBOX_BUTTON_CHIP, LINUXBOX_BUTTON_LINE

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# -----------------------------------------------------------------------------
class LedState(Enum):
    REBOOT = "reboot"
    POWER_OFF = "power_off"
    FACTORY_RESET = "factory_reset"
    NORMAL = "normal"
    NETWORK_ERROR = "network_error"
    NETWORK_LOST = "network_lost"
    STARTUP = "startup"
    MQTT_PARING = "mqtt_paring"
    MQTT_PARED = "mqtt_pared"
    MQTT_ERROR = "mqtt_error"
    MQTT_NORMAL = "mqtt_normal"
    MQTT_ZIGEBB = "mqtt_zigbee"
    MQTT_NETWORK = "mqtt_network"

# -----------------------------------------------------------------------------
class GpioLed:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        
        # Define LED configuration with chip and line numbers
        self.leds = {
            'RED': {'chip': LINUXBOX_LED_R_CHIP, 'line': LINUXBOX_LED_R_LINE},
            'GREEN': {'chip': LINUXBOX_LED_G_CHIP, 'line': LINUXBOX_LED_G_LINE},
            'BLUE': {'chip': LINUXBOX_LED_B_CHIP, 'line': LINUXBOX_LED_B_LINE}
        }
        
        # Thread control
        self.led_thread = None

    def _set_gpio_value(self, chip, line, value):
        """Set GPIO value using gpioset command"""
        try:
            cmd = ["gpioset", str(chip), f"{line}={value}"]
            subprocess.run(cmd, check=True)
            return True
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to set GPIO chip {chip} line {line} to {value}: {e}")
            return False

    def set_color(self, red, green, blue):
        """Set LED colors using gpioset"""
        # Set RED LED
        self._set_gpio_value(
            self.leds['RED']['chip'], 
            self.leds['RED']['line'], 
            1 if red else 0
        )
        
        # Set GREEN LED
        self._set_gpio_value(
            self.leds['GREEN']['chip'], 
            self.leds['GREEN']['line'], 
            1 if green else 0
        )
        
        # Set BLUE LED
        self._set_gpio_value(
            self.leds['BLUE']['chip'], 
            self.leds['BLUE']['line'], 
            1 if blue else 0
        )

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
            elif state == LedState.MQTT_ZIGEBB:
                self.green()         
            elif state == LedState.MQTT_NETWORK:
                self.yellow()                                         
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
            elif state == LedState.FACTORY_RESET:
                if blink_counter == 0:
                    self.white()
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
        
        # Button configuration with chip and line numbers
        self.button = {
            'chip': LINUXBOX_BUTTON_CHIP,
            'line': LINUXBOX_BUTTON_LINE
        }
        
        # Thread control
        self.button_thread = None

    def _initialize_pin(self):
        # No initialization needed for gpioget approach
        pass

    def is_pressed(self):
        """Check if button is pressed using gpioget command"""
        try:
            cmd = ["gpioget", str(self.button['chip']), str(self.button['line'])]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip() == "1"
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to get button state from GPIO chip {self.button['chip']} line {self.button['line']}: {e}")
            return False
    
    def button_control_task(self):
        """Button monitoring thread"""
        press_start = None
        action_triggered = None
        self.logger.info("Starting button monitor...")

        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            if self.is_pressed():
                if press_start is None: 
                    press_start = time.time()
                    action_triggered = None
                
                press_duration = time.time() - press_start

                # Update LED state based on press duration
                if press_duration >= 15 and action_triggered != 'factory_reset':
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.FACTORY_RESET)
                    action_triggered = 'factory_reset'
                elif press_duration >= 9 and action_triggered not in ['factory_reset', 'blue']:
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.MQTT_NORMAL)  # Blue light for 9-15 seconds
                    action_triggered = 'blue'
                elif press_duration >= 6 and action_triggered not in ['factory_reset', 'blue', 'network_setup']:
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.MQTT_NETWORK)  # Yellow for network setup
                    action_triggered = 'network_setup'
                elif press_duration >= 3 and action_triggered not in ['factory_reset', 'blue', 'network_setup', 'zigbee_pairing']:
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.MQTT_ZIGEBB) # Green for Zigbee pairing
                    action_triggered = 'zigbee_pairing'

            else:
                if press_start is not None:
                    press_duration = time.time() - press_start
                    
                    # Handle button release actions
                    if action_triggered == 'factory_reset' and press_duration >= 15:
                        if self.supervisor and hasattr(self.supervisor, 'perform_factory_reset'):
                            self.supervisor.perform_factory_reset()
                    elif action_triggered == 'network_setup' and 6 <= press_duration < 9:
                        if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                            self.supervisor.set_led_state(LedState.MQTT_NORMAL) 
                        # Stop Home Assistant and set network setup flag
                        if self.supervisor and hasattr(self.supervisor, 'perform_wifi_provision_prepare'):
                            self.supervisor.perform_wifi_provision_prepare()
                    elif action_triggered == 'zigbee_pairing' and 3 <= press_duration < 6:
                        # Enter Zigbee pairing mode
                        if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                            self.supervisor.set_led_state(LedState.MQTT_NORMAL)        
                        if self.supervisor and hasattr(self.supervisor, 'start_zigbee_pairing'):
                            self.supervisor.start_zigbee_pairing()
                    
                    # Reset state
                    press_start = None
                    action_triggered = None

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
        # # Check if thread.conf exists and contains device information
        # thread_device_already_detected = False
        # if os.path.exists(self.thread_conf_path):
        #     try:
        #         with open(self.thread_conf_path, 'r') as f:
        #             content = f.read()
        #             if "/dev/ttyAML" in content:
        #                 self.logger.info(f"Thread device previously detected in {self.thread_conf_path}, skipping device detection")
        #                 thread_device_already_detected = True
        #                 self.supervisor.enableThreadSupported()
        #             else:
        #                 self.logger.info(f"Thread configuration file exists but no device detected previously")
        #     except Exception as e:
        #         self.logger.error(f"Error reading Thread configuration file: {e}")
        # else:
        #     # Check if Thread device is connected to /dev/ttyAML6
        #     thread_device_detected = self._check_thread_device()
            
        #     # Create thread.conf file directory if it doesn't exist
        #     try:
        #         os.makedirs(os.path.dirname(self.thread_conf_path), exist_ok=True)
                
        #         # Create the thread.conf file
        #         with open(self.thread_conf_path, 'w') as f:
        #             f.write(f"# Thread configuration created at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        #             if thread_device_detected:
        #                 # Write device path if device is detected
        #                 f.write("/dev/ttyAML6\n")
        #                 self.logger.info(f"Created Thread configuration file with device path at {self.thread_conf_path}")
        #                 self.supervisor.enableThreadSupported()
        #             else:
        #                 # Create empty file if no device is detected
        #                 self.logger.info(f"Created empty Thread configuration file at {self.thread_conf_path}")
        #     except Exception as e:
        #         self.logger.error(f"Failed to create Thread configuration file: {e}")

        # Initialize GPIO pins for Zigbee and Thread modules
        self.logger.info("Reset Zigbee module GPIOZ_1/GPIOZ_3...")
        # Zigbee reset: DB_RSTN1/GPIOZ_1
        # Zigbee boot: DB_BOOT1/GPIOZ_3
        try:
            subprocess.run(["gpioset", "0", "3=0"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "1=1"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "1=0"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "1=1"], check=True)
            
            # self.logger.info("Reset Thread module GPIOA_1/GPIOA_3 ...")
            # # Thread reset: DB_RSTN2/GPIOA_1
            # # Thread boot: DB_BOOT2/GPIOA_3 
            # subprocess.run(["gpioset", "0", "29=0"], check=True)
            # time.sleep(0.2)
            # subprocess.run(["gpioset", "0", "27=1"], check=True)
            # time.sleep(0.2)
            # subprocess.run(["gpioset", "0", "27=0"], check=True)
            # time.sleep(0.2)
            # subprocess.run(["gpioset", "0", "27=1"], check=True)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error executing gpioset command: {e}")
        except Exception as e:
            self.logger.error(f"Error initializing GPIO pins: {e}")



    # def _check_thread_device(self):
    #     """
    #     Check if a Thread device is connected to /dev/ttyAML6
    #     Returns True if device is detected, False otherwise
        
    #     使用 gpioget 0 27 检测，如果得到的结果为0，则/dev/ttyAML6上没有连接设备
    #     如果为1，则对接了设备
    #     """
    #     try:
    #         # 使用 gpioget 检查 GPIO 27 的状态
    #         result = subprocess.run(["gpioget", "0", "27"], capture_output=True, text=True)
            
    #         # 检查命令是否成功执行
    #         if result.returncode != 0:
    #             self.logger.error(f"Failed to get GPIO 27 status: {result.stderr}")
    #             return False
            
    #         # 获取输出并去除空白字符
    #         gpio_value = result.stdout.strip()
            
    #         # 检查 GPIO 值
    #         if gpio_value == "1":
    #             self.logger.info("Thread device detected (GPIO 27 = 1)")
    #             return True
    #         else:
    #             self.logger.info("No Thread device detected (GPIO 27 = 0)")
    #             return False
            
    #     except Exception as e:
    #         self.logger.error(f"Error checking Thread device: {e}")
    #         return False

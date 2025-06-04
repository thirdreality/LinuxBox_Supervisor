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
    # 系统级状态（最高优先级）
    REBOOT = "reboot"
    POWER_OFF = "power_off"
    FACTORY_RESET = "factory_reset"
    
    # 用户按键事件状态（次高优先级）
    USER_EVENT_RED = "red"      #  红灯
    USER_EVENT_BLUE = "blue"    # 蓝灯
    USER_EVENT_YELLOW = "yellow" # 黄灯
    USER_EVENT_GREEN = "green"   # 绿灯
    USER_EVENT_WHITE = "white"   # 白灯
    USER_EVENT_OFF = "off"  
    
    # 普通状态
    NORMAL = "normal"
    NETWORK_ERROR = "network_error"
    NETWORK_LOST = "network_lost"
    STARTUP = "startup"
    MQTT_PARING = "mqtt_paring"
    MQTT_PARED = "mqtt_pared"
    MQTT_ERROR = "mqtt_error"
    MQTT_NORMAL = "mqtt_normal"
    MQTT_ZIGBEE = "mqtt_zigbee"
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
        self.timer_thread = None
        # Store the current LED state
        self.current_led_state = LedState.STARTUP
        # Add a lock for thread safety
        self.state_lock = threading.Lock()
        # Cache for last LED values
        self.last_led_values = {'red': None, 'green': None, 'blue': None}
        # LED control variables
        self.blink_counter = 0
        self.step_counter = 0
        
        # Timer trigger control
        self.timer_trigger_event = threading.Event()
        self.timer_delay = 0.5  # Default delay of 500ms
        self.timer_delay_lock = threading.Lock()
        self.timer_stop_event = threading.Event()
        
        # LED control task notification
        self.led_control_event = threading.Event()

    def _set_gpio_value(self, chip, line, value, led_name):
        """Set GPIO value using gpioset command"""
        # 获取这个LED的上一次值
        last_value = self.last_led_values.get(led_name.lower())
        
        # 如果新值与上一次相同，则跳过
        if last_value is not None and last_value == value:
            return True
            
        try:
            cmd = ["gpioset", str(chip), f"{line}={value}"]
            subprocess.run(cmd, check=True)
            # 缓存新值
            self.last_led_values[led_name.lower()] = value
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
            1 if red else 0,
            'red'
        )
        
        # Set GREEN LED
        self._set_gpio_value(
            self.leds['GREEN']['chip'], 
            self.leds['GREEN']['line'], 
            1 if green else 0,
            'green'
        )
        
        # Set BLUE LED
        self._set_gpio_value(
            self.leds['BLUE']['chip'], 
            self.leds['BLUE']['line'], 
            1 if blue else 0,
            'blue'
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
        """LED control thread - 仅依靠通知机制工作"""
        self.blink_counter = 0
        self.logger.info("Starting LED controller (notification-based)...")
        
        # 启动定时触发线程
        self.start_timer_trigger()
        
        # 初始处理一次LED状态
        with self.state_lock:
            state = self.current_led_state
        self.process_led_state(state)
        
        # 设置初始事件状态为清除
        self.led_control_event.clear()
        
        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            # 无限期等待通知，直到收到通知才处理
            self.led_control_event.wait()
            
            # 收到通知后，清除事件以便下次通知
            self.led_control_event.clear()
            self.logger.debug("LED control task notified by external event")
            
            # 获取当前LED状态
            with self.state_lock:
                state = self.current_led_state
            
            # 使用process_led_state函数处理LED状态
            # 该函数会处理闪烁效果并在需要时自动触发下一次闪烁
            self.process_led_state(state)
    
    def start(self):
        """Start LED control thread"""
        self.led_thread = threading.Thread(target=self.led_control_task, daemon=True)
        self.led_thread.start()
        self.logger.info("LED controller started")
        
    def stop(self):
        """Stop LED controller"""
        if self.led_thread and self.led_thread.is_alive():
            self.led_thread = None
        self.off()  # Turn off LED
        self.logger.info("LED controller stopped")

    def set_led_state(self, state):
        """Set the current LED state"""
        with self.state_lock:
            # 定义状态优先级
            system_states = [LedState.REBOOT, LedState.POWER_OFF, LedState.FACTORY_RESET]
            user_event_states = [
                LedState.USER_EVENT_RED, LedState.USER_EVENT_BLUE, 
                LedState.USER_EVENT_YELLOW, LedState.USER_EVENT_GREEN, 
                LedState.USER_EVENT_WHITE, LedState.USER_EVENT_OFF
            ]
            # 明确列出所有MQTT_相关状态
            mqtt_event_states = [
                LedState.MQTT_PARING,
                LedState.MQTT_PARED,
                LedState.MQTT_ERROR,
                LedState.MQTT_NORMAL,
                LedState.MQTT_ZIGBEE,
                LedState.MQTT_NETWORK
            ]

            # 检查是否需要更新状态
            state_changed = False
            
            # 最高优先级：系统级状态（重启、关机、出厂重置）
            if state in system_states:
                if self.current_led_state != state:
                    self.current_led_state = state
                    state_changed = True
                    self.logger.info(f"Setting LED to system state: {state}")
                return

            # 次高优先级：用户事件状态
            if state in user_event_states:
                # 如果当前状态是系统级，则不更新
                if self.current_led_state in system_states:
                    self.logger.info(f"Not updating LED: current system state {self.current_led_state} has higher priority than user event {state}")
                    return
                # 否则设置为用户事件状态
                if self.current_led_state not in user_event_states or self.current_led_state != state:
                    self.current_led_state = state
                    state_changed = True
                    self.logger.info(f"Setting LED to user event state: {state}")
                    if self.current_led_state == LedState.USER_EVENT_OFF:                    
                        self.current_led_state = LedState.NORMAL
                return

            # MQTT_* 优先级（比普通高，比用户事件低）
            if state in mqtt_event_states:
                # 如果当前状态是系统级或用户事件，则不更新
                if self.current_led_state in system_states or self.current_led_state in user_event_states:
                    self.logger.info(f"Not updating LED: current state {self.current_led_state} has higher priority than MQTT event {state}")
                    return
                # 否则设置为MQTT事件状态
                if self.current_led_state not in mqtt_event_states or self.current_led_state != state:
                    self.current_led_state = state
                    state_changed = True
                    self.logger.info(f"Setting LED to MQTT event state: {state}")
                    if self.current_led_state == LedState.MQTT_NORMAL or self.current_led_state == LedState.MQTT_PARED:                    
                        self.current_led_state = LedState.NORMAL                    
                return

            # 如果当前状态属于高优先级（系统级、用户事件、MQTT事件），则不更新
            if (
                self.current_led_state in system_states
                or self.current_led_state in user_event_states
                or self.current_led_state in mqtt_event_states
            ):
                self.logger.info(
                    f"Not updating LED: current state {self.current_led_state} has higher priority than {state}"
                )
                return
                
            # 处理特殊状态转换
            old_state = self.current_led_state
            self.current_led_state = state
            
            # 检查状态是否发生变化
            if old_state != self.current_led_state:
                state_changed = True
                self.logger.info(f"Setting LED to normal state: {state}")
            
            # 如果状态发生变化，重置step_counter以立即响应
            if state_changed:
                self.step_counter = 0

    def get_led_state(self):
        """Get the current LED state"""
        with self.state_lock:
            return self.current_led_state
            
    def start_timer_trigger(self):
        """Start the timer trigger thread"""
        self.timer_stop_event.clear()
        if self.timer_thread is None or not self.timer_thread.is_alive():
            self.timer_thread = threading.Thread(target=self.timer_trigger_task)
            self.timer_thread.daemon = True
            self.timer_thread.start()
            self.logger.info("Timer trigger thread started")
    
    def stop_timer_trigger(self):
        """Stop the timer trigger thread"""
        self.timer_stop_event.set()
        if self.timer_thread and self.timer_thread.is_alive():
            self.timer_thread.join(1.0)  # Wait for thread to terminate with timeout
            self.logger.info("Timer trigger thread stopped")
    
    def timer_trigger_task(self):
        """Timer trigger thread to periodically trigger LED control"""
        self.logger.info("LED timer trigger thread started")
        
        while not self.timer_stop_event.is_set():
            # Get current delay time (thread-safe)
            with self.timer_delay_lock:
                current_delay = self.timer_delay
            
            # Wait for the specified delay or until triggered
            self.timer_trigger_event.clear()
            triggered = self.timer_trigger_event.wait(current_delay)
            
            if not self.timer_stop_event.is_set():
                # Set LED state based on current state
                with self.state_lock:
                    state = self.current_led_state
                
                # 检查是否是需要闪烁的状态
                needs_blink = state in [
                    LedState.FACTORY_RESET,
                    LedState.NETWORK_ERROR,
                    LedState.MQTT_ERROR,
                    LedState.NETWORK_LOST,
                    LedState.STARTUP,
                    LedState.MQTT_PARING
                ]
                
                # Process the LED state
                self.process_led_state(state)
                
                # 如果是闪烁状态，重置定时器延迟为500ms
                # 并在当前循环结束后立即触发下一次闪烁
                if needs_blink:
                    # 先设置延迟
                    with self.timer_delay_lock:
                        self.timer_delay = 0.5
                    
                    # 不要在这里直接触发LED控制，而是让当前循环结束后自然进入下一次循环
                    # 这样可以避免重复触发和级联效应
                
                # Log only when explicitly triggered (not by timer)
                if triggered:
                    self.logger.debug("LED control triggered by external event")
    
    def process_led_state(self, state):
        """Process the LED state and set appropriate color"""
        # 标记是否需要闪烁效果
        needs_blink = False
        
        # Similar to the match-case in led_control_task
        if state == LedState.REBOOT:
            self.red()
        elif state == LedState.POWER_OFF:
            self.yellow()
        elif state == LedState.FACTORY_RESET:
            # Toggle for blinking effect
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.red()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.USER_EVENT_OFF:
            self.off()
        elif state == LedState.USER_EVENT_RED:
            self.red()
        elif state == LedState.USER_EVENT_BLUE:
            self.blue()
        elif state == LedState.USER_EVENT_YELLOW:
            self.yellow()
        elif state == LedState.USER_EVENT_GREEN:
            self.green()
        elif state == LedState.USER_EVENT_WHITE:
            self.white()
        elif state == LedState.NORMAL or state == LedState.MQTT_NORMAL:
            self.blue()
        elif state == LedState.NETWORK_ERROR:
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.yellow()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.MQTT_ERROR:
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.blue()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.NETWORK_LOST:
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.yellow()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.STARTUP:
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.white()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.MQTT_PARING:
            self.blink_counter = (self.blink_counter + 1) % 2
            if self.blink_counter == 0:
                self.green()
            else:
                self.off()
            needs_blink = True
        elif state == LedState.MQTT_PARED:
            self.green()
        else:
            # 默认情况
            self.logger.warning(f"Unknown LED state: {state}")
            self.off()
    
    def trigger_led_control(self, reset_delay=0.5):
        """Trigger LED control immediately and reset the timer"""
        # Reset timer delay (thread-safe)
        with self.timer_delay_lock:
            self.timer_delay = reset_delay
        
        # Trigger the timer thread
        self.timer_trigger_event.set()
        
        # Notify the LED control task
        self.led_control_event.set()
        self.logger.debug("LED control task notified by external event")
    

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
        self.timer_thread = None
        self.stop_event = threading.Event()
        self.button_pressed = threading.Event()
        self.press_start_time = 0

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
    
    def _check_button_physical_state(self):
        """检查按钮当前物理状态"""
        try:
            cmd = ["gpioget", str(self.button['chip']), str(self.button['line'])]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip() == "1"
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to get button state from GPIO chip {self.button['chip']} line {self.button['line']}: {e}")
            return False
    
    def _monitor_button_events(self):
        """监控按键按下和释放事件的主线程"""
        self.logger.info("Starting button event monitor thread...")
        
        # 错误计数器和状态检查计时器
        error_count = 0
        last_state_check = 0
        
        while not self.stop_event.is_set():
            current_time = time.time()
                
            # 检测按键按下
            if not self.button_pressed.is_set():
                try:
                    cmd = ["gpiomon", "-n", "1", "-r", str(self.button['chip']), str(self.button['line'])]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    
                    if result.returncode == 0:
                        # 按键被按下
                        self.logger.info("Button pressed")
                        self.button_pressed.set()
                        self.press_start_time = time.time()
                        
                        # 启动计时线程
                        if self.timer_thread is None or not self.timer_thread.is_alive():
                            self.timer_thread = threading.Thread(target=self._button_timer_task)
                            self.timer_thread.daemon = True
                            self.timer_thread.start()
                        
                        # 触发LED控制并重置计时器
                        if self.supervisor and hasattr(self.supervisor, 'led') and hasattr(self.supervisor.led, 'trigger_led_control'):
                            self.supervisor.led.trigger_led_control(0.5)  # 重置为500毫秒
                            self.logger.debug("Reset LED timer trigger on button press")
                        
                        # 重置错误计数器
                        error_count = 0
                except subprocess.TimeoutExpired:
                    # 正常超时，继续等待
                    pass
                except Exception as e:
                    self.logger.error(f"Error monitoring button press: {e}")
                    error_count += 1
                    time.sleep(1)  # 出错时等待一会再重试
            
            # 检测按键释放
            else:
                try:
                    cmd = ["gpiomon", "-n", "1", "-f", str(self.button['chip']), str(self.button['line'])]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    
                    if result.returncode == 0:
                        # 按键被释放
                        press_duration = time.time() - self.press_start_time
                        self.logger.info(f"Button released after {press_duration:.2f} seconds")
                        self.button_pressed.clear()
                        
                        # 根据按键时间执行相应操作
                        self._handle_button_release(press_duration)
                        
                        # 触发LED控制并重置计时器
                        if self.supervisor and hasattr(self.supervisor, 'led') and hasattr(self.supervisor.led, 'trigger_led_control'):
                            self.supervisor.led.trigger_led_control(0.5)  # 重置为500毫秒
                            self.logger.debug("Reset LED timer trigger on button release")
                        
                        # 重置错误计数器
                        error_count = 0
                except subprocess.TimeoutExpired:
                    # 正常超时，继续等待
                    pass
                except Exception as e:
                    self.logger.error(f"Error monitoring button release: {e}")
                    error_count += 1
                    time.sleep(1)  # 出错时等待一会再重试
            
            # 如果连续出错超过5次，重置按钮状态
            if error_count > 5:
                self.logger.warning("Too many consecutive errors in button monitoring, resetting button state")
                if self.button_pressed.is_set():
                    self.logger.info("Forcing button state to released due to errors")
                    self.button_pressed.clear()
                    # 尝试恢复到正常状态
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(LedState.NORMAL)
                error_count = 0
    
    def _button_timer_task(self):
        """按键计时线程，根据按键时间显示不同的LED颜色"""
        last_action = None
        
        # 初始状态：白灯
        if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
            self.supervisor.set_led_state(LedState.USER_EVENT_WHITE)  # 白灯
        
        while self.button_pressed.is_set() and not self.stop_event.is_set():
            press_duration = time.time() - self.press_start_time
            
            # 根据按键时间设置LED颜色，使用专用的高优先级用户事件状态
            if press_duration >= 15 and last_action != 'red':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_RED)  # 红灯
                last_action = 'red'
                self.logger.info("Timer: 15+ seconds - Red light (factory reset)")
            elif 9 <= press_duration < 15 and last_action != 'blue':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_BLUE)  # 蓝灯
                last_action = 'blue'
                self.logger.info("Timer: 9-15 seconds - Blue light")
            elif 6 <= press_duration < 9 and last_action != 'yellow':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_YELLOW)  # 黄灯
                last_action = 'yellow'
                self.logger.info("Timer: 6-9 seconds - Yellow light (network setup)")
            elif 3 <= press_duration < 6 and last_action != 'green':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_GREEN)  # 绿灯
                last_action = 'green'
                self.logger.info("Timer: 3-6 seconds - Green light (Zigbee pairing)")
            elif 0 <= press_duration < 3 and last_action != 'white':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_WHITE)  # 白灯
                last_action = 'white'
                self.logger.info("Timer: 0-3 seconds - White light")
            
            time.sleep(0.1)  # 小的睡眠间隔以减少CPU使用
    
    def _handle_button_release(self, press_duration):
        """处理按键释放事件"""
        # 根据按键时间执行相应操作
        if press_duration >= 15:
            # 15秒以上：执行出厂重置
            if self.supervisor and hasattr(self.supervisor, 'perform_factory_reset'):
                self.logger.info("Executing factory reset")
                self.supervisor.perform_factory_reset()
        elif 9 <= press_duration < 15:
            # 9-15秒：蓝灯操作
            self.logger.info("Blue light action completed")
            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
        elif 6 <= press_duration < 9:
            # 6-9秒：网络设置
            self.logger.info("Network setup action triggered")
            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
            if self.supervisor and hasattr(self.supervisor, 'perform_wifi_provision_prepare'):
                self.supervisor.perform_wifi_provision_prepare()
        elif 3 <= press_duration < 6:
            # 3-6秒：Zigbee配对
            self.logger.info("Zigbee pairing action triggered")
            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
            if self.supervisor and hasattr(self.supervisor, 'start_zigbee_pairing'):
                self.supervisor.start_zigbee_pairing()
        else:
            # 0-3秒：恢复正常状态
            self.logger.info("Short press detected, returning to normal state")
            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
    
    def button_control_task(self):
        """启动按键监控线程"""
        self._monitor_button_events()
    
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
    
    # chip 0: gpiochip426
    # refer to pinctrl-meson-axg.c
    def initialize_pin(self):
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



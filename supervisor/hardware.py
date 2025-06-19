# maintainer: guoping.liu@3reality.com

"""Button & LED for HubV3/LinuxBox"""

import os
import time
import logging
import threading
import signal
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

    # 一明一暗算一次(1HZ)
    # 用户按键事件状态（最高优先级）
    USER_EVENT_RED = "red"      #  红灯
    USER_EVENT_BLUE = "blue"    # 蓝灯
    USER_EVENT_YELLOW = "yellow" # 黄灯
    USER_EVENT_GREEN = "green"   # 绿灯
    USER_EVENT_WHITE = "white"   # 白灯
    USER_EVENT_OFF = "off"  

    # 系统级状态（次高优先级）
    REBOOT = "reboot" # 白灯
    FACTORY_RESET = "factory_reset"  # 恢复出厂设置: 红色快闪（4Hz）
    STARTUP = "startup" # 白灯
    STARTUP_OFF = "startup_off" # 白灯    

    # 系统级状态（高优先级）
    SYS_WIFI_CONFIG_PENDING = "sys_wifi_config_pending"  # 配网模式（待配网）: 黄色慢闪（0.5Hz）
    SYS_WIFI_CONFIGURING = "sys_wifi_configuring"  # 配网中（进行连接）: 黄色快闪（3Hz）
    SYS_WIFI_CONFIG_SUCCESS = "sys_wifi_config_success"  # 配网成功: 黄色常亮1秒后转正常运行
    SYS_DEVICE_PAIRING = "sys_device_pairing"  # 添加子设备（扫描中）: 绿色慢闪（1Hz）
    SYS_FIRMWARE_UPDATING = "sys_firmware_updating"  # 设备升级中: 绿色类呼吸灯效果 (1Hz pulse)
    SYS_EVENT_OFF = "sys_event_off" #系统操作完成

    # 系统级运行态
    SYS_SYSTEM_CORRUPTED = "sys_system_corrupted"  # 系统未安装（如系统损坏）: 红色慢闪（0.5Hz）
    SYS_ERROR_CONDITION = "sys_error_condition"  # 异常/错误提示: 红色慢闪(0.5Hz）
    SYS_OFFLINE = "sys_offline"  # 离线: 黄色快闪（3Hz）    
    SYS_NORMAL_OPERATION = "sys_normal_operation"  # 正常运行: 蓝色常亮

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
        # Store the current LED state using a four-tier priority system
        self.user_event_priority_state = None  # Tier 1 (Highest)
        self.system_critical_priority_state = LedState.STARTUP  # Tier 2
        self.system_working_priority_state = None  # Tier 3
        self.general_operational_priority_state = None  # Tier 4
        
        # Determine initial current_led_state based on priorities
        # At init, system_critical_priority_state is set to STARTUP
        if self.user_event_priority_state is not None:
            self.current_led_state = self.user_event_priority_state
        elif self.system_critical_priority_state is not None:
            self.current_led_state = self.system_critical_priority_state
        elif self.system_working_priority_state is not None:
            self.current_led_state = self.system_working_priority_state
        elif self.general_operational_priority_state is not None:
            self.current_led_state = self.general_operational_priority_state
        else:
            # Fallback, though STARTUP should always be active initially
            self.current_led_state = LedState.SYS_NORMAL_OPERATION 

        # self.next_led_state = None # Removed as it's replaced by priority states
        # Add a lock for thread safety
        self.state_lock = threading.Lock()  # Changed to RLock to prevent deadlock
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
            
            # 获取当前LED状态
            with self.state_lock:
                state = self.current_led_state
            
            # 该函数会处理闪烁效果并在需要时自动触发下一次闪烁
            self.logger.info(f"led_control_task: Processing LED state {state}")
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

    def set_led_off_state(self):
        self.set_led_state(LedState.STARTUP_OFF)

    def set_led_state(self, state):
        """Set the current LED state based on priority levels."""
        with self.state_lock:
            old_current_led_state = self.current_led_state
            state_changed = False

            # Define state groups by new four-tier priority
            user_event_states = [ # Tier 1
                LedState.USER_EVENT_RED, LedState.USER_EVENT_BLUE,
                LedState.USER_EVENT_YELLOW, LedState.USER_EVENT_GREEN,
                LedState.USER_EVENT_WHITE, LedState.USER_EVENT_OFF
            ]
            system_critical_states = [ # Tier 2
                LedState.REBOOT, LedState.STARTUP, LedState.STARTUP_OFF, LedState.FACTORY_RESET
            ]
            system_working_states = [ # Tier 3
                LedState.SYS_WIFI_CONFIG_PENDING, LedState.SYS_WIFI_CONFIGURING,
                LedState.SYS_WIFI_CONFIG_SUCCESS, LedState.SYS_DEVICE_PAIRING,
                LedState.SYS_FIRMWARE_UPDATING,
                LedState.SYS_EVENT_OFF
            ]
            general_operational_states = [ # Tier 4
                LedState.SYS_ERROR_CONDITION, LedState.SYS_OFFLINE,
                LedState.SYS_SYSTEM_CORRUPTED, LedState.SYS_NORMAL_OPERATION
            ]

            # Update the respective priority state variable
            if state in user_event_states:
                if state == LedState.USER_EVENT_OFF:
                    self.user_event_priority_state = None
                else:
                    if self.user_event_priority_state != state:
                        self.user_event_priority_state = state
            elif state in system_critical_states:
                if state == LedState.STARTUP_OFF and self.system_critical_priority_state == LedState.STARTUP:
                    self.system_critical_priority_state = None
                else:
                    if self.system_critical_priority_state != state:
                        self.system_critical_priority_state = state
            elif state in system_working_states:
                if state == LedState.SYS_EVENT_OFF:
                    if self.system_working_priority_state is not None:
                        self.system_working_priority_state = None
                else:    
                    if self.system_working_priority_state != state:
                        self.system_working_priority_state = state
            elif state in general_operational_states:
                _general_op_priorities = {
                    LedState.SYS_SYSTEM_CORRUPTED: 0,
                    LedState.SYS_ERROR_CONDITION: 1,
                    LedState.SYS_OFFLINE: 2, 
                    LedState.SYS_NORMAL_OPERATION: 3, # Lower value = higher priority
                }
                
                old_gop_state = self.general_operational_priority_state
                changed_gop_state = False

                if state == LedState.SYS_NORMAL_OPERATION:
                    if self.general_operational_priority_state != LedState.SYS_NORMAL_OPERATION:
                        self.general_operational_priority_state = LedState.SYS_NORMAL_OPERATION
                        changed_gop_state = True
                else: # Incoming state is an error/offline/corrupted state
                    current_gop_state_priority = _general_op_priorities.get(self.general_operational_priority_state, float('inf'))
                    # If current state is SYS_NORMAL_OPERATION, any incoming error state should override it.
                    if self.general_operational_priority_state == LedState.SYS_NORMAL_OPERATION:
                        current_gop_state_priority = float('inf') # Effectively giving any error higher precedence

                    incoming_state_priority = _general_op_priorities.get(state) # 'state' is guaranteed to be in _general_op_priorities here

                    if incoming_state_priority <= current_gop_state_priority:
                        if self.general_operational_priority_state != state:
                            self.general_operational_priority_state = state
                            changed_gop_state = True


            else:
                self.logger.warning(f"Unknown or unhandled LED state received: {state}")

            # Determine the new current_led_state based on the new four-tier priority
            new_current_led_state = LedState.SYS_NORMAL_OPERATION # Default fallback (Tier 4)

            if self.user_event_priority_state is not None: # Tier 1
                new_current_led_state = self.user_event_priority_state
            elif self.system_critical_priority_state is not None: # Tier 2
                new_current_led_state = self.system_critical_priority_state
            elif self.system_working_priority_state is not None: # Tier 3
                new_current_led_state = self.system_working_priority_state
            elif self.general_operational_priority_state is not None: # Tier 4
                new_current_led_state = self.general_operational_priority_state
            
            self.logger.debug(f"[GpioLed set_led_state] Determined new_current_led_state: {new_current_led_state}")
            
            # Check if the actual current_led_state has changed
            if self.current_led_state != new_current_led_state:
                state_changed = True
            
            self.logger.debug(f"[GpioLed set_led_state] Old current_led_state: {old_current_led_state}, New effective_led_state: {new_current_led_state}, state_changed_flag: {state_changed}")

            # Determine if the originally requested 'state' matches the final state of any active priority tier.
            # This is for re-triggering the display pattern even if the overall current_led_state doesn't change.
            # retriggered_priority_match = False
            # if state == self.user_event_priority_state and state is not None: retriggered_priority_match = True
            # elif state == self.system_critical_priority_state and state is not None: retriggered_priority_match = True
            # elif state == self.system_working_priority_state and state is not None: retriggered_priority_match = True
            # elif state == self.general_operational_priority_state and state is not None: # This implies state == new_current_led_state if new_current_led_state is from Tier 4
            #      retriggered_priority_match = True

            if state_changed :         
                self.current_led_state = new_current_led_state # Update to the actual state

                self.step_counter = 0  # Reset step counter for immediate effect

                # Determine and set timer_delay based on the *actual current_led_state*
                calculated_reset_delay = None 
                match self.current_led_state:
                    case LedState.SYS_WIFI_CONFIG_PENDING:
                        calculated_reset_delay = 1    # 0.5Hz
                    case LedState.SYS_WIFI_CONFIGURING:
                        calculated_reset_delay = 0.166  # 3Hz
                    case LedState.SYS_WIFI_CONFIG_SUCCESS: 
                        calculated_reset_delay = 0.5 
                    case LedState.SYS_DEVICE_PAIRING:
                        calculated_reset_delay = 0.5    # 1Hz
                    case LedState.SYS_NORMAL_OPERATION:
                        calculated_reset_delay = 0.5  # Solid, allow updates
                    case LedState.SYS_ERROR_CONDITION:
                        calculated_reset_delay = 1.0    # 0.5Hz
                    case LedState.SYS_OFFLINE:
                        calculated_reset_delay = 0.166  # 3Hz
                    case LedState.SYS_FIRMWARE_UPDATING:
                        calculated_reset_delay = 1.0 # 0.5Hz pulse
                    case LedState.SYS_SYSTEM_CORRUPTED:
                        calculated_reset_delay = 1.0 # 0.5Hz
                    case LedState.STARTUP:
                        calculated_reset_delay = 0.5
                    case LedState.REBOOT:
                        calculated_reset_delay = 0.5
                    case LedState.FACTORY_RESET:
                        calculated_reset_delay = 0.125 # 4Hz
                    case _:
                        # States not listed (e.g., user events, SYS_EVENT_OFF, STARTUP_OFF)
                        # do not trigger a timer_delay change from set_led_state itself.
                        # Their timing might be implicit or handled directly by process_led_state patterns.
                        pass # calculated_reset_delay remains None
                
                if calculated_reset_delay is not None:
                    self.trigger_led_control(reset_delay=calculated_reset_delay)
                    #self.logger.debug(f"[GpioLed set_led_state] Timer delay set to {calculated_reset_delay} for state {self.current_led_state}.")
                else:
                    #self.logger.debug(f"[GpioLed set_led_state] No specific timer delay set for state {self.current_led_state} via set_led_state match block.")
                    #self.logger.debug(f"[GpioLed set_led_state] About to set led_control_event for {self.current_led_state}.")
                    self.led_control_event.set() # Notify the LED control task
                #self.logger.debug(f"[GpioLed set_led_state] led_control_event set.")
            #self.logger.debug(f"[GpioLed set_led_state] END - Releasing state_lock")

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
                
                # Process the LED state
                self.process_led_state(state)
                self.step_counter += 1 # Increment step counter for next cycle
                
                # Log only when explicitly triggered (not by timer)
                if triggered:
                    self.logger.debug("LED control triggered by timer event")
    
    def process_led_state(self, state):
        """Process the LED state and set appropriate color"""   
        match state:
            case LedState.REBOOT:
                self.white() # Solid white during the brief reboot trigger phase
            case LedState.USER_EVENT_OFF:
                self.off()
            case LedState.USER_EVENT_RED:
                self.red()
            case LedState.USER_EVENT_BLUE:
                self.blue()
            case LedState.USER_EVENT_YELLOW:
                self.yellow()
            case LedState.USER_EVENT_GREEN:
                self.green()
            case LedState.USER_EVENT_WHITE:
                self.white()
            case LedState.STARTUP:
                self.white()
            case LedState.SYS_WIFI_CONFIG_PENDING: # Yellow slow flash (1Hz)
                if self.step_counter % 2 == 0:
                    self.yellow()
                else:
                    self.off()
            case LedState.SYS_WIFI_CONFIGURING: # Yellow fast flash (3Hz)
                if self.step_counter % 2 == 0:
                    self.yellow()
                else:
                    self.off()
            case LedState.SYS_WIFI_CONFIG_SUCCESS: # Yellow solid for 1 sec
                self.yellow()
                if self.step_counter >= 1: # After 1 second (2 steps of 0.5s timer_delay)
                    self.logger.info("WIFI_CONFIG_SUCCESS: Display time ended, transitioning to NORMAL_OPERATION.")
                    self.set_led_state(LedState.SYS_EVENT_OFF)
                    self.set_led_state(LedState.SYS_NORMAL_OPERATION)
            case LedState.SYS_DEVICE_PAIRING: # Green slow flash (1Hz)
                if self.step_counter % 2 == 0:
                    self.green()
                else:
                    self.off()
            case LedState.SYS_NORMAL_OPERATION: # Blue solid
                self.blue()
            case LedState.SYS_ERROR_CONDITION: # Red slow flash (1Hz)
                if self.step_counter % 2 == 0:
                    self.red()
                else:
                    self.off()
            case LedState.SYS_OFFLINE: # Yellow fast flash (3Hz)
                if self.step_counter % 2 == 0:
                    self.yellow()
                else:
                    self.off()
            case LedState.SYS_FIRMWARE_UPDATING: # Green breathing (1Hz pulse)
                if self.step_counter % 2 == 0: # On for 1s
                    self.green()
                else: # Off for 1s
                    self.off()
            case LedState.SYS_SYSTEM_CORRUPTED: # Red slow flash (1Hz)
                if self.step_counter % 2 == 0:
                    self.red()
                else:
                    self.off()
            case LedState.FACTORY_RESET: # Example: Red very fast blink
                if self.step_counter % 2 == 0:
                    self.red()
                else:
                    self.off()
            case _: # Default case for any other unhandled states
                self.logger.warning(f"Unknown or unhandled LED state in process_led_state: {state}")
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
        self.logger.debug(f"LED control triggered by external event with delay {reset_delay}")
    

# -----------------------------------------------------------------------------

import os
import signal
import subprocess

class GpioButton:

    def cleanup_gpiomon(self):
        """清理所有gpiomon进程，防止资源残留"""
        try:
            # 查找所有gpiomon进程并kill
            result = subprocess.run(["pgrep", "gpiomon"], capture_output=True, text=True)
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid.isdigit():
                    os.kill(int(pid), signal.SIGKILL)
            self.logger.info("Cleaned up all gpiomon processes")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup gpiomon: {e}")

    def __del__(self):
        # 析构时自动清理gpiomon
        self.cleanup_gpiomon()

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

    
    def _monitor_button_events(self):
        """监控按键按下和释放事件的主线程"""
        self.logger.info("Starting button event monitor thread...")
        
        # 错误计数器和状态检查计时器
        error_count = 0
        last_state_check = 0

        self.logger.info("Check gpiomon procedure...")
        self.cleanup_gpiomon()
        time.sleep(0.5)
        # 检查gpiomon是否还在运行，若有则重复清理并等待
        while True:
            result = subprocess.run(["pgrep", "gpiomon"], capture_output=True, text=True)
            pids = [pid for pid in result.stdout.strip().split("\n") if pid.isdigit()]
            if not pids:
                break
            self.logger.warning(f"gpiomon still running (pids: {pids}), retry cleanup...")
            self.cleanup_gpiomon()
            time.sleep(0.5)
        
        last_press_time = 0
        click_count = 0
        double_click_interval = 0.5  # 500ms内判定为双击

        while not self.stop_event.is_set():
            current_time = time.time()
                
            # 检测按键按下
            if not self.button_pressed.is_set():
                try:
                    cmd = ["gpiomon", "-n", "1", "-r", str(self.button['chip']), str(self.button['line'])]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    
                    if result.returncode == 0:
                        # 按键被按下
                        self.logger.info("[Button]: pressed")
                        # 检查双击
                        if last_press_time != 0 and (time.time() - last_press_time) < double_click_interval:
                            click_count += 1
                        else:
                            click_count = 1
                        last_press_time = time.time()
                        if click_count == 2:
                            self.logger.info("[Button][Button]: xxxxxxxxxxxxxxxxxxxxxx double clicked")
                            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
                            if self.supervisor and hasattr(self.supervisor, 'perform_wifi_provision'):
                                self.supervisor.perform_wifi_provision()
                                                        
                            click_count = 0
                        self.button_pressed.set()
                        self.press_start_time = time.time()
                        
                        # 启动计时线程
                        if self.timer_thread is None or not self.timer_thread.is_alive():
                            self.timer_thread = threading.Thread(target=self._button_timer_task)
                            self.timer_thread.daemon = True
                            self.timer_thread.start()
                                                
                        # 重置错误计数器
                        error_count = 0
                    else:
                        # 增加异常日志
                        self.logger.warning(f"gpiomon returncode={result.returncode}, stdout='{result.stdout}', stderr='{result.stderr}'")
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
                        self.logger.info(f"[Button]: released after {press_duration:.2f} seconds")
                        self.button_pressed.clear()
                        
                        # 根据按键时间执行相应操作
                        self._handle_button_release(press_duration)
                                                
                        # 重置错误计数器
                        error_count = 0
                    else:
                        # 增加异常日志
                        self.logger.warning(f"gpiomon (release) returncode={result.returncode}, stdout='{result.stdout}', stderr='{result.stderr}'")
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
            if press_duration >= 20 and last_action != 'red':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_RED)  # 红灯
                last_action = 'red'
                self.logger.info("Timer: 20+ seconds - Red light (factory reset)")
            # elif 9 <= press_duration < 15 and last_action != 'blue':
            #     if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
            #         self.supervisor.set_led_state(LedState.USER_EVENT_BLUE)  # 蓝灯
            #     last_action = 'blue'
            #     self.logger.info("Timer: 9-15 seconds - Blue light")
            # elif 6 <= press_duration < 9 and last_action != 'yellow':
            #     if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
            #         self.supervisor.set_led_state(LedState.USER_EVENT_YELLOW)  # 黄灯
            #     last_action = 'yellow'
            #    self.logger.info("Timer: 6-9 seconds - Yellow light (network setup)")
            elif 5 <= press_duration < 20 and last_action != 'green':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_GREEN)  # 绿灯
                last_action = 'green'
                self.logger.info("Timer: 5-20 seconds - Green light (Zigbee pairing)")
            elif 0 <= press_duration < 5 and last_action != 'white':
                if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                    self.supervisor.set_led_state(LedState.USER_EVENT_WHITE)  # 白灯
                last_action = 'white'
                self.logger.info("Timer: 0-5 seconds - White light")
            
            time.sleep(0.1)  # 小的睡眠间隔以减少CPU使用
    
    def _handle_button_release(self, press_duration):
        """处理按键释放事件"""
        # 根据按键时间执行相应操作
        if press_duration > 20:
            # 20秒以上：执行出厂重置
            if self.supervisor and hasattr(self.supervisor, 'perform_factory_reset'):
                self.logger.info("Executing factory reset")
                self.supervisor.perform_factory_reset()
        # elif 9 <= press_duration < 15:
        #     # 9-15秒：蓝灯操作
        #     self.logger.info("Blue light action completed")
        #     if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
        #         self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
        # elif 6 <= press_duration < 9:
        #     # 6-9秒：网络设置
        #     self.logger.info("Network setup action triggered")
        #     if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
        #         self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
        #     if self.supervisor and hasattr(self.supervisor, 'perform_wifi_provision_prepare'):
        #         self.supervisor.perform_wifi_provision_prepare()
        elif 5 <= press_duration < 20:
            # 5-20秒：Zigbee配对
            self.logger.info("Zigbee pairing action triggered")
            if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
            if self.supervisor and hasattr(self.supervisor, 'start_zigbee_pairing'):
                self.logger.info("Zigbee pairing action triggered: start_zigbee_pairing")
                self.supervisor.start_zigbee_pairing()
        else:
            # 0-5秒：恢复正常状态
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
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error executing Zigbee gpioset command: {e}")
        except Exception as e:
            self.logger.error(f"Error initializing Zigbee GPIO pins: {e}")

        self.logger.info("Reset Thread module GPIOA_1/GPIOA_3 ...")
        # Thread reset: DB_RSTN2/GPIOA_1
        # Thread boot: DB_BOOT2/GPIOA_3 
        try:            
            subprocess.run(["gpioset", "0", "29=0"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "27=1"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "27=0"], check=True)
            time.sleep(0.2)
            subprocess.run(["gpioset", "0", "27=1"], check=True)

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error executing Thread gpioset command: {e}")
        except Exception as e:
            self.logger.error(f"Error initializing Thread GPIO pins: {e}")            
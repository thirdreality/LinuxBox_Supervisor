# maintainer: guoping.liu@3reality.com

import logging
import threading
from enum import Enum
import subprocess
from .utils import util
from .utils import zigbee_util, setting_util, thread_util
from .websocket_manager import WebSocketManager


class TaskStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


class TaskManager:
    def __init__(self, supervisor):
        self.logger = logging.getLogger(__name__)
        self.supervisor = supervisor
        self._task_lock = threading.RLock()
        self.tasks = {
            "system": self._create_task_entry(),
            "zigbee": self._create_task_entry(),
            "thread": self._create_task_entry(),
            "setting": self._create_task_entry(),
            "wifi": self._create_task_entry(),
        }

    def _create_task_entry(self, status=TaskStatus.IDLE, progress=0, message="", sub_task=""):
        return {
            "status": status.value,
            "progress": progress,
            "message": message,
            "sub_task": sub_task
        }

    def init(self):
        self.logger.info("Initializing Task manager")

    def cleanup(self):
        self.logger.info("Cleaning up Task manager")

    def get_task_info(self, task_type):
        with self._task_lock:
            return self.tasks.get(task_type, {}).copy()

    def _start_task(self, task_type, sub_task_name, target_func, *args, **kwargs):
        with self._task_lock:
            if self.tasks[task_type]["status"] == TaskStatus.RUNNING.value:
                self.logger.warning(f"Task {task_type} is already running.")
                return False

        def progress_callback(percent, message):
            """Update task progress and message"""
            with self._task_lock:
                self.tasks[task_type]["progress"] = percent
                self.tasks[task_type]["message"] = message
            self.logger.info(f"Task {task_type} progress: {percent}% - {message}")

        def complete_callback(success, result_message):
            """Handle task completion"""
            with self._task_lock:
                if success:
                    self.tasks[task_type]["status"] = TaskStatus.SUCCESS.value
                    self.tasks[task_type]["progress"] = 100
                    self.tasks[task_type]["message"] = result_message or "Task completed successfully"
                else:
                    self.tasks[task_type]["status"] = TaskStatus.FAILED.value
                    self.tasks[task_type]["message"] = result_message or "Task failed"
            self.logger.info(f"Task {task_type} completed with status: {self.tasks[task_type]['status']}, message: {result_message}")

        @util.threaded
        def task_wrapper():
            try:
                with self._task_lock:
                    self.tasks[task_type]["status"] = TaskStatus.RUNNING.value
                    self.tasks[task_type]["sub_task"] = sub_task_name
                    self.tasks[task_type]["progress"] = 0
                    self.tasks[task_type]["message"] = ""

                # Call target function with progress and complete callbacks
                target_func(*args, progress_callback=progress_callback, complete_callback=complete_callback, **kwargs)

                # If no complete_callback was called (for backward compatibility)
                with self._task_lock:
                    if self.tasks[task_type]["status"] == TaskStatus.RUNNING.value:
                        self.tasks[task_type]["status"] = TaskStatus.IDLE.value
                        self.tasks[task_type]["progress"] = 100

            except Exception as e:
                self.logger.error(f"Task {task_type} error: {e}")
                with self._task_lock:
                    self.tasks[task_type]["status"] = TaskStatus.ERROR.value
                    self.tasks[task_type]["message"] = str(e)

        task_wrapper()
        return True

    def start_zigbee_switch_zha_mode(self):
        return self._start_task("zigbee", "switch_to_zha", zigbee_util.run_zigbee_switch_zha_mode)

    def start_zigbee_switch_z2m_mode(self):
        return self._start_task("zigbee", "switch_to_z2m", zigbee_util.run_zigbee_switch_z2m_mode)

    def start_zigbee_pairing(self, led_controller=None):
        return self._start_task("zigbee", "pairing", zigbee_util.run_zigbee_pairing, led_controller=led_controller)

    def start_zigbee_ota_update(self):
        return self._start_task("zigbee", "ota", util.run_zigbee_ota_update)

    def start_setting_backup(self):
        return self._start_task("setting", "backup", setting_util.run_setting_backup)

    def start_setting_restore(self, backup_file=None):
        return self._start_task("setting", "restore", setting_util.run_setting_restore, backup_file=backup_file)

    def start_setting_updated(self):
        return self._start_task("setting", "updated", setting_util.run_setting_updated, supervisor=self.supervisor)

    def start_setting_update_z2m_mqtt(self, config: dict):
        """Start long-running setting update task for z2m mqtt config"""
        return self._start_task("setting", "update_z2m_mqtt", setting_util.run_setting_update_z2m_mqtt, config)

    def start_thread_mode_enable(self):
        return self._start_task("thread", "enable", thread_util.run_thread_enable)

    def start_thread_mode_disable(self):
        return self._start_task("thread", "disable", thread_util.run_thread_disable)

    def start_perform_wifi_provision(self):
        return self._start_task("wifi", "provision", self.supervisor.perform_wifi_provision)

    def _try_auto_connect_lte(self):
        """
        尝试自动连接到 LTE 热点
        
        Returns:
            bool: True 如果成功连接，False 如果失败或配置不存在
        """
        import os
        import re
        import time
        
        LTE_CONFIG_FILE = "/etc/lte_3r.conf"
        
        try:
            # 检查配置文件是否存在
            if not os.path.exists(LTE_CONFIG_FILE):
                self.logger.info(f"LTE config file {LTE_CONFIG_FILE} not found, skipping LTE auto-connect")
                return False
            
            # 读取并解析配置文件
            self.logger.info(f"Found LTE config file, reading {LTE_CONFIG_FILE}")
            ssid_prefix = None
            psk = None
            
            with open(LTE_CONFIG_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # 支持 SSID 或 SID 两种写法
                        if key == 'SSID' or key == 'SID':
                            ssid_prefix = value
                        elif key == 'PSK':
                            psk = value
            
            # 验证配置
            if not ssid_prefix or not psk:
                self.logger.warning(f"Invalid LTE config: SSID={ssid_prefix}, PSK={'***' if psk else None}")
                return False
            
            self.logger.info(f"LTE config loaded: SSID prefix='{ssid_prefix}', PSK=***")
            
            # 执行 WiFi 扫描
            self.logger.info("Rescanning WiFi networks...")
            try:
                subprocess.run(['nmcli', 'device', 'wifi', 'rescan'], 
                             capture_output=True, text=True, check=False)
            except Exception as e:
                self.logger.warning(f"WiFi rescan failed: {e}, continuing anyway...")
            
            # 等待 3 秒让扫描完成
            time.sleep(3)
            
            # 获取 WiFi 列表
            self.logger.info("Getting WiFi list...")
            result = subprocess.run(['nmcli', 'device', 'wifi', 'list'], 
                                  capture_output=True, text=True, check=True)
            
            # 解析 WiFi 列表，查找匹配的 SSID
            # SSID 格式：前缀 + 6位MAC (例如: LTE-AABBCC)
            # MAC 格式：[0-9A-F]{6} 或 [0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}
            mac_pattern = r'[0-9A-Fa-f]{6}|[0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}'
            ssid_pattern = re.compile(rf'^{re.escape(ssid_prefix)}({mac_pattern})$')
            
            self.logger.info(f"Looking for SSID pattern: '{ssid_prefix}' + 6-digit MAC (e.g., '{ssid_prefix}AABBCC')")
            
            best_ap = None
            best_signal = -1
            scanned_ssids = []  # 用于收集所有扫描到的SSID
            
            lines = result.stdout.strip().split('\n')
            self.logger.info(f"Found {len(lines) - 1} WiFi networks in scan results")
            # 跳过表头
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 8:
                    continue
                
                # 解析列: IN-USE BSSID SSID MODE CHAN RATE SIGNAL BARS SECURITY
                # SSID 可能包含空格，需要特殊处理
                # 第一列可能是 * 或空，第二列是 BSSID，第三列开始是 SSID
                start_idx = 1 if parts[0] == '*' else 0
                bssid = parts[start_idx]
                
                # 找到 MODE 列（应该是 "Infra"）来定位 SSID 的结束
                mode_idx = -1
                for i, part in enumerate(parts[start_idx + 1:], start=start_idx + 1):
                    if part == 'Infra' or part == 'Adhoc':
                        mode_idx = i
                        break
                
                if mode_idx == -1:
                    continue
                
                # SSID 是 BSSID 和 MODE 之间的所有部分
                ssid_parts = parts[start_idx + 1:mode_idx]
                if not ssid_parts or ssid_parts[0] == '--':
                    continue
                
                ssid = ' '.join(ssid_parts)
                scanned_ssids.append(ssid)  # 收集SSID用于调试
                
                # 尝试获取信号强度（在 RATE 之后）
                try:
                    # 找到信号强度列（应该在 CHAN 和 RATE 之后）
                    # 格式：CHAN RATE SIGNAL
                    signal_idx = mode_idx + 3  # MODE + CHAN + RATE + SIGNAL
                    if signal_idx < len(parts):
                        signal = int(parts[signal_idx])
                    else:
                        continue
                except (ValueError, IndexError):
                    continue
                
                # 检查 SSID 是否匹配
                if ssid_pattern.match(ssid):
                    self.logger.info(f"Found matching LTE AP: {ssid} (Signal: {signal})")
                    if signal > best_signal:
                        best_signal = signal
                        best_ap = {'ssid': ssid, 'signal': signal, 'bssid': bssid}
                else:
                    self.logger.debug(f"SSID '{ssid}' does not match pattern '{ssid_prefix}*' (Signal: {signal})")
            
            # 打印所有扫描到的SSID用于调试
            if scanned_ssids:
                self.logger.info(f"Scanned SSIDs: {', '.join(scanned_ssids[:10])}" + 
                               (f" ... and {len(scanned_ssids) - 10} more" if len(scanned_ssids) > 10 else ""))
            
            # 如果没有找到匹配的 AP
            if not best_ap:
                self.logger.info(f"No LTE AP matching '{ssid_prefix}*' found")
                return False
            
            self.logger.info(f"Selected best LTE AP: {best_ap['ssid']} (Signal: {best_ap['signal']})")
            
            # 设置 LED 为配网模式
            from .hardware import LedState
            if hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                self.logger.info("LED set to WiFi config pending state")
            
            # 连接到选中的 AP
            self.logger.info(f"Connecting to {best_ap['ssid']}...")
            try:
                connect_result = subprocess.run(
                    ['nmcli', 'device', 'wifi', 'connect', best_ap['ssid'], 
                     'password', psk],
                    capture_output=True, text=True, check=True, timeout=30
                )
                
                self.logger.info(f"Successfully connected to {best_ap['ssid']}")
                
                # 连接成功，清除配网模式 LED
                if hasattr(self.supervisor, 'clear_led_state'):
                    self.supervisor.clear_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                    self.logger.info("LED config pending state cleared")
                
                return True
                
            except subprocess.TimeoutExpired:
                self.logger.error(f"Timeout connecting to {best_ap['ssid']}")
                return False
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to connect to {best_ap['ssid']}: {e.stderr}")
                return False
            
        except FileNotFoundError as e:
            self.logger.warning(f"Required command not found: {e}")
            return False
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed during LTE auto-connect: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during LTE auto-connect: {e}")
            return False

    def start_auto_wifi_provision(self):
        @util.threaded
        def task():
            self.logger.info("Checking for existing network connections...")
            try:
                # Run 'nmcli c' and capture output
                result = subprocess.run(['nmcli', 'c'], capture_output=True, text=True, check=True)
                # The output contains a header line. If there are more than 1 line, connections exist.
                lines = result.stdout.strip().split('\n')
                if len(lines) <= 1:
                    self.logger.info("No network connections found.")
                    
                    # 尝试自动连接 LTE 热点
                    if self._try_auto_connect_lte():
                        self.logger.info("Successfully auto-connected to LTE hotspot, skipping WiFi provisioning")
                        return
                    
                    # LTE 自动连接失败，启动 WiFi 配网
                    self.logger.info("LTE auto-connect failed or not configured. Starting wifi provisioning task.")
                    self.start_perform_wifi_provision()
                else:
                    self.logger.info(f"Found {len(lines) - 1} existing network connections. Skipping provisioning.")
            except FileNotFoundError:
                self.logger.warning("'nmcli' command not found. Skipping auto wifi provisioning.")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Error checking network connections: {e.stdout} {e.stderr}")
            except Exception as e:
                self.logger.error(f"An unexpected error occurred during auto wifi provisioning check: {e}")

        task()
        return None

    def start_zha_channel_switch(self, channel: int):
        """Start ZHA channel switching task"""
        #return self._start_task("zigbee", f"switch_channel_{channel}", self._run_zha_channel_switch, channel)
        return self._run_zha_channel_switch(channel)

    def start_z2m_channel_switch(self, channel: int):
        """Start Z2M channel switching task"""
        #return self._start_task("zigbee", f"switch_channel_{channel}", 
        return self._run_z2m_channel_switch(channel)

    def start_thread_channel_switch(self, channel: int):
        """Start Thread channel switching task"""
        #return self._start_task("thread", f"switch_channel_{channel}", self._run_thread_channel_switch, channel)
        return self._run_thread_channel_switch(channel)

    def start_zha_firmware_update_notification(self):
        """Start ZHA firmware update notification task"""
        return self._start_task("zigbee", "firmware_update_notify", self._run_zha_firmware_update_notification)

    def _run_zha_channel_switch(self, channel: int, progress_callback=None, complete_callback=None):
        """Run ZHA channel switching using WebSocket manager"""
        try:
            if progress_callback:
                progress_callback(10, f"Initializing ZHA channel switch to {channel}")
            
            ws_manager = WebSocketManager()
            
            if progress_callback:
                progress_callback(50, f"Switching ZHA channel to {channel}")
            
            success = ws_manager.switch_zha_channel_sync(channel)
            
            if success:
                message = f"Successfully switched ZHA channel to {channel}"
                ws_manager.delayed_zha_backup_sync()
                
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(True, message)
                return True, message
            else:
                message = f"Failed to switch ZHA channel to {channel}"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(False, message)
                return False, message
        except Exception as e:
            error_msg = f"Error during ZHA channel switch: {e}"
            self.logger.error(error_msg)
            if progress_callback:
                progress_callback(100, error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            return False, error_msg

    def _run_z2m_channel_switch(self, channel: int, progress_callback=None, complete_callback=None):
        """Run Z2M channel switching using ChannelManager"""
        try:
            if progress_callback:
                progress_callback(10, f"Initializing Z2M channel switch to {channel}")
            
            from .channel_manager import ChannelManager
            channel_manager = ChannelManager()
            
            if progress_callback:
                progress_callback(50, f"Switching Z2M channel to {channel}")
            
            success = channel_manager.switch_z2m_channel(channel)
            
            if success:
                message = f"Successfully switched Z2M channel to {channel}"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(True, message)
                return True, message
            else:
                message = f"Failed to switch Z2M channel to {channel}"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(False, message)
                return False, message
        except Exception as e:
            error_msg = f"Error during Z2M channel switch: {e}"
            self.logger.error(error_msg)
            if progress_callback:
                progress_callback(100, error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            return False, error_msg

    def _run_thread_channel_switch(self, channel: int, progress_callback=None, complete_callback=None):
        """Run Thread channel switching using WebSocket manager"""
        try:
            if progress_callback:
                progress_callback(10, f"Initializing Thread channel switch to {channel}")
            
            ws_manager = WebSocketManager()
            
            if progress_callback:
                progress_callback(50, f"Switching Thread channel to {channel}")
            
            success = ws_manager.switch_thread_channel_sync(channel)
            
            if success:
                message = f"Successfully switched Thread channel to {channel}"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(True, message)
                return True, message
            else:
                message = f"Failed to switch Thread channel to {channel}"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(False, message)
                return False, message
        except Exception as e:
            error_msg = f"Error during Thread channel switch: {e}"
            self.logger.error(error_msg)
            if progress_callback:
                progress_callback(100, error_msg)
            if complete_callback:
                complete_callback(False, error_msg)
            return False, error_msg

    def _run_zha_firmware_update_notification(self, progress_callback=None, complete_callback=None):
        """Run ZHA firmware update notification using WebSocket manager"""
        try:
            if progress_callback:
                progress_callback(10, "Initializing ZHA firmware update notification")
            
            ws_manager = WebSocketManager()
            
            if progress_callback:
                progress_callback(30, "Getting ZHA devices list")
            
            devices = ws_manager.get_zha_devices_sync()
            
            if progress_callback:
                progress_callback(50, f"Found {len(devices)} ZHA devices, sending firmware update notifications")
            
            success = ws_manager.notify_zha_devices_firmware_update_sync()
            
            if success:
                message = f"Successfully sent firmware update notifications to {len(devices)} ZHA devices"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(True, message)
            else:
                message = f"Failed to send firmware update notifications to ZHA devices"
                if progress_callback:
                    progress_callback(100, message)
                if complete_callback:
                    complete_callback(False, message)
                    
        except Exception as e:
            error_msg = f"Error during ZHA firmware update notification: {e}"
            self.logger.error(error_msg)
            if progress_callback:
                progress_callback(100, error_msg)
            if complete_callback:
                complete_callback(False, error_msg)

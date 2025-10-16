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

    def start_setting_local_backup(self):
        return self._start_task("setting", "local_backup", setting_util.run_setting_local_backup)

    def start_setting_local_restore(self, backup_file=None):
        return self._start_task("setting", "local_restore", setting_util.run_setting_local_restore, backup_file=backup_file)

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
        Try to auto-connect to LTE hotspot
        
        Returns:
            bool: True if successfully connected, False if failed or config not found
        """
        import os
        import re
        import time
        
        LTE_CONFIG_FILE = "/etc/lte_3r.conf"
        
        try:
            # Check if configuration file exists
            if not os.path.exists(LTE_CONFIG_FILE):
                self.logger.info(f"LTE config file {LTE_CONFIG_FILE} not found, skipping LTE auto-connect")
                return False
            
            # Read and parse configuration file
            self.logger.info(f"Found LTE config file, reading {LTE_CONFIG_FILE}")
            ssid_prefix = None
            psk = None
            debug_enabled = False
            debug_regex = None
            
            with open(LTE_CONFIG_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Support both SSID and SID format
                        if key == 'SSID' or key == 'SID':
                            ssid_prefix = value
                        elif key == 'PSK':
                            psk = value
                        elif key == 'DEBUG':
                            v = value.lower()
                            debug_enabled = v in ('1', 'true', 'yes', 'on')
                        elif key == 'DEBUG_REGEX':
                            debug_regex = value
            
            # Validate configuration
            if not ssid_prefix or not psk:
                self.logger.warning(f"Invalid LTE config: SSID={ssid_prefix}, PSK={'***' if psk else None}")
                return False
            
            self.logger.info(f"LTE config loaded: SSID prefix='{ssid_prefix}', PSK=***")
            
            # Execute WiFi scan
            self.logger.info("Rescanning WiFi networks...")
            try:
                subprocess.run(['nmcli', 'device', 'wifi', 'rescan'], 
                             capture_output=True, text=True, check=False)
            except Exception as e:
                self.logger.warning(f"WiFi rescan failed: {e}, continuing anyway...")
            
            # Wait 3 seconds for scan to complete
            time.sleep(3)
            
            # Get WiFi list
            self.logger.info("Getting WiFi list...")
            result = subprocess.run(['nmcli', 'device', 'wifi', 'list'], 
                                  capture_output=True, text=True, check=True)
            
            # Parse WiFi list, find matching SSID
            # SSID format: prefix + 6-digit MAC (e.g.: LTE-AABBCC)
            # MAC format: [0-9A-F]{6} or [0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}
            if debug_enabled:
                mac_pattern = debug_regex if debug_regex else r'S.*'
                self.logger.warning(f"LTE debug mode enabled. Using debug pattern: '^{re.escape(ssid_prefix)}({mac_pattern})$'")
            else:
                mac_pattern = r'[0-9A-Fa-f]{6}|[0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}'
            ssid_pattern = re.compile(rf'^{re.escape(ssid_prefix)}({mac_pattern})$')
            
            self.logger.info(f"Looking for SSID pattern: '{ssid_prefix}' + {'debug regex' if debug_enabled else '6-digit MAC'}")
            self.logger.info(f"Compiled regex pattern: ^{re.escape(ssid_prefix)}({mac_pattern})$")
            
            # Test a few examples
            test_examples = [
                (f"{ssid_prefix}S123", True) if debug_enabled else (f"{ssid_prefix}AABBCC", True),
                (f"{ssid_prefix}SOMETHING", True) if debug_enabled else (f"{ssid_prefix}AA:BB:CC", True), 
                (f"{ssid_prefix}Software", True if debug_enabled and re.compile(mac_pattern).match('Software') else False),
                (f"{ssid_prefix}VPN", True if debug_enabled and re.compile(mac_pattern).match('VPN') else False)
            ]
            self.logger.info(f"Pattern matching test examples:")
            for example, expected in test_examples:
                match_result = bool(ssid_pattern.match(example))
                self.logger.info(f"  '{example}' -> {match_result}")
            
            best_ap = None
            best_signal = -1
            scanned_ssids = []  # Collect all scanned SSIDs
            non_matching_examples = []  # Collect first few non-matching examples
            
            lines = result.stdout.strip().split('\n')
            self.logger.info(f"Found {len(lines) - 1} WiFi networks in scan results")
            
            # Print first few lines of raw data for debugging
            self.logger.info(f"First 3 lines of nmcli output:")
            for i, line in enumerate(lines[:4]):
                self.logger.info(f"  Line {i}: {line}")
            
            # Skip header line
            parsed_count = 0
            skipped_count = 0
            
            for line_num, line in enumerate(lines[1:], start=2):
                parts = line.split()
                if len(parts) < 8:
                    skipped_count += 1
                    self.logger.debug(f"Line {line_num} skipped (too few parts: {len(parts)}): {line[:80]}")
                    continue
                
                # Parse columns: IN-USE BSSID SSID MODE CHAN RATE SIGNAL BARS SECURITY
                # SSID may contain spaces, needs special handling
                # First column may be * or empty, second is BSSID, SSID starts from third
                start_idx = 1 if parts[0] == '*' else 0
                bssid = parts[start_idx]
                
                # Find MODE column (should be "Infra") to locate SSID end
                mode_idx = -1
                for i, part in enumerate(parts[start_idx + 1:], start=start_idx + 1):
                    if part == 'Infra' or part == 'Adhoc':
                        mode_idx = i
                        self.logger.debug(f"Line {line_num}: Found MODE '{part}' at index {i}, BSSID={bssid}")
                        break
                
                if mode_idx == -1:
                    skipped_count += 1
                    self.logger.debug(f"Line {line_num} skipped (no MODE found): {line[:80]}")
                    continue
                
                # SSID is all parts between BSSID and MODE
                ssid_parts = parts[start_idx + 1:mode_idx]
                if not ssid_parts or ssid_parts[0] == '--':
                    skipped_count += 1
                    self.logger.debug(f"Line {line_num}: SSID is empty or '--', skipping. BSSID={bssid}, ssid_parts={ssid_parts}")
                    continue
                
                ssid = ' '.join(ssid_parts)
                scanned_ssids.append(ssid)  # Collect SSID for debugging
                
                # Try to get signal strength (after RATE)
                # Format: MODE CHAN RATE_NUM RATE_UNIT SIGNAL
                # Example: Infra 1 405 Mbit/s 100
                try:
                    # SIGNAL is at position 4 after MODE (MODE + CHAN + RATE_NUM + RATE_UNIT + SIGNAL)
                    signal_idx = mode_idx + 4
                    if signal_idx < len(parts):
                        signal = int(parts[signal_idx])
                        self.logger.debug(f"Line {line_num}: Parsed SSID='{ssid}', signal={signal}, mode_idx={mode_idx}, signal_idx={signal_idx}")
                    else:
                        skipped_count += 1
                        self.logger.debug(f"Line {line_num}: Signal index {signal_idx} out of range (parts length={len(parts)}), SSID='{ssid}'")
                        continue
                except (ValueError, IndexError) as e:
                    skipped_count += 1
                    self.logger.debug(f"Line {line_num} signal parse error: {e}, SSID='{ssid}', trying to parse '{parts[signal_idx] if signal_idx < len(parts) else 'N/A'}'")
                    continue
                
                parsed_count += 1
                
                # Check if SSID matches the full pattern
                pattern_match = ssid_pattern.match(ssid)
                
                # Log first 10 scanned SSIDs for debugging
                if parsed_count <= 10:
                    starts_with_prefix = ssid.startswith(ssid_prefix)
                    self.logger.info(f"#{parsed_count} SSID='{ssid}', signal={signal}, starts_with_prefix={starts_with_prefix}, pattern_match={bool(pattern_match)}")
                
                if pattern_match:
                    # First match is the strongest signal (list already sorted)
                    self.logger.info(f"✓ Found matching LTE AP: {ssid} (Signal: {signal}, BSSID: {bssid})")
                    best_ap = {'ssid': ssid, 'signal': signal, 'bssid': bssid}
                    # Found first match, can exit now
                    break
                else:
                    # Collect first 5 non-matching SSIDs starting with target prefix as examples
                    if len(non_matching_examples) < 5 and ssid.startswith(ssid_prefix):
                        non_matching_examples.append(f"'{ssid}' (expected format: '{ssid_prefix}AABBCC')")
            
            self.logger.info(f"Parsed {parsed_count} APs successfully, skipped {skipped_count} entries")
            
            # Print all scanned SSIDs for debugging
            if scanned_ssids:
                self.logger.info(f"Scanned SSIDs: {', '.join(scanned_ssids[:10])}" + 
                               (f" ... and {len(scanned_ssids) - 10} more" if len(scanned_ssids) > 10 else ""))
            
            # If no matching AP found
            if not best_ap:
                self.logger.info(f"No LTE AP matching '{ssid_prefix}*' found")
                
                # Show non-matching examples to help user understand why no match
                if non_matching_examples:
                    self.logger.info(f"Found SSIDs starting with '{ssid_prefix}' but not matching MAC pattern:")
                    for example in non_matching_examples:
                        self.logger.info(f"  - {example}")
                    self.logger.info(f"Pattern requires: '{ssid_prefix}' followed by 6 hex digits (e.g., '{ssid_prefix}A1B2C3' or '{ssid_prefix}AA:BB:CC')")
                
                return False
            
            self.logger.info(f"Selected best LTE AP: {best_ap['ssid']} (Signal: {best_ap['signal']})")
            
            # Set LED to provisioning mode
            from .hardware import LedState
            if hasattr(self.supervisor, 'set_led_state'):
                self.supervisor.set_led_state(LedState.SYS_WIFI_CONFIG_PENDING)
                self.logger.info("LED set to WiFi config pending state")
            
            # Connect to selected AP
            self.logger.info(f"Connecting to {best_ap['ssid']}...")
            try:
                connect_result = subprocess.run(
                    ['nmcli', 'device', 'wifi', 'connect', best_ap['ssid'], 
                     'password', psk],
                    capture_output=True, text=True, check=True, timeout=30
                )
                
                self.logger.info(f"Successfully connected to {best_ap['ssid']}")
                
                # Connection successful, clear provisioning mode LED
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
                    
                    # Try to auto-connect to LTE hotspot
                    if self._try_auto_connect_lte():
                        self.logger.info("Successfully auto-connected to LTE hotspot, skipping WiFi provisioning")
                        return
                    
                    # LTE auto-connect failed, start WiFi provisioning
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

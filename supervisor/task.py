# maintainer: guoping.liu@3reality.com

import logging
import threading
from enum import Enum
import subprocess
from .utils import util
from .utils import zigbee_util, setting_util, thread_util


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

    def start_thread_mode_enable(self):
        return self._start_task("thread", "enable", thread_util.run_thread_enable)

    def start_thread_mode_disable(self):
        return self._start_task("thread", "disable", thread_util.run_thread_disable)

    def start_perform_wifi_provision(self):
        return self._start_task("wifi", "provision", self.supervisor.perform_wifi_provision)

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
                    self.logger.info("No network connections found. Starting wifi provisioning task.")
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

# maintainer: guoping.liu@3reality.com

import logging
import threading
from enum import Enum

from .utils import util
from .utils import zigbee_util, setting_util, thread_util

class TaskStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

class TaskManager:
    def __init__(self, supervisor=None):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self._task_lock = threading.RLock()
        self._tasks = {
            "zigbee": self._create_task_entry(),
            "thread": self._create_task_entry(),
            "setting": self._create_task_entry(),
        }

    def _create_task_entry(self, status=TaskStatus.IDLE, progress=0, message="", sub_task=""):
        return {
            "status": status.value,
            "progress": progress,
            "message": message,
            "sub_task": sub_task,
        }

    def init(self):
        self.logger.info("Initializing Task manager")

    def cleanup(self):
        self.logger.info("Cleaning up Task manager")

    def get_task_info(self, task_type):
        with self._task_lock:
            return self._tasks.get(task_type, {}).copy()

    def _update_task_status(self, task_type, status=None, progress=None, message=None, sub_task=None):
        with self._task_lock:
            if task_type in self._tasks:
                task = self._tasks[task_type]
                if status is not None:
                    task["status"] = status.value
                if progress is not None:
                    task["progress"] = progress
                if message is not None:
                    task["message"] = message
                if sub_task is not None:
                    task["sub_task"] = sub_task

    def _start_task(self, task_type, sub_task_name, target_func, *args, **kwargs):
        self.logger.info(f"[0]_start_task ...")
        with self._task_lock:
            if self._tasks[task_type]["status"] == TaskStatus.RUNNING.value:
                self.logger.warning(f"Cannot start '{sub_task_name}'. Task type '{task_type}' is already running with sub-task '{self._tasks[task_type]['sub_task']}'.")
                return False
            self._update_task_status(task_type, status=TaskStatus.RUNNING, progress=0, message="Task starting...", sub_task=sub_task_name)

        def _internal_progress(percent, message):
            self.logger.info(f"Task '{task_type}/{sub_task_name}' progress: {percent}% - {message}")
            self._update_task_status(task_type, status=TaskStatus.RUNNING, progress=percent, message=message)

        def _internal_complete(success, result):
            if success:
                self.logger.info(f"Task '{task_type}/{sub_task_name}' completed successfully: {result}")
                self._update_task_status(task_type, status=TaskStatus.SUCCESS, progress=100, message=str(result))
            else:
                self.logger.error(f"Task '{task_type}/{sub_task_name}' failed: {result}")
                self._update_task_status(task_type, status=TaskStatus.FAILED, message=str(result))

        @util.threaded
        def task_wrapper():
            try:
                self.logger.info(f"[1]_start_task ...")
                kwargs['progress_callback'] = _internal_progress
                kwargs['complete_callback'] = _internal_complete
                target_func(*args, **kwargs)
            except Exception as e:
                self.logger.exception(f"Unhandled exception in task '{task_type}/{sub_task_name}'")
                _internal_complete(False, f"An unexpected error occurred: {e}")

        self.logger.info(f"Starting task: {task_type}/{sub_task_name}")
        task_wrapper()
        return True

    # --- Zigbee Tasks ---
    def start_zigbee_switch_zha_mode(self):
        self.logger.info(f"start_zigbee_switch_zha_mode ...")
        return self._start_task("zigbee", "switch_to_zha", zigbee_util.run_zigbee_switch_zha_mode)

    def start_zigbee_switch_z2m_mode(self):
        self.logger.info(f"start_zigbee_switch_z2m_mode ...")
        return self._start_task("zigbee", "switch_to_z2m", zigbee_util.run_zigbee_switch_z2m_mode)

    def start_zigbee_pairing(self):
        # Assuming zigbee_util.run_zigbee_pairing exists and accepts callbacks
        return self._start_task("zigbee", "pairing", zigbee_util.run_zigbee_pairing)

    def start_zigbee_ota(self):
        return self._start_task("zigbee", "ota", util.run_zigbee_ota_update)

    # --- Setting Tasks ---
    def start_setting_backup(self):
        return self._start_task("setting", "backup", setting_util.run_setting_backup)

    def start_setting_restore(self):
        return self._start_task("setting", "restore", setting_util.run_setting_restore)

    # --- Thread Tasks ---
    def start_thread_mode_enable(self):
        return self._start_task("thread", "enable", thread_util.run_thread_enable)

    def start_thread_mode_disable(self):
        return self._start_task("thread", "disable", thread_util.run_thread_disable)


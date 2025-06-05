# maintainer: guoping.liu@3reality.com

import os
import threading
import time
import logging
import signal
import json
import sys
import subprocess


from .utils import util


class TaskManager:
    def __init__(self, supervisor=None):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor        

    def init(self):
        self.logger.info("Initializing Task manager")

    def cleanup(self):
        """
        Clean up Task manager resources
        """
        self.logger.info("Cleaning up Task manager")

    def start_zigbee_switch_zha_mode(self):
        def _internal_progress(percent):
            self.logger.info(f"zigbee switch to zha: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"zigbee switch to zha success: {result}")
            else:
                self.logger.info(f"zigbee switch to zha failed: {result}")

        @util.threaded
        def zigbee_switch_zha_task():
            util.run_zigbee_switch_zha_mode(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        zigbee_switch_zha_task()

    def start_zigbee_switch_z2m_mode(self):
        def _internal_progress(percent):
            self.logger.info(f"zigbee switch to z2m: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"zigbee switch to z2m success: {result}")
            else:
                self.logger.info(f"zigbee switch to z2m failed: {result}")

        @util.threaded
        def zigbee_switch_z2m_task():
            util.run_zigbee_switch_z2m_mode(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        zigbee_switch_z2m_task()

    def start_zigbee_pairing(self):
        def _internal_progress(percent):
            self.logger.info(f"zigbee paring: percent={percent}")

        def _internal_complete(success, result):
            if success:
                self.ogger.info(f"zigbee paring success: {result}")
            else:
                self.logger.info(f"zigbee paring failed: {result}")

        @util.threaded
        def pairing_task():
            mode = util.get_ha_zigbee_mode()
            if mode == "zha":
                util.run_zha_pairing(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
            elif mode == "mqtt":
                util.run_mqtt_pairing(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
            else:
                self.logger.warning("未检测到Zigbee配对模式 (zha/mqtt)")
        pairing_task()

    def start_zigbee_ota(self):
        def _internal_progress(percent):
            self.logger.info(f"zigbee ota update: percent={percent}")

        def _internal_complete(success, result):
            if success:
                self.logger.info(f"zigbee ota update success: {result}")
            else:
                self.logger.info(f"zigbee ota update failed: {result}")

        @util.threaded
        def zigbee_ota_task():
            util.run_zigbee_ota_update(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        zigbee_ota_task()

    def start_thread_mode_enable(self):
        def _internal_progress(percent):
            self.logger.info(f"enable thread support: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"enable thread support success: {result}")
            else:
                self.logger.info(f"enable thread support failed: {result}")

        @util.threaded
        def thread_enable_task():
            util.run_thread_enable_mode(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        thread_enable_task()


    def start_thread_mode_disable(self):
        def _internal_progress(percent):
            self.logger.info(f"disable thread support: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"disable thread support success: {result}")
            else:
                self.logger.info(f"disable thread support failed: {result}")

        @util.threaded
        def thread_disable_task():
            util.run_thread_disable_mode(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        thread_disable_task()        


    def start_setting_backup(self):
        def _internal_progress(percent):
            self.logger.info(f"system setting backup: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"system setting backup success: {result}")
            else:
                self.logger.info(f"system setting backup failed: {result}")

        @util.threaded
        def system_setting_backup_task():
            util.run_system_setting_backup(
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        system_setting_backup_task()


    def start_setting_restore(self, restore_file=None):
        def _internal_progress(percent):
            self.logger.info(f"system setting restore: percent={percent}")
            
        def _internal_complete(success, result):
            if success:
                self.logger.info(f"system setting restore success: {result}")
            else:
                self.logger.info(f"system setting restore failed: {result}")

        @util.threaded
        def system_setting_backup_task():
            util.run_system_setting_restore(
                backup_file = restore_file,
                progress_callback=_internal_progress,
                complete_callback=_internal_complete)
        system_setting_backup_task()



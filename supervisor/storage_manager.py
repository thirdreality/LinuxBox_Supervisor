#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import threading
import time
import subprocess
import glob
from pathlib import Path


logger = logging.getLogger("Supervisor")


class StorageManager:
    """
    Internal Storage Space Management Service
    
    Features:
    - Check disk space every hour
    - Clean log files when /dev/mmc* disk usage exceeds 95%
    - Clean Home Assistant and Zigbee2MQTT logs
    """
    
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor.StorageManager")
        self.running = False
        self.check_thread = None
        
        # Check interval (seconds), default 1 hour
        self.check_interval = 3600
        
        # Disk usage threshold (percentage)
        self.usage_threshold = 95
        
        # Home Assistant log path
        self.ha_log_dir = "/var/lib/homeassistant/homeassistant"
        self.ha_log_files = [
            "home-assistant.log",
            "home-assistant.log.1"
        ]
        
        # Zigbee2MQTT log path
        self.z2m_log_dir = "/opt/zigbee2mqtt/data/log"
        
    def start(self):
        """Start storage space management service"""
        if self.running:
            self.logger.info("Storage manager is already running")
            return
            
        self.running = True
        self.check_thread = threading.Thread(
            target=self._monitor_loop, 
            daemon=True,
            name="StorageMonitor"
        )
        self.check_thread.start()
        self.logger.info("Storage manager started")
        
    def stop(self):
        """Stop storage space management service"""
        self.running = False
        if self.check_thread:
            self.check_thread.join(timeout=2)
        self.logger.info("Storage manager stopped")
        
    def _monitor_loop(self):
        """Monitor loop: check disk space every hour"""
        while self.running:
            try:
                self.logger.info("Starting disk space check...")
                self._check_and_cleanup()
            except Exception as e:
                self.logger.error(f"Disk space check error: {e}")
            
            # Wait for next check (every hour)
            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)
    
    def _check_and_cleanup(self):
        """Check disk usage and clean logs if needed"""
        # Get usage info for all /dev/mmc* devices
        mmc_devices = self._get_mmc_devices()
        
        if not mmc_devices:
            self.logger.debug("No /dev/mmc* devices found")
            return
        
        for device, usage_percent in mmc_devices.items():
            self.logger.info(f"Device {device} usage: {usage_percent}%")
            
            if usage_percent >= self.usage_threshold:
                self.logger.warning(
                    f"Device {device} usage {usage_percent}% exceeds threshold {self.usage_threshold}%, starting log cleanup..."
                )
                self._cleanup_logs()
            else:
                self.logger.info(
                    f"Device {device} usage is normal ({usage_percent}% < {self.usage_threshold}%)"
                )
    
    def _get_mmc_devices(self):
        """Get usage percentage for all /dev/mmc* devices"""
        devices = {}
        
        try:
            # Use df command to get all mount point information
            result = subprocess.run(
                ["df", "-h"],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse df output
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip header line
                parts = line.split()
                if len(parts) >= 6:
                    device_name = parts[0]
                    # Check if it's a /dev/mmc* device
                    if device_name.startswith('/dev/mmc'):
                        try:
                            # Extract usage percentage (remove % symbol)
                            usage_str = parts[4].rstrip('%')
                            usage_percent = int(usage_str)
                            devices[device_name] = usage_percent
                        except (ValueError, IndexError) as e:
                            self.logger.warning(f"Failed to parse device {device_name} usage: {e}")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to execute df command: {e}")
        except Exception as e:
            self.logger.error(f"Error getting mmc device information: {e}")
        
        return devices
    
    def _cleanup_logs(self):
        """Clean log files"""
        cleaned_files = []
        
        # 1. Clean Home Assistant logs
        cleaned_files.extend(self._cleanup_ha_logs())
        
        # 2. Clean Zigbee2MQTT logs
        cleaned_files.extend(self._cleanup_z2m_logs())
        
        if cleaned_files:
            self.logger.info(f"Cleaned {len(cleaned_files)} log files:")
            for file_path in cleaned_files:
                self.logger.info(f"  - {file_path}")
            
            # Force sync to ensure data is written to NAND flash
            try:
                subprocess.run(["sync"], check=True, timeout=10)
                self.logger.info("File system sync completed")
            except subprocess.TimeoutExpired:
                self.logger.warning("Sync command timed out")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to execute sync command: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected error during sync: {e}")
        else:
            self.logger.info("No log files found to clean")
    
    def _cleanup_ha_logs(self):
        """Clean Home Assistant log files"""
        cleaned = []
        
        # Check if directory exists
        if not os.path.exists(self.ha_log_dir):
            self.logger.debug(f"Home Assistant log directory does not exist: {self.ha_log_dir}")
            return cleaned
        
        # Clean each log file
        for log_file in self.ha_log_files:
            log_path = os.path.join(self.ha_log_dir, log_file)
            
            # Check if file exists
            if not os.path.exists(log_path):
                self.logger.debug(f"File does not exist: {log_path}")
                continue
            
            try:
                # Truncate file (keep file, only clear content)
                with open(log_path, 'w') as f:
                    f.truncate(0)
                
                self.logger.info(f"Cleared file: {log_path}")
                cleaned.append(log_path)
            except Exception as e:
                self.logger.error(f"Failed to clear file {log_path}: {e}")
        
        return cleaned
    
    def _cleanup_z2m_logs(self):
        """Clean Zigbee2MQTT log files"""
        cleaned = []
        
        # Check if directory exists
        if not os.path.exists(self.z2m_log_dir):
            self.logger.debug(f"Zigbee2MQTT log directory does not exist: {self.z2m_log_dir}")
            return cleaned
        
        try:
            # Get all timestamp directories (e.g.: 2025-10-13.10-34-23)
            log_dirs = [
                d for d in os.listdir(self.z2m_log_dir)
                if os.path.isdir(os.path.join(self.z2m_log_dir, d))
            ]
            
            if not log_dirs:
                self.logger.debug(f"No subdirectories found in Zigbee2MQTT log directory: {self.z2m_log_dir}")
                return cleaned
            
            # Iterate through each timestamp directory
            for log_dir in log_dirs:
                log_dir_path = os.path.join(self.z2m_log_dir, log_dir)
                log_file_path = os.path.join(log_dir_path, "log.log")
                
                # Check if log.log file exists
                if not os.path.exists(log_file_path):
                    self.logger.debug(f"log.log file does not exist: {log_file_path}")
                    continue
                
                try:
                    # Clear file content
                    with open(log_file_path, 'w') as f:
                        f.truncate(0)
                    
                    self.logger.info(f"Cleared file: {log_file_path}")
                    cleaned.append(log_file_path)
                except Exception as e:
                    self.logger.error(f"Failed to clear file {log_file_path}: {e}")
            
        except Exception as e:
            self.logger.error(f"Error processing Zigbee2MQTT log directory: {e}")
        
        return cleaned
    
    def manual_cleanup(self):
        """Manually trigger cleanup (for testing or emergency cleanup)"""
        self.logger.info("Manually triggered log cleanup...")
        self._cleanup_logs()
        
    def manual_check(self):
        """Manually trigger disk check (for testing)"""
        self.logger.info("Manually triggered disk space check...")
        self._check_and_cleanup()


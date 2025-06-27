# maintainer: guoping.liu@3reality.com

import os
import logging
import threading
import subprocess
import time
import re
from .const import DEVICE_MODEL_NAME, DEVICE_BUILD_NUMBER
from .hardware import LedState

T3R_RELEASE_FILE = "/etc/t3r-release"

def _get_t3r_release_info():
    """Parses the /etc/t3r-release file and returns a dictionary."""
    release_info = {}
    if not os.path.exists(T3R_RELEASE_FILE):
        logging.warning(f"Release file not found: {T3R_RELEASE_FILE}")
        return release_info
    
    try:
        with open(T3R_RELEASE_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                try:
                    key, value = line.split('=', 1)
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    release_info[key.strip()] = value.strip()
                except ValueError:
                    logging.warning(f"Skipping malformed line in {T3R_RELEASE_FILE}: {line}")
    except IOError as e:
        logging.error(f"Error reading {T3R_RELEASE_FILE}: {e}")
        
    return release_info

class HomeAssistantInfo:
    def __init__(self):
        self.installed = False
        self.enabled = False
        # thirdreality-hacore-config
        self.config = ""
        # thirdreality-python3
        self.python = ""
        # thirdreality-hacore
        self.core = ""
        # thirdreality-otbr-agent
        self.otbr = ""
        # thirdreality-zigbee-mqtt
        self.z2m = ""


class OpenHabInfo:
    def __init__(self):
        self.installed = False
        self.enabled = False
        self.version = ""

class SystemInfo:
    def __init__(self):
        release_info = _get_t3r_release_info()

        self.model = release_info.get("PRETTY_NAME", DEVICE_MODEL_NAME)
        self.build_number = DEVICE_BUILD_NUMBER
        self.version = release_info.get("VERSION", "v0.0.1")
        self.name = "3RHUB-XXXX"
        self.support_zigbee=True
        self.support_thread=True  # Fixed to always support thread        
        self.mode = "homeassistant-core"
        self.memory_size = ""  # 设备内存大小，单位为MB
        self.storage_space = ""  # 存储空间大小，单位为GB
        self.hainfo = HomeAssistantInfo()
        self.openhabinfo = OpenHabInfo()

class ProcedureInfo:
    def __init__(self):
        self.tag = ""
        self.finished = False
        self.success = True
        self.percent= 0


def get_package_version(package_name):
    """查询指定包的版本号"""
    try:
        # 使用dpkg-query命令查询包版本
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package_name],
            capture_output=True,
            text=True,
            check=False
        )
            
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return ""
    except Exception as e:
        return ""

def get_memory_size():
    """获取设备内存大小，单位为MB"""
    try:
        # 使用/proc/meminfo获取内存信息
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
        
        # 使用正则表达式提取MemTotal值
        match = re.search(r'MemTotal:\s+(\d+)\s+kB', meminfo)
        if match:
            # 将kB转换为MB并返回
            mem_kb = int(match.group(1))
            mem_mb = mem_kb // 1024
            return str(mem_mb)
        else:
            return ""
    except Exception as e:
        logging.error(f"Error getting memory size: {e}")
        return ""

def get_storage_space():
    """获取存储空间大小，返回总空间(GB)和可用空间(GB)"""
    try:
        # 使用df命令获取根分区信息
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            # 解析输出，跳过标题行
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                # 分割行并获取总大小和可用大小
                parts = lines[1].split()
                if len(parts) >= 4:
                    total_size = parts[1]  # 例如：7.8G
                    avail_size = parts[3]  # 例如：3.2G
                    return {"total": total_size, "available": avail_size}
        
        return {"total": "", "available": ""}
    except Exception as e:
        logging.error(f"Error getting storage space: {e}")
        return {"total": "", "available": ""}

class SystemInfoUpdater:
    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.sys_info_thread = None
        
        # Initialize device name immediately
        self._initialize_device_name()
    
    def _initialize_device_name(self):
        """Initialize device name with retry mechanism"""
        if not hasattr(self.supervisor, 'system_info'):
            self.logger.error("Supervisor does not have system_info attribute")
            return
            
        device_name = self._generate_device_name_with_retry()
        self.supervisor.system_info.name = device_name
        self.logger.info(f"Device name initialized: {device_name}")
    
    def _generate_device_name_with_retry(self, max_retries=3, retry_delay=0.5):
        """Generate device name with retry mechanism for MAC address retrieval"""
        from .utils.wifi_utils import get_wlan0_mac_for_localname
        import time
        
        for attempt in range(max_retries):
            try:
                mac_str = get_wlan0_mac_for_localname()
                if mac_str:
                    # Use same algorithm as btgatt-server.c and LinuxBoxAdvertisement
                    device_name = f"3RHUB-{mac_str[-8:]}"  # Use only last 8 characters of MAC address
                    self.logger.info(f"Generated device name from MAC (attempt {attempt + 1}): {device_name}")
                    return device_name
                else:
                    self.logger.warning(f"Failed to get MAC address (attempt {attempt + 1}/{max_retries})")
                    
            except Exception as e:
                self.logger.warning(f"Error getting MAC address (attempt {attempt + 1}/{max_retries}): {e}")
            
            # Wait before retry (except for last attempt)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        
        # Fallback: try to read /etc/machine-id if all MAC retrieval attempts failed
        try:
            with open('/etc/machine-id', 'r') as f:
                machine_id = f.read().strip()
                if machine_id and len(machine_id) >= 8:
                    # Use last 8 characters and convert to uppercase
                    machine_suffix = machine_id[-8:].upper()
                    fallback_name = f"3RHUB-{machine_suffix}"
                    self.logger.info(f"Using machine-id based fallback name: {fallback_name}")
                    return fallback_name
        except Exception as e:
            self.logger.warning(f"Failed to read /etc/machine-id: {e}")
        
        # Final fallback if machine-id is also unavailable
        final_fallback = "3RHUB-EMB"
        self.logger.warning(f"All attempts failed, using final fallback: {final_fallback}")
        return final_fallback
    
    def system_info_update_task(self):
        self.logger.info("Starting updating system information ...")
        
        try:
            if not hasattr(self.supervisor, 'system_info'):
                self.logger.error("Supervisor does not have system_info attribute")
                return
                
            # Get SystemInfo object
            sys_info = self.supervisor.system_info
            
            # Update device name if it's still the default
            if sys_info.name in ["3RHUB-XXXX", "3RHUB-EMB"] or not sys_info.name:
                device_name = self._generate_device_name_with_retry()
                sys_info.name = device_name
                self.logger.info(f"Updated device name: {device_name}")
            
            # Ensure HomeAssistantInfo object exists
            if not hasattr(sys_info, 'hainfo'):
                sys_info.hainfo = HomeAssistantInfo()
                
            # Get HomeAssistantInfo object
            ha_info = sys_info.hainfo
            
            # Query thirdreality-hacore-config package version
            ha_info.config = get_package_version("thirdreality-hacore-config")
            self.logger.info(f"thirdreality-hacore-config version: {ha_info.config}")
            
            # Query thirdreality-python3 package version
            ha_info.python = get_package_version("thirdreality-python3")
            self.logger.info(f"thirdreality-python3 version: {ha_info.python}")
            
            # Query thirdreality-hacore package version
            ha_info.core = get_package_version("thirdreality-hacore")
            self.logger.info(f"thirdreality-hacore version: {ha_info.core}")
            
            # Query thirdreality-otbr-agent package version
            ha_info.otbr = get_package_version("thirdreality-otbr-agent")
            self.logger.info(f"thirdreality-otbr-agent version: {ha_info.otbr}")

            # Query thirdreality-zigbee-mqtt package version
            ha_info.z2m = get_package_version("thirdreality-zigbee-mqtt")
            self.logger.info(f"thirdreality-zigbee-mqtt version: {ha_info.z2m}")

            # Set installed status
            ha_info.installed = bool(ha_info.core and ha_info.python)

            ha_info.enabled = ha_info.installed
            
            # 设置LED
            if hasattr(self.supervisor, 'set_led_state'):
                if not ha_info.installed:
                    self.logger.info("Software not fully installed, set LED SYS_SYSTEM_CORRUPTED")
                    self.supervisor.set_led_state(LedState.SYS_SYSTEM_CORRUPTED)

            # Get device memory size
            sys_info.memory_size = get_memory_size()

            # Thread support is always enabled
            sys_info.support_thread = True
            # # Check otbr-agent service status
            # from supervisor.utils import util
            # if util.is_service_running("otbr-agent.service"):
            #     sys_info.support_thread = True
            # else:
            #     sys_info.support_thread = False
            self.logger.info(f"Thread support: {sys_info.support_thread} (fixed to always true)")
            self.logger.info(f"Device memory size: {sys_info.memory_size} MB")
            
            # Get storage space information
            storage_info = get_storage_space()
            sys_info.storage_space = storage_info
            self.logger.info(f"Storage space - Total: {storage_info['total']}, Available: {storage_info['available']}")
            
            self.logger.info("System information update completed")
            
            # After system info update is complete, check if auto WiFi provision is needed
            self._check_auto_wifi_provision_needed()
            
        except Exception as e:
            self.logger.error(f"Error updating system information: {e}")
        
        # Task completed, thread will exit
    
    def _check_auto_wifi_provision_needed(self):
        """Check if auto WiFi provision is needed after system startup is complete"""
        try:
            if not self.supervisor:
                return
                
            self.logger.info("System startup complete, checking if auto WiFi provision is needed...")
            
            # Trigger auto WiFi provision check
            self.supervisor.on_system_ready_check_wifi_provision()
            
        except Exception as e:
            self.logger.error(f"Error checking auto WiFi provision: {e}")
    
    def start(self):
        """Start thread to execute system information update task"""
        if self.sys_info_thread and self.sys_info_thread.is_alive():
            self.logger.info("System information update already running")
            return
            
        self.sys_info_thread = threading.Thread(target=self.system_info_update_task, daemon=True)
        self.sys_info_thread.start()
        self.logger.info("System information update started")
        
    def stop(self):
        """Use supervisor.running to close, here for show"""
        self.logger.info("System Information stopped")

    def update_software_status_and_led(self):
        """
        更新HomeAssistant和OpenHAB的installed/enabled状态，并根据结果设置LED状态。
        只依赖core和python字段。
        """
        if not hasattr(self.supervisor, 'system_info'):
            self.logger.error("Supervisor does not have system_info attribute")
            return
        sys_info = self.supervisor.system_info
        # HomeAssistant
        ha_info = getattr(sys_info, 'hainfo', None)
        if ha_info:           
            # Query thirdreality-hacore-config package version
            ha_info.config = get_package_version("thirdreality-hacore-config")
            self.logger.info(f"thirdreality-hacore-config version: {ha_info.config}")
            
            # Query thirdreality-python3 package version
            ha_info.python = get_package_version("thirdreality-python3")
            self.logger.info(f"thirdreality-python3 version: {ha_info.python}")
            
            # Query thirdreality-hacore package version
            ha_info.core = get_package_version("thirdreality-hacore")
            self.logger.info(f"thirdreality-hacore version: {ha_info.core}")
            
            # Query thirdreality-otbr-agent package version
            ha_info.otbr = get_package_version("thirdreality-otbr-agent")
            self.logger.info(f"thirdreality-otbr-agent version: {ha_info.otbr}")

            # Query thirdreality-zigbee-mqtt package version
            ha_info.z2m = get_package_version("thirdreality-zigbee-mqtt")
            self.logger.info(f"thirdreality-zigbee-mqtt version: {ha_info.z2m}")

            ha_info.installed = bool(ha_info.core and ha_info.python)
            ha_info.enabled = ha_info.installed

        # OpenHAB
        openhab_info = getattr(sys_info, 'openhabinfo', None)
        if openhab_info:
            openhab_info.installed = bool(openhab_info.version)
            openhab_info.enabled = openhab_info.installed



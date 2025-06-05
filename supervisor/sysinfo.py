# maintainer: guoping.liu@thirdreality.com

import logging
import threading
import subprocess
import time
import os
import re

from .const import DEVICE_MODEL_NAME,DEVICE_BUILD_NUMBER

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
        self.model = DEVICE_MODEL_NAME
        self.build_number = DEVICE_BUILD_NUMBER
        self.name = "3RHUB-XXXX"
        self.support_zigbee=True
        self.support_thread=False        
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
    
    def system_info_update_task(self):
        self.logger.info("Starting updating system information ...")
        
        try:
            if not hasattr(self.supervisor, 'system_info'):
                self.logger.error("Supervisor does not have system_info attribute")
                return
                
            # 获取SystemInfo对象
            sys_info = self.supervisor.system_info
            
            # 确保HomeAssistantInfo对象存在
            if not hasattr(sys_info, 'hainfo'):
                sys_info.hainfo = HomeAssistantInfo()
                
            # 获取HomeAssistantInfo对象
            ha_info = sys_info.hainfo
            
            # 查询thirdreality-hacore-config包版本
            ha_info.config = get_package_version("thirdreality-hacore-config")
            self.logger.info(f"thirdreality-hacore-config version: {ha_info.config}")
            
            # 查询thirdreality-python3包版本
            ha_info.python = get_package_version("thirdreality-python3")
            self.logger.info(f"thirdreality-python3 version: {ha_info.python}")
            
            # 查询thirdreality-hacore包版本
            ha_info.core = get_package_version("thirdreality-hacore")
            self.logger.info(f"thirdreality-hacore version: {ha_info.core}")
            
            # 查询thirdreality-otbr-agent包版本
            ha_info.otbr = get_package_version("thirdreality-otbr-agent")
            self.logger.info(f"thirdreality-otbr-agent version: {ha_info.otbr}")

            # 查询thirdreality-zigbee-mqtt包版本
            ha_info.z2m = get_package_version("thirdreality-zigbee-mqtt")
            self.logger.info(f"thirdreality-zigbee-mqtt version: {ha_info.z2m}")

            # 设置installed状态
            ha_info.installed = bool(ha_info.core and ha_info.python and ha_info.config)

            ha_info.enabled = ha_info.installed
            
            # 获取设备内存大小
            sys_info.memory_size = get_memory_size()
            self.logger.info(f"Device memory size: {sys_info.memory_size} MB")
            
            # 获取存储空间信息
            storage_info = get_storage_space()
            sys_info.storage_space = storage_info
            self.logger.info(f"Storage space - Total: {storage_info['total']}, Available: {storage_info['available']}")
            
            self.logger.info("System information update completed")
        except Exception as e:
            self.logger.error(f"Error updating system information: {e}")
        
        # 任务完成，线程将退出
    
    
    def start(self):
        """启动线程执行一次系统信息更新任务"""
        if self.sys_info_thread and self.sys_info_thread.is_alive():
            self.logger.info("System information update already running")
            return
            
        self.sys_info_thread = threading.Thread(target=self.system_info_update_task, daemon=True)
        self.sys_info_thread.start()
        self.logger.info("System information update started")
        
    def stop(self):
        """使用supervisor.running关闭，这里做做样子"""
        self.logger.info("System Information stopped")

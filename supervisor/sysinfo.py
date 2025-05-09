# maintainer: guoping.liu@thirdreality.com

import logging
import threading
import subprocess
import time

from .const import DEVICE_MODEL_NAME,DEVICE_CURRENT_VERSION,DEVICE_BUILD_NUMBER

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

class Zigbee2mqttInfo:
    def __init__(self):
        self.installed = False
        self.enabled = False
        self.zigbee2mqtt = ""

class homekitbridgeInfo:
    def __init__(self):
        self.installed = False
        self.enabled = False
        self.version = ""

class SystemInfo:
    def __init__(self):
        self.model = DEVICE_MODEL_NAME
        self.version = DEVICE_CURRENT_VERSION
        self.build_number = DEVICE_BUILD_NUMBER
        self.name = "3RHUB-XXXX"
        self.support_zigbee=True
        self.support_thread=False        
        self.mode = "homeassistant-core"
        self.hainfo = HomeAssistantInfo()
        self.z2minfo = Zigbee2mqttInfo()
        self.hbinfo = homekitbridgeInfo()

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
            ha_info.config = self._get_package_version("thirdreality-hacore-config")
            self.logger.info(f"thirdreality-hacore-config version: {ha_info.config}")
            
            # 查询thirdreality-python3包版本
            ha_info.python = self._get_package_version("thirdreality-python3")
            self.logger.info(f"thirdreality-python3 version: {ha_info.python}")
            
            # 查询thirdreality-hacore包版本
            ha_info.core = self._get_package_version("thirdreality-hacore")
            self.logger.info(f"thirdreality-hacore version: {ha_info.core}")
            
            # 查询thirdreality-otbr-agent包版本
            ha_info.otbr = self._get_package_version("thirdreality-otbr-agent")
            self.logger.info(f"thirdreality-otbr-agent version: {ha_info.otbr}")
            
            # 设置installed状态
            ha_info.installed = bool(ha_info.core and ha_info.python and ha_info.config)

            ha_info.enabled = ha_info.installed
            
            self.logger.info("System information update completed")
        except Exception as e:
            self.logger.error(f"Error updating system information: {e}")
        
        # 任务完成，线程将退出
    
    def _get_package_version(self, package_name):
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
                self.logger.warning(f"Package {package_name} not found")
                return ""
        except Exception as e:
            self.logger.error(f"Error getting version for {package_name}: {e}")
            return ""

    
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

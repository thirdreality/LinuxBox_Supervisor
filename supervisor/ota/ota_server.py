# maintainer: guoping.liu@3reality.com

import os
import threading
import time
import logging
import json
import tempfile
import subprocess
import shutil
import urllib.request
import urllib.error

class SupervisorOTAServer:
    """OTA Server that integrates with Supervisor for shared state"""
    
    def __init__(self, supervisor):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self.server = None
        self.server_thread = None
        self.version_url = "https://raw.githubusercontent.com/thirdreality/LinuxBox-Installer/refs/heads/main/version.json"
        self.release_base_url = "https://github.com/thirdreality/LinuxBox-Installer/releases/download"
        self.check_interval = 3600  # 检查更新的间隔，默认1小时

    def start(self):
        """启动线程， 并且维护OTA状态"""
        self.server_thread = threading.Thread(target=self.ota_update_task, daemon=True)
        self.server_thread.start()
        self.logger.info("OTA server started")
        
    def stop(self):
        """使用supervisor.running关闭，这里做做样子"""
        self.logger.info("OTA server stopped")
    
    def ota_update_task(self):
        """OTA更新监控任务"""
        self.logger.info("Starting OTA update monitor...")
        
        while self.supervisor and hasattr(self.supervisor, 'running') and self.supervisor.running.is_set():
            try:
                self._check_and_install_updates()
            except Exception as e:
                self.logger.error(f"Error in OTA update task: {e}")
            
            # 等待下一次检查
            time.sleep(self.check_interval)
    
    def _check_and_install_updates(self):
        """检查并安装更新"""
        # 创建临时目录
        try:
            temp_dir = tempfile.mkdtemp(prefix="ota_update_")
            self.logger.info(f"Created temporary directory: {temp_dir}")
        except Exception as e:
            self.logger.error(f"Failed to create temporary directory: {e}")
            return
        
        try:
            # 下载版本信息文件
            version_file = os.path.join(temp_dir, "version.json")
            try:
                self.logger.info(f"Downloading version info from {self.version_url}")
                urllib.request.urlretrieve(self.version_url, version_file)
            except urllib.error.URLError as e:
                self.logger.error(f"Failed to download version info: {e}")
                return
            
            # 解析版本信息
            try:
                with open(version_file, 'r') as f:
                    version_info = json.load(f)
                self.logger.info(f"Version info: {version_info}")
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse version info: {e}")
                return
            
            # 处理 homeassistant 部分
            if "homeassistant" in version_info:
                ha_info = version_info["homeassistant"]
                
                # 安装顺序：hacore-config -> python3 -> hacore -> otbr-agent
                components = [
                    "hacore-config",
                    "python3",
                    "hacore",
                    "otbr-agent"
                ]
                
                for component in components:
                    if component in ha_info:
                        comp_info = ha_info[component]
                        version = comp_info.get("version")
                        release = comp_info.get("release")
                        
                        if version and release:
                            # 构建下载URL
                            if component == "otbr-agent":
                                # otbr-agent 使用特殊格式
                                download_url = f"{self.release_base_url}/{release}/{component}_{version}.deb"
                            else:
                                download_url = f"{self.release_base_url}/{release}/{component}_{version}.deb"
                            
                            # 下载并安装
                            self._download_and_install(download_url, temp_dir, component)
                            
            
            # 处理 zigbee2mqtt 部分
            if "zigbee2mqtt" in version_info:
                z2m_info = version_info["zigbee2mqtt"]
                
                # 检查是否有版本和发布信息
                version = z2m_info.get("version")
                release = z2m_info.get("release")
                
                if version and release:
                    # 构建下载URL
                    download_url = f"{self.release_base_url}/{release}/zigbee2mqtt_{version}.deb"
                    
                    # 更新OTA状态
                    if hasattr(self.supervisor, 'ota_status'):
                        self.supervisor.ota_status.status = "Updating zigbee2mqtt"
                        self.supervisor.ota_status.progress = 50
                    
                    # 下载并安装
                    success = self._download_and_install(download_url, temp_dir, "zigbee2mqtt")
                    
                    # 更新OTA状态
                    if hasattr(self.supervisor, 'ota_status'):
                        if success:
                            self.supervisor.ota_status.status = "zigbee2mqtt updated"
                        else:
                            self.supervisor.ota_status.status = "zigbee2mqtt update failed"
                        self.supervisor.ota_status.progress = 100
            
            # 处理 HomeKitBridge 部分
            if "homekitbridge" in version_info:
                hkb_info = version_info["homekitbridge"]
                
                # 检查是否有版本和发布信息
                version = hkb_info.get("version")
                release = hkb_info.get("release")
                
                if version and release:
                    # 构建下载URL
                    download_url = f"{self.release_base_url}/{release}/homekitbridge_{version}.deb"
                    
                    # 更新OTA状态
                    if hasattr(self.supervisor, 'ota_status'):
                        self.supervisor.ota_status.status = "Updating HomeKitBridge"
                        self.supervisor.ota_status.progress = 50
                    
                    # 下载并安装
                    success = self._download_and_install(download_url, temp_dir, "homekitbridge")
                    
                    # 更新OTA状态
                    if hasattr(self.supervisor, 'ota_status'):
                        if success:
                            self.supervisor.ota_status.status = "HomeKitBridge updated"
                        else:
                            self.supervisor.ota_status.status = "HomeKitBridge update failed"
                        self.supervisor.ota_status.progress = 100
        
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir)
                self.logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                self.logger.error(f"Failed to clean up temporary directory: {e}")
    
    def _download_and_install(self, url, temp_dir, component):
        """下载并安装组件"""
        # 构建本地文件路径
        local_file = os.path.join(temp_dir, f"{component}.deb")
        
        try:
            # 下载文件
            self.logger.info(f"Downloading {component} from {url}")
            urllib.request.urlretrieve(url, local_file)
            
            # 安装文件
            self.logger.info(f"Installing {component}")
            result = subprocess.run(["dpkg", "-i", local_file], capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info(f"Successfully installed {component}")
                return True
            else:
                self.logger.error(f"Failed to install {component}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error installing {component}: {e}")
            return False
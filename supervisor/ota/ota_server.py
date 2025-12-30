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
import socket
from .. import const

class SupervisorOTAServer:
    """OTA Server that integrates with Supervisor for shared state"""
    
    def __init__(self, supervisor):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self.server = None
        self.server_thread = None
        self.version_url = const.VERSION_URL  # Use constant from const.py
        self.release_base_url = const.DOWNLOAD_BASE_URL  # Use constant from const.py
        self.check_interval = 3600  # 检查更新的间隔，默认1小时
        # component -> debian package name mapping
        self.component_to_package = {
            "python3": "thirdreality-python3",
            "hacore": "thirdreality-hacore",
            "hacore-config": "thirdreality-hacore-config",
            "otbr-agent": "thirdreality-otbr-agent",
            "zigbee-mqtt": "thirdreality-zigbee-mqtt",
        }

    def _safe_rmtree(self, path):
        try:
            if path and os.path.isdir(path):
                shutil.rmtree(path)
        except Exception as e:
            self.logger.warning(f"[ota] Failed to remove cache dir {path}: {e}")

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
        
        time.sleep(30)
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
            # 下载版本信息文件（增加超时与耗时日志）
            version_file = os.path.join(temp_dir, "version.json")
            try:
                self.logger.info(f"[ota] Downloading version info from {self.version_url}")
                start_ts = time.time()
                with urllib.request.urlopen(self.version_url, timeout=15) as resp:
                    with open(version_file, 'wb') as f:
                        f.write(resp.read())
                cost = round(time.time() - start_ts, 2)
                if cost > 5:
                    self.logger.warning(f"[ota] Download version.json took {cost}s (slow)")
                else:
                    self.logger.info(f"[ota] Download version.json ok, cost {cost}s")
            except urllib.error.URLError as e:
                self.logger.error(f"[ota] Failed to download version info: {e}")
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
                
                # 安装顺序：python3 -> hacore -> otbr-agent -> zigbee-mqtt
                components = [
                    "python3",
                    "hacore",
                    "otbr-agent",
                    "zigbee-mqtt"
                ]
                
                for component in components:
                    if component in ha_info:
                        comp_info = ha_info[component]
                        version = comp_info.get("version")
                        release = comp_info.get("release")
                        
                        if version and release:
                            # 映射到实际包名
                            package_name = self.component_to_package.get(component, component)
                            # 下载并安装（带版本比较），使用双 URL 检测
                            # filename 使用 component 名称
                            self._download_and_install(component, release, version, package_name)
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir)
                self.logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                self.logger.error(f"Failed to clean up temporary directory: {e}")
    
    def get_installed_version(self, pkg_name):
        try:
            res = subprocess.run(["dpkg-query", "-W", "-f=${Version}", pkg_name], capture_output=True, text=True)
            if res.returncode == 0:
                ver = (res.stdout or "").strip()
                return ver if ver else None
            return None
        except Exception:
            return None

    def is_installed_version_less(self, installed, target):
        # 使用 dpkg --compare-versions 做可靠比较
        try:
            cmp_res = subprocess.run(["dpkg", "--compare-versions", installed, "lt", target])
            return cmp_res.returncode == 0
        except Exception:
            # 无法比较时，默认为需要升级
            return True

    def _download_and_install(self, component, release, version, package_name):
        """下载并安装组件（含版本检查、缓存到/var/cache、postinst处理）
        
        Args:
            component: Component name (used as filename)
            release: Release tag
            version: Version number
            package_name: Debian package name
        """

        # 1) 版本检查
        installed_version = self.get_installed_version(package_name)
        if installed_version:
            if self.is_installed_version_less(installed_version, version):
                self.logger.info(f"[ota] {package_name}: installed {installed_version} < target {version}, will upgrade")
            else:
                self.logger.info(f"[ota] {package_name}: installed {installed_version} >= target {version}, skip")
                return True
        else:
            self.logger.info(f"[ota] {package_name}: not installed, will skip {version}")
            return False

        # 2) 构建下载URL并找到可用的URL
        github_url = f"{const.DOWNLOAD_BASE_URL}/{release}/{component}_{version}.deb"
        gitee_url = f"{const.DOWNLOAD_BASE_URL_GITEE}/{release}/{component}_{version}.deb"
        
        download_url = self._find_available_download_url([github_url, gitee_url])
        if not download_url:
            self.logger.error(f"[ota] File not found in both GitHub and Gitee for {component} {version}")
            return False

        # 3) 下载到 /var/cache/apt/<component>/ 目录
        cache_dir = os.path.join("/var/cache/apt", component)
        local_file = None
        try:
            # ensure cache dir
            os.makedirs(cache_dir, exist_ok=True)

            # download
            local_file = os.path.join(cache_dir, f"{component}_{version}.deb")
            self.logger.info(f"[ota] Downloading {component} from {download_url} -> {local_file}")
            urllib.request.urlretrieve(download_url, local_file)

            # 3) install
            self.logger.info(f"[ota] Installing {package_name} from {local_file}")
            # result = subprocess.run(["dpkg", "-i", local_file], capture_output=True, text=True)
            # if result.returncode != 0:
            #     self.logger.error(f"[ota] Failed to install {package_name}: {result.stderr}")
            #     self._safe_rmtree(cache_dir)
            #     return False
        except Exception as e:
            self.logger.error(f"[ota] Error processing {package_name}: {e}")
            # self._safe_rmtree(cache_dir)
            return False

        # 4) postinst fix-dependency（可选）
        # try:
        #     postinst_file = f"/var/lib/dpkg/info/{package_name}.postinst"
        #     if os.path.exists(postinst_file):
        #         self.logger.info(f"[ota] Running postinst fix-dependency for {package_name}")
        #         subprocess.run([postinst_file, "fix-dependency"], check=False)
        # except Exception as e:
        #     self.logger.warning(f"[ota] postinst execution error for {package_name}: {e}")

        self.logger.info(f"[ota] {package_name} install/upgrade to {version} finished")
        return True
    
    def _find_available_download_url(self, urls):
        """Quickly find an available download URL from a list of URLs
        
        Uses HEAD request to check if file exists, with short timeout for quick detection.
        Tries URLs in order and returns the first available one.
        
        Args:
            urls: List of URLs to check
            
        Returns:
            str: First available URL, or None if none are available
        """
        for url in urls:
            try:
                # Use HEAD request to check if file exists (faster than GET)
                req = urllib.request.Request(url, method='HEAD')
                req.add_header('User-Agent', 'LinuxBox-Supervisor/1.0')
                
                # Short timeout for quick detection (2 seconds)
                with urllib.request.urlopen(req, timeout=2) as response:
                    # Check if status is OK (200)
                    if response.status == 200:
                        self.logger.info(f"[ota] Found available file at: {url}")
                        return url
            except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout) as e:
                # File not found or timeout, try next URL
                self.logger.debug(f"[ota] File not available at {url}: {e}")
                continue
            except Exception as e:
                # Other errors, log and continue
                self.logger.debug(f"[ota] Error checking {url}: {e}")
                continue
        
        return None
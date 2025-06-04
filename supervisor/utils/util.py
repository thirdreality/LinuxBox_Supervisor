import threading
import logging
import os
import json
import subprocess

# ====== Merged from utils/utils.py below ======
"""
System utility functions for performing system operations like reboot, shutdown, and factory reset.
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class OtaStatus:
    """
    Class to store WiFi connection status information
    """
    def __init__(self):
        self.software_mode = "homeassistant-core"
        self.install = "false"
        self.process = "1"

def execute_system_command(command):
    """
    Execute a system command and handle exceptions.
    
    Args:
        command: List containing the command and its arguments
        
    Returns:
        bool: True if command executed successfully, False otherwise
    """
    try:
        subprocess.run(command, check=True)
        logging.info(f"Successfully executed: {' '.join(command)}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with return code {e.returncode}: {' '.join(command)}")
        return False
    except Exception as e:
        logging.error(f"Failed to execute command: {' '.join(command)}, Error: {str(e)}")
        return False

def compare_versions(current_version, new_version):
    """
    比较两个版本号，判断是否需要更新
    
    Args:
        current_version: 当前版本号，格式为 "x.y.z"
        new_version: 新版本号，格式为 "x.y.z"
        
    Returns:
        bool: 如果新版本大于当前版本，返回 True，否则返回 False
    """
    if not current_version:
        # 如果当前版本为空，则需要更新
        return True
        
    try:
        # 将版本号拆分为数字列表
        current_parts = [int(x) for x in current_version.split('.')]
        new_parts = [int(x) for x in new_version.split('.')]
        
        # 确保两个列表长度相同
        while len(current_parts) < len(new_parts):
            current_parts.append(0)
        while len(new_parts) < len(current_parts):
            new_parts.append(0)
        
        # 逐个比较版本号的各个部分
        for i in range(len(current_parts)):
            if new_parts[i] > current_parts[i]:
                return True
            elif new_parts[i] < current_parts[i]:
                return False
        
        # 如果所有部分都相等，则不需要更新
        return False
    except Exception as e:
        logging.error(f"Error comparing versions {current_version} and {new_version}: {e}")
        # 出错时保守处理，不更新
        return False

def get_installed_version(package_name):
    """
    获取已安装软件包的版本
    
    Args:
        package_name: 软件包名称
        
    Returns:
        str: 版本号，如果未安装则返回空字符串
    """
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package_name],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return ""
    except Exception as e:
        logging.error(f"Error getting version for {package_name}: {e}")
        return ""

def is_service_running(service_name):
    try:
        result = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
        return result.stdout.strip() == "active"
    except Exception as e:
        logging.error(f"Error checking if service {service_name} is running: {e}")
        return False

def is_service_enabled(service_name):
    try:
        result = subprocess.run(["systemctl", "is-enabled", service_name], capture_output=True, text=True)
        return result.stdout.strip() == "enabled"
    except Exception as e:
        logging.error(f"Error checking if service {service_name} is enabled: {e}")
        return False

def get_service_status(service_name):
    try:
        result = subprocess.run(["systemctl", "status", service_name], capture_output=True, text=True)
        return result.stdout
    except Exception as e:
        logging.error(f"Error getting status for service {service_name}: {e}")
        return ""

def enable_service(service_name, enable):
    try:
        if enable:
            subprocess.run(["systemctl", "enable", service_name], check=True)
        else:
            subprocess.run(["systemctl", "disable", service_name], check=True)
        return True
    except Exception as e:
        logging.error(f"Error {'enabling' if enable else 'disabling'} service {service_name}: {e}")
        return False

def start_service(service_name, start):
    try:
        if start:
            subprocess.run(["systemctl", "start", service_name], check=True)
        else:
            subprocess.run(["systemctl", "stop", service_name], check=True)
        return True
    except Exception as e:
        logging.error(f"Error {'starting' if start else 'stopping'} service {service_name}: {e}")
        return False

def perform_reboot():
    """
        Safely stop necessary services and reboot the system.
        
        This function stops Docker service before rebooting to prevent data corruption.
        
        Returns:
            bool: True if reboot command was executed successfully, False otherwise
        """
    try:
        subprocess.run(["systemctl", "stop", "docker"], check=True)
        subprocess.run(["reboot"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing reboot: {e}")
        return False

def perform_power_off():
    """
        Safely stop necessary services and shut down the system.
        
        This function stops Docker service before shutdown to prevent data corruption.
        
        Returns:
            bool: True if shutdown command was executed successfully, False otherwise
        """
    try:
        subprocess.run(["systemctl", "stop", "docker"], check=True)
        subprocess.run(["poweroff"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing power off: {e}")
        return False

def perform_factory_reset():
    try:
        subprocess.run(["/usr/local/bin/factory_reset.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error performing factory reset: {e}")
        return False

def perform_wifi_provision_prepare():
    try:
        subprocess.run(["/usr/local/bin/wifi_provision_prepare.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error preparing WiFi provision: {e}")
        return False

def perform_wifi_provision_restore():
    try:
        subprocess.run(["/usr/local/bin/wifi_provision_restore.sh"], check=True)
        return True
    except Exception as e:
        logging.error(f"Error restoring WiFi provision: {e}")
        return False

# ====== End of merged content ======

def get_ha_zigbee_mode(config_file="/var/lib/homeassistant/homeassistant/.storage/core.config_entries"):
    """
    检查 HomeAssistant 当前 Zigbee 工作模式。
    - 如果有 "domain": "mqtt"，返回 'z2m'
    - 如果有 "domain": "zha"，返回 'zha'
    - 都没有则返回 'none'
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 正确获取 entries 列表
            entries = data.get('data', {}).get('entries', [])
            has_zha = any(e.get('domain') == 'zha' for e in entries)
            has_mqtt = any(e.get('domain') == 'mqtt' for e in entries)
            if has_mqtt:
                return 'z2m'
            elif has_zha:
                return 'zha'
            else:
                return 'none'
    except Exception as e:
        logging.error(f"读取 HomeAssistant config_entries 失败: {e}")
        return 'none'


def detect_zigbee_mode(config_file="/srv/homeassistant/config/.storage/core.config_entrity"):
    """
    检查配置文件中包含ZHA或MQTT配置。
    返回: 'zha', 'mqtt', 或 None
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()
            if "zha" in content:
                return "zha"
            if "mqtt" in content:
                return "mqtt"
    except Exception as e:
        logging.error(f"读取配置文件失败: {e}")
    return None

def run_zha_pairing():
    """
    启动ZHA配对流程
    """
    script = "/srv/homeassistant/bin/home_assistant_zha_enable.py"
    try:
        subprocess.run(["python3", script], check=True)
        logging.info("ZHA配对流程已启动")
    except Exception as e:
        logging.error(f"ZHA配对启动失败: {e}")

def run_mqtt_pairing():
    """
    启动MQTT配对流程
    """
    # 这里假设有一个专门的MQTT配对脚本
    script = "/srv/homeassistant/bin/home_assistant_z2m_enable.py"
    try:
        subprocess.run(["python3", script], check=True)
        logging.info("MQTT配对流程已启动")
    except Exception as e:
        logging.error(f"MQTT配对启动失败: {e}")

def run_zigbee_ota_update():
    """
    启动Zigbee OTA信息刷新
    """
    # 这里假设有一个OTA刷新脚本
    script = "/srv/homeassistant/bin/home_assistant_zigbee_ota_update.py"
    try:
        subprocess.run(["python3", script], check=True)
        logging.info("Zigbee OTA信息已刷新")
    except Exception as e:
        logging.error(f"Zigbee OTA刷新失败: {e}")

def threaded(func):
    def wrapper(*args, **kwargs):
        t = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        t.start()
        return t
    return wrapper

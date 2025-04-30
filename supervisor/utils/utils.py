"""
System utility functions for performing system operations like reboot, shutdown, and factory reset.
"""

import subprocess
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SystemInfo:

    def __init__(self):
        self.model = "LinuxBox"
        self.version = "1.0.0"
        self.software_mode = "homeassistant-core"
        self.software_version = "2025.4.4"

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
        logging.error(f"Error getting installed version for {package_name}: {e}")
        return ""

def is_service_running(service_name):
    # Check if a service is running using systemctl is-active
    # Returns True if active, False otherwise
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True
        )
        return result.stdout.strip() == "active"
    except Exception as e:
        logging.error(f"Error checking service status for {service_name}: {e}")
        return False

def perform_reboot():
    """
    Safely stop necessary services and reboot the system.
    
    This function stops Docker service before rebooting to prevent data corruption.
    
    Returns:
        bool: True if reboot command was executed successfully, False otherwise
    """
    logging.info("Initiating system reboot")
    execute_system_command(["systemctl", "stop", "docker"])
    return execute_system_command(["reboot"])


def perform_power_off():
    """
    Safely stop necessary services and shut down the system.
    
    This function stops Docker service before shutdown to prevent data corruption.
    
    Returns:
        bool: True if shutdown command was executed successfully, False otherwise
    """
    logging.info("Initiating system shutdown")
    execute_system_command(["systemctl", "stop", "docker"])
    return execute_system_command(["shutdown", "now"])


def perform_factory_reset():
    """
    Perform a factory reset by clearing configurations and rebooting.
    
    This function removes all configuration files in the /config directory,
    stops Docker service, and reboots the system to complete the reset.
    
    Returns:
        bool: True if all commands were executed successfully, False otherwise
    """
    logging.info("Performing factory reset...")
    # Remove configuration files
    success = execute_system_command(["rm", "-rf", "/config/*"])
    if not success:
        logging.error("Failed to remove configuration files")
        return False
    
    execute_system_command(["systemctl", "stop", "docker"])
    return execute_system_command(["reboot"])

def perform_wifi_provision_prepare():
    execute_system_command(["systemctl", "stop", "home-assistant"])
    return True

def perform_wifi_provision_restore():
    execute_system_command(["systemctl", "start", "home-assistant"])
    return True
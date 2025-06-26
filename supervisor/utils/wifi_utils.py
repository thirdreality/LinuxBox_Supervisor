# maintainer: guoping.liu@3reality.com

import subprocess
import logging
import glob
import os
import configparser

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def execute_command(command):
    """
    Execute a system command and return the result and status code
    
    Args:
        command: The command string to execute
        
    Returns:
        tuple: (result string, status code) - status code 0 indicates success
    """
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return result.stdout.strip(), 0
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {command}, Error: {e.stderr.strip()}")
        return e.stderr.strip(), e.returncode


def is_interface_existing(interface="wlan0"):
    """
    Check if a network interface exists
    
    Args:
        interface: The network interface name to check, defaults to wlan0
        
    Returns:
        bool: True if the interface exists, False otherwise
    """
    try:
        with open(f"/sys/class/net/{interface}/operstate", "r"):
            return True
    except FileNotFoundError:
        logging.warning(f"Interface {interface} not found")
        return False


def is_network_connected():
    """
    Check if wlan0 is connected to a network
    
    Returns:
        bool: True if connected, False otherwise
    """
    try:
        result = subprocess.run(["iw", "dev", "wlan0", "link"], capture_output=True, text=True)
        return "Connected" in result.stdout
    except subprocess.SubprocessError as e:
        logging.error(f"Failed to check network connection: {e}")
        return False


def get_wlan0_ip():
    """
    Get the IPv4 address of the wlan0 interface
    
    Returns:
        str or None: Returns the IP address, or None if not available
    """
    command = "ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1"
    result, status = execute_command(command)
    
    if status == 0 and result:
        return result
    logging.warning("Failed to get wlan0 IP address")
    return None


def get_wlan0_mac():
    """
    Get the MAC address of the wlan0 interface
    
    Returns:
        str or None: Returns the MAC address, or None if not available
    """
    command = "cat /sys/class/net/wlan0/address"
    result, status = execute_command(command)
    
    if status == 0 and result:
        return result
    logging.warning("Failed to get wlan0 MAC address")
    return None

def get_wlan0_mac_for_localname():
    """
    Get the MAC address of wlan0 formatted for BLE local name: no colons, uppercase.
    Returns:
        str or None: e.g., 'AABBCCDDEEFF', or None if not available
    """
    mac = get_wlan0_mac()
    if mac:
        return mac.replace(':', '').upper()
    return None


def check_wifi_connected():
    """
    Check if WiFi is connected
    
    Returns:
        bool: True if connected, False otherwise
    """
    command = "nmcli -t -f GENERAL.STATE device show wlan0"
    result, state = execute_command(command)
    return state == 0 and "(connected)" in result


def get_active_connection_name():
    """
    Get the name of the current active network connection
    
    Returns:
        str or None: Returns the connection name, or None if no active connection
    """
    command = "nmcli -t -f NAME connection show --active"
    result, status = execute_command(command)
    
    if status == 0 and result:
        return result
    return None


def has_active_connection():
    """
    Check if there is an active network connection
    
    Returns:
        bool: True if there is an active connection, False otherwise
    """
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'TYPE,STATE,NAME', 'connection', 'show', '--active'],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        logging.error(f"Command 'nmcli' failed with exit code {e.returncode}")
        logging.error(e.stderr)
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return False

def _get_info_nmcli():
    """Get WiFi info using nmcli"""
    try:
        # Get the name of the current active WiFi connection
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME', 'connection', 'show', '--active'],
            capture_output=True, text=True, check=True
        )
        connections = result.stdout.strip().split('\n')

        for conn_name in connections:
            if conn_name:
                try:
                    # Check connection type
                    type_result = subprocess.run(
                        ['nmcli', '-t', '-f', 'connection.type', 'connection', 'show', conn_name],
                        capture_output=True, text=True, check=True
                    )
                    if '802-11-wireless' in type_result.stdout:
                        # Get SSID and PSK
                        ssid_cmd = [
                            'nmcli', '-t', '-f', '802-11-wireless.ssid',
                            'connection', 'show', conn_name
                        ]
                        psk_cmd = [
                            'nmcli', '-s', '-t', '-f', '802-11-wireless-security.psk',
                            'connection', 'show', conn_name
                        ]
                        ssid_result = subprocess.run(ssid_cmd, capture_output=True, text=True)
                        psk_result = subprocess.run(psk_cmd, capture_output=True, text=True)

                        ssid = ssid_result.stdout.strip().split(':')[-1] if ssid_result.stdout.strip() else None
                        psk = psk_result.stdout.strip().split(':')[-1] if psk_result.stdout.strip() else None

                        if ssid:
                            return ssid, psk
                except subprocess.CalledProcessError:
                    continue
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to execute nmcli command: {e}")
    return None, None

def _get_info_from_config():
    """Read WiFi info from NetworkManager config files"""
    config_dir = '/etc/NetworkManager/system-connections/'

    if not os.path.exists(config_dir):
        return None, None

    try:
        config_files = glob.glob(os.path.join(config_dir, '*'))
        for config_file in config_files:
            if os.path.isfile(config_file):
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    config = configparser.ConfigParser()
                    config.read_string(content)
                    if config.has_section('wifi'):
                        ssid = config.get('wifi', 'ssid', fallback=None)
                        psk = None
                        if config.has_section('wifi-security'):
                            psk = config.get('wifi-security', 'psk', fallback=None)
                        if ssid:
                            return ssid, psk
                except Exception as e:
                    logging.error(f"Failed to parse config file {config_file}: {e}")
                    continue
    except PermissionError:
        logging.error("Root permission is required to access NetworkManager config files.")
    return None, None

def get_current_wifi_info():
    """Get current WiFi connection info"""
    # First try using nmcli
    ssid, psk = _get_info_nmcli()
        
    # If nmcli fails, try reading config files
    if not ssid or not psk:
        ssid, psk = _get_info_from_config()
            
    return ssid, psk    
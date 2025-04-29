"""
System utility functions for performing system operations like reboot, shutdown, and factory reset.
"""

import subprocess
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    except subprocess.SubprocessError as e:
        logging.error(f"Error executing {' '.join(command)}: {e}")
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

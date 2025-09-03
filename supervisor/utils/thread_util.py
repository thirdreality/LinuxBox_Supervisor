# maintainer: guoping.liu@3reality.com

import time
import logging
import json
import subprocess
from supervisor.ptest.rcp_test import get_rcp_info

logger = logging.getLogger("Supervisor")

def _check_service_running(service_name):
    """Check if a systemd service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name], 
            capture_output=True, 
            text=True, 
            check=False
        )
        return result.stdout.strip() == "active"
    except Exception as e:
        logger.error(f"Error checking service status for {service_name}: {e}")
        return False

def run_thread_enable(progress_callback, complete_callback):
    """Simulates enabling Thread mode."""
    logger.info("Enabling Thread mode...")
    try:
        progress_callback(50, "Processing...")
        time.sleep(2)
        complete_callback(True, "Thread enabled.")
    except Exception as e:
        logger.error(f"Failed to enable thread: {e}")
        complete_callback(False, str(e))

def run_thread_disable(progress_callback, complete_callback):
    """Simulates disabling Thread mode."""
    logger.info("Disabling Thread mode...")
    try:
        progress_callback(50, "Processing...")
        time.sleep(2)
        complete_callback(True, "Thread disabled.")
    except Exception as e:
        logger.error(f"Failed to disable thread: {e}")
        complete_callback(False, str(e))

def get_thread_info():
    """
    Get Thread information and return as JSON string.
    
    Returns:
        str: JSON string containing Thread RCP information
    """
    try:
        logger.info("Getting Thread information...")
        
        # Check otbr-agent service status
        otbr_agent_running = _check_service_running("otbr-agent.service")
        
        result = {
            "services": {
                "otbr_agent": otbr_agent_running
            }
        }
        
        # If otbr-agent is not running, try to get RCP info directly
        if not otbr_agent_running:
            logger.info("otbr-agent.service not running, trying RCP direct communication...")
            try:
                rcp_info = get_rcp_info()
                if rcp_info:
                    result["status"] = "success"
                    result["rcp_info"] = rcp_info
                    logger.info("Successfully retrieved RCP information")
                else:
                    result["status"] = "error"
                    result["error"] = "Failed to get RCP information"
                    result["rcp_info"] = None
                    logger.warning("Failed to get RCP information")
            except Exception as e:
                result["status"] = "error"
                result["error"] = f"RCP communication error: {str(e)}"
                result["rcp_info"] = None
                logger.error(f"RCP communication failed: {e}")
        else:
            result["status"] = "service_running"
            result["message"] = "otbr-agent.service is running, RCP port may be occupied"
            logger.info("otbr-agent.service is running, skipping RCP direct communication")
        
        return json.dumps(result, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "status": "error",
            "error": f"Failed to get Thread info: {str(e)}",
            "services": {
                "otbr_agent": False
            }
        }
        logger.error(f"Error in get_thread_info: {e}")
        return json.dumps(error_result, ensure_ascii=False, indent=2)

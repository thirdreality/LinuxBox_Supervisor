# maintainer: guoping.liu@3reality.com

import time
import logging

logger = logging.getLogger("Supervisor")

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

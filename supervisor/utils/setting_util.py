# maintainer: guoping.liu@3reality.com

import time
import logging

logger = logging.getLogger("Supervisor")

def run_setting_backup(progress_callback, complete_callback):
    """Simulates a setting backup process."""
    logger.info("Starting setting backup...")
    try:
        for i in range(1, 6):
            time.sleep(1)
            progress_callback(i * 20, f"Backing up step {i}/5")
        complete_callback(True, "Backup completed successfully.")
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        complete_callback(False, str(e))

def run_setting_restore(progress_callback, complete_callback):
    """Simulates a setting restore process."""
    logger.info("Starting setting restore...")
    try:
        for i in range(1, 4):
            time.sleep(1)
            progress_callback(i * 33, f"Restoring step {i}/3")
        progress_callback(100, "Finalizing...")
        complete_callback(True, "Restore completed successfully.")
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        complete_callback(False, str(e))

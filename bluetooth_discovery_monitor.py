#!/usr/bin/env python3.11
"""
Bluetooth Discovery Monitor Script

This script monitors the time it takes for Home Assistant to start scanning
after restarting the Bluetooth service. It tracks the transition from
Discovering: no to Discovering: yes in bluetoothctl show output.
"""

import subprocess
import re
import time
import random
import sys
from datetime import datetime


def run_command(command):
    """Execute a shell command and return the output"""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {command}")
        return None
    except Exception as e:
        print(f"Error executing command '{command}': {e}")
        return None


def restart_bluetooth_service():
    """Restart the Bluetooth service"""
    print("Restarting Bluetooth service...")
    commands = [
        "systemctl stop bluetooth",
        "systemctl start bluetooth",
        "sleep 2"  # Wait for service to fully start
    ]
    
    for cmd in commands:
        result = run_command(cmd)
        if result is None:
            print(f"Failed to execute: {cmd}")
            return False
    
    print("Bluetooth service restarted successfully")
    return True


def get_discovering_status():
    """Get the current Discovering status from bluetoothctl show"""
    output = run_command("bluetoothctl show")
    if output is None:
        return None
    
    # Look for "Discovering: yes" or "Discovering: no"
    match = re.search(r'Discovering:\s*(yes|no)', output)
    if match:
        return match.group(1)
    return None


def monitor_discovery_start():
    """Monitor the transition from Discovering: no to Discovering: yes"""
    print("Starting discovery monitoring...")
    print("Waiting for Discovering status to change from 'no' to 'yes'...")
    
    start_time = time.time()
    check_count = 0
    
    while True:
        check_count += 1
        current_time = time.time()
        elapsed_time = current_time - start_time
        
        status = get_discovering_status()
        
        if status is None:
            print(f"Check {check_count}: Unable to get Discovering status")
        else:
            print(f"Check {check_count}: Discovering: {status} (Elapsed: {elapsed_time:.1f}s)")
            
            if status == "yes":
                print(f"\n🎉 Discovery started after {elapsed_time:.1f} seconds!")
                return elapsed_time
        
        # Check every second
        time.sleep(1)
        
        # Safety timeout - if it takes more than 5 minutes, stop
        if elapsed_time > 3600:
            print("Timeout: Discovery did not start within 5 minutes")
            return None


def random_wait():
    """Wait for a random time between 30-90 seconds"""
    wait_time = random.randint(30, 90)
    print(f"\nRandom wait: {wait_time} seconds...")
    
    for remaining in range(wait_time, 0, -1):
        print(f"Waiting: {remaining}s remaining", end='\r')
        time.sleep(1)
    print("\nRandom wait completed!")


def main():
    """Main function"""
    print("=" * 60)
    print("Bluetooth Discovery Monitor")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    cycle_count = 0
    
    try:
        while True:
            cycle_count += 1
            print(f"\n🔄 Starting cycle {cycle_count}")
            print("-" * 40)
            
            # Step 1: Restart Bluetooth service
            if not restart_bluetooth_service():
                print("Failed to restart Bluetooth service. Exiting.")
                sys.exit(1)
            
            # Step 2: Monitor discovery start
            discovery_time = monitor_discovery_start()
            
            if discovery_time is not None:
                print(f"\n📊 Cycle {cycle_count} Results:")
                print(f"   Discovery start time: {discovery_time:.1f} seconds")
                print(f"   Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"\n❌ Cycle {cycle_count}: Failed to detect discovery start")
            
            # Step 3: Random wait before next cycle
            if cycle_count > 1:  # Skip wait after first cycle
                random_wait()
            
    except KeyboardInterrupt:
        print(f"\n\n⏹️  Monitoring stopped by user after {cycle_count} cycles")
        print("=" * 60)
        print("Monitoring completed!")
        print("=" * 60)


if __name__ == "__main__":
    main() 
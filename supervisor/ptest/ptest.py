#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Product Test Module for LinuxBox/HubV3"""

import os
import sys
import time
import subprocess
import logging
import re
from ..hardware import GpioLed, LedState
from ..sysinfo import _get_t3r_release_info, get_package_version
from ..const import PTEST_WIFI_SSID, PTEST_WIFI_PASSWORD

# Configure logging for ptest
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("PTest")

class ProductTest:
    def __init__(self, supervisor=None):
        # Use supervisor's LED instance if available, otherwise create our own
        self.supervisor = supervisor
        if supervisor and hasattr(supervisor, 'led'):
            self.led = supervisor.led
        else:
            self.led = GpioLed()
        self.test_results = {}
        
    def run_command(self, cmd, shell=True, capture_output=True, text=True):
        """Execute system command and return result"""
        try:
            result = subprocess.run(cmd, shell=shell, capture_output=capture_output, text=text, timeout=30)
            return result
        except subprocess.TimeoutExpired:
            logger.error(f"Command timeout: {cmd}")
            return None
        except Exception as e:
            logger.error(f"Command execution failed: {cmd}, error: {e}")
            return None

    def check_package_installed(self, package_name):
        """Check if a package is installed"""
        result = self.run_command(f"dpkg -l | grep {package_name}")
        return result and result.returncode == 0

    def check_service_status(self, service_name):
        """Check if a service is running"""
        result = self.run_command(f"systemctl is-active {service_name}")
        return result and result.stdout.strip() == "active"

    def test_01_device_info(self):
        """Test 1: Print device model and version information"""
        print("\n=== Test 1: Device Information ===")
        
        try:
            release_info = _get_t3r_release_info()
            model = release_info.get("MODLE", "Unknown")
            version = release_info.get("VERSION", "Unknown")
            
            print(f"Model : {model}")
            print(f"VERSION : {version}")
            
            self.test_results['device_info'] = True
            return True
        except Exception as e:
            logger.error(f"Device info test failed: {e}")
            self.test_results['device_info'] = False
            return False

    def test_02_memory_storage(self):
        """Test 2: Check memory and storage"""
        print("\n=== Test 2: Memory and Storage Check ===")
        
        memory_pass = False
        storage_pass = False
        
        # Check memory (should be > 1.5GB)
        try:
            result = self.run_command("free -k")
            if result and result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.startswith('Mem:'):
                        parts = line.split()
                        total_memory_kb = int(parts[1])
                        available_memory_kb = int(parts[6]) if len(parts) > 6 else int(parts[3])
                        total_memory_gb = total_memory_kb / 1024 / 1024
                        available_memory_gb = available_memory_kb / 1024 / 1024
                        
                        print(f"Memory : {available_memory_gb:.1f}GB/{total_memory_gb:.1f}GB")
                        
                        if total_memory_gb > 1.5:
                            print("test_result pass")
                            memory_pass = True
                        else:
                            print("test_result fail")
                        break
        except Exception as e:
            logger.error(f"Memory check failed: {e}")
            print("test_result fail")

        # Check storage (should be > 7GB)
        try:
            result = self.run_command("df -h /")
            if result and result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if '/' in line and not line.startswith('Filesystem'):
                        parts = line.split()
                        total_storage = parts[1]
                        available_storage = parts[3]
                        
                        print(f"Storage: {available_storage}/{total_storage}")
                        
                        # Extract numeric value from storage size
                        total_size_match = re.search(r'(\d+\.?\d*)G', total_storage)
                        if total_size_match:
                            total_size = float(total_size_match.group(1))
                            if total_size > 7:
                                print("test_result pass")
                                storage_pass = True
                            else:
                                print("test_result fail")
                        break
        except Exception as e:
            logger.error(f"Storage check failed: {e}")
            print("test_result fail")

        self.test_results['memory_storage'] = memory_pass and storage_pass
        return memory_pass and storage_pass

    def test_03_led_colors(self):
        """Test 3: Check LED colors"""
        print("\n=== Test 3: LED Color Check ===")
        
        red_pass = False
        green_pass = False
        blue_pass = False
        yellow_pass = False
        cyan_pass = False
        magenta_pass = False
        
        # Test RED LED
        try:
            print("Testing RED LED...")
            self.led.set_led_state(LedState.USER_EVENT_RED)
            time.sleep(1)
            print("SET LED RED Command : test_result pass")
            red_pass = True
        except Exception as e:
            logger.error(f"RED LED test failed: {e}")
            print("SET LED RED Command : test_result fail")
        
        # Test GREEN LED
        try:
            print("Testing GREEN LED...")
            self.led.set_led_state(LedState.USER_EVENT_GREEN)
            time.sleep(1)
            print("SET LED GREEN Command : test_result pass")
            green_pass = True
        except Exception as e:
            logger.error(f"GREEN LED test failed: {e}")
            print("SET LED GREEN Command : test_result fail")
        
        # Test BLUE LED
        try:
            print("Testing BLUE LED...")
            self.led.set_led_state(LedState.USER_EVENT_BLUE)
            time.sleep(1)
            print("SET LED BLUE Command : test_result pass")
            blue_pass = True
        except Exception as e:
            logger.error(f"BLUE LED test failed: {e}")
            print("SET LED BLUE Command : test_result fail")
        
        # Test YELLOW LED
        try:
            print("Testing YELLOW LED...")
            self.led.set_led_state(LedState.USER_EVENT_YELLOW)
            time.sleep(1)
            print("SET LED YELLOW Command : test_result pass")
            yellow_pass = True
        except Exception as e:
            logger.error(f"YELLOW LED test failed: {e}")
            print("SET LED YELLOW Command : test_result fail")
        
        # Test CYAN LED
        try:
            print("Testing CYAN LED...")
            self.led.set_led_state(LedState.USER_EVENT_CYAN)
            time.sleep(1)
            print("SET LED CYAN Command : test_result pass")
            cyan_pass = True
        except Exception as e:
            logger.error(f"CYAN LED test failed: {e}")
            print("SET LED CYAN Command : test_result fail")
        
        # Test MAGENTA LED
        try:
            print("Testing MAGENTA LED...")
            self.led.set_led_state(LedState.USER_EVENT_MAGENTA)
            time.sleep(1)
            print("SET LED MAGENTA Command : test_result pass")
            magenta_pass = True
        except Exception as e:
            logger.error(f"MAGENTA LED test failed: {e}")
            print("SET LED MAGENTA Command : test_result fail")
        
        # Turn off test mode LED
        try:
            print("Turning off LED...")
            self.led.set_led_state(LedState.USER_EVENT_OFF)
            print("LED turned off successfully")
        except Exception as e:
            logger.error(f"LED off failed: {e}")
        
        # Overall result
        overall_pass = red_pass and green_pass and blue_pass and yellow_pass and cyan_pass and magenta_pass
        self.test_results['led_colors'] = overall_pass
        return overall_pass

    def test_04_button(self):
        """Test 4: Button test"""
        print("\n=== Test 4: Button Check ===")
        print("Please press the button within 15 seconds...")
        print("Monitoring LED state for USER_EVENT_WHITE...")
        
        # Monitor LED state for 5 seconds, checking every 200ms
        start_time = time.time()
        timeout = 15.0  # 5 seconds timeout
        check_interval = 0.2  # 200 milliseconds
        
        button_pressed = False
        
        try:
            while time.time() - start_time < timeout:
                # Check current LED state
                if self.led and hasattr(self.led, 'get_led_state'):
                    current_state = self.led.get_led_state()
                    if current_state == LedState.USER_EVENT_WHITE:
                        print("Button press detected (LED is WHITE)!")
                        button_pressed = True
                        break
                
                # Wait for next check
                time.sleep(check_interval)
            
            if button_pressed:
                print("Button test: test_result pass")
                self.test_results['button'] = True
                return True
            else:
                print("Button test: test_result fail - No button press detected within 5 seconds")
                self.test_results['button'] = False
                return False
                
        except Exception as e:
            logger.error(f"Button test failed: {e}")
            print("Button test: test_result fail")
            self.test_results['button'] = False
            return False

    def test_05_bluetooth(self):
        """Test 5: Bluetooth check"""
        print("\n=== Test 5: Bluetooth Check ===")
        
        version = ""
        running = False
        mac = ""
        
        # Check bluetoothd version
        try:
            result = self.run_command("bluetoothd -v")
            if result and result.returncode == 0:
                version = result.stdout.strip()
                print(f"bluetoothd version: {version}")
        except Exception as e:
            logger.error(f"Bluetooth version check failed: {e}")

        # Check bluetooth service status
        try:
            running = self.check_service_status("bluetooth.service")
            status = "active" if running else "inactive"
            print(f"bluetooth.service: {status}")
        except Exception as e:
            logger.error(f"Bluetooth service check failed: {e}")

        # Get BLE MAC address
        try:
            result = self.run_command("hciconfig -a")
            if result and result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'BD Address:' in line:
                        mac_match = re.search(r'([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})', line)
                        if mac_match:
                            mac = mac_match.group(1)
                            print(f"BLE MAC : {mac}")
                            break
        except Exception as e:
            logger.error(f"BLE MAC check failed: {e}")

        # Determine pass/fail
        if version and running and mac:
            print("test_result pass")
            self.test_results['bluetooth'] = True
            return True
        else:
            print("test_result fail")
            self.test_results['bluetooth'] = False
            return False

    def test_06_wifi_network(self):
        """Test 6: WiFi and NetworkManager check"""
        print("\n=== Test 6: WiFi and NetworkManager Check ===")
        print(f"WiFi Test Configuration: SSID={PTEST_WIFI_SSID}")
        
        running = False
        mac = ""
        scan_pass = False
        connect_pass = False
        reset_pass = False
        
        # Check NetworkManager status
        try:
            running = self.check_service_status("NetworkManager")
            status = "active" if running else "inactive"
            print(f"NetworkManager: {status}")
        except Exception as e:
            logger.error(f"NetworkManager check failed: {e}")

        # Get WiFi MAC address
        try:
            result = self.run_command("ifconfig wlan0")
            if result and result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'ether' in line or 'HWaddr' in line:
                        mac_match = re.search(r'([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})', line)
                        if mac_match:
                            mac = mac_match.group(1)
                            print(f"WIFI MAC: {mac}")
                            break
        except Exception as e:
            logger.error(f"WiFi MAC check failed: {e}")

        # WiFi scan test
        try:
            # Rescan for networks
            self.run_command("nmcli device wifi rescan")
            time.sleep(2)
            
            # List available networks
            result = self.run_command("nmcli device wifi list")
            if result and result.returncode == 0:
                if PTEST_WIFI_SSID in result.stdout:
                    print("WIFI Scan: pass")
                    scan_pass = True
                else:
                    print(f"WIFI Scan: fail - {PTEST_WIFI_SSID} network not found")
            else:
                print("WIFI Scan: fail")
        except Exception as e:
            logger.error(f"WiFi scan failed: {e}")
            print("WIFI Scan: fail")

        # WiFi connect test
        try:
            # Connect to test network
            result = self.run_command(f"nmcli device wifi connect {PTEST_WIFI_SSID} password {PTEST_WIFI_PASSWORD}")
            time.sleep(3)
            
            # Check if connected
            conn_result = self.run_command("nmcli connection show --active")
            ip_result = self.run_command("ifconfig wlan0")
            
            if (conn_result and PTEST_WIFI_SSID in conn_result.stdout and 
                ip_result and 'inet ' in ip_result.stdout):
                print("WIFI connect: pass")
                connect_pass = True
            else:
                print("WIFI connect: fail")
        except Exception as e:
            logger.error(f"WiFi connect failed: {e}")
            print("WIFI connect: fail")

        # WiFi reset test
        try:
            # Delete the connection
            self.run_command(f"nmcli connection delete {PTEST_WIFI_SSID}")
            print("WIFI reset: pass")
            reset_pass = True
        except Exception as e:
            logger.error(f"WiFi reset failed: {e}")
            print("WIFI reset: fail")

        # Determine overall result
        if running and mac and scan_pass and connect_pass and reset_pass:
            print("test_result pass")
            self.test_results['wifi_network'] = True
            return True
        else:
            print("test_result fail")
            self.test_results['wifi_network'] = False
            return False

    def test_07_zigbee(self):
        """Test 7: Zigbee check"""
        print("\n=== Test 7: Zigbee Check ===")
        
        zha_conf_path = "/var/lib/homeassistant/zha.conf"
        ha_running = False
        ieee = ""
        radio = ""
        
        # Check if Home Assistant is running
        try:
            ha_running = self.check_service_status("home-assistant")
            status = "active" if ha_running else "inactive"
            print(f"home-assistant: {status}")
        except Exception as e:
            logger.error(f"Home Assistant check failed: {e}")

        # Check if zha.conf exists
        if not os.path.exists(zha_conf_path):
            # Check if thirdreality-hacore is installed
            if not self.check_package_installed("thirdreality-hacore"):
                print("WARNING: thirdreality-hacore not installed")
                print("test_result skip")
                self.test_results['zigbee'] = False
                return False
            else:
                print("ERROR: thirdreality-hacore installed but zha.conf missing")
                print("test_result fail")
                self.test_results['zigbee'] = False
                return False

        # Parse zha.conf
        try:
            with open(zha_conf_path, 'r') as f:
                content = f.read()
                
            # Extract Device IEEE
            ieee_match = re.search(r'Device IEEE:\s*([0-9a-fA-F:]+)', content)
            if ieee_match:
                ieee = ieee_match.group(1)
                print(f"ZIGBEE IEEE: {ieee}")
            
            # Extract Radio Type
            radio_match = re.search(r'Radio Type:\s*(\w+)', content)
            if radio_match:
                radio = radio_match.group(1)
                print(f"ZIGBEE RADIO: {radio}")
                
        except Exception as e:
            logger.error(f"Failed to parse zha.conf: {e}")

        # Determine pass/fail
        if ieee and radio:
            print("test_result pass")
            self.test_results['zigbee'] = True
            return True
        else:
            print("test_result fail")
            self.test_results['zigbee'] = False
            return False

    def test_08_thread(self):
        """Test 8: Thread check"""
        print("\n=== Test 8: Thread Check ===")
        
        # Check wpan0 interface
        try:
            result = self.run_command("ifconfig wpan0")
            if result and result.returncode == 0:
                if 'UP' in result.stdout and 'RUNNING' in result.stdout:
                    print("test_result pass")
                    self.test_results['thread'] = True
                    return True
        except Exception as e:
            logger.error(f"wpan0 check failed: {e}")

        # Check if otbr-agent is installed
        if not self.check_package_installed("thirdreality-otbr-agent"):
            print("WARNING: thirdreality-otbr-agent not installed")
            print("test_result skip")
            self.test_results['thread'] = False
            return False

        # Check otbr-agent service
        try:
            if self.check_service_status("otbr-agent.service"):
                print("ERROR: otbr-agent service running but thread interface failed")
                print("test_result fail")
                self.test_results['thread'] = False
                return False
        except Exception as e:
            logger.error(f"otbr-agent service check failed: {e}")

        print("test_result fail")
        self.test_results['thread'] = False
        return False

    def run_all_tests(self):
        """Run all product tests"""
        print("Starting Product Test Suite...")
        print("=" * 50)
        
        tests = [
            self.test_01_device_info,
            self.test_02_memory_storage,
            self.test_03_led_colors,
            self.test_04_button,
            self.test_05_bluetooth,
            self.test_06_wifi_network,
            self.test_07_zigbee,
            self.test_08_thread
        ]
        
        results = []
        for test in tests:
            try:
                result = test()
                results.append(result)
            except Exception as e:
                logger.error(f"Test failed with exception: {e}")
                results.append(False)
        
        # Print summary
        print("\n" + "=" * 50)
        print("Test Summary:")
        print("=" * 50)
        
        passed = sum(results)
        total = len(results)
        
        for i, (test_name, result) in enumerate(zip([
            "Device Info", "Memory/Storage", "LED Colors", "Button",
            "Bluetooth", "WiFi/Network", "Zigbee", "Thread"
        ], results)):
            status = "PASS" if result else "FAIL"
            print(f"{i+1:2d}. {test_name:<15} : {status}")
        
        print("-" * 50)
        print(f"Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print("ALL TESTS PASSED!")
        else:
            print("SOME TESTS FAILED!")
        
        return passed == total

def run_product_test(supervisor=None):
    """Main entry point for product test"""
    try:
        test = ProductTest(supervisor=supervisor)
        return test.run_all_tests()
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        return False
    except Exception as e:
        logger.error(f"Product test failed: {e}")
        return False

if __name__ == "__main__":
    run_product_test()

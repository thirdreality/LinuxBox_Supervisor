# maintainer: guoping.liu@thirdreality.com

import os
import threading
import time
import logging
import socket
import tempfile
import json

from .hardware import LedState


class SupervisorProxy:
    '''通过本地socket，连接SupervisorClient和Supervisor, 方便本地调试，以及其他模块服用本地功能'''
    SOCKET_PATH = "/tmp/led_socket"

    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.stop_event = threading.Event()
        
    def _is_tmp_mounted(self):
        return os.system("mountpoint -q /tmp") == 0

    def _ensure_tmp_ready(self, timeout=60, interval=1):
        start_time = time.time()
        
        while not self._is_tmp_mounted():
            time.sleep(interval)

        successful_check = False

        time.sleep(5)

        while time.time() - start_time < timeout:
            if self.stop_event.is_set():
                break
            try:
                if os.path.exists("/tmp") and os.access("/tmp", os.W_OK):
                    fd, temp_path = tempfile.mkstemp(dir='/tmp')
                    try:
                        os.write(fd, b'Test Write')
                        os.fsync(fd)  # Ensure data is flushed to disk
                        successful_check = True
                    finally:
                        os.close(fd)
                        os.remove(temp_path)
                    if successful_check:
                        logging.info("checking /tmp: OK")
                        return True
            except OSError as e:
                logging.error(f"OS error while checking /tmp: {e}")
            
            time.sleep(interval)

        return False

    def _setup_socket(self):
        self._ensure_tmp_ready()
        time.sleep(1)

        if os.path.exists(self.SOCKET_PATH):
            os.remove(self.SOCKET_PATH)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.SOCKET_PATH)
        self.server.listen(1)
        self.server.settimeout(1.0)

    def run(self):
        self._setup_socket()
        logging.info("Starting local socket monitor...")
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
                with conn:
                    data = conn.recv(1024).decode('utf-8')
                    response = self.handle_request(data)
                    conn.sendall(response.encode('utf-8'))
            except socket.timeout:
                continue

    def stop(self):
        self.stop_event.set()
        if hasattr(self, 'proxy_thread') and self.proxy_thread.is_alive():
            self.proxy_thread.join(timeout=5)  # 等待最多5秒
            if self.proxy_thread.is_alive():
                self.logger.warning("Proxy thread did not terminate gracefully")

    def handle_request(self, data):
        try:
            # 解析JSON数据
            payload = json.loads(data)
            
            # 检查是否包含cmd-led指令
            if "cmd-led" in payload:
                state_str = payload["cmd-led"].strip().lower()
                try:
                    # 将状态字符串转换为LedState枚举
                    state = LedState(state_str)
                    # 使用supervisor设置LED状态
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(state)
                        self.logger.info(f"LED state set to {state}")
                        return "LED state set successfully"
                    else:
                        error_msg = "Supervisor not available or missing set_led_state method"
                        self.logger.error(error_msg)
                        return error_msg
                except ValueError:
                    error_msg = f"Invalid LED state: {state_str}"
                    self.logger.error(error_msg)
                    return error_msg
            elif "cmd-ota" in payload:
                command_str = payload["cmd-ota"].strip().lower()
                try:
                    if self.supervisor and hasattr(self.supervisor, 'set_ota_command'):
                        self.supervisor.set_ota_command(command_str)
                        return "OTA command successfully"
                    else:
                        error_msg = "Supervisor not available or missing set_ota_command method"
                        self.logger.error(error_msg)
                        return error_msg
                except ValueError:
                    error_msg = f"Invalid OTA command: {command_str}"
                    self.logger.error(error_msg)
                    return error_msg                
            else:
                error_msg = "Missing cmd-led in request"
                self.logger.error(error_msg)
                return error_msg
        except json.JSONDecodeError:
            error_msg = f"Invalid JSON format: {data}"
            self.logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Unexpected error handling request: {e}"
            self.logger.error(error_msg)
            return error_msg
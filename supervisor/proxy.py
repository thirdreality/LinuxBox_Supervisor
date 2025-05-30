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
    SOCKET_PATH = "/run/led_socket"  # 使用/run目录，这是一个内存文件系统，通常总是可写的

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
        # 直接尝试创建socket，不再检查/tmp目录
        try:
            # 确保目录存在
            socket_dir = os.path.dirname(self.SOCKET_PATH)
            if not os.path.exists(socket_dir):
                try:
                    # 尝试创建目录，如果需要的话
                    os.makedirs(socket_dir, exist_ok=True)
                except Exception as e_dir:
                    self.logger.warning(f"Could not create directory {socket_dir}: {e_dir}")
            
            # 移除已存在的socket文件
            if os.path.exists(self.SOCKET_PATH):
                os.remove(self.SOCKET_PATH)
                
            # 创建和绑定socket
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(self.SOCKET_PATH)
            self.server.listen(1)
            self.server.settimeout(1.0)
            self.logger.info(f"Socket created at {self.SOCKET_PATH}")
        except Exception as e:
            self.logger.error(f"Failed to create socket at {self.SOCKET_PATH}: {e}")
            # 如果创建失败，直接抛出异常
            raise

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
            
            # 处理LED命令（特殊处理，因为需要转换为LedState枚举）
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
            
            # 处理其他命令类型
            # 定义命令类型和对应的方法映射
            command_mapping = {
                "cmd-ota": "set_ota_command",
                "cmd-thread": "set_thread_command",
                "cmd-zigbee": "set_zigbee_command"
            }
            
            # 查找匹配的命令类型
            for cmd_key, method_name in command_mapping.items():
                if cmd_key in payload:
                    command_str = payload[cmd_key].strip().lower()
                    cmd_type = cmd_key.replace("cmd-", "")
                    
                    try:
                        # 检查supervisor是否有对应的方法
                        if self.supervisor and hasattr(self.supervisor, method_name):
                            # 动态调用对应的方法
                            getattr(self.supervisor, method_name)(command_str)
                            self.logger.info(f"{cmd_type.capitalize()} command executed: {command_str}")
                            return f"{cmd_type} command successfully"
                        else:
                            error_msg = f"Supervisor not available or missing {method_name} method"
                            self.logger.error(error_msg)
                            return error_msg
                    except ValueError:
                        error_msg = f"Invalid {cmd_type} command: {command_str}"
                        self.logger.error(error_msg)
                        return error_msg
                    except Exception as e:
                        error_msg = f"Error executing {cmd_type} command: {e}"
                        self.logger.error(error_msg)
                        return error_msg
                    
                    # 如果找到并处理了命令，就不需要继续检查其他命令类型
                    return
            
            # 如果没有找到支持的命令
            error_msg = "Missing valid command in request. Supported commands: cmd-led, cmd-ota, cmd-thread, cmd-zigbee"
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
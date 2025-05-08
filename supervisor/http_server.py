"""
HTTP Server for LinuxBox Finder that mirrors the functionality of the BLE GATT server.
This server runs when WiFi is connected and provides the same APIs as the BLE service.
"""

import json
import hashlib
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import signal
import sys
import logging

from .utils import utils
from .hardware import LedState

# 使用与supervisor相同的logger

class SupervisorHTTPServer:
    """HTTP Server that integrates with Supervisor for shared state"""
    
    def __init__(self, supervisor, port=8086):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self.port = port
        self.server = None
        self.server_thread = None
        self.running = threading.Event()
    
    def start(self):
        """启动HTTP服务器"""
        if self.server:
            self.logger.warning("HTTP Server already running")
            return
        
        try:
            # 创建一个带有supervisor引用的handler类
            handler = self._create_handler()
            
            # 创建并启动服务器
            self.server = HTTPServer(("0.0.0.0", self.port), handler)
            self.running.set()
            
            # 在一个单独的线程中运行服务器
            self.server_thread = threading.Thread(target=self._run_server, daemon=True)
            self.server_thread.start()
            
            self.logger.info(f"HTTP Server starting on port {self.port}")
            
            # 如果supervisor有存储的IP地址，记录URL
            if hasattr(self.supervisor, 'wifi_info') and self.supervisor.wifi_info.get('ip_address'):
                ip = self.supervisor.wifi_info.get('ip_address')
                self.logger.info(f"HTTP Server accessible at: http://{ip}:{self.port}/")
        
        except Exception as e:
            self.logger.error(f"Failed to start HTTP Server: {e}")
    
    def stop(self):
        """停止HTTP服务器"""
        if self.server:
            self.running.clear()
            self.server.shutdown()
            self.server = None
            self.logger.info("HTTP Server stopped")
    
    def _run_server(self):
        """在一个单独的线程中运行HTTP服务器"""
        while self.running.is_set():
            try:
                self.server.serve_forever()
            except Exception as e:
                if self.running.is_set():  # 仅当仍应该运行时记录错误
                    self.logger.error(f"HTTP Server error: {e}")
                    time.sleep(5)  # 等待一段时间再重试
    
    def _create_handler(self):
        """创建一个能访问supervisor的HTTP请求处理器类"""
        supervisor = self.supervisor
        logger = self.logger
        
        class LinuxBoxHTTPHandler(BaseHTTPRequestHandler):
            """HTTP请求处理器"""
            
            # 存储对supervisor的引用
            _supervisor = supervisor
            _logger = logger
            
            # 重写日志方法，使用我们的logger
            def log_message(self, format, *args):
                self._logger.info(f"{self.client_address[0]} - {format % args}")
            
            def _set_headers(self, content_type="application/json"):
                self.send_response(200)
                self.send_header('Content-type', content_type)
                self.send_header('Access-Control-Allow-Origin', '*')  # 启用CORS
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()
            
            def do_OPTIONS(self):
                """处理CORS预检请求"""
                self._set_headers()
            
            def do_GET(self):
                """处理GET请求"""
                parsed_path = urlparse(self.path)
                path = parsed_path.path
                
                # WiFi状态特性
                if path == "/api/wifi/status":
                    self._handle_wifi_status()
                
                # 系统信息特性
                elif path == "/api/system/info":
                    self._handle_sys_info()
                elif path == "/api/software/info":
                    self._handle_software_info()           
                elif path == "/api/service/info":
                    self._handle_service_info()
                elif path == "/api/firmware/info":
                    self._handle_firmware_info()            
                # 处理未知路径
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
            
            def do_POST(self):
                """处理POST请求"""
                parsed_path = urlparse(self.path)
                path = parsed_path.path
                
                # 获取内容长度
                content_length = int(self.headers['Content-Length']) if 'Content-Length' in self.headers else 0
                
                # 读取请求正文
                post_data = self.rfile.read(content_length).decode('utf-8')

                self._logger.info(f"POST data: {post_data}")
                
                # 检查Content-Type
                content_type = self.headers.get('Content-Type', '')
                self._logger.info(f"Content-Type: {content_type}")
                
                # WiFi配置特性
                if path == "/api/wifi/config":
                    self._handle_wifi_config(post_data)
                
                # 系统命令特性（写操作）
                elif path == "/api/system/command":
                    self._handle_sys_command(post_data)          
                # 处理未知路径
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
            
            def _handle_wifi_status(self):
                """处理GET /api/wifi/status - 等同于WifiStatusCharacteristic"""
                # 从supervisor获取WiFi状态信息
                if hasattr(self._supervisor, 'wifi_status'):
                    wifi_status = self._supervisor.wifi_status
                    result = {
                        "connected": wifi_status.connected,
                        "ssid": wifi_status.ssid,
                        "ip_address": wifi_status.ip_address,
                        "mac_address": wifi_status.mac_address,
                        "error_message": wifi_status.error_message if hasattr(wifi_status, 'error_message') else ""
                    }
                else:
                    # 如果supervisor没有WiFi信息，返回默认值
                    result = {
                        "connected": False,
                        "ssid": "",
                        "ip_address": "",
                        "mac_address": "",
                        "error_message": "WiFi information not available"
                    }
                
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())
            
            def _handle_sys_info(self):
                """处理GET /api/system/info - 等同于SystemInfoCharacteristic"""
                result = {}
                # 从supervisor获取系统信息
                if hasattr(self._supervisor, 'system_info') and self._supervisor.system_info:
                    system_info = self._supervisor.system_info
                    result = {
                        "Device Model": system_info.model,
                        "Device Name": system_info.name,
                        "Current Version": system_info.version,
                        "Build Number": system_info.build_number,
                        "Uptime": int(time.time() - self._supervisor.start_time) if hasattr(self._supervisor, 'start_time') else 0
                    }
                else:
                    # 默认系统信息
                    result = {
                        "model": "LinuxBox",
                        "version": "1.0.0",
                        "name": "3RHUB-Unknown",
                        "uptime": int(time.time() - self._supervisor.start_time) if hasattr(self._supervisor, 'start_time') else 0
                    }
                
                if hasattr(self._supervisor, 'wifi_status'):
                    wifi_status = self._supervisor.wifi_status
                    result['Connected'] = wifi_status.connected
                    result['SSID'] = wifi_status.ssid
                    result['Ip Address'] = wifi_status.ip_address
                    result['Mac Address'] = wifi_status.mac_address
                    
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())
            

            def _handle_software_info(self): 
                homeassistant_core_result={
                    'name': 'Home Assistant'
                }
                zigbee2mqtt_result = {
                    'name': 'zigbee2mqtt'
                }
                homekitbridge_result = {
                    'name': 'Homekit Bridge'
                }

                if hasattr(self._supervisor, 'system_info') and self._supervisor.system_info:
                    system_info = self._supervisor.system_info
                    homeassistant_core_items = [
                        {
                            "name": "hacore-config",
                            "version":system_info.hainfo.config
                        },
                        {
                            "name": "python3",
                            "version":system_info.hainfo.python
                        },
                        {
                            "name": "hacore",
                            "version":system_info.hainfo.core
                        },
                        {
                            "name": "otbr-agent",
                            "version":system_info.hainfo.otbr
                        },                                                                        
                    ]

                    homeassistant_core_result['installed'] = system_info.hainfo.installed
                    homeassistant_core_result['enabled'] = system_info.hainfo.enabled
                    homeassistant_core_result['software'] = homeassistant_core_items

                    zigbee2mqtt_items = [
                        {
                            "name": "zigbee2mqt",
                            "version":system_info.z2minfo.zigbee2mqtt
                        },
                    ]
                    zigbee2mqtt_result['installed'] = system_info.z2minfo.installed
                    zigbee2mqtt_result['enabled'] = system_info.z2minfo.enabled
                    zigbee2mqtt_result['software'] = zigbee2mqtt_items

                    homekitbridge_items = [
                    ]
                    homekitbridge_result['installed'] = system_info.hbinfo.installed
                    homekitbridge_result['enabled'] = system_info.hbinfo.enabled
                    homekitbridge_result['software'] = homekitbridge_items

                result = {
                    "homeassistant_core":homeassistant_core_result,
                    "zigbee2mqtt":zigbee2mqtt_result,
                    "homekitbridge":homekitbridge_result
                }

                self._set_headers()
                self.wfile.write(json.dumps(result).encode())                

            def _handle_service_info(self): 
                homeassistant_core_result={
                    'name': 'Home Assistant'
                }
                zigbee2mqtt_result = {
                    'name': 'zigbee2mqtt'
                }
                homekitbridge_result = {
                    'name': 'Homekit Bridge'
                }

                # Define and check Home Assistant services
                homeassistant_core_services_status = []
                homeassistant_core_services = [
                    "home-assistant.service",                                                  
                    "matter-server.service",
                    "otbr-agent.service",
                ]

                # Check status of each Home Assistant service
                for service in homeassistant_core_services:
                    is_running = utils.is_service_running(service)
                    is_enabled = utils.is_service_enabled(service)
                    service_info = {
                        "name": service,
                        "running": is_running,
                        "enabled": is_enabled
                    }
                    homeassistant_core_services_status.append(service_info)
                
                homeassistant_core_result['service'] = homeassistant_core_services_status

                # Define and check Zigbee2MQTT services
                zigbee2mqtt_services_status = []
                zigbee2mqtt_services = [
                    "zigbee2mqtt.service"
                ]

                # Check status of each Zigbee2MQTT service
                for service in zigbee2mqtt_services:
                    is_running = utils.is_service_running(service)
                    is_enabled = utils.is_service_enabled(service)
                    service_info = {
                        "name": service,
                        "running": is_running,
                        "enabled": is_enabled
                    }
                    zigbee2mqtt_services_status.append(service_info)

                zigbee2mqtt_result['service'] = zigbee2mqtt_services_status

                # Define and check HomeKit Bridge services
                homekitbridge_services_status = []
                homekitbridge_services = [
                    "homekit-bridge.service"
                ]

                # Check status of each HomeKit Bridge service
                for service in homekitbridge_services:
                    is_running = utils.is_service_running(service)
                    is_enabled = utils.is_service_enabled(service)
                    service_info = {
                        "name": service,
                        "running": is_running,
                        "enabled": is_enabled
                    }
                    homekitbridge_services_status.append(service_info)

                homekitbridge_result['service'] = homekitbridge_services_status

                # Create final result
                result = {
                    "homeassistant_core": homeassistant_core_result,
                    "zigbee2mqtt": zigbee2mqtt_result,
                    "homekitbridge": homekitbridge_result
                }

                self._set_headers()
                self.wfile.write(json.dumps(result).encode())                  

            def _handle_firmware_info(self):
                homeassistant_core_result={
                    'name': 'Home Assistant'
                }
                zigbee2mqtt_result = {
                    'name': 'zigbee2mqtt'
                }
                homekitbridge_result = {
                    'name': 'Homekit Bridge'
                }

                result = {
                    "homeassistant_core":homeassistant_core_result,
                    "zigbee2mqtt":zigbee2mqtt_result,
                    "homekitbridge":homekitbridge_result
                }
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())                

            def _handle_wifi_config(self, post_data):
                """处理POST /api/wifi/config - 等同于WifiConfigCharacteristic"""
                try:
                    data = json.loads(post_data)
                    ssid = data.get("ssid", "")
                    password = data.get("password", "")
                    
                    if not ssid:
                        self._send_error("SSID is required")
                        return
                    
                    # 调用supervisor的WiFi配置方法
                    if hasattr(self._supervisor, 'configure_wifi') and callable(self._supervisor.configure_wifi):
                        success = self._supervisor.configure_wifi(ssid, password)
                        if success:
                            self._set_headers()
                            self.wfile.write(json.dumps({"success": True}).encode())
                        else:
                            self._send_error("Failed to configure WiFi")
                    else:
                        # 如果supervisor没有WiFi配置方法，返回错误
                        self._send_error("WiFi configuration not supported")
                
                except json.JSONDecodeError:
                    self._send_error("Invalid JSON")
                except Exception as e:
                    self._send_error(f"Error: {str(e)}")
            
            def _handle_sys_command(self, post_data):
                try:
                    # 解析POST数据（k=v&k=v的格式）
                    self._logger.info(f"Processing system command with data: {post_data}")
                    
                    # 解析参数
                    params = {}
                    signature = None
                    
                    # 按&分割参数
                    param_pairs = post_data.split('&')
                    for pair in param_pairs:
                        if '=' in pair:
                            key, value = pair.split('=', 1)
                            if key == '_sig':
                                signature = value
                            else:
                                params[key] = value
                    
                    # 验证必须有command参数
                    if 'command' not in params:
                        self._send_error("Command is required")
                        return
                    
                    # 验证签名
                    if not signature:
                        self._send_error("Signature is required")
                        return
                    
                    # 按key排序并重新组装参数字符串（不包含_sig）
                    sorted_keys = sorted(params.keys())
                    param_string = '&'.join([f"{k}={params[k]}" for k in sorted_keys])
                    
                    # 添加安全密钥并计算MD5
                    security_string = f"{param_string}&ThirdReality"
                    calculated_md5 = hashlib.md5(security_string.encode()).hexdigest()
                    
                    self._logger.info(f"Calculated signature: {calculated_md5}")
                    
                    # 验证签名
                    if calculated_md5 != signature:
                        self._logger.warning("Security verification failed: Invalid signature")
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Unauthorized: Invalid signature"}).encode())
                        return
                    
                    # 签名验证通过，处理命令
                    command = params.get("command", "")
                    
                    # 处理系统命令
                    if command == "reboot":
                        # 直接调用supervisor的重启方法
                        self._supervisor.set_led_state(LedState.REBOOT)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                        threading.Timer(3.0, self._supervisor.perform_reboot).start()
                    
                    elif command == "factory_reset":
                        # 直接调用supervisor的出厂重置方法
                        self._supervisor.set_led_state(LedState.FACTORY_RESET)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                        threading.Timer(3.0, self._supervisor.perform_factory_reset).start()
                    elif command == "delete_networks":
                        # 直接调用supervisor的删除网络配置方法
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                        threading.Timer(1.0, self._supervisor.perform_delete_networks).start()                        
                    elif command == "prepare_wifi_provision":
                        # 直接调用supervisor的准备配网方法
                        restore_need = utils.is_service_running("home-assistant")
                        result = {
                            "restore": restore_need,
                            "success": True,
                        }

                        self._set_headers()
                        self.wfile.write(json.dumps(result).encode())
                        if restore_need:
                            threading.Timer(1.0, self._supervisor.perform_wifi_provision_prepare).start()
                    elif command == "hello_world":
                        self._set_headers()
                        result = {
                            "model": "LinuxBox",
                            "success": True,
                            "msg": "Hello ThirdReality"
                        }
                        self.wfile.write(json.dumps(result).encode())
                                        
                    else:
                        self._send_error(f"Unknown command: {command}")
                
                except Exception as e:
                    self._logger.error(f"Error processing system command: {str(e)}")
                    self._send_error(f"Error: {str(e)}")
            
            def _send_error(self, message):
                """发送错误响应"""
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": message}).encode())
        
        return LinuxBoxHTTPHandler

def signal_handler(signum, frame):
    """处理终止信号"""
    logging.getLogger("Supervisor").info(f"Received signal {signum}, exiting HTTP server gracefully...")
    http_server.stop()
    sys.exit(0)

if __name__ == "__main__":
    # 测试服务器
    from supervisor import Supervisor
    supervisor = Supervisor()
    supervisor.init()
    
    # 定义信号处理器，用于清理退出终止信号
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    http_server = SupervisorHTTPServer(supervisor)
    http_server.start()
    
    try:
        # 保持主线程活跃
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        http_server.stop()
        supervisor.cleanup()
        print("HTTP server stopped")

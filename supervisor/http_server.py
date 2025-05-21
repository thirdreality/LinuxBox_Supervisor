"""
HTTP Server for LinuxBox Finder that mirrors the functionality of the BLE GATT server.
This server runs when WiFi is connected and provides the same APIs as the BLE service.
"""

import json
import hashlib
import threading
import time
import base64
import urllib.parse
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import signal
import sys
import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor

from .utils import utils
from .hardware import LedState

from .sysinfo import get_package_version

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
        self.thread_pool = ThreadPoolExecutor(max_workers=5)  # 创建线程池用于处理文件下载
    
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
            self.thread_pool.shutdown(wait=False)  # 关闭线程池
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
                query_params = parse_qs(parsed_path.query)
                
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
                # 处理带有服务名称参数的服务信息请求
                elif path.startswith("/api/service/info/"):
                    service_name = path.split("/")[-1]
                    self._handle_service_info(service_name)
                elif path == "/api/firmware/info":
                    self._handle_firmware_info()
                # 处理文件下载请求
                elif path == "/api/example/file_node":
                    self._handle_file_download(query_params)
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
                                
                # 系统命令特性（写操作）
                if path == "/api/system/command":
                    self._handle_sys_command(post_data)
                elif path == "/api/service/control":
                    self._handle_service_command(post_data)
                elif path == "/api/software/command":
                    self._handle_software_command(post_data)                               
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
                        "Build Number": system_info.build_number,
                        "Zigbee Support": system_info.support_zigbee,
                        "Thread Support": system_info.support_thread,
                        "Memory": f"{system_info.memory_size} MB",
                        "Storage": f"{system_info.storage_space['available']}/{system_info.storage_space['total']}"   
                    }
                else:
                    # 默认系统信息
                    result = {
                        "model": "LinuxBox",
                        "version": "1.0.0",
                        "name": "3RHUB-Unknown"
                    }
                
                if hasattr(self._supervisor, 'wifi_status'):
                    wifi_status = self._supervisor.wifi_status
                    result['WIFI Connected'] = wifi_status.connected
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

                    if not system_info.hainfo.config:
                        system_info.hainfo.config = get_package_version("thirdreality-hacore-config")
                    if not system_info.hainfo.python:
                        system_info.hainfo.python = get_package_version("thirdreality-python3")
                    if not system_info.hainfo.core:
                        system_info.hainfo.core = get_package_version("thirdreality-hacore")
                    if not system_info.hainfo.otbr:
                        system_info.hainfo.otbr = get_package_version("thirdreality-otbr-agent")
                        
                    # 如果所有四个包版本都不为空，则设置installed和enabled为True
                    if (system_info.hainfo.config and system_info.hainfo.python and 
                        system_info.hainfo.core and system_info.hainfo.otbr):
                        system_info.hainfo.installed = True
                        system_info.hainfo.enabled = True

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

            def _handle_service_info(self, service_name=None): 
                """处理服务信息请求，可选择指定特定服务"""
                # 定义服务配置
                service_configs = {
                    "homeassistant_core": {
                        "name": "Home Assistant",
                        "services": [
                            "home-assistant.service",
                            "matter-server.service",
                            "otbr-agent.service"
                        ]
                    },
                    "zigbee2mqtt": {
                        "name": "zigbee2mqtt",
                        "services": [
                            "zigbee2mqtt.service"
                        ]
                    },
                    "homekitbridge": {
                        "name": "Homekit Bridge",
                        "services": [
                            "homekit-bridge.service"
                        ]
                    }
                }
                
                # 如果指定了服务名称但不存在，返回404
                if service_name and service_name not in service_configs:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Service '{service_name}' not found"}).encode())
                    return
                
                # 确定需要处理的服务
                services_to_process = [service_name] if service_name else service_configs.keys()
                
                # 结果字典
                result = {}
                
                # 处理每个服务
                for service_key in services_to_process:
                    config = service_configs[service_key]
                    service_result = {
                        "name": config["name"]
                    }
                    
                    # 检查服务状态
                    services_status = []
                    for service in config["services"]:
                        is_running = utils.is_service_running(service)
                        is_enabled = utils.is_service_enabled(service)
                        service_info = {
                            "name": service,
                            "running": is_running,
                            "enabled": is_enabled
                        }
                        services_status.append(service_info)
                    
                    service_result["service"] = services_status
                    result[service_key] = service_result
                
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
                    elif command == "hello_world":
                        self._set_headers()
                        result = {
                            "model": const.DEVICE_MODEL_NAME,
                            "success": True,
                            "msg": "Hello ThirdReality"
                        }
                        self.wfile.write(json.dumps(result).encode())
                                        
                    else:
                        self._send_error(f"Unknown command: {command}")
                
                except Exception as e:
                    self._logger.error(f"Error processing system command: {str(e)}")
                    self._send_error(f"Error: {str(e)}")
            

            def _handle_software_command(self, post_data):
                try:
                    # 解析POST数据（k=v&k=v的格式）
                    self._logger.info(f"Processing Software Package command with data: {post_data}")
                    
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
                    if 'action' not in params:
                        self._send_error("action is required")
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
                    action = params.get("action", "")
                    param = params.get("param", "")
                    
                    # 处理param参数
                    package_name = ""
                    if param:
                        try:
                            # 1. URL解码
                            url_decoded = urllib.parse.unquote(param)
                            self._logger.info(f"URL decoded param: {url_decoded}")
                            
                            # 2. Base64解码
                            base64_decoded = base64.b64decode(url_decoded).decode('utf-8')
                            self._logger.info(f"Base64 decoded param: {base64_decoded}")
                            
                            # 3. 解析JSON
                            param_json = json.loads(base64_decoded)
                            self._logger.info(f"Parsed JSON param: {param_json}")
                            
                            # 4. 提取package和service信息
                            package_name = param_json.get("package", "")
                            self._logger.info(f"Extracted package: {package_name}")
                        except Exception as e:
                            self._logger.error(f"Error processing param: {e}")
                            self._send_error(f"Invalid param format: {str(e)}")
                            return
                    
                    # 处理系统命令
                    if action == "install":
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())                    
                    elif action == "uninstall":
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                    elif action == "enable":
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())                   
                    elif action == "disable":
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())    
                    elif action == "upgrade":
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())   
                    else:
                        self._send_error(f"Unknown action: {action}")
                
                except Exception as e:
                    self._logger.error(f"Error processing system command: {str(e)}")
                    self._send_error(f"Error: {str(e)}")

            def _handle_service_command(self, post_data):
                try:
                    # 解析POST数据（k=v&k=v的格式）
                    self._logger.info(f"Processing Service with data: {post_data}")
                    
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
                    
                    # 验证必须有action参数
                    if 'action' not in params:
                        self._send_error("action is required")
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
                    action = params.get("action", "")
                    param = params.get("param", "")
                    
                    # 处理param参数
                    package_name = ""
                    service_name = ""
                    if param:
                        try:
                            # 1. URL解码
                            url_decoded = urllib.parse.unquote(param)
                            self._logger.info(f"URL decoded param: {url_decoded}")
                            
                            # 2. Base64解码
                            base64_decoded = base64.b64decode(url_decoded).decode('utf-8')
                            self._logger.info(f"Base64 decoded param: {base64_decoded}")
                            
                            # 3. 解析JSON
                            param_json = json.loads(base64_decoded)
                            self._logger.info(f"Parsed JSON param: {param_json}")
                            
                            # 4. 提取package和service信息
                            package_name = param_json.get("package", "")
                            service_name = param_json.get("service", "")
                            self._logger.info(f"Extracted package: {package_name}, service: {service_name}")
                        except Exception as e:
                            self._logger.error(f"Error processing param: {e}")
                            self._send_error(f"Invalid param format: {str(e)}")
                            return
                    
                    # 处理系统命令
                    if not service_name:
                        self._send_error("Service name is required")
                        return
                        
                    result = {"success": False, "message": ""}
                    
                    try:
                        if action == "enable":
                            # 启用服务
                            self._logger.info(f"Enabling service: {service_name}")
                            process = subprocess.run(["systemctl", "enable", service_name], 
                                                    capture_output=True, text=True, check=False)
                            
                            if process.returncode == 0:
                                result["success"] = True
                                result["message"] = f"Service {service_name} enabled successfully"
                            else:
                                result["message"] = f"Failed to enable service: {process.stderr}"
                                
                        elif action == "disable":
                            # 禁用服务
                            self._logger.info(f"Disabling service: {service_name}")
                            process = subprocess.run(["systemctl", "disable", service_name], 
                                                    capture_output=True, text=True, check=False)
                            
                            if process.returncode == 0:
                                result["success"] = True
                                result["message"] = f"Service {service_name} disabled successfully"
                            else:
                                result["message"] = f"Failed to disable service: {process.stderr}"
                                
                        elif action == "start":
                            # 启动服务
                            self._logger.info(f"Starting service: {service_name}")
                            process = subprocess.run(["systemctl", "start", service_name], 
                                                    capture_output=True, text=True, check=False)
                            
                            if process.returncode == 0:
                                result["success"] = True
                                result["message"] = f"Service {service_name} started successfully"
                            else:
                                result["message"] = f"Failed to start service: {process.stderr}"
                                
                        elif action == "stop":
                            # 停止服务
                            self._logger.info(f"Stopping service: {service_name}")
                            process = subprocess.run(["systemctl", "stop", service_name], 
                                                    capture_output=True, text=True, check=False)
                            
                            if process.returncode == 0:
                                result["success"] = True
                                result["message"] = f"Service {service_name} stopped successfully"
                            else:
                                result["message"] = f"Failed to stop service: {process.stderr}"
                        else:
                            self._send_error(f"Unknown action: {action}")
                            return
                            
                    except Exception as e:
                        self._logger.error(f"Error executing systemctl command: {e}")
                        result["message"] = f"Error: {str(e)}"
                    
                    # 返回结果
                    self._set_headers()
                    self.wfile.write(json.dumps(result).encode())
                
                except Exception as e:
                    self._logger.error(f"Error processing system command: {str(e)}")
                    self._send_error(f"Error: {str(e)}")


            def _send_error(self, message):
                """发送错误响应"""
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": message}).encode())
                
            def _handle_file_download(self, query_params):
                """处理文件下载请求，只允许下载/home目录下的文件"""
                # 获取文件路径参数
                if 'file_path' not in query_params:
                    self._send_error("Missing file_path parameter")
                    return
                    
                file_path = query_params['file_path'][0]
                
                # 验证文件路径是否在/home目录下
                real_path = os.path.realpath(file_path)  # 解析符号链接并获取绝对路径
                if not real_path.startswith('/home/'):
                    self._logger.warning(f"Attempted to access restricted file: {file_path}")
                    self._send_error("Access denied: Only files in /home directory can be downloaded")
                    return
                
                # 检查文件是否存在
                if not os.path.isfile(real_path):
                    self._send_error(f"File not found: {file_path}")
                    return
                
                # 创建一个线程来处理文件下载，但在当前请求上下文中完成响应
                # 这样可以保证响应头和数据在同一个请求处理流程中发送
                try:
                    # 获取文件大小
                    file_size = os.path.getsize(real_path)
                    
                    # 获取文件名
                    file_name = os.path.basename(real_path)
                    
                    # 确定文件的MIME类型
                    content_type, _ = mimetypes.guess_type(real_path)
                    if content_type is None:
                        content_type = 'application/octet-stream'
                    
                    # 设置响应头
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Content-Disposition', f'attachment; filename="{file_name}"')
                    self.send_header('Access-Control-Allow-Origin', '*')  # 启用CORS
                    self.end_headers()
                    
                    # 启动一个后台线程来处理大文件的读取和发送，避免阻塞主线程
                    def send_file_in_background():
                        try:
                            # 读取并发送文件内容
                            with open(real_path, 'rb') as file:
                                # 分块读取和发送文件，避免内存问题
                                chunk_size = 8192  # 8KB 块
                                while True:
                                    chunk = file.read(chunk_size)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                            
                            self._logger.info(f"File downloaded successfully: {real_path}")
                        except Exception as e:
                            self._logger.error(f"Error sending file {real_path}: {str(e)}")
                    
                    # 直接在当前线程中发送文件，而不是提交到线程池
                    # 这样可以确保响应头和数据在同一个请求上下文中处理
                    with open(real_path, 'rb') as file:
                        # 分块读取和发送文件，避免内存问题
                        chunk_size = 8192  # 8KB 块
                        while True:
                            chunk = file.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    
                    self._logger.info(f"File downloaded successfully: {real_path}")
                    
                except Exception as e:
                    self._logger.error(f"Error downloading file {real_path}: {str(e)}")
                    # 如果还没有发送响应头，则发送错误
                    try:
                        self._send_error(f"Error downloading file: {str(e)}")
                    except:
                        pass  # 可能已经发送了部分响应，忽略错误
        
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

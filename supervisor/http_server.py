# maintainer: guoping.liu@3reality.com

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

from .utils import util
from .hardware import LedState

from .sysinfo import get_package_version

# 使用与supervisor相同的logger

class SupervisorHTTPServer:
    """HTTP Server that integrates with Supervisor for shared state"""
    
    # 集中管理API密钥和安全配置
    API_SECRET_KEY = "ThirdReality"  # 理想情况下应从环境变量或配置文件加载
    MAX_RETRIES = 3  # 服务器错误最大重试次数
    RETRY_DELAY = 5  # 重试延迟（秒）
    ALLOWED_DOWNLOAD_PATHS = ["/home/"]  # 允许下载的路径前缀
    
    def __init__(self, supervisor, port=8086):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self.port = port
        self.server = None
        self.server_thread = None
        self.running = threading.Event()
        self.thread_pool = ThreadPoolExecutor(max_workers=5)  # 创建线程池用于处理文件下载
        self.start_time = time.time()  # 记录启动时间，用于健康检查
    
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
        """在一个单独的线程中运行HTTP服务器，添加重试机制"""
        retry_count = 0
        
        while self.running.is_set():
            try:
                self.server.serve_forever()
            except Exception as e:
                if self.running.is_set():  # 仅当仍应该运行时记录错误
                    retry_count += 1
                    if retry_count > self.MAX_RETRIES:
                        self.logger.error(f"HTTP Server failed after {self.MAX_RETRIES} retries: {e}")
                        self.running.clear()  # 停止服务器
                        # 通知supervisor HTTP服务器失败
                        if hasattr(self.supervisor, 'on_http_server_failure'):
                            self.supervisor.on_http_server_failure(e)
                        break
                    
                    self.logger.warning(f"HTTP Server error (retry {retry_count}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY)  # 使用配置的延迟时间
    
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
                try:
                    # 解析URL和查询参数
                    parsed_url = urllib.parse.urlparse(self.path)
                    path = parsed_url.path
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    
                    self._logger.info(f"GET request: {path}")
                    
                    # 处理不同的API端点
                    if path == "/api/wifi/status":
                        self._handle_wifi_status()
                    elif path == "/api/system/info":
                        self._handle_system_info()
                    elif path == "/api/software/info":
                        self._handle_software_info()
                    elif path.startswith("/api/service/info"):
                        # 支持两种方式获取服务名：
                        # 1. 通过路径: /api/service/info/服务名
                        # 2. 通过查询参数: /api/service/info?service=服务名
                        path_parts = path.split('/')
                        if len(path_parts) > 4 and path_parts[4]:  # 通过路径获取
                            service_name = path_parts[4]
                            self._logger.info(f"Getting service info for {service_name} (via path)")
                            self._handle_service_info(service_name)
                        else:  # 尝试通过查询参数获取
                            service_name = query_params.get('service', [None])[0]
                            if service_name:
                                self._logger.info(f"Getting service info for {service_name} (via query param)")
                                self._handle_service_info(service_name)
                            else:
                                # 没有提供服务名，返回所有服务的信息
                                self._logger.info("No service name provided, returning info for all services")
                                self._handle_service_info(None)
                    elif path == "/api/zigbee/info":
                        self._handle_zigbee_info()
                    elif path == "/api/browser/info":
                        self._handle_browser_info()       
                    elif path == "/api/example/node":
                        self._handle_file_download(query_params)
                    elif path == "/api/setting/info":
                        self._handle_setting_info()
                    elif path == "/api/health" or path == "/health":
                        # 处理健康检查请求
                        self._handle_health_check()
                    elif path.startswith('/api/task/info'):
                        query_components = parse_qs(urlparse(self.path).query)
                        task_type = query_components.get("task", [None])[0]

                        if not task_type:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Missing 'task' query parameter."}).encode())
                            return

                        if task_type not in ["zigbee", "thread", "setting"]:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": f"Invalid task type: {task_type}"}).encode())
                            return
                        
                        task_info = self._supervisor.task_manager.get_task_info(task_type)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True, "task": task_type, "data": task_info}).encode())
                    else:
                        # 返回404 Not Found
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        
                except Exception as e:
                    self._logger.error(f"Error handling GET request: {str(e)}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Internal Server Error"}).encode())
            
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
                # elif path == "/api/software/command":
                #     self._handle_software_command(post_data)
                # elif path == "/api/zigbee/command":
                #     self._handle_zigbee_command(post_data)
                # elif path == "/api/setting/command":
                #     self._handle_setting_command(post_data)
                # 处理未知路径
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
            
            def _handle_wifi_status(self):
                """处理GET /api/wifi/status - 等同于WifiStatusCharacteristic"""
                # 默认结果
                result = {
                    "connected": False,
                    "ssid": "",
                    "ip_address": "",
                    "mac_address": "",
                    "message": "WiFi information not available"
                }
                
                # 从管理器获取WiFi状态信息
                if hasattr(self._supervisor, 'wifi_status'):
                    wifi_status = self._supervisor.wifi_status
                    result = {
                        "connected": wifi_status.connected,
                        "ssid": wifi_status.ssid,
                        "ip_address": wifi_status.ip_address,
                        "mac_address": wifi_status.mac_address,
                        "message": wifi_status.error_message if hasattr(wifi_status, 'error_message') else ""
                    }
                
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())
            
            def _handle_system_info(self):
                """处理GET /api/system/info - 等同于SystemInfoCharacteristic"""
                result = {}
                try:
                    # 优先从supervisor获取系统信息
                    if hasattr(self._supervisor, 'system_info') and self._supervisor.system_info:
                        system_info = self._supervisor.system_info
                        # 安全获取 storage_space
                        storage = system_info.storage_space if isinstance(system_info.storage_space, dict) else {"available": "", "total": ""}
                        result = {
                            "Device Model": getattr(system_info, "model", "Unknown"),
                            "Device Name": getattr(system_info, "name", "Unknown"),
                            "Version": getattr(system_info, "version", "Unknown"),
                            "Build Number": getattr(system_info, "build_number", "Unknown"),
                            "Zigbee Support": getattr(system_info, "support_zigbee", False),
                            "Thread Support": getattr(system_info, "support_thread", False),
                            "Memory": f"{getattr(system_info, 'memory_size', '')} MB",
                            "Storage": f"{storage.get('available', '')}/{storage.get('total', '')}"
                        }
                    else:
                        # 默认系统信息（统一风格）
                        result = {
                            "Device Model": "LinuxBox",
                            "Device Name": "3RHUB-Unknown",
                            "Build Number": "1.0.0",
                            "Zigbee Support": False,
                            "Thread Support": False,
                            "Memory": "",
                            "Storage": "/"
                        }

                    # WiFi 信息
                    if hasattr(self._supervisor, 'wifi_status') and self._supervisor.wifi_status:
                        wifi_status = self._supervisor.wifi_status
                        result["WIFI Connected"] = getattr(wifi_status, "connected", False)
                        result["SSID"] = getattr(wifi_status, "ssid", "")
                        result["Ip Address"] = getattr(wifi_status, "ip_address", "")
                        result["Mac Address"] = getattr(wifi_status, "mac_address", "")
                except Exception as e:
                    # 捕获异常，返回最简系统信息和错误提示
                    result = {
                        "Device Model": "LinuxBox",
                        "Device Name": "3RHUB-Unknown",
                        "Build Number": "1.0.0",
                        "Error": f"system info error: {e}"
                    }
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())


            def _handle_setting_info(self):
                """返回最近5个backup文件信息"""
                backup_dir = "/lib/thirdreality/backup"
                if not os.path.exists(backup_dir):
                    os.makedirs(backup_dir) 
                
                files = []
                try:
                    if os.path.isdir(backup_dir):
                        all_files = [f for f in os.listdir(backup_dir) if os.path.isfile(os.path.join(backup_dir, f))]
                        # 按修改时间排序，取最新5个
                        all_files.sort(key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)), reverse=True)
                        files = all_files[:5]
                    result = {"backups": files}
                except Exception as e:
                    result = {"error": str(e)}
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())


            def _handle_browser_info(self):
                """查询 HomeAssistant/zigbee2mqtt 服务状态并返回可访问URL"""
                browser_url = []
                error_message = ""
                try:
                    # 获取当前IP地址
                    ip = ""
                    if hasattr(self._supervisor, 'wifi_status') and self._supervisor.wifi_status:
                        ip = getattr(self._supervisor.wifi_status, 'ip_address', "")
                    if not ip:
                        raise Exception("No IP address available")

                    # 检查服务状态
                    def is_service_active(service):
                        try:
                            result = subprocess.run([
                                "systemctl", "is-active", service
                            ], capture_output=True, text=True)
                            return result.returncode == 0 and result.stdout.strip() == "active"
                        except Exception:
                            return False

                    # HomeAssistant
                    if is_service_active("home-assistant.service"):
                        browser_url.append({
                            "name": "HomeAssistant",
                            "url": f"http://{ip}:8123"
                        })
                    # zigbee2mqtt
                    if is_service_active("zigbee2mqtt.service"):
                        browser_url.append({
                            "name": "zigbee2mqtt",
                            "url": f"http://{ip}:8099"
                        })
                    if not browser_url:
                        error_message = "No browser services are currently running."
                except Exception as e:
                    error_message = f"Error: {e}"
                result = {
                    "browser_url": browser_url,
                    "message": error_message
                }
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())

            def _handle_software_info(self): 
                homeassistant_core_result={
                    'name': 'Home Assistant'
                }
                openhab_result = {
                    'name': 'OpenHAB'
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
                        {
                            "name": "zigbee-mqtt",
                            "version":system_info.hainfo.z2m
                        }, 
                    ]

                    homeassistant_core_result['installed'] = system_info.hainfo.installed
                    homeassistant_core_result['enabled'] = system_info.hainfo.enabled

                    # # get_ha_zigbee_mode
                    # zigbee_mode = util.get_ha_zigbee_mode()
                    # if zigbee_mode == "z2m":
                    #     homeassistant_core_result['zigbee'] = "z2m"
                    # elif zigbee_mode == "zha":
                    #     homeassistant_core_result['zigbee'] = "zha"
                    # else:
                    #     homeassistant_core_result['zigbee'] = "none"

                    homeassistant_core_result['software'] = homeassistant_core_items

                    openhab_items = [
                    ]
                    
                    openhab_result['installed'] = system_info.openhabinfo.installed
                    openhab_result['enabled'] = system_info.openhabinfo.enabled
                    openhab_result['software'] = openhab_items

                result = {
                    "homeassistant_core":homeassistant_core_result,
                    "openhab":openhab_result,
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
                            "otbr-agent.service",
                            "mosquitto.service",
                            "zigbee2mqtt.service"
                        ]
                    },
                    "openhab": {
                        "name": "openhab",
                        "services": [
                            "openhab.service"
                        ]
                    },
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
                        is_running = util.is_service_running(service)
                        is_enabled = util.is_service_enabled(service)
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

            def _handle_zigbee_info(self):
                """处理Zigbee信息请求，返回Zigbee的模式（zha、z2m或none）"""
                try:
                    from supervisor.utils.zigbee_util import get_ha_zigbee_mode
                    zigbee_mode = get_ha_zigbee_mode()
                    result = {"zigbee": zigbee_mode}
                    self._set_headers()
                    self.wfile.write(json.dumps(result).encode())
                except Exception as e:
                    self._logger.error(f"Error getting Zigbee info: {e}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
            
            def _handle_health_check(self):
                """处理健康检查请求，返回服务器状态信息"""
                # 计算服务器运行时间
                uptime_seconds = time.time() - self._supervisor.http_server.start_time
                uptime_str = self._format_uptime(uptime_seconds)
                
                # 获取系统资源信息
                mem_info = self._get_memory_info()
                cpu_load = self._get_cpu_load()
                disk_usage = self._get_disk_usage()
                
                # 组装健康状态响应
                health_status = {
                    "status": "ok",
                    "version": self._supervisor.system_info.build_number if hasattr(self._supervisor, 'system_info') else "unknown",
                    "uptime": uptime_str,
                    "uptime_seconds": int(uptime_seconds),
                    "timestamp": int(time.time()),
                    "resources": {
                        "memory": mem_info,
                        "cpu": cpu_load,
                        "disk": disk_usage
                    }
                }
                
                # 返回健康状态响应
                self._set_headers()
                self.wfile.write(json.dumps(health_status).encode())
            
            def _format_uptime(self, seconds):
                """格式化运行时间"""
                days, remainder = divmod(int(seconds), 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                if days > 0:
                    return f"{days}d {hours}h {minutes}m {seconds}s"
                elif hours > 0:
                    return f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    return f"{minutes}m {seconds}s"
                else:
                    return f"{seconds}s"
            
            def _get_memory_info(self):
                """获取内存使用情况"""
                try:
                    with open('/proc/meminfo', 'r') as f:
                        mem_info = {}
                        for line in f:
                            if 'MemTotal' in line or 'MemFree' in line or 'MemAvailable' in line:
                                key, value = line.split(':', 1)
                                value = value.strip().split()[0]  # 去除单位，只保留数字
                                mem_info[key.strip()] = int(value)
                        
                        # 计算内存使用百分比
                        if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:
                            used = mem_info['MemTotal'] - mem_info['MemAvailable']
                            mem_info['UsedPercent'] = round(used / mem_info['MemTotal'] * 100, 1)
                        
                        return mem_info
                except Exception as e:
                    self._logger.error(f"Error getting memory info: {e}")
                    return {"error": str(e)}
            
            def _get_cpu_load(self):
                """获取CPU负载"""
                try:
                    with open('/proc/loadavg', 'r') as f:
                        load = f.read().strip().split()
                        return {
                            "load_1min": float(load[0]),
                            "load_5min": float(load[1]),
                            "load_15min": float(load[2])
                        }
                except Exception as e:
                    self._logger.error(f"Error getting CPU load: {e}")
                    return {"error": str(e)}
            
            def _get_disk_usage(self):
                """获取磁盘使用情况"""
                try:
                    # 使用df命令获取磁盘使用情况
                    process = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, check=False)
                    if process.returncode == 0:
                        lines = process.stdout.strip().split('\n')
                        if len(lines) >= 2:  # 至少有标题行和数据行
                            parts = lines[1].split()
                            if len(parts) >= 5:
                                return {
                                    "filesystem": parts[0],
                                    "size": parts[1],
                                    "used": parts[2],
                                    "available": parts[3],
                                    "use_percent": parts[4]
                                }
                    
                    # 如果上面的方法失败，尝试使用statvfs
                    import os
                    st = os.statvfs('/')
                    total = st.f_blocks * st.f_frsize
                    free = st.f_bfree * st.f_frsize
                    used = total - free
                    return {
                        "total_bytes": total,
                        "used_bytes": used,
                        "free_bytes": free,
                        "use_percent": round(used / total * 100, 1)
                    }
                except Exception as e:
                    self._logger.error(f"Error getting disk usage: {e}")
                    return {"error": str(e)}                
            
            def _handle_sys_command(self, post_data):
                """处理系统命令请求，使用共用签名验证方法"""
                try:
                    self._logger.info(f"Processing system command with data: {post_data}")
                    
                    # 使用共用方法解析和验证参数
                    params, signature, is_valid = self._parse_post_data(post_data)
                    
                    # 验证必须有command参数
                    if 'command' not in params:
                        self._send_error("Command is required")
                        return
                    
                    # 验证签名
                    if not signature:
                        self._send_error("Signature is required")
                        return
                    
                    # 验证签名有效性
                    if not is_valid:
                        self._logger.warning("Security verification failed: Invalid signature")
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Unauthorized: Invalid signature"}).encode())
                        return
                    
                    # 签名验证通过，处理命令
                    command = params.get("command", "")
                    self._logger.info(f"Processing validated command: {command}")

                    action = params.get("action", "")

                    param_data_base64 = params.get("param", "")
                    param_dict = {}
                    if param_data_base64:
                        try:
                            param_data_url_decoded = urllib.parse.unquote(param_data_base64)
                            param_data_json = base64.b64decode(param_data_url_decoded).decode()
                            self._logger.info(f"param_data (decoded): {param_data_json}")
                            param_dict = json.loads(param_data_json)
                            action = param_dict.get('action')
                        except Exception as e:
                            self._logger.error(f"param解码失败: {e}")
                            self._set_headers()
                            self.wfile.write(json.dumps({"success": False, "error": "param decode error"}).encode())
                            return

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

                    elif command == "stop_wifi_provision":
                        # 直接调用supervisor的停止Wi-Fi配置方法
                        self._logger.info("HTTP Server: Received stop_wifi_provision command.")
                        success = self._supervisor.finish_wifi_provision() # This method already runs in a thread
                        self._set_headers()
                        # The success here indicates the command was received and initiated, 
                        # not necessarily that Wi-Fi provisioning has fully stopped yet.
                        self.wfile.write(json.dumps({"success": success, "message": "Wi-Fi provisioning stop initiated."}).encode())

                    elif command == "zigbee":
                        if action == "zha":
                            self._logger.info("[Zigbee] Attempting to switch mode to: zha")
                            if self._supervisor.start_zigbee_switch_zha():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Successfully started switch to ZHA mode."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start switch to ZHA mode."}).encode())
                        elif action == "z2m":
                            self._logger.info(f"[Zigbee] Attempting to switch mode to: z2m")
                            if self._supervisor.start_zigbee_switch_z2m():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Successfully started switch to Z2M mode."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start switch to Z2M mode."}).encode())
                        elif action == "disable":
                            self._logger.warning("[Zigbee] 'disable' action is not yet implemented.")
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "The 'disable' action is not yet implemented."}).encode())
                        else:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Invalid action for zigbee command."}).encode())

                    elif command == "setting":
                        backup_dir = "/lib/thirdreality/backup"
                        if not os.path.exists(backup_dir):
                            os.makedirs(backup_dir)

                        if action == "backup":
                            self._logger.info("[Setting] Backup requested")
                            if self._supervisor.start_setting_backup():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Backup process started successfully."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start backup process."}).encode())

                        elif action == "restore":
                            filename = param_dict.get("file")
                            if not filename:
                                if self._supervisor.start_setting_restore():
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": "Restore process started successfully."}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": "Failed to start restore process."}).encode())
                                return

                            backup_file_path = os.path.join(backup_dir, filename)
                            if not os.path.isfile(backup_file_path):
                                self._set_headers(status_code=404)
                                self.wfile.write(json.dumps({"success": False, "error": "Backup file not found."}).encode())
                                return
                            
                            self._logger.info(f"[Setting] Restore from {filename} requested")
                            # NOTE: Assuming start_setting_restore knows which file to use,
                            # as its signature in supervisor.py takes no arguments.
                            if self._supervisor.start_setting_restore():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": f"Restore from {filename} started successfully."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start restore process."}).encode())
                        
                        else:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Invalid action for setting command."}).encode())

                    elif command == "hello_world":
                        self._set_headers()
                        result = {
                            "model": const.DEVICE_MODEL_NAME if 'const' in globals() else "Unknown",
                            "success": True,
                            "msg": "Hello ThirdReality"
                        }
                        self.wfile.write(json.dumps(result).encode())
                                        
                    else:
                        self._send_error(f"Unknown command: {command}")
                
                except Exception as e:
                    self._logger.error(f"Error processing system command: {str(e)}")
                    self._send_error(f"Error: {str(e)}")
                    # 添加详细的异常跟踪


            # def _handle_zigbee_command(self, post_data):
            #     """切换zigbee模式: action=zha, z2m, disable"""
            #     try:
            #         params = json.loads(post_data) if post_data.strip().startswith('{') else dict(urllib.parse.parse_qsl(post_data))
            #         action = params.get('action')
            #         if action not in ('zha', 'z2m', 'disable'):
            #             self._set_headers()
            #             self.wfile.write(json.dumps({"success": False, "error": "Invalid action"}).encode())
            #             return
            #         # 这里只写日志，实际切换逻辑需后续实现
            #         self._logger.info(f"[Zigbee] Switch mode to: {action}")
            #         # TODO: 调用supervisor方法或具体切换实现
            #         self._set_headers()
            #         self.wfile.write(json.dumps({"success": True, "mode": action}).encode())
            #     except Exception as e:
            #         self._set_headers()
            #         self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())


            # def _handle_setting_command(self, post_data):
            #     """处理系统备份和恢复: action=backup, restore, restore可带filename"""
            #     try:
            #         params = json.loads(post_data) if post_data.strip().startswith('{') else dict(urllib.parse.parse_qsl(post_data))
            #         action = params.get('action')
            #         filename = params.get('filename')
            #         backup_dir = "/lib/thirdreality/backup"
            #         if not os.path.exists(backup_dir):
            #             os.makedirs(backup_dir) 

            #         if action == 'backup':
            #             # 这里只写日志，实际备份逻辑需后续实现
            #             self._logger.info("[Setting] Backup requested")
            #             # TODO: 调用supervisor方法或具体实现备份
            #             self._set_headers()
            #             self.wfile.write(json.dumps({"success": True, "msg": "Backup started"}).encode())
            #         elif action == 'restore':
            #             if filename:
            #                 self._logger.info(f"[Setting] Restore from {filename}")
            #                 # TODO: 调用supervisor方法或具体实现恢复
            #                 found = False
            #                 if os.path.isfile(os.path.join(backup_dir, filename)):
            #                     found = True
            #                 if found:
            #                     self._set_headers()
            #                     self.wfile.write(json.dumps({"success": True, "msg": f"Restore from {filename} started"}).encode())
            #                 else:
            #                     self._set_headers()
            #                     self.wfile.write(json.dumps({"success": False, "error": "Backup file not found"}).encode())
            #             else:
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": False, "error": "No filename specified"}).encode())
            #         else:
            #             self._set_headers()
            #             self.wfile.write(json.dumps({"success": False, "error": "Invalid action"}).encode())
            #     except Exception as e:
            #         self._set_headers()
            #         self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())


            def _handle_service_command(self, post_data):
                """处理服务控制命令，使用集中的签名验证逻辑"""
                try:
                    self._logger.info(f"Processing service command with data: {post_data}")
                    # 解析POST参数和签名
                    params, signature, is_valid = self._parse_post_data(post_data)
                    # 校验action参数
                    if 'action' not in params:
                        self._send_error("action is required")
                        return
                    # 校验签名
                    if not signature:
                        self._send_error("Signature is required")
                        return
                    if not is_valid:
                        self._logger.warning("Security verification failed: Invalid signature")
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Unauthorized: Invalid signature"}).encode())
                        return
                    action = params['action']
                    service_name = params.get('service')

                    # 如果params中没有service，尝试从param_json中解析
                    if not service_name and 'param' in params:
                        try:
                            param_data_base64 = params['param']
                            import urllib.parse, base64
                            param_data_url_decoded = urllib.parse.unquote(param_data_base64)
                            param_data_json = base64.b64decode(param_data_url_decoded).decode()
                            self._logger.info(f"param_data (decoded): {param_data_json}")
                            param_dict = json.loads(param_data_json)
                            service_name = param_dict.get('service')
                            self._logger.info(f"service (from param_data): {service_name}")
                        except Exception as e:
                            self._logger.error(f"Failed to parse param_data for service: {e}")

                    self._logger.info(f"action: {action}")
                    self._logger.info(f"service: {service_name}")

                    if not service_name:
                        self._send_error("Service name is required")
                        return
                    result = {"success": False}
                    try:
                        if action == "enable":
                            self._logger.info(f"Enabling service: {service_name}")
                            process = subprocess.run(["systemctl", "enable", service_name], capture_output=True, text=True)
                            result = {"success": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr}
                        elif action == "disable":
                            self._logger.info(f"Disabling service: {service_name}")
                            process = subprocess.run(["systemctl", "disable", service_name], capture_output=True, text=True)
                            result = {"success": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr}
                        elif action == "start":
                            self._logger.info(f"Starting service: {service_name}")
                            process = subprocess.run(["systemctl", "start", service_name], capture_output=True, text=True)
                            result = {"success": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr}
                        elif action == "stop":
                            self._logger.info(f"Stopping service: {service_name}")
                            process = subprocess.run(["systemctl", "stop", service_name], capture_output=True, text=True)
                            result = {"success": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr}
                        else:
                            result = {"success": False, "error": f"Unknown action: {action}"}
                        self._set_headers()
                        self.wfile.write(json.dumps(result).encode())
                    except Exception as e:
                        self._logger.error(f"Error executing systemctl command: {e}")
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
                except Exception as e:
                    self._logger.error(f"Error in _handle_service_command: {str(e)}")
                    self._set_headers()
                    self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())

            # def _handle_software_command(self, post_data):
            #     """处理软件命令请求，使用共用签名验证方法"""
            #     try:
            #         self._logger.info(f"Processing software command with data: {post_data}")
                    
            #         # 使用共用方法解析和验证参数
            #         params, signature, is_valid = self._parse_post_data(post_data)
                    
            #         # 验证必须有command参数
            #         if 'command' not in params:
            #             self._send_error("Command is required")
            #             return
                    
            #         # 验证签名
            #         if not signature:
            #             self._send_error("Signature is required")
            #             return
                    
            #         # 验证签名有效性
            #         if not is_valid:
            #             self._logger.warning("Security verification failed: Invalid signature")
            #             self.send_response(401)
            #             self.send_header('Content-type', 'application/json')
            #             self.end_headers()
            #             self.wfile.write(json.dumps({"error": "Unauthorized: Invalid signature"}).encode())
            #             return
                    
            #         # 签名验证通过，处理命令
            #         command = params.get("command", "")
            #         self._logger.info(f"Processing validated software command: {command}")
                    
            #         try:
            #             # 处理软件命令
            #             if command == "update":
            #                 # 获取软件包URL
            #                 if 'url' not in params:
            #                     self._send_error("URL is required for update command")
            #                     return
                            
            #                 url = params['url']
                            
            #                 # 验证URL是否有效
            #                 if not url.startswith('http'):
            #                     self._send_error("Invalid URL format")
            #                     return
                            
            #                 # TODO: Implement update logic here
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Update started"}).encode())
                            
            #             elif command == "install":
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Install command received"}).encode())                    
            #             elif command == "uninstall":
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Uninstall command received"}).encode())
            #             elif command == "enable":
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Enable command received"}).encode())                   
            #             elif command == "disable":
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Disable command received"}).encode())
            #             elif command == "upgrade":
            #                 self._set_headers()
            #                 self.wfile.write(json.dumps({"success": True, "message": "Upgrade command received"}).encode())
            #             else:
            #                 self._send_error(f"Unknown command: {command}")
            #         except Exception as e:
            #             self._logger.error(f"Error executing systemctl command: {e}")
            #             self._logger.error(traceback.format_exc())
            #             self._send_error(f"Error executing command: {str(e)}")                                
            #     except Exception as e:
            #         self._logger.error(f"Error processing software command: {str(e)}")
            #         self._logger.error(traceback.format_exc())
            #         self._send_error(f"Error processing command: {str(e)}")


            def _verify_signature(self, params, signature):
                """验证请求签名
                            
                Args:
                    params: 参数字典
                    signature: 请求提供的签名
                                
                Returns:
                    bool: 签名是否有效
                """
                try:
                    # 获取API密钥
                    secret_key = self._supervisor.http_server.API_SECRET_KEY
                                
                    # 按key排序并重新组装参数字符串
                    sorted_keys = sorted(params.keys())
                    param_string = '&'.join([f"{k}={params[k]}" for k in sorted_keys])
                                
                    # 添加安全密钥并计算MD5
                    security_string = f"{param_string}&{secret_key}"
                    calculated_md5 = hashlib.md5(security_string.encode()).hexdigest()
                                
                    self._logger.debug(f"Signature verification: expected={calculated_md5}, received={signature}")
                                
                    return calculated_md5 == signature
                except Exception as e:
                    self._logger.error(f"Error verifying signature: {e}")
                    return False
            
            def _parse_post_data(self, post_data):
                """解析POST数据并验证签名
                    
                Args:
                    post_data: POST请求数据
                        
                Returns:
                    tuple: (params, signature, is_valid)
                """
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
                    
                # 验证签名
                if not signature:
                    return params, signature, False
                    
                is_valid = self._verify_signature(params, signature)
                return params, signature, is_valid
                    
            def _send_error(self, message):
                """发送错误响应"""
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": message}).encode())
                    
            def _handle_file_download(self, query_params):
                """处理文件下载请求，增强安全性检查"""
                # 获取文件路径参数
                if 'file_path' not in query_params:
                    self._send_error("Missing file_path parameter")
                    return
                        
                file_path = query_params['file_path'][0]
                    
                # 更严格的路径验证
                real_path = os.path.realpath(file_path)  # 解析符号链接并获取绝对路径
                    
                # 检查是否在允许的路径中
                allowed = False
                for allowed_path in self._supervisor.http_server.ALLOWED_DOWNLOAD_PATHS:
                    if real_path.startswith(allowed_path):
                        allowed = True
                        break
                            
                if not allowed:
                    self._logger.warning(f"Security: Attempted to access restricted file: {file_path}")
                    self._send_error("Access denied: File access restricted to allowed directories")
                    return
                    
                # 检查文件是否存在且是常规文件
                if not os.path.isfile(real_path):
                    self._send_error(f"File not found: {file_path}")
                    return
                        
                # 检查文件大小限制
                try:
                    file_size = os.path.getsize(real_path)
                    max_size = 100 * 1024 * 1024  # 100MB限制
                        
                    if file_size > max_size:
                        self._logger.warning(f"File too large for download: {file_path} ({file_size} bytes)")
                        self._send_error(f"File too large for download. Maximum size is 100MB.")
                        return
                            
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
                        
                    # 使用线程池处理大文件传输
                    def send_file_content():
                        try:
                            with open(real_path, 'rb') as file:
                                chunk_size = 8192  # 8KB 块
                                while True:
                                    chunk = file.read(chunk_size)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                            self._logger.info(f"File downloaded successfully: {real_path}")
                            return True
                        except Exception as e:
                            self._logger.error(f"Error sending file {real_path}: {str(e)}")
                            return False
                        
                    # 对于小文件，直接在当前线程中发送
                    # 对于大文件，使用线程池
                    if file_size < 1024 * 1024:  # 1MB以下的文件直接发送
                        send_file_content()
                    else:
                        # 对于大文件，使用线程池处理
                        self._supervisor.http_server.thread_pool.submit(send_file_content)
                        
                except Exception as e:
                    self._logger.error(f"Error preparing file download {real_path}: {str(e)}")
                    # 如果还没有发送响应头，则发送错误
                    try:
                        self._send_error(f"Error preparing file download: {str(e)}")
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

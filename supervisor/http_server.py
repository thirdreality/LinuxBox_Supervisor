"""
HTTP Server for LinuxBox Finder that mirrors the functionality of the BLE GATT server.
This server runs when WiFi is connected and provides the same APIs as the BLE service.
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import signal
import sys
import logging

# 使用与supervisor相同的logger
logger = logging.getLogger("Supervisor")

class SupervisorHTTPServer:
    """HTTP Server that integrates with Supervisor for shared state"""
    
    def __init__(self, supervisor, port=8086):
        self.supervisor = supervisor
        self.port = port
        self.server = None
        self.server_thread = None
        self.running = threading.Event()
    
    def start(self):
        """启动HTTP服务器"""
        if self.server:
            logger.warning("HTTP Server already running")
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
            
            logger.info(f"HTTP Server started on port {self.port}")
            
            # 如果supervisor有存储的IP地址，记录URL
            if hasattr(self.supervisor, 'wifi_info') and self.supervisor.wifi_info.get('ip_address'):
                ip = self.supervisor.wifi_info.get('ip_address')
                logger.info(f"HTTP Server accessible at: http://{ip}:{self.port}/")
        
        except Exception as e:
            logger.error(f"Failed to start HTTP Server: {e}")
    
    def stop(self):
        """停止HTTP服务器"""
        if self.server:
            self.running.clear()
            self.server.shutdown()
            self.server = None
            logger.info("HTTP Server stopped")
    
    def _run_server(self):
        """在一个单独的线程中运行HTTP服务器"""
        while self.running.is_set():
            try:
                self.server.serve_forever()
            except Exception as e:
                if self.running.is_set():  # 仅当仍应该运行时记录错误
                    logger.error(f"HTTP Server error: {e}")
                    time.sleep(5)  # 等待一段时间再重试
    
    def _create_handler(self):
        """创建一个能访问supervisor的HTTP请求处理器类"""
        supervisor = self.supervisor
        
        class LinuxBoxHTTPHandler(BaseHTTPRequestHandler):
            """HTTP请求处理器"""
            
            # 存储对supervisor的引用
            _supervisor = supervisor
            
            # 重写日志方法，使用我们的logger
            def log_message(self, format, *args):
                logger.info(f"{self.client_address[0]} - {format % args}")
            
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
                if hasattr(self._supervisor, 'wifi_info') and self._supervisor.wifi_info:
                    wifi_info = self._supervisor.wifi_info
                    result = {
                        "connected": wifi_info.get('connected', False),
                        "ssid": wifi_info.get('ssid', ''),
                        "ip_address": wifi_info.get('ip_address', ''),
                        "mac_address": wifi_info.get('mac_address', ''),
                        "error_message": wifi_info.get('error_message', '')
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
                # 从supervisor获取系统信息
                if hasattr(self._supervisor, 'system_info') and self._supervisor.system_info:
                    system_info = self._supervisor.system_info
                    result = system_info
                else:
                    # 默认系统信息
                    result = {
                        "model": "LinuxBox",
                        "version": "1.0.0",
                        "hostname": "linuxbox",
                        "uptime": int(time.time() - self._supervisor.start_time) if hasattr(self._supervisor, 'start_time') else 0
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
                """处理POST /api/system/command - 等同于SystemCommandCharacteristic"""
                try:
                    data = json.loads(post_data)
                    command = data.get("command", "")
                    
                    if not command:
                        self._send_error("Command is required")
                        return
                    
                    # 处理系统命令
                    if command == "reboot":
                        # 调用supervisor的重启方法
                        if hasattr(self._supervisor, '_perform_reboot') and callable(self._supervisor._perform_reboot):
                            self._set_headers()
                            self.wfile.write(json.dumps({"success": True}).encode())
                            # 设置定时任务，在响应发送后重启
                            threading.Timer(1.0, self._supervisor._perform_reboot).start()
                        else:
                            self._send_error("Reboot not supported")
                    
                    elif command == "factory_reset":
                        # 调用supervisor的出厂重置方法
                        if hasattr(self._supervisor, '_perform_factory_reset') and callable(self._supervisor._perform_factory_reset):
                            self._set_headers()
                            self.wfile.write(json.dumps({"success": True}).encode())
                            # 设置定时任务，在响应发送后重置
                            threading.Timer(1.0, self._supervisor._perform_factory_reset).start()
                        else:
                            self._send_error("Factory reset not supported")
                    
                    else:
                        self._send_error(f"Unknown command: {command}")
                
                except json.JSONDecodeError:
                    self._send_error("Invalid JSON")
                except Exception as e:
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
    print(f"Received signal {signum}, exiting HTTP server gracefully...")
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
    
    http_server = HTTPServer(supervisor)
    http_server.start()
    
    try:
        # 保持主线程活跃
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        http_server.stop()
        supervisor.cleanup()
        print("HTTP server stopped")

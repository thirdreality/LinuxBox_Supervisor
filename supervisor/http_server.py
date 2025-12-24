# maintainer: guoping.liu@3reality.com

"""
HTTP Server for LinuxBox Finder that mirrors the functionality of the BLE GATT server.
This server runs when WiFi is connected and provides the same APIs as the BLE service.
"""

import json
import hashlib
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
import threading
import glob
from concurrent.futures import ThreadPoolExecutor

from .utils import util
from .hardware import LedState
from . import const

from .sysinfo import get_package_version

# Static files directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

# 使用与supervisor相同的logger

class SupervisorHTTPServer:
    """HTTP Server that integrates with Supervisor for shared state"""
    
    # Centralized management of API secret keys and security configuration
    API_SECRET_KEY = "ThirdReality"  # Ideally should be loaded from environment variable or config file
    MAX_RETRIES = 3  # Maximum number of retries for server errors
    RETRY_DELAY = 5  # Retry delay (seconds)
    ALLOWED_DOWNLOAD_PATHS = ["/home/"]  # Allowed download path prefixes
    
    def __init__(self, supervisor, port=8086):
        self.logger = logging.getLogger("Supervisor")
        self.supervisor = supervisor
        self.port = port
        self.server = None
        self.running = threading.Event()
        self.thread_pool = ThreadPoolExecutor(max_workers=5)  # Create thread pool for file downloads
        self.start_time = time.time()  # Record start time for health check
    
    def start(self):
        """Start HTTP server"""
        if self.server:
            self.logger.warning("HTTP Server already running")
            return
        
        try:
            # Create a handler class with supervisor reference
            handler = self._create_handler()
            
            # Create and start server
            self.server = HTTPServer(("0.0.0.0", self.port), handler)
            self.running.set()
            
            # Run server in a separate thread
            self._run_server()
            
            self.logger.info(f"HTTP Server starting on port {self.port}")
            
            # If supervisor has stored IP address, log the URL
            if hasattr(self.supervisor, 'wifi_info') and self.supervisor.wifi_info.get('ip_address'):
                ip = self.supervisor.wifi_info.get('ip_address')
                self.logger.info(f"HTTP Server accessible at: http://{ip}:{self.port}/")
        
        except Exception as e:
            self.logger.error(f"Failed to start HTTP Server: {e}")
    
    def stop(self):
        """Stop HTTP server"""
        if self.server:
            self.running.clear()
            self.server.shutdown()
            self.server = None
            self.thread_pool.shutdown(wait=False)  # Shutdown thread pool
            self.logger.info("HTTP Server stopped")
    
    @util.threaded
    def _run_server(self):
        """Run HTTP server in a separate thread with retry mechanism"""
        retry_count = 0
        
        while self.running.is_set():
            try:
                self.server.serve_forever()
            except Exception as e:
                if self.running.is_set():  # Only log error if still supposed to run
                    retry_count += 1
                    if retry_count > self.MAX_RETRIES:
                        self.logger.error(f"HTTP Server failed after {self.MAX_RETRIES} retries: {e}")
                        self.running.clear()  # Stop server
                        # Notify supervisor HTTP server failure
                        if hasattr(self.supervisor, 'on_http_server_failure'):
                            self.supervisor.on_http_server_failure(e)
                        break
                    
                    self.logger.warning(f"HTTP Server error (retry {retry_count}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY)  # Use configured delay
    
    def _create_handler(self):
        """Create an HTTP request handler class with supervisor access"""
        supervisor = self.supervisor
        logger = self.logger
        
        class LinuxBoxHTTPHandler(BaseHTTPRequestHandler):
            """HTTP request handler"""
            
            # Store reference to supervisor
            _supervisor = supervisor
            _logger = logger
            
            # Override log method to use our logger
            def log_message(self, format, *args):
                self._logger.info(f"{self.client_address[0]} - {format % args}")
            
            def _set_headers(self, content_type="application/json", status_code=200):
                self.send_response(status_code)
                self.send_header('Content-type', content_type)
                self.send_header('Access-Control-Allow-Origin', '*')  # Enable CORS
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()
            
            def do_OPTIONS(self):
                """Handle CORS preflight request"""
                self._set_headers()
            
            def do_GET(self):
                """Handle GET request"""
                try:
                    # Parse URL and query parameters
                    parsed_url = urllib.parse.urlparse(self.path)
                    path = parsed_url.path
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    
                    self._logger.info(f"GET request: {path}")
                    
                    # Handle different API endpoints
                    if path == "/" or path == "/index.html":
                        self._serve_static_file("/index.html")
                    elif path.startswith("/static/"):
                        self._serve_static_file(path)
                    elif path == "/api/wifi/status":
                        self._handle_wifi_status()
                    elif path == "/api/system/info":
                        self._handle_system_info()
                    elif path == "/api/software/info":
                        self._handle_software_info()
                    elif path == "/api/v2/software/info":
                        self._handle_software_info_v2()
                    elif path.startswith("/api/service/info"):
                        # Support two ways to get service name:
                        # 1. By path: /api/service/info/<service_name>
                        # 2. By query param: /api/service/info?service=<service_name>
                        path_parts = path.split('/')
                        if len(path_parts) > 4 and path_parts[4]:  # By path
                            service_name = path_parts[4]
                            self._logger.info(f"Getting service info for {service_name} (via path)")
                            self._handle_service_info(service_name)
                        else:  # Try by query param
                            service_name = query_params.get('service', [None])[0]
                            if service_name:
                                self._logger.info(f"Getting service info for {service_name} (via query param)")
                                self._handle_service_info(service_name)
                            else:
                                # No service name provided, return info for all services
                                self._logger.info("No service name provided, returning info for all services")
                                self._handle_service_info(None)
                    elif path == "/api/zigbee/info":
                        self._handle_zigbee_info()
                    elif path == "/api/channel/info":
                        self._handle_channel_info(query_params)
                    elif path == "/api/browser/info":
                        self._handle_browser_info()       
                    elif path == "/api/example/node":
                        self._handle_file_download(query_params)
                    elif path == "/api/setting/info":
                        self._handle_setting_info()
                    elif path == "/api/health" or path == "/health":
                        # Handle health check request
                        self._handle_health_check()
                    elif path.startswith('/api/task/info'):
                        query_components = parse_qs(urlparse(self.path).query)
                        task_type = query_components.get("task", [None])[0]

                        if not task_type:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Missing 'task' query parameter."}).encode())
                            return

                        if task_type not in ["zigbee", "thread", "setting", "ota"]:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": f"Invalid task type: {task_type}"}).encode())
                            return
                        
                        task_info = self._supervisor.task_manager.get_task_info(task_type)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True, "task": task_type, "data": task_info}).encode())
                    else:
                        # Return 404 Not Found
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        
                except Exception as e:
                    self._logger.error(f"Error handling GET request: {str(e)}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Internal Server Error"}).encode())
            
            def _is_special_command(self, path, post_data):
                """Check if the POST request is a special system command that is allowed when console is disabled"""
                # OTA upgrade is always allowed
                if path == "/api/ota/upgrade":
                    return True
                
                # Setting update is always allowed
                if path == "/api/setting/update":
                    return True
                
                # System commands: reboot and factory_reset are allowed
                if path == "/api/system/command":
                    try:
                        params, _, _ = self._parse_post_data(post_data)
                        command = params.get("command", "")
                        return command in ("reboot", "factory_reset")
                    except Exception as e:
                        self._logger.warning(f"Failed to parse POST data for special command check: {e}")
                        return False
                
                return False

            def do_POST(self):
                """Handle POST request"""
                parsed_path = urlparse(self.path)
                path = parsed_path.path
                
                # Get content length
                content_length = int(self.headers['Content-Length']) if 'Content-Length' in self.headers else 0
                
                # Read request body
                post_data = self.rfile.read(content_length).decode('utf-8')

                self._logger.info(f"POST data: {post_data}")
                
                # Check if serial-getty@ttyAML0.service is masked or disabled
                # If masked/disabled, reject all POST requests except reboot command
                try:
                    result = subprocess.run(
                        ["systemctl", "is-enabled", "serial-getty@ttyAML0.service"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    enabled_status = result.stdout.strip()
                    # enabled-status can be: enabled, enabled-runtime, disabled, masked, static,
                    # indirect, generated, transient, alias, linked-runtime, linked, invalid
                    is_disabled = (
                        result.returncode != 0 or
                        enabled_status in ["disabled", "masked"] or
                        enabled_status not in ["enabled", "enabled-runtime"]
                    )
                    
                    if is_disabled and not self._is_special_command(path, post_data):
                        self._logger.warning(
                            f"serial-getty@ttyAML0.service status is '{enabled_status}', rejecting POST request"
                        )
                        self._set_headers(status_code=403)
                        self.wfile.write(json.dumps({
                            "success": False,
                            "error": "Action is not supported now."
                        }).encode())
                        return
                except Exception as e:
                    self._logger.warning(f"Failed to check serial-getty@ttyAML0.service status: {e}, allowing POST request")
                    # If check fails, allow the request (backward compatibility)
                
                # Check Content-Type
                content_type = self.headers.get('Content-Type', '')
                self._logger.info(f"Content-Type: {content_type}")
                                
                # System command feature (write operation)
                if path == "/api/system/command":
                    self._handle_sys_command(post_data)
                elif path == "/api/service/control":
                    self._handle_service_command(post_data)
                elif path == "/api/setting/update":
                    self._handle_setting_update(post_data)
                elif path == "/api/ota/upgrade":
                    self._handle_ota_upgrade(post_data)
                # elif path == "/api/software/command":
                #     self._handle_software_command(post_data)
                # elif path == "/api/zigbee/command":
                #     self._handle_zigbee_command(post_data)
                # elif path == "/api/setting/command":
                #     self._handle_setting_command(post_data)
                # Handle unknown path
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
            
            def _handle_wifi_status(self):
                """Handle GET /api/wifi/status - Equivalent to WifiStatusCharacteristic"""
                # Default result
                result = {
                    "connected": False,
                    "ssid": "",
                    "ip_address": "",
                    "mac_address": "",
                    "message": "WiFi information not available"
                }
                
                # Get WiFi status info from manager
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
                """Handle GET /api/system/info - Equivalent to SystemInfoCharacteristic"""
                result = {}
                try:
                    # Prefer to get system info from supervisor
                    if hasattr(self._supervisor, 'system_info') and self._supervisor.system_info:
                        system_info = self._supervisor.system_info
                        # Safely get storage_space
                        storage = system_info.storage_space if isinstance(system_info.storage_space, dict) else {"available": "", "total": ""}
                        result = {
                            "Device Model": getattr(system_info, "model", "Unknown"),
                            "Device Name": getattr(system_info, "name", "Unknown"),
                            "Model ID": getattr(system_info, "model_id", "Unknown"),
                            "Version": getattr(system_info, "version", "Unknown"),
                            "Build Number": getattr(system_info, "build_number", "Unknown"),
                            "Zigbee Support": getattr(system_info, "support_zigbee", False),
                            "Thread Support": getattr(system_info, "support_thread", True),
                            "Memory": f"{getattr(system_info, 'memory_size', '')} MB",
                            "Storage": f"{storage.get('available', '')}/{storage.get('total', '')}"
                        }
                    else:
                        # Default system info (unified style)
                        result = {
                            "Device Model": "LinuxBox",
                            "Device Name": "3RHUB-Unknown",
                            "Model ID": "3RLB01081MH",
                            "Build Number": "1.0.0",
                            "Zigbee Support": False,
                            "Thread Support": False,
                            "Memory": "",
                            "Storage": "/"
                        }

                    # WiFi info
                    if hasattr(self._supervisor, 'wifi_status') and self._supervisor.wifi_status:
                        wifi_status = self._supervisor.wifi_status
                        result["WIFI Connected"] = getattr(wifi_status, "connected", False)
                        result["SSID"] = getattr(wifi_status, "ssid", "")
                        result["Ip Address"] = getattr(wifi_status, "ip_address", "")
                        result["Mac Address"] = getattr(wifi_status, "mac_address", "")
                except Exception as e:
                    # Catch exception, return minimal system info and error message
                    result = {
                        "Device Model": "LinuxBox",
                        "Device Name": "3RHUB-Unknown",
                        "Model ID": "3RLB01081MH",
                        "Build Number": "1.0.0",
                        "Error": f"system info error: {e}"
                    }
                # Use cached installed services info from system_info
                try:
                    if hasattr(self._supervisor, 'system_info') and hasattr(self._supervisor.system_info, 'installed_services'):
                        result["Services"] = ",".join(self._supervisor.system_info.installed_services)
                    else:
                        result["Services"] = ""
                except Exception:
                    # If any error occurs, ensure the field exists
                    result["Services"] = ""
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())

            def _handle_setting_info(self):
                """Return info of the latest 5 backup files"""
                import supervisor.const as const
                
                # Determine backup directory based on configuration
                if const.BACKUP_STORAGE_MODE == "internal":
                    backup_dir = const.BACKUP_INTERNAL_PATH
                elif const.BACKUP_STORAGE_MODE == "external":
                    backup_dir = const.BACKUP_EXTERNAL_PATH
                else:
                    # Default to internal path for unknown modes
                    backup_dir = const.BACKUP_INTERNAL_PATH
                
                files = []
                try:
                    # Create backup directory if it doesn't exist
                    if not os.path.exists(backup_dir):
                        os.makedirs(backup_dir, exist_ok=True)
                        self._logger.info(f"Created backup directory: {backup_dir}")
                    
                    if os.path.isdir(backup_dir):
                        all_files = [f for f in os.listdir(backup_dir) if os.path.isfile(os.path.join(backup_dir, f)) and f.startswith("setting_") and f.endswith(".tar.gz")]
                        # Sort by modification time, get the latest 5
                        all_files.sort(key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)), reverse=True)
                        # Remove "setting_" prefix and ".tar.gz" suffix, only return timestamp
                        files = [f.replace("setting_", "").replace(".tar.gz", "") for f in all_files[:5]]
                        self._logger.debug(f"Found {len(files)} backup files in {backup_dir}")
                    else:
                        self._logger.warning(f"Backup directory is not accessible: {backup_dir}")
                    
                    result = {"backups": files, "backup_path": backup_dir, "storage_mode": const.BACKUP_STORAGE_MODE}
                except Exception as e:
                    self._logger.error(f"Error getting backup files from {backup_dir}: {e}")
                    result = {"error": str(e), "backup_path": backup_dir, "storage_mode": const.BACKUP_STORAGE_MODE}
                
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())

            def _handle_browser_info(self):
                """Query HomeAssistant/zigbee2mqtt service status and return accessible URLs"""
                browser_url = []
                error_message = ""
                try:
                    # Get current IP address
                    ip = ""
                    if hasattr(self._supervisor, 'wifi_status') and self._supervisor.wifi_status:
                        ip = getattr(self._supervisor.wifi_status, 'ip_address', "")
                    if not ip:
                        raise Exception("No IP address available")

                    # Check service status
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
                    if not system_info.hainfo.z2m:
                        system_info.hainfo.z2m = get_package_version("thirdreality-zigbee-mqtt")

                    # If all four package versions are not empty, set installed and enabled to True
                    if (system_info.hainfo.core and system_info.hainfo.python):
                        system_info.hainfo.installed = True
                        system_info.hainfo.enabled = True
                    else:
                        system_info.hainfo.installed = False
                        system_info.hainfo.enabled = False

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

            def _handle_software_info_v2(self):
                """Handle GET /api/v2/software/info - Query all predefined software versions"""
                self._logger.info("Handling /api/v2/software/info request")
                # 预定义的软件包列表
                predefined_packages = [
                    "linux-image-current-meson64",
                    "linuxbox-supervisor",
                    "thirdreality-bridge",
                    "thirdreality-board-firmware",
                    "thirdreality-hacore",
                    "thirdreality-zigbee-mqtt",
                    "thirdreality-otbr-agent",

                    "thirdreality-music-assistant",
                    "thirdreality-openhab",
                    "thirdreality-zwave",
                    "thirdreality-enocean",

                    "thirdreality-python3",
                ]
                
                result = {}
                
                for package in predefined_packages:
                    try:
                        version = get_package_version(package)
                        if version:  # 只有查询到版本才添加
                            result[package] = version
                    except Exception:
                        # 查询失败时静默跳过，不记录错误日志
                        pass
                
                self._logger.info(f"/api/v2/software/info result count={len(result)}")
                self._set_headers()
                self.wfile.write(json.dumps(result).encode())

            def _handle_service_info(self, service_name=None): 
                """Handle service info request, can specify a particular service"""
                # Define service config
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
                
                # If a service name is specified but does not exist, return 404
                if service_name and service_name not in service_configs:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Service '{service_name}' not found"}).encode())
                    return
                
                # Determine which services to process
                services_to_process = [service_name] if service_name else service_configs.keys()
                
                # Result dict
                result = {}
                
                # Process each service
                for service_key in services_to_process:
                    config = service_configs[service_key]
                    service_result = {
                        "name": config["name"]
                    }
                    
                    # Check service status
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
                """Handle Zigbee info request, return Zigbee mode (zha, z2m, or none)"""
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
            
            def _handle_channel_info(self, query_params):
                """Handle channel info request, return Zigbee and Thread channel information"""
                try:
                    from supervisor.channel_manager import ChannelManager
                    
                    channel_manager = ChannelManager()
                    
                    # Check if specific channel type is requested
                    channel_type = query_params.get('type', [None])[0]
                    
                    if channel_type:
                        # Return specific channel type
                        if channel_type not in ['zigbee', 'thread']:
                            self.send_response(400)
                            self.send_header('Content-type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({"error": f"Invalid channel type: {channel_type}. Must be 'zigbee' or 'thread'"}).encode())
                            return
                        
                        result = channel_manager.get_channel_by_type(channel_type)
                    else:
                        # Return all channels
                        result = channel_manager.get_all_channels()
                    
                    self._set_headers()
                    self.wfile.write(json.dumps(result).encode())
                    
                except Exception as e:
                    self._logger.error(f"Error getting channel info: {e}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
            
            def _handle_health_check(self):
                """Handle health check request, return server status info"""
                # Calculate server uptime
                uptime_seconds = time.time() - self._supervisor.http_server.start_time
                uptime_str = self._format_uptime(uptime_seconds)
                
                # Get system resource info
                mem_info = self._get_memory_info()
                cpu_load = self._get_cpu_load()
                disk_usage = self._get_disk_usage()
                
                # Assemble health status response
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
                
                # Return health status response
                self._set_headers()
                self.wfile.write(json.dumps(health_status).encode())
            
            def _format_uptime(self, seconds):
                """Format uptime string"""
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
                """Get memory usage info"""
                try:
                    with open('/proc/meminfo', 'r') as f:
                        mem_info = {}
                        for line in f:
                            if 'MemTotal' in line or 'MemFree' in line or 'MemAvailable' in line:
                                key, value = line.split(':', 1)
                                value = value.strip().split()[0]  # Remove unit, keep only number
                                mem_info[key.strip()] = int(value)
                        
                        # Calculate memory usage percent
                        if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:
                            used = mem_info['MemTotal'] - mem_info['MemAvailable']
                            mem_info['UsedPercent'] = round(used / mem_info['MemTotal'] * 100, 1)
                        
                        return mem_info
                except Exception as e:
                    self._logger.error(f"Error getting memory info: {e}")
                    return {"error": str(e)}
            
            def _get_cpu_load(self):
                """Get CPU load"""
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
                """Get disk usage info"""
                try:
                    # Use df command to get disk usage
                    process = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, check=False)
                    if process.returncode == 0:
                        lines = process.stdout.strip().split('\n')
                        if len(lines) >= 2:  # At least header and data line
                            parts = lines[1].split()
                            if len(parts) >= 5:
                                return {
                                    "filesystem": parts[0],
                                    "size": parts[1],
                                    "used": parts[2],
                                    "available": parts[3],
                                    "use_percent": parts[4]
                                }
                    
                    # If above method fails, try statvfs
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
                """Handle system command request, use shared signature verification method"""
                try:
                    self._logger.info(f"Processing system command with data: {post_data}")
                    
                    # Use shared method to parse and verify params
                    params, signature, is_valid = self._parse_post_data(post_data)
                    
                    # Must have command param
                    if 'command' not in params:
                        self._send_error("Command is required")
                        return
                    
                    # Must have signature
                    if not signature:
                        self._send_error("Signature is required")
                        return
                    
                    # Verify signature validity
                    if not is_valid:
                        self._logger.warning("Security verification failed: Invalid signature")
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Unauthorized: Invalid signature"}).encode())
                        return
                    
                    # Signature verified, process command
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
                            self._logger.error(f"param decode failed: {e}")
                            self._set_headers()
                            self.wfile.write(json.dumps({"success": False, "error": "param decode error"}).encode())
                            return

                    # Process system command
                    if command == "reboot":
                        # Directly call supervisor reboot method
                        self._supervisor.set_led_state(LedState.REBOOT)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                        threading.Timer(3.0, self._supervisor.perform_reboot).start()
                    
                    elif command == "factory_reset":
                        # Directly call supervisor factory reset method
                        self._supervisor.set_led_state(LedState.FACTORY_RESET)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": True}).encode())
                        threading.Timer(3.0, self._supervisor.perform_factory_reset).start()

                    elif command == "zigbee":
                        if action == "scan":
                            self._logger.info("[Zigbee] Attempting to start pairing scan")
                            if self._supervisor.start_zigbee_pairing():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Zigbee pairing process started."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start Zigbee pairing."}).encode())
                        elif action == "zha":
                            # Check if home-assistant.service exists
                            if not util.is_service_present("home-assistant.service"):
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": "ZHA mode is not supported: home-assistant.service is not installed"}).encode())
                                return
                            self._logger.info("[Zigbee] Attempting to switch mode to: zha")
                            if self._supervisor.start_zigbee_switch_zha():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Successfully started switch to ZHA mode."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start switch to ZHA mode."}).encode())
                        elif action == "z2m":
                            # Check if zigbee2mqtt.service exists
                            if not util.is_service_present("zigbee2mqtt.service"):
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": "Z2M mode is not supported: zigbee2mqtt.service is not installed"}).encode())
                                return
                            self._logger.info(f"[Zigbee] Attempting to switch mode to: z2m")
                            if self._supervisor.start_zigbee_switch_z2m():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Successfully started switch to Z2M mode."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start switch to Z2M mode."}).encode())
                        elif action.startswith("channel_"):
                            # Handle channel setting via action parameter
                            try:
                                channel = int(action.split("_")[1])
                                if channel < 11 or channel > 26:
                                    self._set_headers(status_code=400)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Invalid Zigbee channel: {channel}. Must be between 11 and 26. (Recommended: 15, 20, 25)"}).encode())
                                    return
                                
                                self._logger.info(f"[Zigbee] Attempting to set channel to: {channel}")
                                if self._supervisor.start_zigbee_channel_switch(channel):
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": f"Successfully started Zigbee channel switch to {channel}."}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Failed to start Zigbee channel switch to {channel}."}).encode())
                            except (ValueError, IndexError):
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": f"Invalid channel format in action: {action}"}).encode())
                        elif action == "disable":
                            self._logger.warning("[Zigbee] 'disable' action is not yet implemented.")
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "The 'disable' action is not yet implemented."}).encode())
                        else:
                            # 只支持新协议 param={"action":"channel", "value":"XX"}
                            if param_dict and param_dict.get("action") == "channel" and "value" in param_dict:
                                try:
                                    channel = int(param_dict["value"])
                                    if channel < 11 or channel > 26:
                                        self._set_headers(status_code=400)
                                        self.wfile.write(json.dumps({"success": False, "error": f"Invalid Zigbee channel: {channel}. Must be between 11 and 26. (Recommended: 15, 20, 25)"}).encode())
                                        return
                                    self._logger.info(f"[Zigbee] Attempting to set channel to: {channel} (via param)")
                                    if self._supervisor.start_zigbee_channel_switch(channel):
                                        self._set_headers()
                                        self.wfile.write(json.dumps({"success": True, "msg": f"Successfully started Zigbee channel switch to {channel}."}).encode())
                                    else:
                                        self._set_headers(status_code=500)
                                        self.wfile.write(json.dumps({"success": False, "error": f"Failed to start Zigbee channel switch to {channel}."}).encode())
                                except (ValueError, TypeError):
                                    self._set_headers(status_code=400)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Invalid channel value in param: {param_dict.get('value')}"}).encode())
                            else:
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": "Invalid action for zigbee command or param format."}).encode())

                    elif command == "thread":
                        # Check if otbr-agent.service exists
                        if not util.is_service_present("otbr-agent.service"):
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Thread channel switch is not supported: otbr-agent.service is not installed"}).encode())
                            return
                        if action and action.startswith("channel_"):
                            # Handle Thread channel setting via action parameter
                            try:
                                channel = int(action.split("_")[1])
                                if channel < 11 or channel > 26:
                                    self._set_headers(status_code=400)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Invalid Thread channel: {channel}. Must be between 11 and 26. (Recommended: 15, 20, 25)"}).encode())
                                    return
                                
                                self._logger.info(f"[Thread] Attempting to set channel to: {channel}")
                                if self._supervisor.start_thread_channel_switch(channel):
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": f"Successfully started Thread channel switch to {channel}. Note: Thread channel changes may take up to 300 seconds to take effect."}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Failed to start Thread channel switch to {channel}."}).encode())
                            except (ValueError, IndexError):
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": f"Invalid channel format in action: {action}"}).encode())
                        else:
                            # 只支持新协议 param={"action":"channel", "value":"XX"}
                            if param_dict and param_dict.get("action") == "channel" and "value" in param_dict:
                                try:
                                    channel = int(param_dict["value"])
                                    if channel < 11 or channel > 26:
                                        self._set_headers(status_code=400)
                                        self.wfile.write(json.dumps({"success": False, "error": f"Invalid Thread channel: {channel}. Must be between 11 and 26. (Recommended: 15, 20, 25)"}).encode())
                                        return
                                    self._logger.info(f"[Thread] Attempting to set channel to: {channel} (via param)")
                                    if self._supervisor.start_thread_channel_switch(channel):
                                        self._set_headers()
                                        self.wfile.write(json.dumps({"success": True, "msg": f"Successfully started Thread channel switch to {channel}. Note: Thread channel changes may take up to 300 seconds to take effect."}).encode())
                                    else:
                                        self._set_headers(status_code=500)
                                        self.wfile.write(json.dumps({"success": False, "error": f"Failed to start Thread channel switch to {channel}."}).encode())
                                except (ValueError, TypeError):
                                    self._set_headers(status_code=400)
                                    self.wfile.write(json.dumps({"success": False, "error": f"Invalid channel value in param: {param_dict.get('value')}"}).encode())
                            else:
                                self._set_headers(status_code=400)
                                self.wfile.write(json.dumps({"success": False, "error": "Invalid action for thread command or param format."}).encode())

                    elif command == "led":
                        # Fast LED control via HTTP: action on/off
                        if action == "on":
                            try:
                                if hasattr(self._supervisor, 'led') and hasattr(self._supervisor.led, 'enable'):
                                    self._supervisor.led.enable()
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": "LED enabled (on)"}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": "LED controller not available"}).encode())
                            except Exception as e:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
                        elif action == "off":
                            try:
                                if hasattr(self._supervisor, 'led') and hasattr(self._supervisor.led, 'disable'):
                                    self._supervisor.led.disable()
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": "LED disabled (off)"}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": "LED controller not available"}).encode())
                            except Exception as e:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
                        else:
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "Invalid action for led command. Use 'on' or 'off'."}).encode())

                    elif command == "setting":
                        # Determine backup directory based on configuration
                        if const.BACKUP_STORAGE_MODE == "internal":
                            backup_dir = const.BACKUP_INTERNAL_PATH
                        elif const.BACKUP_STORAGE_MODE == "external":
                            backup_dir = const.BACKUP_EXTERNAL_PATH
                        else:
                            # Default to internal path for unknown modes
                            backup_dir = const.BACKUP_INTERNAL_PATH
                        
                        if action == "backup":
                            self._logger.info("[Setting] Backup requested")
                            
                            # Check if USB storage is mounted at /mnt
                            if not os.path.ismount("/mnt"):
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "USB Storage missing"}).encode())
                                return
                            
                            if not os.path.exists(backup_dir):
                                os.makedirs(backup_dir)

                            if self._supervisor.start_setting_backup():
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": "Backup process started successfully."}).encode())
                            else:
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "Failed to start backup process."}).encode())

                        elif action == "restore":
                            # Check if USB storage is mounted at /mnt
                            if not os.path.ismount("/mnt"):
                                self._set_headers(status_code=500)
                                self.wfile.write(json.dumps({"success": False, "error": "USB Storage missing"}).encode())
                                return
                            
                            timestamp = param_dict.get("file")
                            
                            # Define restore record directory for both branches
                            restore_record_dir = "/usr/lib/thirdreality/conf"
                            
                            if not timestamp:
                                # No specific file, restore from latest backup
                                # Remove all restore records to allow user-initiated restore operation
                                restore_record_pattern = os.path.join(restore_record_dir, "restore_record_*.json")
                                removed_count = 0
                                for record_file in glob.glob(restore_record_pattern):
                                    try:
                                        os.remove(record_file)
                                        self._logger.info(f"Removed restore record: {record_file}")
                                        removed_count += 1
                                    except Exception as e:
                                        self._logger.warning(f"Failed to remove restore record {record_file}: {e}")
                                self._logger.info(f"Removed {removed_count} restore record(s) for latest backup restore")
                                
                                if self._supervisor.start_setting_restore():
                                    self._set_headers()
                                    self.wfile.write(json.dumps({"success": True, "msg": "Restore process started successfully."}).encode())
                                else:
                                    self._set_headers(status_code=500)
                                    self.wfile.write(json.dumps({"success": False, "error": "Failed to start restore process."}).encode())
                                return

                            # Convert timestamp to full filename
                            full_filename = f"setting_{timestamp}.tar.gz"
                            backup_file_path = os.path.join(backup_dir, full_filename)
                            if not os.path.isfile(backup_file_path):
                                self._set_headers(status_code=404)
                                self.wfile.write(json.dumps({"success": False, "error": f"Backup file {full_filename} not found."}).encode())
                                return
                            
                            self._logger.info(f"[Setting] Restore from {full_filename} (timestamp: {timestamp}) requested")
                            
                            # Remove restore record to allow user-initiated restore operation
                            restore_record_pattern = os.path.join(restore_record_dir, f"restore_record_{timestamp}.json")
                            if os.path.exists(restore_record_pattern):
                                try:
                                    os.remove(restore_record_pattern)
                                    self._logger.info(f"Removed existing restore record: {restore_record_pattern}")
                                except Exception as e:
                                    self._logger.warning(f"Failed to remove restore record {restore_record_pattern}: {e}")
                            else:
                                self._logger.info(f"No existing restore record found for {timestamp}")
                            
                            # Pass the timestamp to the restore function
                            if self._supervisor.start_setting_restore(timestamp):
                                self._set_headers()
                                self.wfile.write(json.dumps({"success": True, "msg": f"Restore from {full_filename} started successfully."}).encode())
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
                    # Add detailed exception trace

            def _handle_service_command(self, post_data):
                """Handle service control command, use centralized signature verification logic"""
                try:
                    self._logger.info(f"Processing service command with data: {post_data}")
                    # Parse POST params and signature
                    params, signature, is_valid = self._parse_post_data(post_data)
                    # Validate action param
                    if 'action' not in params:
                        self._send_error("action is required")
                        return
                    # Validate signature
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

                    # If params has no service, try to parse from param_json
                    if not service_name and 'param' in params:
                        try:
                            param_data_base64 = params['param']
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

            def _handle_setting_update(self, post_data):
                """Handle setting update request with signature verification and long task progress"""
                try:
                    # Parse and verify
                    params, signature, is_valid = self._parse_post_data(post_data)
                    if 'type' not in params:
                        self._send_error("type is required")
                        return
                    if 'param' not in params:
                        self._send_error("param is required")
                        return
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

                    update_type = params['type']
                    # param 还是经过 url-encode 和 base64 的，沿用统一解析逻辑
                    try:
                        param_data_base64 = params['param']
                        param_data_url_decoded = urllib.parse.unquote(param_data_base64)
                        param_data_json = base64.b64decode(param_data_url_decoded).decode()
                        self._logger.info(f"param_data (decoded): {param_data_json}")
                        param_dict = json.loads(param_data_json)
                    except Exception as e:
                        self._logger.error(f"param decode failed: {e}")
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": False, "error": "param decode error"}).encode())
                        return

                    # Dispatch different update types
                    if update_type == 'z2m-mqtt':
                        # Check if zigbee2mqtt.service exists
                        if not util.is_service_present("zigbee2mqtt.service"):
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "z2m-mqtt update is not supported: zigbee2mqtt.service is not installed"}).encode())
                            return
                        # Check if linuxbox-hubv3-bridge.service exists
                        if util.is_service_present("linuxbox-hubv3-bridge.service"):
                            self._set_headers(status_code=400)
                            self.wfile.write(json.dumps({"success": False, "error": "z2m-mqtt update is not supported: zlinuxbox-hubv3-bridge.service is installed"}).encode())
                            return                            
                        started = self._supervisor.task_manager.start_setting_update_z2m_mqtt(param_dict)
                        self._set_headers()
                        self.wfile.write(json.dumps({"success": bool(started), "task": "setting", "sub_task": "update_z2m_mqtt"}).encode())
                    else:
                        self._set_headers(status_code=400)
                        self.wfile.write(json.dumps({"success": False, "error": f"Unknown update type: {update_type}"}).encode())
                except Exception as e:
                    self._logger.error(f"Error in _handle_setting_update: {str(e)}")
                    self._set_headers()
                    self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())

            def _verify_signature(self, params, signature):
                """Verify request signature
                Args:
                    params: parameter dict
                    signature: signature provided by request
                Returns:
                    bool: whether signature is valid
                """
                try:
                    # Get API secret key
                    secret_key = self._supervisor.http_server.API_SECRET_KEY
                                
                    # Sort keys and reassemble param string
                    sorted_keys = sorted(params.keys())
                    param_string = '&'.join([f"{k}={params[k]}" for k in sorted_keys])
                                
                    # Add security key and calculate MD5
                    security_string = f"{param_string}&{secret_key}"
                    calculated_md5 = hashlib.md5(security_string.encode()).hexdigest()
                                
                    self._logger.debug(f"Signature verification: expected={calculated_md5}, received={signature}")
                                
                    return calculated_md5 == signature
                except Exception as e:
                    self._logger.error(f"Error verifying signature: {e}")
                    return False
            
            def _parse_post_data(self, post_data):
                """Parse POST data and verify signature
                Args:
                    post_data: POST request data
                Returns:
                    tuple: (params, signature, is_valid)
                """
                params = {}
                signature = None
                    
                # Split params by &
                param_pairs = post_data.split('&')
                for pair in param_pairs:
                    if '=' in pair:
                        key, value = pair.split('=', 1)
                        if key == '_sig':
                            signature = value
                        else:
                            params[key] = value
                    
                # Verify signature
                if not signature:
                    return params, signature, False
                    
                is_valid = self._verify_signature(params, signature)
                return params, signature, is_valid
                    
            def _send_error(self, message):
                """Send error response"""
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": message}).encode())
                    
            def _handle_file_download(self, query_params):
                """Handle file download request, enhance security check"""
                # Get file_path param
                if 'file_path' not in query_params:
                    self._send_error("Missing file_path parameter")
                    return
                        
                file_path = query_params['file_path'][0]
                    
                # Stricter path validation
                real_path = os.path.realpath(file_path)  # Resolve symlink and get absolute path
                    
                # Check if in allowed paths
                allowed = False
                for allowed_path in self._supervisor.http_server.ALLOWED_DOWNLOAD_PATHS:
                    if real_path.startswith(allowed_path):
                        allowed = True
                        break
                            
                if not allowed:
                    self._logger.warning(f"Security: Attempted to access restricted file: {file_path}")
                    self._send_error("Access denied: File access restricted to allowed directories")
                    return
                    
                # Check if file exists and is regular file
                if not os.path.isfile(real_path):
                    self._send_error(f"File not found: {file_path}")
                    return
                        
                # Check file size limit
                try:
                    file_size = os.path.getsize(real_path)
                    max_size = 100 * 1024 * 1024  # 100MB limit
                        
                    if file_size > max_size:
                        self._logger.warning(f"File too large for download: {file_path} ({file_size} bytes)")
                        self._send_error(f"File too large for download. Maximum size is 100MB.")
                        return
                            
                    # Get file name
                    file_name = os.path.basename(real_path)
                        
                    # Determine MIME type
                    content_type, _ = mimetypes.guess_type(real_path)
                    if content_type is None:
                        content_type = 'application/octet-stream'
                        
                    # Set response headers
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Content-Disposition', f'attachment; filename="{file_name}"')
                    self.send_header('Access-Control-Allow-Origin', '*')  # Enable CORS
                    self.end_headers()
                        
                    # Use thread pool for large file transfer
                    def send_file_content():
                        try:
                            with open(real_path, 'rb') as file:
                                chunk_size = 8192  # 8KB chunk
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
                    # For small files, send directly in current thread
                    # For large files, use thread pool
                    if file_size < 1024 * 1024:  # Files under 1MB send directly
                        send_file_content()
                    else:
                        # For large files, use thread pool
                        self._supervisor.http_server.thread_pool.submit(send_file_content)
                        
                except Exception as e:
                    self._logger.error(f"Error preparing file download {real_path}: {str(e)}")
                    # If response headers not sent yet, send error
                    try:
                        self._send_error(f"Error preparing file download: {str(e)}")
                    except:
                        pass  # May have already sent partial response, ignore error

            def _serve_static_file(self, path):
                """Serve static files from the static directory"""
                try:
                    # Security: prevent directory traversal
                    if '..' in path or path.startswith('//'):
                        self.send_response(403)
                        self.end_headers()
                        self.wfile.write(b"Forbidden")
                        return
                    
                    # Map path to file
                    if path == '/index.html' or path == '/':
                        file_path = os.path.join(STATIC_DIR, 'index.html')
                    else:
                        # Remove leading /static/ prefix
                        relative_path = path[8:] if path.startswith('/static/') else path[1:]
                        file_path = os.path.join(STATIC_DIR, relative_path)
                    
                    # Normalize and verify path is within STATIC_DIR
                    file_path = os.path.normpath(file_path)
                    if not file_path.startswith(os.path.normpath(STATIC_DIR)):
                        self.send_response(403)
                        self.end_headers()
                        self.wfile.write(b"Forbidden")
                        return
                    
                    # Check if file exists
                    if not os.path.isfile(file_path):
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return
                    
                    # Determine MIME type
                    content_type, _ = mimetypes.guess_type(file_path)
                    if content_type is None:
                        content_type = 'application/octet-stream'
                    
                    # Read and send file
                    with open(file_path, 'rb') as f:
                        content = f.read()
                    
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', len(content))
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(content)
                    
                except Exception as e:
                    self._logger.error(f"Error serving static file {path}: {e}")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"Internal Server Error")

            def _handle_ota_upgrade(self, post_data):
                """Handle OTA upgrade request"""
                try:
                    # Parse JSON body
                    try:
                        data = json.loads(post_data)
                    except json.JSONDecodeError:
                        self._set_headers(status_code=400)
                        self.wfile.write(json.dumps({"success": False, "error": "Invalid JSON"}).encode())
                        return
                    
                    # Support both old format (software) and new format (package, versionKey)
                    package_name = data.get('package') or data.get('software')
                    version_key = data.get('versionKey')
                    version = data.get('version')
                    release = data.get('release')
                    
                    if not package_name or not version or not release:
                        self._set_headers(status_code=400)
                        self.wfile.write(json.dumps({"success": False, "error": "Missing required parameters: package/software, version, release"}).encode())
                        return
                    
                    self._logger.info(f"[OTA] Upgrade request: package={package_name}, versionKey={version_key}, version={version}, release={release}")
                    
                    # Build download URL using versionKey if available, otherwise use package name
                    release_base_url = "https://github.com/thirdreality/LinuxBox-Installer/releases/download"
                    filename = version_key or package_name
                    download_url = f"{release_base_url}/{release}/{filename}_{version}.deb"
                    
                    # Start OTA upgrade task
                    started = self._supervisor.task_manager.start_ota_upgrade(
                        software=package_name,
                        version=version,
                        download_url=download_url
                    )
                    
                    if started:
                        self._set_headers()
                        self.wfile.write(json.dumps({
                            "success": True, 
                            "message": f"OTA upgrade started for {package_name}",
                            "task": "ota"
                        }).encode())
                    else:
                        self._set_headers(status_code=409)
                        self.wfile.write(json.dumps({
                            "success": False, 
                            "error": "Another upgrade task is already running"
                        }).encode())
                        
                except Exception as e:
                    self._logger.error(f"Error handling OTA upgrade: {e}")
                    self._set_headers(status_code=500)
                    self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
        
        return LinuxBoxHTTPHandler

def signal_handler(signum, frame):
    """Handle termination signal"""
    logging.getLogger("Supervisor").info(f"Received signal {signum}, exiting HTTP server gracefully...")
    http_server.stop()
    sys.exit(0)

if __name__ == "__main__":
    # Test server
    from supervisor import Supervisor
    supervisor = Supervisor()
    supervisor.init()
    
    # Define signal handler, for clean exit on termination signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    http_server = SupervisorHTTPServer(supervisor)
    http_server.start()
    
    try:
        # Keep main thread alive
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        http_server.stop()
        supervisor.cleanup()
        print("HTTP server stopped")

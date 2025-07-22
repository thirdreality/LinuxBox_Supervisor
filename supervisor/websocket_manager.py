# maintainer: guoping.liu@3reality.com

"""WebSocket Manager for HomeAssistant API operations"""

import asyncio
import json
import logging
import aiohttp
import subprocess
from typing import Optional, Dict, Any, List
from .token_manager import TokenManager

class WebSocketManager:
    def __init__(self, host: str = "localhost", port: int = 8123, token_mode: int = None, 
                 username: str = None, password: str = None):
        self.logger = logging.getLogger("Supervisor")
        self.host = host
        self.port = port
        self.token_manager = TokenManager(token_mode)
        self.username = username
        self.password = password
        self.request_id = 0
        
    def _get_next_request_id(self) -> int:
        """Get next request ID for WebSocket messages"""
        self.request_id += 1
        return self.request_id

    async def _connect_and_authenticate(self) -> Optional[aiohttp.ClientWebSocketResponse]:
        """Connect to HomeAssistant WebSocket and authenticate"""
        try:
            # Get access token based on token mode
            token = self.token_manager.get_access_token(
                host=self.host, 
                username=self.username, 
                password=self.password
            )
            if not token:
                self.logger.error("Failed to get access token")
                return None
                
            # Connect to WebSocket using aiohttp
            uri = f"ws://{self.host}:{self.port}/api/websocket"
            session = aiohttp.ClientSession()
            websocket = await session.ws_connect(uri)
            
            # Wait for auth required message
            auth_msg = await websocket.receive_json()
            
            if auth_msg.get('type') != 'auth_required':
                self.logger.error(f"Unexpected message type: {auth_msg.get('type')}")
                await websocket.close()
                await session.close()
                return None
                
            # Send authentication
            auth_response = {
                'type': 'auth',
                'access_token': token
            }
            await websocket.send_json(auth_response)
            
            # Wait for auth result
            result = await websocket.receive_json()
            
            if result.get('type') != 'auth_ok':
                self.logger.error(f"Authentication failed: {result}")
                await websocket.close()
                await session.close()
                return None
                
            self.logger.info("WebSocket authentication successful")
            # Store session for cleanup
            websocket._session = session
            return websocket
            
        except Exception as e:
            self.logger.error(f"Error connecting to WebSocket: {e}")
            # Clean up session if it was created
            if 'session' in locals():
                try:
                    await session.close()
                except Exception as cleanup_error:
                    self.logger.debug(f"Error closing session during cleanup: {cleanup_error}")
            return None

    async def _send_request_and_wait_response(self, websocket: aiohttp.ClientWebSocketResponse, 
                                            request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send request and wait for response"""
        try:
            # Send request
            await websocket.send_json(request)
            
            # Wait for response
            response = await websocket.receive_json()
            
            # Check if response matches our request ID
            if response.get('id') != request.get('id'):
                self.logger.warning(f"Response ID mismatch: expected {request.get('id')}, got {response.get('id')}")
                
            return response
            
        except Exception as e:
            self.logger.error(f"Error sending request: {e}")
            return None

    async def switch_zha_channel(self, channel: int) -> bool:
        """
        Switch ZHA channel (official way: zha/network/change_channel)
        Args:
            channel: Channel number (11-26)
        Returns:
            True if successful, False otherwise
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return False
            try:
                req_id = self._get_next_request_id()
                request = {
                    "id": req_id,
                    "type": "zha/network/change_channel",
                    "new_channel": channel
                }
                print(f"[ZHA] Sending WebSocket request: {request}")
                self.logger.info(f"[ZHA] Sending WebSocket request: {request}")
                response = await self._send_request_and_wait_response(websocket, request)
                self.logger.info(f"[ZHA] Received WebSocket response: {response}")
                if not response or not response.get('success'):
                    self.logger.error("Failed to switch ZHA channel")
                    return False
                self.logger.info(f"Successfully switched ZHA channel to {channel}")

                # 切换频道成功后，异步延迟5秒再发备份指令
                # asyncio.create_task(self._delayed_zha_backup())

                return True
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.error(f"Error switching ZHA channel: {e}")
            return False

    async def _delayed_zha_backup(self):
        await asyncio.sleep(5)
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                self.logger.warning("_delayed_zha_backup: websocket connect failed")
                return
            try:
                # 先发送 zha/network/settings 指令
                settings_req_id = self._get_next_request_id()
                settings_request = {
                    "type": "zha/network/settings",
                    "id": settings_req_id
                }
                print(f"[ZHA] (delayed) Sending settings request: {settings_request}")
                self.logger.info(f"[ZHA] (delayed) Sending settings request: {settings_request}")
                settings_response = await self._send_request_and_wait_response(websocket, settings_request)
                self.logger.info(f"[ZHA] (delayed) Settings response: {settings_response}")

                # 再发送 zha/network/backups/create 指令
                backup_req_id = self._get_next_request_id()
                backup_request = {
                    "type": "zha/network/backups/create",
                    "id": backup_req_id
                }
                print(f"[ZHA] (delayed) Sending backup creation request: {backup_request}")
                self.logger.info(f"[ZHA] (delayed) Sending backup creation request: {backup_request}")
                backup_response = await self._send_request_and_wait_response(websocket, backup_request)
                self.logger.info(f"[ZHA] (delayed) Backup creation response: {backup_response}")
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.warning(f"ZHA backup creation failed (non-critical): {e}")

    async def switch_thread_channel(self, channel: int) -> bool:
        """
        Switch Thread channel (OTBR)
        Args:
            channel: Channel number (11-26)
        Returns:
            True if successful, False otherwise
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return False
            try:
                # Step 1: 获取OTBR信息，拿到extended_address
                req_id = self._get_next_request_id()
                info_request = {'id': req_id, 'type': 'otbr/info'}
                self.logger.info(f"[Thread] Sending WebSocket request for OTBR info: {info_request}")
                info_response = await self._send_request_and_wait_response(websocket, info_request)
                self.logger.info(f"[Thread] Received OTBR info response: {info_response}")
                if not info_response or not info_response.get('success'):
                    self.logger.error("Failed to get OTBR info for Thread channel switch")
                    return False
                result = info_response.get('result', {})
                if not result or not isinstance(result, dict):
                    self.logger.error("OTBR info result is empty or invalid")
                    return False
                # 取第一个border router的extended_address
                first_br = next(iter(result.values()), None)
                if not first_br or 'extended_address' not in first_br:
                    self.logger.error("No extended_address found in OTBR info")
                    return False
                extended_address = first_br['extended_address']
                # Step 2: 设置channel
                req_id2 = self._get_next_request_id()
                set_channel_request = {
                    'type': 'otbr/set_channel',
                    'extended_address': extended_address,
                    'channel': channel,
                    'id': req_id2
                }
                self.logger.info(f"[Thread] Sending WebSocket set_channel request: {set_channel_request}")
                set_channel_response = await self._send_request_and_wait_response(websocket, set_channel_request)
                self.logger.info(f"[Thread] Received set_channel response: {set_channel_response}")
                if not set_channel_response or not set_channel_response.get('success'):
                    self.logger.error("Failed to switch Thread channel via otbr/set_channel")
                    return False
                self.logger.info(f"Successfully switched Thread channel to {channel}")
                return True
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.error(f"Error switching Thread channel: {e}")
            return False

    async def notify_zha_devices_firmware_update(self) -> bool:
        """
        Notify all ZHA devices to update firmware
        Returns:
            True if successful, False otherwise
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return False
                
            try:
                # Get all ZHA devices
                request_id = self._get_next_request_id()
                get_devices_request = {
                    'id': request_id,
                    'type': 'zha/devices'
                }
                
                devices_response = await self._send_request_and_wait_response(websocket, get_devices_request)
                if not devices_response or devices_response.get('success') is False:
                    self.logger.error("Failed to get ZHA devices")
                    return False
                    
                devices = devices_response.get('result', [])
                if not devices:
                    self.logger.warning("No ZHA devices found")
                    return True
                    
                # Send firmware update notification to each device
                success_count = 0
                for device in devices:
                    device_id = device.get('ieee')
                    if not device_id:
                        continue
                        
                    request_id = self._get_next_request_id()
                    update_request = {
                        'id': request_id,
                        'type': 'call_service',
                        'domain': 'zha',
                        'service': 'ota_notify',
                        'service_data': {
                            'ieee': device_id
                        }
                    }
                    
                    update_response = await self._send_request_and_wait_response(websocket, update_request)
                    if update_response and update_response.get('success') is True:
                        success_count += 1
                        self.logger.info(f"Firmware update notification sent to device {device_id}")
                    else:
                        self.logger.warning(f"Failed to send firmware update notification to device {device_id}")
                        
                self.logger.info(f"Firmware update notifications sent to {success_count}/{len(devices)} devices")
                return success_count > 0
                
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
                
        except Exception as e:
            self.logger.error(f"Error notifying ZHA devices for firmware update: {e}")
            return False

    async def get_zha_devices(self) -> List[Dict[str, Any]]:
        """
        Get all ZHA devices
        Returns:
            List of ZHA devices or empty list if failed
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return []
                
            try:
                request_id = self._get_next_request_id()
                request = {
                    'id': request_id,
                    'type': 'zha/devices'
                }
                
                response = await self._send_request_and_wait_response(websocket, request)
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get ZHA devices")
                    return []
                    
                return response.get('result', [])
                
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
                
        except Exception as e:
            self.logger.error(f"Error getting ZHA devices: {e}")
            return []

    async def get_thread_devices(self) -> List[Dict[str, Any]]:
        """
        Get all Thread devices
        Returns:
            List of Thread devices or empty list if failed
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return []
                
            try:
                request_id = self._get_next_request_id()
                request = {
                    'id': request_id,
                    'type': 'thread/devices'
                }
                
                response = await self._send_request_and_wait_response(websocket, request)
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get Thread devices")
                    return []
                    
                return response.get('result', [])
                
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
                
        except Exception as e:
            self.logger.error(f"Error getting Thread devices: {e}")
            return []

    async def get_zha_devices_with_token(self, token: str) -> List[Dict[str, Any]]:
        """
        Get all ZHA devices using a specified access token
        Args:
            token: HomeAssistant access token (web token or long-lived token)
        Returns:
            List of ZHA devices or empty list if failed
        """
        try:
            uri = f"ws://{self.host}:{self.port}/api/websocket"
            session = aiohttp.ClientSession()
            websocket = await session.ws_connect(uri)
            try:
                # Wait for auth required
                auth_msg = await websocket.receive_json()
                if auth_msg.get('type') != 'auth_required':
                    self.logger.error(f"Unexpected message type: {auth_msg.get('type')}")
                    await websocket.close()
                    await session.close()
                    return []
                # Send authentication
                await websocket.send_json({'type': 'auth', 'access_token': token})
                result = await websocket.receive_json()
                if result.get('type') != 'auth_ok':
                    self.logger.error(f"Authentication failed: {result}")
                    await websocket.close()
                    await session.close()
                    return []
                # Send ZHA devices request
                request_id = self._get_next_request_id()
                request = {'id': request_id, 'type': 'zha/devices'}
                await websocket.send_json(request)
                response = await websocket.receive_json()
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get ZHA devices")
                    return []
                return response.get('result', [])
            finally:
                await websocket.close()
                await session.close()
        except Exception as e:
            self.logger.error(f"Error getting ZHA devices with token: {e}")
            return []

    async def disable_bluetooth(self) -> bool:
        """
        Disable the Bluetooth integration via Home Assistant WebSocket API.
        Returns:
            True if successful, False otherwise
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return False
            try:
                # Step 1: 获取所有config entries
                req_id = self._get_next_request_id()
                get_entries_request = {
                    'id': req_id,
                    'type': 'config_entries/get'
                }
                response = await self._send_request_and_wait_response(websocket, get_entries_request)
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get config entries for Bluetooth disable")
                    return False
                entries = response.get('result', [])
                # Step 2: 查找domain为bluetooth的entry
                bluetooth_entry = next((e for e in entries if e.get('domain') == 'bluetooth'), None)
                if not bluetooth_entry or not bluetooth_entry.get('entry_id'):
                    self.logger.error("No Bluetooth config entry found")
                    return False
                entry_id = bluetooth_entry['entry_id']
                # Step 3: 发送disable命令
                req_id2 = self._get_next_request_id()
                disable_request = {
                    'id': req_id2,
                    'type': 'config_entries/disable',
                    'entry_id': entry_id,
                    'disabled_by': 'user'
                }
                disable_response = await self._send_request_and_wait_response(websocket, disable_request)
                if not disable_response or disable_response.get('success') is False:
                    self.logger.error("Failed to disable Bluetooth integration")
                    return False
                self.logger.info("Bluetooth integration disabled successfully")
                return True
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.error(f"Error disabling Bluetooth integration: {e}")
            return False

    async def enable_bluetooth(self) -> bool:
        """
        Enable the Bluetooth integration via Home Assistant WebSocket API.
        Returns:
            True if successful, False otherwise
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                return False
            try:
                # Step 1: 获取所有config entries
                req_id = self._get_next_request_id()
                get_entries_request = {
                    'id': req_id,
                    'type': 'config_entries/get'
                }
                response = await self._send_request_and_wait_response(websocket, get_entries_request)
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get config entries for Bluetooth enable")
                    return False
                entries = response.get('result', [])
                # Step 2: 查找domain为bluetooth的entry
                bluetooth_entry = next((e for e in entries if e.get('domain') == 'bluetooth'), None)
                if not bluetooth_entry or not bluetooth_entry.get('entry_id'):
                    self.logger.error("No Bluetooth config entry found")
                    return False
                entry_id = bluetooth_entry['entry_id']
                # Step 3: 发送enable命令（实际上是disable，disabled_by为None）
                req_id2 = self._get_next_request_id()
                enable_request = {
                    'id': req_id2,
                    'type': 'config_entries/disable',
                    'entry_id': entry_id,
                    'disabled_by': None
                }
                enable_response = await self._send_request_and_wait_response(websocket, enable_request)
                if not enable_response or enable_response.get('success') is False:
                    self.logger.error("Failed to enable Bluetooth integration")
                    return False
                self.logger.info("Bluetooth integration enabled successfully")
                return True
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.error(f"Error enabling Bluetooth integration: {e}")
            return False

    async def check_bluetooth_enabled(self) -> bool:
        """
        Check if Bluetooth integration is enabled via Home Assistant WebSocket API.
        Returns:
            True if Bluetooth is enabled, False if disabled or not found
        Raises:
            ConnectionError: If Home Assistant is not available
        """
        try:
            websocket = await self._connect_and_authenticate()
            if not websocket:
                raise ConnectionError("Home Assistant WebSocket connection failed")
            try:
                req_id = self._get_next_request_id()
                request = {
                    'id': req_id,
                    'type': 'config/device_registry/list'
                }
                response = await self._send_request_and_wait_response(websocket, request)
                if not response or response.get('success') is False:
                    self.logger.error("Failed to get device registry for Bluetooth status check")
                    return False
                devices = response.get('result', [])
                self.logger.info(f"Total devices in registry: {len(devices)}")
                
                # Look for Bluetooth devices in device registry
                bluetooth_devices = []
                for device in devices:
                    identifiers = device.get('identifiers', [])
                    connections = device.get('connections', [])
                    self.logger.debug(f"Device: {device.get('name', 'Unknown')}, identifiers: {identifiers}, connections: {connections}")
                    
                    # Check if device is Bluetooth by identifiers or connections
                    is_bluetooth = False
                    if identifiers and any('bluetooth' in str(identifier) for identifier in identifiers):
                        is_bluetooth = True
                    elif connections and any(conn[0] == 'bluetooth' for conn in connections):
                        is_bluetooth = True
                    
                    if is_bluetooth:
                        bluetooth_devices.append(device)
                        self.logger.info(f"Found Bluetooth device: {device.get('name', 'Unknown')}, identifiers: {identifiers}, connections: {connections}")
                
                self.logger.info(f"Total Bluetooth devices found: {len(bluetooth_devices)}")
                if not bluetooth_devices:
                    self.logger.info("No Bluetooth device found in device registry")
                    return False
                
                # Check if any Bluetooth device is enabled (disabled_by is None)
                self.logger.info("Checking Bluetooth devices status:")
                for device in bluetooth_devices:
                    disabled_by = device.get('disabled_by')
                    is_enabled = disabled_by is None
                    device_name = device.get('name', 'Unknown')
                    device_id = device.get('id', 'Unknown')
                    self.logger.info(f"  - Device: {device_name} (ID: {device_id})")
                    self.logger.info(f"    disabled_by: {disabled_by}")
                    self.logger.info(f"    enabled: {is_enabled}")
                    if is_enabled:
                        self.logger.info(f"Found enabled Bluetooth device: {device_name}")
                        return True
                
                # If we found Bluetooth devices but none are enabled, return False
                self.logger.info("Bluetooth devices found but all are disabled")
                return False
            finally:
                await websocket.close()
                if hasattr(websocket, '_session'):
                    await websocket._session.close()
        except Exception as e:
            self.logger.error(f"Error checking Bluetooth status: {e}")
            return False

    def run_async_task(self, coro):
        """Helper method to run async tasks from sync context"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            return loop.run_until_complete(coro)
        except Exception as e:
            self.logger.error(f"Error in async task: {e}")
            raise
        finally:
            # Ensure all pending tasks are cleaned up
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    if not task.done():
                        task.cancel()
                        self.logger.debug(f"Cancelled pending task: {task}")
            except Exception as cleanup_error:
                self.logger.debug(f"Error during task cleanup: {cleanup_error}")

    def switch_zha_channel_sync(self, channel: int) -> bool:
        """Synchronous wrapper for switch_zha_channel"""
        return self.run_async_task(self.switch_zha_channel(channel))

    def switch_thread_channel_sync(self, channel: int) -> bool:
        """Synchronous wrapper for switch_thread_channel"""
        return self.run_async_task(self.switch_thread_channel(channel))

    def notify_zha_devices_firmware_update_sync(self) -> bool:
        """Synchronous wrapper for notify_zha_devices_firmware_update"""
        return self.run_async_task(self.notify_zha_devices_firmware_update())

    def get_zha_devices_sync(self) -> List[Dict[str, Any]]:
        """Synchronous wrapper for get_zha_devices"""
        return self.run_async_task(self.get_zha_devices())

    def get_thread_devices_sync(self) -> List[Dict[str, Any]]:
        """Synchronous wrapper for get_thread_devices"""
        return self.run_async_task(self.get_thread_devices())

    def check_bluetooth_enabled_sync(self) -> bool:
        """Synchronous wrapper for check_bluetooth_enabled"""
        try:
            return self.run_async_task(self.check_bluetooth_enabled())
        except ConnectionError as e:
            self.logger.error(f"Home Assistant not available for Bluetooth status check: {e}")
            raise

    def _connect_and_authenticate_sync(self) -> Optional[aiohttp.ClientWebSocketResponse]:
        """Synchronous version of _connect_and_authenticate"""
        return self.run_async_task(self._connect_and_authenticate())

    def _send_request_and_wait_response_sync(self, websocket: aiohttp.ClientWebSocketResponse, 
                                           request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Synchronous version of _send_request_and_wait_response"""
        return self.run_async_task(self._send_request_and_wait_response(websocket, request))

    def _close_websocket_sync(self, websocket: aiohttp.ClientWebSocketResponse):
        """Synchronous version of WebSocket cleanup"""
        try:
            if websocket:
                self.run_async_task(websocket.close())
                if hasattr(websocket, '_session'):
                    self.run_async_task(websocket._session.close())
        except Exception as e:
            self.logger.error(f"Error closing WebSocket: {e}")

    def disable_bluetooth_sync(self) -> bool:
        """Synchronous wrapper for disable_bluetooth"""
        return self.run_async_task(self.disable_bluetooth())

    def enable_bluetooth_sync(self) -> bool:
        """Synchronous wrapper for enable_bluetooth"""
        return self.run_async_task(self.enable_bluetooth())
    
    def delayed_zha_backup_sync(self):
        """Synchronous wrapper for _delayed_zha_backup"""
        return self.run_async_task(self._delayed_zha_backup())

    def cleanup(self):
        """Clean up any remaining resources"""
        # This method can be called during shutdown to ensure all resources are properly cleaned up
        self.logger.debug("WebSocketManager cleanup called")
        
        # Cancel any pending async tasks
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, we can't cancel tasks from here
                # The cleanup will happen when the loop stops
                self.logger.debug("Event loop is running, cleanup will happen when loop stops")
            else:
                # Cancel all pending tasks
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    if not task.done():
                        task.cancel()
                        self.logger.debug(f"Cancelled pending task: {task}")
        except RuntimeError:
            # No event loop in current thread
            self.logger.debug("No event loop in current thread")
        except Exception as e:
            self.logger.debug(f"Error during WebSocketManager cleanup: {e}")

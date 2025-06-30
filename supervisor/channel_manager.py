# maintainer: guoping.liu@3reality.com

"""Channel Manager for querying ZHA, Zigbee2MQTT, and Thread channel information"""

import logging
import json
import subprocess
import os
from typing import Dict, Any, Optional
from .websocket_manager import WebSocketManager
from .utils.zigbee_util import get_ha_zigbee_mode

class ChannelManager:
    def __init__(self):
        self.logger = logging.getLogger("Supervisor")
        self.ws_manager = WebSocketManager()
        
    def get_all_channels(self) -> Dict[str, Any]:
        """
        Get all channel information (Zigbee, Thread)
        Returns:
            Dict with channel information: {"zigbee_mode": "z2m", "zigbee": 15, "thread": 25}
        """
        result = {
            "zigbee_mode": "none",
            "zigbee": 0,
            "thread": 0
        }
        
        try:
            # Get HomeAssistant Zigbee mode
            zigbee_mode = get_ha_zigbee_mode()
            self.logger.info(f"Current Zigbee mode: {zigbee_mode}")
            result["zigbee_mode"] = zigbee_mode
            
            if zigbee_mode == 'zha':
                # ZHA is active, get ZHA channel
                zha_channel = self._get_zha_channel()
                result["zigbee"] = zha_channel
                self.logger.info(f"ZHA channel: {zha_channel}")
                
            elif zigbee_mode == 'z2m':
                # Zigbee2MQTT is active, get Z2M channel
                z2m_channel = self._get_z2m_channel()
                result["zigbee"] = z2m_channel
                self.logger.info(f"Z2M channel: {z2m_channel}")
            
            # Get Thread channel (independent of Zigbee mode)
            thread_channel = self._get_thread_channel()
            result["thread"] = thread_channel
            self.logger.info(f"Thread channel: {thread_channel}")
            
        except Exception as e:
            self.logger.error(f"Error getting channel information: {e}")
        
        return result
    
    def get_channel_by_type(self, channel_type: str) -> Dict[str, Any]:
        """
        Get channel information for a specific type
        Args:
            channel_type: "zigbee" or "thread"
        Returns:
            Dict with channel information for the specified type
        """
        if channel_type == "zigbee":
            zigbee_mode = get_ha_zigbee_mode()
            if zigbee_mode == 'zha':
                return {"zigbee_mode": zigbee_mode, "zigbee": self._get_zha_channel()}
            elif zigbee_mode == 'z2m':
                return {"zigbee_mode": zigbee_mode, "zigbee": self._get_z2m_channel()}
            else:
                return {"zigbee_mode": zigbee_mode, "zigbee": 0}
        elif channel_type == "thread":
            return {"thread": self._get_thread_channel()}
        else:
            self.logger.error(f"Unknown channel type: {channel_type}")
            return {}
    
    def _get_zha_channel(self) -> int:
        """
        Get ZHA channel from HomeAssistant via WebSocket
        Returns:
            Channel number or 0 if failed
        """
        try:
            ws = self.ws_manager._connect_and_authenticate_sync()
            if not ws:
                return 0
            try:
                req_id = self.ws_manager._get_next_request_id()
                req = {'id': req_id, 'type': 'zha/network/settings'}
                resp = self.ws_manager._send_request_and_wait_response_sync(ws, req)
                if resp and resp.get('success'):
                    channel = (
                        resp.get('result', {})
                            .get('settings', {})
                            .get('network_info', {})
                            .get('channel')
                    )
                    if channel:
                        self.logger.info(f"Found ZHA channel via WebSocket: {channel}")
                        return int(channel)
            finally:
                self.ws_manager._close_websocket_sync(ws)
            return 0
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return 0
    
    def _get_z2m_channel(self) -> int:
        """
        Get Zigbee2MQTT channel from MQTT
        Returns:
            Channel number or 0 if failed
        """
        try:
            # Try to get from MQTT topic
            return self._get_z2m_channel_from_mqtt()
            
        except Exception as e:
            self.logger.error(f"Error getting Z2M channel: {e}")
            return 0
    
    def _get_z2m_channel_from_mqtt(self) -> int:
        """
        Get Zigbee2MQTT channel from MQTT topic
        Returns:
            Channel number or 0 if failed
        """
        try:
            # Use mosquitto_sub to get bridge info
            cmd = [
                "/usr/bin/mosquitto_sub",
                "-h", "localhost",
                "-t", "zigbee2mqtt/bridge/info",
                "-u", "thirdreality",
                "-P", "thirdreality",
                "-C", "1",  # Only get one message
                "-W", "5"   # Wait 5 seconds
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                # Get channel from network.channel path
                channel = data.get('network', {}).get('channel')
                if channel:
                    self.logger.info(f"Found Z2M channel via MQTT: {channel}")
                    return int(channel)
            
            return 0
            
        except Exception as e:
            self.logger.error(f"Error getting Z2M channel from MQTT: {e}")
            return 0
    
    def _get_thread_channel(self) -> int:
        """
        Get Thread channel from OpenThread Border Router via WebSocket
        Returns:
            Channel number or 0 if failed
        """
        try:
            ws = self.ws_manager._connect_and_authenticate_sync()
            if not ws:
                return 0
            try:
                req_id = self.ws_manager._get_next_request_id()
                req = {'id': req_id, 'type': 'otbr/info'}
                resp = self.ws_manager._send_request_and_wait_response_sync(ws, req)
                if resp and resp.get('success'):
                    result = resp.get('result', {})
                    if isinstance(result, dict) and result:
                        channel = list(result.values())[0].get('channel')
                        if channel:
                            self.logger.info(f"Found Thread channel via WebSocket: {channel}")
                            return int(channel)
            finally:
                self.ws_manager._close_websocket_sync(ws)
            return 0
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return 0
    
    def switch_z2m_channel(self, channel: int) -> bool:
        """
        Switch Z2M channel via MQTT (official way: set options then restart)
        Args:
            channel: Channel number (11-26)
        Returns:
            True if successful, False otherwise
        """
        try:
            # Step 1: 设置channel
            options_cmd = [
                "/usr/bin/mosquitto_pub",
                "-h", "localhost",
                "-t", "zigbee2mqtt/bridge/request/options",
                "-m", json.dumps({"options": {"advanced": {"channel": channel}}}),
                "-u", "thirdreality",
                "-P", "thirdreality"
            ]
            self.logger.info(f"[Z2M] Sending options command: {' '.join(options_cmd)}")
            result = subprocess.run(options_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                self.logger.error(f"Failed to send options command: {result.stderr}")
                return False

            # Step 2: 重启zigbee2mqtt
            restart_cmd = [
                "/usr/bin/mosquitto_pub",
                "-h", "localhost",
                "-t", "zigbee2mqtt/bridge/request/restart",
                "-m", "{}",
                "-u", "thirdreality",
                "-P", "thirdreality"
            ]
            self.logger.info(f"[Z2M] Sending restart command: {' '.join(restart_cmd)}")
            result2 = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=30)
            if result2.returncode != 0:
                self.logger.error(f"Failed to send restart command: {result2.stderr}")
                return False

            self.logger.info(f"Successfully switched Z2M channel to {channel} (restart required)")
            return True
        except Exception as e:
            self.logger.error(f"Error switching Z2M channel: {e}")
            return False 
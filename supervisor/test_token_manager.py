#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for WebSocket Manager and Token Manager
"""

import sys
import os
import logging

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from .token_manager import TokenManager
from .websocket_manager import WebSocketManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_token_manager():
    """Test Token Manager functionality"""
    print("=== Testing Token Manager ===")
    
    token_manager = TokenManager()
    
    # Test long-lived access token
    print("Testing get_long_lived_access_tokens()...")
    long_token = token_manager.get_long_lived_access_tokens()
    if long_token:
        print(f"✓ Long-lived token retrieved: {long_token[:20]}...")
    else:
        print("✗ Failed to retrieve long-lived token")
    
    # Test web access token (default)
    print("Testing get_web_access_tokens()...")
    web_token = token_manager.get_web_access_tokens()
    if web_token:
        print(f"✓ Web token retrieved: {web_token[:20]}...")
    else:
        print("✗ Failed to retrieve web token")
    
    # Test web access token with custom username/password
    print("Testing get_web_access_tokens(username='root', password='1234')...")
    custom_token = token_manager.get_web_access_tokens(username='root', password='1234')
    if custom_token:
        print(f"✓ Custom web token retrieved: {custom_token[:20]}...")
    else:
        print("✗ Failed to retrieve custom web token")
    
    # Test token cache
    print("Testing token cache...")
    cached_token = token_manager.get_web_access_tokens()
    if cached_token == web_token:
        print("✓ Token cache working correctly")
    else:
        print("✗ Token cache not working correctly")

def test_websocket_manager():
    """Test WebSocket Manager functionality"""
    print("\n=== Testing WebSocket Manager ===")
    
    ws_manager = WebSocketManager()
    
    # Test getting ZHA devices
    print("Testing get_zha_devices_sync()...")
    try:
        devices = ws_manager.get_zha_devices_sync()
        print(f"✓ Retrieved {len(devices)} ZHA devices")
        for device in devices[:3]:  # Show first 3 devices
            print(f"  - Device: {device.get('name', 'Unknown')} (IEEE: {device.get('ieee', 'Unknown')})")
    except Exception as e:
        print(f"✗ Failed to get ZHA devices: {e}")
    
    # Test getting Thread devices
    print("Testing get_thread_devices_sync()...")
    try:
        devices = ws_manager.get_thread_devices_sync()
        print(f"✓ Retrieved {len(devices)} Thread devices")
        for device in devices[:3]:  # Show first 3 devices
            print(f"  - Device: {device.get('name', 'Unknown')} (IEEE: {device.get('ieee', 'Unknown')})")
    except Exception as e:
        print(f"✗ Failed to get Thread devices: {e}")

def test_websocket_manager_with_web_token():
    """Test WebSocket Manager with web token (custom username/password)"""
    print("\n=== Testing WebSocket Manager with Web Token ===")
    token_manager = TokenManager()
    ws_manager = WebSocketManager()
    # 获取web token
    web_token = token_manager.get_web_access_tokens(username='root', password='1234')
    if not web_token:
        print("✗ Failed to retrieve web token for WebSocket test")
        return
    # 查询ZHA设备
    try:
        devices = ws_manager.run_async_task(ws_manager.get_zha_devices_with_token(web_token))
        print(f"✓ Retrieved {len(devices)} ZHA devices using web token")
        for device in devices[:3]:
            print(f"  - Device: {device.get('name', 'Unknown')} (IEEE: {device.get('ieee', 'Unknown')})")
    except Exception as e:
        print(f"✗ Failed to get ZHA devices with web token: {e}")

def main():
    """Main test function"""
    print("Starting WebSocket Manager and Token Manager tests...")
    try:
        test_token_manager()
        test_websocket_manager()
        test_websocket_manager_with_web_token()
        print("\n=== Tests completed ===")
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for Token Mode functionality
"""

import sys
import os
import logging

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from .token_manager import TokenManager
from .websocket_manager import WebSocketManager
from .const import TOKEN_MODE_AUTO, TOKEN_MODE_LONGLIVED, TOKEN_MODE_OAUTH2

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_token_modes():
    """Test different token modes"""
    print("=== Testing Token Modes ===")
    
    # Test longlived mode
    print("\n--- Testing LONGLIVED Mode ---")
    token_manager_longlived = TokenManager(TOKEN_MODE_LONGLIVED)
    token = token_manager_longlived.get_access_token()
    if token:
        print(f"✓ Longlived mode token: {token[:20]}...")
    else:
        print("✗ Longlived mode failed to get token")
    
    # Test oauth2 mode
    print("\n--- Testing OAUTH2 Mode ---")
    token_manager_oauth2 = TokenManager(TOKEN_MODE_OAUTH2)
    token = token_manager_oauth2.get_access_token(username='root', password='1234')
    if token:
        print(f"✓ OAuth2 mode token: {token[:20]}...")
    else:
        print("✗ OAuth2 mode failed to get token")
    
    # Test auto mode
    print("\n--- Testing AUTO Mode ---")
    token_manager_auto = TokenManager(TOKEN_MODE_AUTO)
    token = token_manager_auto.get_access_token(username='root', password='1234')
    if token:
        print(f"✓ Auto mode token: {token[:20]}...")
    else:
        print("✗ Auto mode failed to get token")

def test_websocket_with_modes():
    """Test WebSocket Manager with different token modes"""
    print("\n=== Testing WebSocket Manager with Token Modes ===")
    
    # Test with longlived mode
    print("\n--- WebSocket with LONGLIVED Mode ---")
    ws_longlived = WebSocketManager(token_mode=TOKEN_MODE_LONGLIVED)
    try:
        devices = ws_longlived.get_zha_devices_sync()
        print(f"✓ Longlived mode: Retrieved {len(devices)} ZHA devices")
    except Exception as e:
        print(f"✗ Longlived mode failed: {e}")
    
    # Test with oauth2 mode
    print("\n--- WebSocket with OAUTH2 Mode ---")
    ws_oauth2 = WebSocketManager(
        token_mode=TOKEN_MODE_OAUTH2,
        username='root',
        password='1234'
    )
    try:
        devices = ws_oauth2.get_zha_devices_sync()
        print(f"✓ OAuth2 mode: Retrieved {len(devices)} ZHA devices")
    except Exception as e:
        print(f"✗ OAuth2 mode failed: {e}")
    
    # Test with auto mode
    print("\n--- WebSocket with AUTO Mode ---")
    ws_auto = WebSocketManager(
        token_mode=TOKEN_MODE_AUTO,
        username='root',
        password='1234'
    )
    try:
        devices = ws_auto.get_zha_devices_sync()
        print(f"✓ Auto mode: Retrieved {len(devices)} ZHA devices")
    except Exception as e:
        print(f"✗ Auto mode failed: {e}")

def test_token_mode_behavior():
    """Test the behavior of different token modes"""
    print("\n=== Testing Token Mode Behavior ===")
    
    # Test auto mode fallback behavior
    print("\n--- Testing Auto Mode Fallback ---")
    token_manager = TokenManager(TOKEN_MODE_AUTO)
    
    # First call should try long-lived first
    print("Testing auto mode with long-lived available...")
    token1 = token_manager.get_access_token()
    print(f"First call result: {'✓ Success' if token1 else '✗ Failed'}")
    
    # Test with oauth2 credentials
    print("Testing auto mode with oauth2 credentials...")
    token2 = token_manager.get_access_token(username='root', password='1234')
    print(f"Second call result: {'✓ Success' if token2 else '✗ Failed'}")
    
    # Test cache behavior
    print("Testing token cache...")
    token3 = token_manager.get_access_token(username='root', password='1234')
    if token3 == token2:
        print("✓ Token cache working correctly")
    else:
        print("✗ Token cache not working correctly")

def main():
    """Main test function"""
    print("Starting Token Mode tests...")
    try:
        test_token_modes()
        test_websocket_with_modes()
        test_token_mode_behavior()
        print("\n=== All tests completed ===")
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
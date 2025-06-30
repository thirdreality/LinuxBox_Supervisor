#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for Channel Info API
"""

import sys
import os
import logging
import urllib.request
import json

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from .channel_manager import ChannelManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_channel_manager():
    """Test ChannelManager directly"""
    print("=== Testing ChannelManager ===")
    
    channel_manager = ChannelManager()
    
    # Test getting all channels
    print("\n--- Testing get_all_channels() ---")
    all_channels = channel_manager.get_all_channels()
    print(f"All channels: {all_channels}")
    
    # Test getting specific channel types
    print("\n--- Testing get_channel_by_type() ---")
    for channel_type in ['zha', 'z2m', 'thread']:
        channel_info = channel_manager.get_channel_by_type(channel_type)
        print(f"{channel_type.upper()} channel: {channel_info}")

def test_http_api():
    """Test HTTP API endpoints"""
    print("\n=== Testing HTTP API ===")
    
    base_url = "http://localhost:8086"
    
    # Test all channels endpoint
    print("\n--- Testing /api/channel/info ---")
    try:
        url = f"{base_url}/api/channel/info"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print(f"All channels response: {data}")
    except Exception as e:
        print(f"Error testing all channels endpoint: {e}")
    
    # Test specific channel type endpoints
    print("\n--- Testing /api/channel/info?type=zha ---")
    try:
        url = f"{base_url}/api/channel/info?type=zha"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print(f"ZHA channel response: {data}")
    except Exception as e:
        print(f"Error testing ZHA channel endpoint: {e}")
    
    print("\n--- Testing /api/channel/info?type=z2m ---")
    try:
        url = f"{base_url}/api/channel/info?type=z2m"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print(f"Z2M channel response: {data}")
    except Exception as e:
        print(f"Error testing Z2M channel endpoint: {e}")
    
    print("\n--- Testing /api/channel/info?type=thread ---")
    try:
        url = f"{base_url}/api/channel/info?type=thread"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print(f"Thread channel response: {data}")
    except Exception as e:
        print(f"Error testing Thread channel endpoint: {e}")
    
    # Test invalid channel type
    print("\n--- Testing /api/channel/info?type=invalid ---")
    try:
        url = f"{base_url}/api/channel/info?type=invalid"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print(f"Invalid channel type response: {data}")
    except Exception as e:
        print(f"Error testing invalid channel type endpoint: {e}")

def main():
    """Main test function"""
    print("Starting Channel Info tests...")
    try:
        test_channel_manager()
        test_http_api()
        print("\n=== All tests completed ===")
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
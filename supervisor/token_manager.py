# maintainer: guoping.liu@3reality.com

"""Token Manager for HomeAssistant API access"""

import os
import time
import json
import logging
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any
from .const import TOKEN_MODE, TOKEN_MODE_AUTO, TOKEN_MODE_LONGLIVED, TOKEN_MODE_OAUTH2

class TokenManager:
    def __init__(self, token_mode: int = None):
        self.logger = logging.getLogger("Supervisor")
        self.config_file = "/etc/automation-robot.conf"
        
        # Use provided token_mode or default from const
        self.token_mode = token_mode if token_mode is not None else TOKEN_MODE
        
        # Cache for web access tokens
        self._web_token_cache: Optional[str] = None
        self._web_token_timestamp: float = 0
        self._web_token_expiry: int = 30 * 60  # 30 minutes in seconds
        
        # Default credentials for web login
        self.default_user = "shushi"
        self.default_password = "shushi6688"
        self.default_host = "localhost"
        self.default_port = 8123

    def get_access_token(self, host: str = None, username: str = None, password: str = None) -> Optional[str]:
        """
        Get access token based on configured token mode
        Args:
            host: HomeAssistant host (for oauth2 mode)
            username: Username (for oauth2 mode)
            password: Password (for oauth2 mode)
        Returns:
            Access token string or None if failed
        """
        if self.token_mode == TOKEN_MODE_LONGLIVED:
            # Only use long-lived token
            self.logger.debug("Using longlived token mode")
            return self.get_long_lived_access_tokens()
            
        elif self.token_mode == TOKEN_MODE_OAUTH2:
            # Only use oauth2 token
            self.logger.debug("Using oauth2 token mode")
            return self.get_web_access_tokens(host, username, password)
            
        elif self.token_mode == TOKEN_MODE_AUTO:
            # Auto mode: prefer long-lived, fallback to oauth2
            self.logger.debug("Using auto token mode")
            token = self.get_long_lived_access_tokens()
            if token:
                self.logger.info("Auto mode: using long-lived token")
                return token
            else:
                self.logger.info("Auto mode: long-lived token not available, trying oauth2")
                return self.get_web_access_tokens(host, username, password)
        
        else:
            self.logger.error(f"Unknown token mode: {self.token_mode}")
            return None

    def get_long_lived_access_tokens(self) -> Optional[str]:
        """
        Get long-lived access tokens from configuration file
        Returns the token string or None if not found
        """
        try:
            if not os.path.exists(self.config_file):
                self.logger.error(f"Configuration file not found: {self.config_file}")
                return None
            with open(self.config_file, 'r') as f:
                config_content = f.read()
            lines = config_content.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('token=') or line.startswith('access_token='):
                    token = line.split('=', 1)[1].strip()
                    if token:
                        self.logger.info("Successfully retrieved long-lived access token (with prefix)")
                        return token
            # 如果没有前缀，直接取第一行非空内容
            for line in lines:
                line = line.strip()
                if line:
                    self.logger.info("Successfully retrieved long-lived access token (no prefix)")
                    return line
            self.logger.warning("No token found in configuration file")
            return None
        except Exception as e:
            self.logger.error(f"Error reading long-lived access token: {e}")
            return None

    def get_web_access_tokens(self, host: str = None, username: str = None, password: str = None) -> Optional[str]:
        """
        Get web access tokens by simulating web login to HomeAssistant
        Returns cached token if still valid, otherwise performs new login
        """
        # Use default values if not provided
        host = host or self.default_host
        username = username or self.default_user
        password = password or self.default_password
        
        # Check if cached token is still valid
        current_time = time.time()
        if (self._web_token_cache and 
            (current_time - self._web_token_timestamp) < self._web_token_expiry):
            self.logger.debug("Using cached web access token")
            return self._web_token_cache
            
        # Perform new login to get fresh token
        try:
            token = self._perform_web_login(host, username, password)
            if token:
                self._web_token_cache = token
                self._web_token_timestamp = current_time
                self.logger.info("Successfully obtained new web access token")
                return token
            else:
                self.logger.error("Failed to obtain web access token")
                return None
                
        except Exception as e:
            self.logger.error(f"Error during web login: {e}")
            return None

    def _perform_web_login(self, host: str, username: str, password: str) -> Optional[str]:
        """
        Perform web login to HomeAssistant and return access token
        """
        try:
            base_url = f"http://{host}:{self.default_port}"
            
            # Step 1: Get auth providers
            providers = self._get_auth_providers(base_url)
            if not providers:
                self.logger.error("No auth providers found")
                return None
                
            # Step 2: Start login flow
            flow_id = self._start_login_flow(base_url, providers)
            if not flow_id:
                self.logger.error("Failed to start login flow")
                return None
                
            # Step 3: Complete login flow
            code = self._complete_login_flow(base_url, flow_id, providers, username, password)
            if not code:
                self.logger.error("Failed to complete login flow")
                return None
                
            # Step 4: Exchange code for token
            token = self._exchange_code_for_token(base_url, code)
            if not token:
                self.logger.error("Failed to exchange code for token")
                return None
                
            return token
            
        except Exception as e:
            self.logger.error(f"Error in web login process: {e}")
            return None

    def _get_auth_providers(self, base_url: str) -> Optional[str]:
        """Get available authentication providers"""
        try:
            url = f"{base_url}/auth/providers"
            headers = {
                "credentials": "same-origin",
                "Content-Type": "application/json"
            }
            
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                resp_text = response.read().decode('utf-8')
                data = json.loads(resp_text)
            providers = data.get('providers', [])
            
            for provider in providers:
                if provider.get('type') == 'homeassistant':
                    return 'homeassistant'
                elif provider.get('type') == 'trusted_networks':
                    return 'trusted_networks'
                    
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting auth providers: {e}")
            return None

    def _start_login_flow(self, base_url: str, provider_type: str) -> Optional[str]:
        """Start login flow and return flow_id"""
        try:
            url = f"{base_url}/auth/login_flow"
            
            body = {
                "client_id": base_url,
                "redirect_uri": f"{base_url}/lovelace/home?auth_callback=1",
                "handler": [provider_type, None]
            }
            
            headers = {
                "credentials": "same-origin",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "User-Agent": "automation-robot/1.0"
            }
            
            data = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, headers=headers, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                resp_text = response.read().decode('utf-8')
                data = json.loads(resp_text)
            return data.get('flow_id')
            
        except Exception as e:
            self.logger.error(f"Error starting login flow: {e}")
            return None

    def _complete_login_flow(self, base_url: str, flow_id: str, provider_type: str, 
                           username: str, password: str) -> Optional[str]:
        """Complete login flow and return authorization code"""
        try:
            url = f"{base_url}/auth/login_flow/{flow_id}"
            
            if provider_type == 'homeassistant':
                body = {
                    "username": username,
                    "password": password,
                    "client_id": base_url
                }
            elif provider_type == 'trusted_networks':
                # For trusted networks, we need to get the user first
                user_info = self._get_trusted_networks_user(base_url, flow_id)
                if not user_info:
                    return None
                body = {
                    "user": user_info['user'],
                    "client_id": base_url
                }
            else:
                self.logger.error(f"Unsupported provider type: {provider_type}")
                return None
                
            headers = {
                "credentials": "same-origin",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "User-Agent": "automation-robot/1.0"
            }
            
            data = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, headers=headers, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                resp_text = response.read().decode('utf-8')
                data = json.loads(resp_text)
            return data.get('result')
            
        except Exception as e:
            self.logger.error(f"Error completing login flow: {e}")
            return None

    def _get_trusted_networks_user(self, base_url: str, flow_id: str) -> Optional[Dict[str, Any]]:
        """Get user information for trusted networks authentication"""
        try:
            url = f"{base_url}/auth/login_flow/{flow_id}"
            
            headers = {
                "credentials": "same-origin",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "User-Agent": "automation-robot/1.0"
            }
            
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                resp_text = response.read().decode('utf-8')
                data = json.loads(resp_text)
            data_schema = data.get('data_schema', [])
            
            if data_schema and len(data_schema) > 0:
                options = data_schema[0].get('options', [])
                if options and len(options) > 0:
                    return {
                        'user': options[0][0],
                        'user_name': options[0][1]
                    }
                    
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting trusted networks user: {e}")
            return None

    def _exchange_code_for_token(self, base_url: str, code: str) -> Optional[str]:
        """Exchange authorization code for access token"""
        try:
            url = f"{base_url}/auth/token"
            
            payload = {
                "client_id": base_url,
                "code": code,
                "grant_type": "authorization_code"
            }
            
            headers = {
                "credentials": "same-origin",
                "Accept": "*/*",
                "User-Agent": "automation-robot/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            data = urllib.parse.urlencode(payload).encode('utf-8')
            req = urllib.request.Request(url, headers=headers, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                resp_text = response.read().decode('utf-8')
                data = json.loads(resp_text)
            return data.get('access_token')
            
        except Exception as e:
            self.logger.error(f"Error exchanging code for token: {e}")
            return None

    def clear_web_token_cache(self):
        """Clear the cached web token"""
        self._web_token_cache = None
        self._web_token_timestamp = 0
        self.logger.info("Web token cache cleared")

    def is_web_token_valid(self) -> bool:
        """Check if the cached web token is still valid"""
        if not self._web_token_cache:
            return False
            
        current_time = time.time()
        return (current_time - self._web_token_timestamp) < self._web_token_expiry 
# maintainer: guoping.liu@3reality.com

import os
import threading
import time
import logging
import socket
import tempfile
import json

from .hardware import LedState


class SupervisorProxy:
    '''Connects SupervisorClient and Supervisor via local socket for local debugging and reuse of local functions by other modules.'''
    SOCKET_PATH = "/run/led_socket"  # Use /run directory, which is a memory filesystem and usually always writable

    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.logger = logging.getLogger("Supervisor")
        self.stop_event = threading.Event()

    def _setup_socket(self):
        # Directly try to create socket, no longer check /tmp directory
        try:
            # Ensure the directory exists
            socket_dir = os.path.dirname(self.SOCKET_PATH)
            if not os.path.exists(socket_dir):
                try:
                    # Try to create the directory if needed
                    os.makedirs(socket_dir, exist_ok=True)
                except Exception as e_dir:
                    self.logger.warning(f"Could not create directory {socket_dir}: {e_dir}")
            
            # Remove existing socket file if present
            if os.path.exists(self.SOCKET_PATH):
                os.remove(self.SOCKET_PATH)
                
            # Create and bind the socket
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(self.SOCKET_PATH)
            self.server.listen(1)
            self.server.settimeout(1.0)
            self.logger.info(f"Socket created at {self.SOCKET_PATH}")
        except Exception as e:
            self.logger.error(f"Failed to create socket at {self.SOCKET_PATH}: {e}")
            # If creation fails, raise the exception
            raise

    def run(self):
        self._setup_socket()

        logging.info("Starting local socket monitor...")
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
                with conn:
                    data = conn.recv(1024).decode('utf-8')
                    response = self.handle_request(data)
                    conn.sendall(response.encode('utf-8'))
            except socket.timeout:
                continue

    def stop(self):
        self.stop_event.set()
        if hasattr(self, 'proxy_thread') and self.proxy_thread.is_alive():
            self.proxy_thread.join(timeout=5)  # Wait for up to 5 seconds
            if self.proxy_thread.is_alive():
                self.logger.warning("Proxy thread did not terminate gracefully")

    def handle_request(self, data):
        try:
            # Parse JSON data
            payload = json.loads(data)
            
            # Handle LED command (special handling, as it needs to convert to LedState enum)
            if "cmd-led" in payload:
                state_str = payload["cmd-led"].strip().lower()
                try:
                    # Support mapping of simple color names to USER_EVENT
                    user_event_map = {
                        'red': LedState.USER_EVENT_RED,
                        'blue': LedState.USER_EVENT_BLUE,
                        'yellow': LedState.USER_EVENT_YELLOW,
                        'green': LedState.USER_EVENT_GREEN,
                        'white': LedState.USER_EVENT_WHITE,
                        'off': LedState.USER_EVENT_OFF
                    }
                    state = None
                    try:
                        state = LedState(state_str)
                    except ValueError:
                        state = user_event_map.get(state_str)
                    if state is None:
                        error_msg = f"Invalid LED state: {state_str}"
                        self.logger.error(error_msg)
                        return error_msg
                    # Use supervisor to set LED state
                    if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                        self.supervisor.set_led_state(state)
                    return "LED state has been set"
                except Exception as e:
                    error_msg = f"Error setting LED state: {e}"
                    self.logger.error(error_msg)
                    return error_msg
            
            # Handle other command types
            # Define command types and corresponding method mapping
            command_mapping = {
                "cmd-ota": "set_ota_command",
                "cmd-thread": "set_thread_command",
                "cmd-zigbee": "set_zigbee_command",
                "cmd-setting": "set_setting_command"
            }
            
            # Find matching command type
            for cmd_key, method_name in command_mapping.items():
                if cmd_key in payload:
                    command_str = payload[cmd_key].strip().lower()
                    cmd_type = cmd_key.replace("cmd-", "")
                    
                    try:
                        # Check if supervisor has the corresponding method
                        if self.supervisor and hasattr(self.supervisor, method_name):
                            # Dynamically call the corresponding method
                            getattr(self.supervisor, method_name)(command_str)
                            self.logger.info(f"{cmd_type.capitalize()} command executed: {command_str}")
                            return f"{cmd_type} command successfully executed"
                        else:
                            error_msg = f"Supervisor not available or missing {method_name} method"
                            self.logger.error(error_msg)
                            return error_msg
                    except ValueError:
                        error_msg = f"Invalid {cmd_type} command: {command_str}"
                        self.logger.error(error_msg)
                        return error_msg
                    except Exception as e:
                        error_msg = f"Error executing {cmd_type} command: {e}"
                        self.logger.error(error_msg)
                        return error_msg
                    
                    # If a command is found and processed, no need to check other command types
                    return
            
            # If no supported command is found
            error_msg = "Missing valid command in request. Supported commands: cmd-led, cmd-ota, cmd-thread, cmd-zigbee"
            self.logger.error(error_msg)
            return error_msg
        except json.JSONDecodeError:
            error_msg = f"Invalid JSON format: {data}"
            self.logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Unexpected error handling request: {e}"
            self.logger.error(error_msg)
            return error_msg
# maintainer: guoping.liu@3reality.com

import os
import threading
import time
import logging
import socket
import tempfile
import json
import sys
from io import StringIO
import queue

from .hardware import LedState


class SupervisorProxy:
    '''Connects SupervisorClient and Supervisor via local socket for local debugging and reuse of local functions by other modules.'''
    SOCKET_PATH = "/run/led_socket"  # Use /run directory, which is a memory filesystem and usually always writable
    BUFFER_SIZE = 4096  # Increase buffer size

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

    def _recv_all(self, conn, length):
        """Receive exactly 'length' bytes from socket"""
        data = b''
        while len(data) < length:
            packet = conn.recv(length - len(data))
            if not packet:
                break
            data += packet
        return data

    def _recv_json(self, conn):
        """Receive JSON data with length prefix"""
        try:
            # First, receive the length of the JSON data (4 bytes)
            raw_msglen = self._recv_all(conn, 4)
            if not raw_msglen:
                return None
            msglen = int.from_bytes(raw_msglen, 'big')
            
            # Receive the actual JSON data
            raw_msg = self._recv_all(conn, msglen)
            if not raw_msg:
                return None
            
            return json.loads(raw_msg.decode('utf-8'))
        except Exception as e:
            self.logger.error(f"Error receiving JSON: {e}")
            return None

    def _send_json(self, conn, obj):
        """Send JSON data with length prefix"""
        try:
            msg = json.dumps(obj).encode('utf-8')
            msglen = len(msg)
            # Send length first (4 bytes)
            conn.sendall(msglen.to_bytes(4, 'big'))
            # Send the actual data
            conn.sendall(msg)
        except Exception as e:
            self.logger.error(f"Error sending JSON: {e}")

    def _send_stream_chunk(self, conn, chunk_type, data):
        """Send a stream chunk with type and data"""
        chunk = {
            'type': chunk_type,
            'data': data,
            'timestamp': time.time()
        }
        self._send_json(conn, chunk)

    def run(self):
        self._setup_socket()

        logging.info("Starting local socket monitor...")
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
                # Handle each connection in a separate thread
                threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue

    def _handle_connection(self, conn):
        """Handle a single connection"""
        try:
            with conn:
                conn.settimeout(2.0)  # Set a reasonable timeout
                
                # Read first 4 bytes to check protocol
                first_4_bytes = conn.recv(4)
                if len(first_4_bytes) < 4:
                    return
                
                # Try to interpret as length prefix
                try:
                    potential_length = int.from_bytes(first_4_bytes, 'big')
                    # If it's a reasonable JSON length (1 to 1MB), assume new protocol
                    if 10 <= potential_length <= 1024*1024:
                        # Read the rest of the JSON data
                        remaining_data = self._recv_all(conn, potential_length)
                        if remaining_data:
                            payload = json.loads(remaining_data.decode('utf-8'))
                            self.logger.info(f"[Proxy] Using new protocol, received payload: {str(payload)[:100]}")
                            
                            # Check if this is a streaming command (like ptest)
                            if self._is_streaming_command(payload):
                                self.handle_streaming_request(conn, payload)
                                return
                            else:
                                response = self.handle_request_data(payload)
                                # For new protocol, send JSON response
                                self._send_json(conn, {'response': response})
                                return
                except:
                    pass
                
                # Fall back to old protocol - treat first_4_bytes as start of JSON
                remaining_data = conn.recv(4092)  # Read remaining data (4096 - 4)
                full_data = first_4_bytes + remaining_data
                
                try:
                    data_str = full_data.decode('utf-8').rstrip('\x00')  # Remove null padding
                    payload = json.loads(data_str)
                    self.logger.info(f"[Proxy] Using legacy protocol, received payload: {str(payload)[:100]}") 
                    
                    response = self.handle_request_data(payload)
                    # For legacy compatibility, send simple string response
                    conn.sendall(response.encode('utf-8'))
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse JSON in legacy mode: {e}, data: {full_data[:100]}")
                    conn.sendall(b"Invalid JSON format")
                    
        except Exception as e:
            self.logger.error(f"Error handling connection: {e}")

    def _is_streaming_command(self, payload):
        """Check if the command requires streaming response"""
        streaming_commands = ['cmd-ptest']
        return any(cmd in payload for cmd in streaming_commands)

    def handle_streaming_request(self, conn, payload):
        """Handle requests that need streaming responses"""
        self.logger.info(f"[Proxy HandleStreamingRequest] Start handling streaming request")
        
        try:
            if "cmd-ptest" in payload:
                ptest_mode = payload["cmd-ptest"].strip().lower()
                
                if ptest_mode == "start":
                    # Send start signal
                    self._send_stream_chunk(conn, 'start', 'Starting product test...')
                    
                    # Import and run ptest with real-time output capture
                    from .ptest.ptest import ProductTest
                    
                    # Create test instance and run with streaming output
                    test = ProductTest(supervisor=self.supervisor)
                    
                    # Override print function to capture output
                    original_print = print
                    def streaming_print(*args, **kwargs):
                        output = ' '.join(str(arg) for arg in args)
                        original_print(*args, **kwargs)  # Still print to console
                        self._send_stream_chunk(conn, 'output', output)
                    
                    # Replace print temporarily
                    import builtins
                    builtins.print = streaming_print
                    
                    try:
                        result = test.run_all_tests()
                        self._send_stream_chunk(conn, 'result', result)
                    finally:
                        builtins.print = original_print
                    
                    # Send completion signal
                    self._send_stream_chunk(conn, 'end', 'Product test completed')
                elif ptest_mode == "finish":
                    # Send start signal
                    self._send_stream_chunk(conn, 'start', 'Starting product test finish procedure...')
                    
                    # Import and run finish with real-time output capture
                    from .ptest.ptest import finish_product_test
                    
                    # Override logger to capture output
                    import logging
                    original_logger_info = logging.Logger.info
                    def streaming_logger_info(self_logger, msg, *args, **kwargs):
                        output = str(msg) % args if args else str(msg)
                        original_logger_info(self_logger, msg, *args, **kwargs)
                        self._send_stream_chunk(conn, 'output', output)
                    
                    # Temporarily replace logger.info
                    logging.Logger.info = streaming_logger_info
                    
                    try:
                        result = finish_product_test(supervisor=self.supervisor)
                        self._send_stream_chunk(conn, 'result', result)
                    finally:
                        logging.Logger.info = original_logger_info
                    
                    # Send completion signal
                    self._send_stream_chunk(conn, 'end', 'Product test finish procedure completed (system will reboot)')
                else:
                    self._send_stream_chunk(conn, 'error', f'Unknown ptest mode: {ptest_mode}. Supported modes: start, finish')
            else:
                self._send_stream_chunk(conn, 'error', 'Unsupported streaming command')
                
        except Exception as e:
            error_msg = f"Error in streaming request: {e}"
            self.logger.error(error_msg)
            self._send_stream_chunk(conn, 'error', error_msg)

    def handle_request_data(self, payload):
        """Handle request payload (dict) instead of JSON string"""
        self.logger.info(f"[Proxy HandleRequestData] Start handling request")
        try:
            # Handle LED command (support on/off/clear and colors)
            if "cmd-led" in payload:
                state_str = payload["cmd-led"].strip().lower()
                try:
                    # Handle on/off/clear/disable commands
                    if state_str in ("on", "enable", "enabled"):
                        if self.supervisor and hasattr(self.supervisor, 'led') and hasattr(self.supervisor.led, 'enable'):
                            self.supervisor.led.enable()
                            return "LED module enabled (on)"
                        return "LED module enable failed"
                    # "disable"/"disabled"/"off" for disabling LED module (persistent)
                    if state_str in ("disable", "disabled", "module_off", "off"):
                        if self.supervisor and hasattr(self.supervisor, 'led') and hasattr(self.supervisor.led, 'disable'):
                            self.supervisor.led.disable()
                            return "LED module disabled (off)"
                        return "LED module disable failed"
                    # "clear" for clearing user event state (USER_EVENT_OFF)
                    if state_str in ("clear",):
                        # Clear user event state, does not change module enable/disable status
                        if self.supervisor and hasattr(self.supervisor, 'set_led_state'):
                            self.supervisor.set_led_state(LedState.USER_EVENT_OFF)
                            return "LED user event cleared"
                        return "LED clear failed"

                    # Support mapping of simple color names to USER_EVENT
                    user_event_map = {
                        'red': LedState.USER_EVENT_RED,
                        'blue': LedState.USER_EVENT_BLUE,
                        'yellow': LedState.USER_EVENT_YELLOW,
                        'green': LedState.USER_EVENT_GREEN,
                        'white': LedState.USER_EVENT_WHITE,
                        'cyan': LedState.USER_EVENT_CYAN,
                        'magenta': LedState.USER_EVENT_MAGENTA,
                        'purple': LedState.USER_EVENT_MAGENTA,
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
                        self.logger.info(f"[Proxy HandleRequestData] Calling supervisor.set_led_state with {state}")
                        self.supervisor.set_led_state(state)
                        self.logger.info(f"[Proxy HandleRequestData] supervisor.set_led_state returned")
                    return "LED state has been set"
                except Exception as e:
                    error_msg = f"Error setting LED state: {e}"
                    self.logger.error(error_msg)
                    return error_msg

            # Handle ptest command (non-streaming mode for backward compatibility)
            if "cmd-ptest" in payload:
                ptest_mode = payload["cmd-ptest"].strip().lower()
                
                if ptest_mode == "start":
                    try:
                        from .ptest.ptest import run_product_test
                        result = run_product_test(supervisor=self.supervisor)
                        return f"Product test completed with result: {result}"
                    except Exception as e:
                        error_msg = f"Error running product test: {e}"
                        self.logger.error(error_msg)
                        return error_msg
                elif ptest_mode == "finish":
                    try:
                        from .ptest.ptest import finish_product_test
                        # Run in a separate thread to avoid blocking
                        import threading
                        def run_finish():
                            finish_product_test(supervisor=self.supervisor)
                        thread = threading.Thread(target=run_finish, daemon=True)
                        thread.start()
                        return "Product test finish procedure started (will reboot after completion)"
                    except Exception as e:
                        error_msg = f"Error starting product test finish: {e}"
                        self.logger.error(error_msg)
                        return error_msg
                else:
                    error_msg = f"Invalid ptest mode: {ptest_mode}. Supported modes: start, finish"
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
                            result = getattr(self.supervisor, method_name)(command_str)
                            self.logger.info(f"{cmd_type.capitalize()} command executed: {command_str}")
                            # Return the actual result instead of generic success message
                            return result if result is not None else f"{cmd_type} command successfully executed"
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
            
            # If no supported command is found
            error_msg = "Missing valid command in request. Supported commands: cmd-led, cmd-ota, cmd-thread, cmd-zigbee, cmd-ptest"
            self.logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Unexpected error handling request: {e}"
            self.logger.error(error_msg)
            return error_msg

    def stop(self):
        self.stop_event.set()
        if hasattr(self, 'proxy_thread') and self.proxy_thread.is_alive():
            self.proxy_thread.join(timeout=5)  # Wait for up to 5 seconds
            if self.proxy_thread.is_alive():
                self.logger.warning("Proxy thread did not terminate gracefully")

    def handle_request(self, data):
        """Legacy method for backward compatibility"""
        self.logger.info(f"[Proxy HandleRequest] Start handling request for data: {data[:100]}") 
        try:
            # Parse JSON data
            payload = json.loads(data)
            return self.handle_request_data(payload)
        except json.JSONDecodeError:
            error_msg = f"Invalid JSON format: {data}"
            self.logger.error(error_msg)
            return error_msg
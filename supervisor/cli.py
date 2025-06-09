# maintainer: guoping.liu@3reality.com

import socket
import logging
import json

class SupervisorClient:
    SOCKET_PATH = "/run/led_socket"
    TIMEOUT = 2.0  # Increased from 0.5s to handle potential delays
    
    def send_command(self, cmd_type, value, error_prefix="command"):
        """
        Generic method to send commands to the supervisor socket
        
        Args:
            cmd_type (str): Type of command (led, ota, zigbee, thread)
            value (str): Command value/parameter
            error_prefix (str): Prefix for error messages
            
        Returns:
            str: Response from server or None if error occurred
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.TIMEOUT)
                client.connect(self.SOCKET_PATH)
                
                # Format the command key based on the command type
                cmd_key = f"cmd-{cmd_type}"
                payload = json.dumps({cmd_key: value})
                
                # Send command and get response
                client.sendall(payload.encode('utf-8'))
                response = client.recv(1024).decode('utf-8')
                logging.info(f"Server response: {response}")
                return response
        except (socket.timeout, FileNotFoundError, ConnectionRefusedError) as e:
            logging.error(f"Error in {error_prefix}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in {error_prefix}: {e}")
        return None
   
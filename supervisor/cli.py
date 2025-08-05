# maintainer: guoping.liu@3reality.com

import socket
import logging
import json

class SupervisorClient:
    SOCKET_PATH = "/run/led_socket"
    TIMEOUT = 30.0  # Increased timeout for long-running commands
    
    def _recv_all(self, sock, length):
        """Receive exactly 'length' bytes from socket"""
        data = b''
        while len(data) < length:
            packet = sock.recv(length - len(data))
            if not packet:
                break
            data += packet
        return data

    def _recv_json(self, sock):
        """Receive JSON data with length prefix"""
        try:
            # First, receive the length of the JSON data (4 bytes)
            raw_msglen = self._recv_all(sock, 4)
            if not raw_msglen:
                return None
            msglen = int.from_bytes(raw_msglen, 'big')
            
            # Receive the actual JSON data
            raw_msg = self._recv_all(sock, msglen)
            if not raw_msg:
                return None
            
            return json.loads(raw_msg.decode('utf-8'))
        except Exception as e:
            logging.error(f"Error receiving JSON: {e}")
            return None

    def _send_json(self, sock, obj):
        """Send JSON data with length prefix"""
        try:
            msg = json.dumps(obj).encode('utf-8')
            msglen = len(msg)
            # Send length first (4 bytes)
            sock.sendall(msglen.to_bytes(4, 'big'))
            # Send the actual data
            sock.sendall(msg)
        except Exception as e:
            logging.error(f"Error sending JSON: {e}")

    def send_command(self, cmd_type, value, error_prefix="command"):
        """
        Generic method to send commands to the supervisor socket
        
        Args:
            cmd_type (str): Type of command (led, ota, zigbee, thread, ptest)
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
                payload = {cmd_key: value}
                
                # Check if this is a streaming command
                if cmd_type == 'ptest':
                    return self._handle_streaming_command(client, payload)
                else:
                    return self._handle_regular_command(client, payload)
                    
        except (socket.timeout, FileNotFoundError, ConnectionRefusedError) as e:
            logging.error(f"Error in {error_prefix}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in {error_prefix}: {e}")
        return None

    def _handle_regular_command(self, client, payload):
        """Handle regular (non-streaming) commands"""
        # Use old protocol for backward compatibility
        client.sendall(json.dumps(payload).encode('utf-8'))
        response = client.recv(4096).decode('utf-8')
        logging.info(f"Server response: {response}")
        return response

    def _handle_streaming_command(self, client, payload):
        """Handle streaming commands like ptest"""
        # Send command using new protocol
        self._send_json(client, payload)
        
        # Receive streaming responses
        print("Connecting to supervisor for streaming output...")
        
        while True:
            chunk = self._recv_json(client)
            if chunk is None:
                break
                
            chunk_type = chunk.get('type', 'unknown')
            data = chunk.get('data', '')
            
            if chunk_type == 'start':
                print(f"🚀 {data}")
            elif chunk_type == 'output':
                print(data)
            elif chunk_type == 'error':
                print(f"❌ Error: {data}")
                break
            elif chunk_type == 'result':
                result_text = "✅ SUCCESS" if data else "❌ FAILED"
                print(f"📊 Test Result: {result_text}")
            elif chunk_type == 'end':
                print(f"🏁 {data}")
                break
                
        return "Streaming command completed"
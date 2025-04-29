# maintainer: guoping.liu@thirdreality.com

import socket
import logging
import json

class SupervisorClient:
    SOCKET_PATH = "/tmp/led_socket"
    TIMEOUT = 0.5

    def set_led_state(self, state):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.TIMEOUT)
                client.connect(self.SOCKET_PATH)
                payload = json.dumps({"cmd-led": state})
                client.sendall(payload.encode('utf-8'))
                response = client.recv(1024).decode('utf-8')
                logging.info(f"Server response: {response}")
        except (socket.timeout, FileNotFoundError, ConnectionRefusedError) as e:
            logging.error(f"Error in setting LED state: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")

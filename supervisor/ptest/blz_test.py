#!/usr/bin/env python3
"""
Accurate BL702 communication test based on real logs
"""

import serial
import struct
import time
import binascii

class AccurateBL702Test:
    def __init__(self, verbose=False):
        self.ser = None
        self.tx_seq = 0  # TX sequence number
        self.verbose = verbose
        
    def compute_crc(self, data):
        """Compute CRC-16 (same algorithm as firmware/logs)."""
        def calc_crc16(new_byte, prev_result):
            prev_result = ((prev_result >> 8) | (prev_result << 8)) & 0xFFFF
            prev_result ^= new_byte
            prev_result ^= (prev_result & 0xFF) >> 4
            prev_result ^= ((prev_result << 8) << 4) & 0xFFFF
            prev_result ^= (((prev_result & 0xFF) << 5) | ((prev_result & 0xFF) >> 3) << 8) & 0xFFFF
            return prev_result
        
        crc16 = 0xFFFF
        for byte in data:
            crc16 = calc_crc16(byte, crc16)
        return struct.pack('>H', crc16)  # big-endian bytes
    
    def escape_frame(self, data):
        """Escape frame payload bytes."""
        escaped = bytearray()
        for byte in data:
            if byte in (0x42, 0x4C, 0x07):
                escaped.append(0x07)
                escaped.append(byte ^ 0x10)
            else:
                escaped.append(byte)
        return bytes(escaped)
    
    def unescape_frame(self, data):
        """Unescape frame payload bytes."""
        unescaped = bytearray()
        it = iter(data)
        for byte in it:
            if byte == 0x07:
                unescaped.append(next(it) ^ 0x10)
            else:
                unescaped.append(byte)
        return bytes(unescaped)
    
    def build_frame(self, frame_id, payload=b''):
        """Build a complete frame."""
        # As seen in logs: frmCtrl=0x00, combined seq byte
        seq = (self.tx_seq << 4) | 0  # TX seq in high 4 bits, RX seq = 0
        
        # Frame: frmCtrl + seq + frame_id (little-endian) + payload
        frame_data = struct.pack('<BBH', 0x00, seq, frame_id) + payload
        
        # Compute CRC and append
        crc = self.compute_crc(frame_data)
        frame_with_crc = frame_data + crc
        
        # Escape
        escaped = self.escape_frame(frame_with_crc)
        
        # Add frame delimiters
        final_frame = bytes([0x42]) + escaped + bytes([0x4C])
        
        # Advance sequence number
        self.tx_seq = (self.tx_seq + 1) % 16
        
        return final_frame
    
    def connect(self):
        """Open serial port."""
        try:
            self.ser = serial.Serial('/dev/ttyAML3', 2000000, timeout=3)
            if self.verbose:
                print(f"Serial open: {self.ser.name}")
            return True
        except Exception as e:
            if self.verbose:
                print(f"Open failed: {e}")
            return False
    
    def disconnect(self):
        """Close serial port."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            if self.verbose:
                print("Serial closed")
    
    def send_frame(self, frame):
        """Send a frame."""
        if not self.ser or not self.ser.is_open:
            return False
        
        if self.verbose:
            print(f"TX: {binascii.hexlify(frame).decode().upper()}")
        self.ser.write(frame)
        self.ser.flush()
        return True
    
    def wait_for_response(self, timeout=3.0):
        """Wait for a response frame."""
        buffer = bytearray()
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.ser.in_waiting > 0:
                data = self.ser.read(self.ser.in_waiting)
                buffer.extend(data)
                if self.verbose:
                    print(f"RX: {binascii.hexlify(data).decode().upper()}")
                
                # Find a complete frame
                while buffer:
                    start_idx = buffer.find(0x42)
                    if start_idx == -1:
                        buffer.clear()
                        break
                    
                    stop_idx = buffer.find(0x4C, start_idx + 1)
                    if stop_idx == -1:
                        break
                    
                    # Extract frame data
                    raw_frame = buffer[start_idx:stop_idx + 1]
                    frame_data = buffer[start_idx + 1:stop_idx]
                    buffer = buffer[stop_idx + 1:]
                    
                    if len(frame_data) == 0:
                        continue
                    
                    if self.verbose:
                        print(f"Frame: {binascii.hexlify(raw_frame).decode().upper()}")
                    
                    # Unescape
                    unescaped = self.unescape_frame(frame_data)
                    if self.verbose:
                        print(f"Unescaped: {binascii.hexlify(unescaped).decode().upper()}")
                    
                    if len(unescaped) >= 6:
                        return unescaped
            
            time.sleep(0.01)
        
        return None

    def wait_for_frame(self, expected_frame_ids, timeout=3.0):
        """Wait for a frame with an expected frame_id; ACK and ignore others.

        expected_frame_ids: iterable of expected frame ids (e.g. {0x0010})
        Returns: the matching unescaped frame bytes (header+payload+CRC), or None
        """
        buffer = bytearray()
        start_time = time.time()

        def try_extract_one():
            nonlocal buffer
            start_idx = buffer.find(0x42)
            if start_idx == -1:
                buffer.clear()
                return None
            stop_idx = buffer.find(0x4C, start_idx + 1)
            if stop_idx == -1:
                return None

            raw_frame = buffer[start_idx:stop_idx + 1]
            frame_body = buffer[start_idx + 1:stop_idx]
            buffer = buffer[stop_idx + 1:]

            if len(frame_body) == 0:
                return None

            if self.verbose:
                print(f"Frame: {binascii.hexlify(raw_frame).decode().upper()}")
            unescaped = self.unescape_frame(frame_body)
            if self.verbose:
                print(f"Unescaped: {binascii.hexlify(unescaped).decode().upper()}")

            if len(unescaped) < 6:
                return None

            frmCtrl, seq, frame_id = struct.unpack('<BBH', unescaped[:4])

            # ACK any data frame
            self.send_ack(unescaped)

            if frame_id in expected_frame_ids:
                return unescaped
            else:
                if self.verbose:
                    print(f"Ignore non-target frame_id=0x{frame_id:04X}")
                return None

        if self.verbose:
            print(f"Waiting for frame_id in {', '.join(f'0x{x:04X}' for x in expected_frame_ids)} (timeout {timeout}s)...")
        while time.time() - start_time < timeout:
            if self.ser.in_waiting > 0:
                chunk = self.ser.read(self.ser.in_waiting)
                buffer.extend(chunk)
                if self.verbose:
                    print(f"RX: {binascii.hexlify(chunk).decode().upper()}")

                while True:
                    fr = try_extract_one()
                    if fr is not None:
                        return fr
                    # no complete frame available, or no more frames after extraction
                    if buffer.find(0x42) == -1:
                        break

            time.sleep(0.01)

        return None
    
    def send_ack(self, received_frame):
        """Send ACK frame (based on observed log behavior)."""
        if len(received_frame) < 2:
            return
        
        rx_seq = received_frame[1] & 0x0F  # extract RX sequence
        tx_seq = rx_seq << 4  # ACK's TX sequence
        
        ack_data = struct.pack('<BBH', 0x00, tx_seq, 0x0001)  # ACK frame_id = 0x0001
        crc = self.compute_crc(ack_data)
        ack_frame_with_crc = ack_data + crc
        escaped = self.escape_frame(ack_frame_with_crc)
        final_ack = bytes([0x42]) + escaped + bytes([0x4C])
        
        if self.verbose:
            print(f"TX ACK: {binascii.hexlify(final_ack).decode().upper()}")
        self.ser.write(final_ack)
        self.ser.flush()
    
    def parse_get_value_response(self, frame_data):
        """Parse GET_VALUE response."""
        if len(frame_data) < 6:
            return None
        
        frmCtrl, seq, frame_id = struct.unpack('<BBH', frame_data[:4])
        payload = frame_data[4:-2]  # strip CRC
        
        if self.verbose:
            print(f"Frame parse:")
            print(f"   frmCtrl: 0x{frmCtrl:02X}")
            print(f"   seq: 0x{seq:02X}")
            print(f"   frame_id: 0x{frame_id:04X}")
        
        if frame_id == 0x0010 and len(payload) >= 2:  # GET_VALUE response
            status = payload[0]
            value_length = payload[1]
            value = payload[2:2+value_length] if len(payload) >= 2+value_length else b''
            
            if self.verbose:
                print(f"   status: {status} ({'ok' if status == 0 else 'error'})")
                print(f"   value_length: {value_length}")
                print(f"   raw: {binascii.hexlify(value).decode().upper()}")
            
            if status == 0:
                return value
        
        return None
    
    def network_init(self):
        """Network initialization."""
        if self.verbose:
            print("\nNetwork init...")
        
        # Send NETWORK_INIT (0x0034)
        frame = self.build_frame(0x0034)
        if not self.send_frame(frame):
            return False
        
        # Wait for 0x0034 response; ignore 0x0035 callbacks
        response = self.wait_for_frame({0x0034}, timeout=5.0)
        if response:
            if self.verbose:
                print("Network init ok")
            return True
        else:
            if self.verbose:
                print("Network init failed (no 0x0034)")
            return False
    
    def get_mac_address(self):
        """Read MAC address."""
        if self.verbose:
            print("\nRead MAC address...")
        
        # Build GET_VALUE for value_id = 0x20
        payload = struct.pack('<B', 0x20)
        frame = self.build_frame(0x0010, payload)
        
        if not self.send_frame(frame):
            return None
        
        # Wait for 0x0010 response; ignore other frames
        response = self.wait_for_frame({0x0010}, timeout=5.0)
        if response:
            value = self.parse_get_value_response(response)
            
            if value and len(value) == 8:
                # Example: raw 00005ae24c75e14c (LE) -> 4c:e1:75:4c:e2:5a:00:00
                mac_str = ':'.join(f'{b:02x}' for b in reversed(value))
                if self.verbose:
                    print(f"MAC: {mac_str}")
                return mac_str
        
        if self.verbose:
            print("Read MAC failed")
        return None
    
    def get_app_version(self):
        """Read application version."""
        if self.verbose:
            print("\nRead application version...")
        
        # Build GET_VALUE for value_id = 0x21
        payload = struct.pack('<B', 0x21)
        frame = self.build_frame(0x0010, payload)
        
        if not self.send_frame(frame):
            return None
        
        # Wait for 0x0010 response; ignore other frames
        response = self.wait_for_frame({0x0010}, timeout=5.0)
        if response:
            value = self.parse_get_value_response(response)
            
            if value:
                try:
                    version = value.decode('utf-8').rstrip('\x00')
                    if self.verbose:
                        print(f"App version: {version}")
                    return version
                except:
                    version_hex = binascii.hexlify(value).decode()
                    if self.verbose:
                        print(f"App version (hex): {version_hex}")
                    return version_hex
        
        if self.verbose:
            print("Read app version failed")
        return None

    def get_stack_version(self):
        """Read stack version (build, major, minor, patch)."""
        if self.verbose:
            print("\nRead stack version...")
        payload = struct.pack('<B', 0x01)  # BLZ_VALUE_ID_STACK_VERSION
        frame = self.build_frame(0x0010, payload)
        if not self.send_frame(frame):
            return None
        response = self.wait_for_frame({0x0010}, timeout=5.0)
        if response:
            value = self.parse_get_value_response(response)
            if value and len(value) >= 5:
                build = value[0] | (value[1] << 8)
                major = value[2]
                minor = value[3]
                patch = value[4]
                info = {"build": build, "major": major, "minor": minor, "patch": patch}
                if self.verbose:
                    print(f"Stack version: build={build}, {major}.{minor}.{patch}")
                return info
        if self.verbose:
            print("Read stack version failed")
        return None

    def get_network_parameters(self):
        """Read network parameters (GET_NETWORK_PARAMETERS, frame_id=0x002B)."""
        if self.verbose:
            print("\nRead network parameters...")
        frame = self.build_frame(0x002B)
        if not self.send_frame(frame):
            return None
        resp = self.wait_for_frame({0x002B}, timeout=5.0)
        if not resp:
            if self.verbose:
                print("Read network parameters failed (timeout)")
            return None

        # Parse payload: status(1) node_type(1) ext_pan_id(8 LE) pan_id(2 LE)
        #                tx_power(1) channel(1) nwk_manager(2 LE) nwk_update_id(1) channel_mask(4 LE)
        payload = resp[4:-2]
        if len(payload) < 1 + 1 + 8 + 2 + 1 + 1 + 2 + 1 + 4:
            if self.verbose:
                print("Network parameters payload length error")
            return None
        idx = 0
        status = payload[idx]; idx += 1
        node_type = payload[idx]; idx += 1
        ext_pan_id = int.from_bytes(payload[idx:idx+8], 'little'); idx += 8
        pan_id = int.from_bytes(payload[idx:idx+2], 'little'); idx += 2
        tx_power = payload[idx]; idx += 1
        channel = payload[idx]; idx += 1
        nwk_manager = int.from_bytes(payload[idx:idx+2], 'little'); idx += 2
        nwk_update_id = payload[idx]; idx += 1
        channel_mask = int.from_bytes(payload[idx:idx+4], 'little'); idx += 4

        info = {
            "status": status,
            "node_type": node_type,
            "ext_pan_id": ext_pan_id,
            "pan_id": pan_id,
            "tx_power": tx_power,
            "channel": channel,
            "nwk_manager": nwk_manager,
            "nwk_update_id": nwk_update_id,
            "channel_mask": channel_mask,
        }
        if self.verbose:
            print(
                f"Network params: node_type={node_type}, ext_pan_id=0x{ext_pan_id:016X}, pan_id=0x{pan_id:04X}, "
                f"tx_power={tx_power}, channel={channel}, nwk_manager=0x{nwk_manager:04X}, "
                f"nwk_update_id={nwk_update_id}, channel_mask=0x{channel_mask:08X}"
            )
        return info

# New interface functions for ptest.py to call
def get_blz_mac(uart_device="/dev/ttyAML3", baudrate=2000000, timeout=3.0, verbose=False):
    """
    Get BL702 MAC address
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        verbose (bool): Enable verbose logging
        
    Returns:
        str: MAC address string or None if failed
    """
    try:
        tester = AccurateBL702Test(verbose=verbose)
        if not tester.connect():
            return None
        
        try:
            # Flush pending RX
            time.sleep(0.1)
            if tester.ser.in_waiting > 0:
                tester.ser.read(tester.ser.in_waiting)
            
            # Network init
            if not tester.network_init():
                if verbose:
                    print("Network init failed, continue...")
            
            time.sleep(0.5)
            
            # Read MAC
            mac = tester.get_mac_address()
            return mac
            
        finally:
            tester.disconnect()
    except Exception as e:
        if verbose:
            print(f"Failed to get BL702 MAC: {e}")
        return None

def get_blz_version(uart_device="/dev/ttyAML3", baudrate=2000000, timeout=3.0, verbose=False):
    """
    Get BL702 application version
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        verbose (bool): Enable verbose logging
        
    Returns:
        str: Application version string or None if failed
    """
    try:
        tester = AccurateBL702Test(verbose=verbose)
        if not tester.connect():
            return None
        
        try:
            # Flush pending RX
            time.sleep(0.1)
            if tester.ser.in_waiting > 0:
                tester.ser.read(tester.ser.in_waiting)
            
            # Network init
            if not tester.network_init():
                if verbose:
                    print("Network init failed, continue...")
            
            time.sleep(0.5)
            
            # Read application version
            version = tester.get_app_version()
            return version
            
        finally:
            tester.disconnect()
    except Exception as e:
        if verbose:
            print(f"Failed to get BL702 version: {e}")
        return None

def get_blz_stack_version(uart_device="/dev/ttyAML3", baudrate=2000000, timeout=3.0, verbose=False):
    """
    Get BL702 stack version
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        verbose (bool): Enable verbose logging
        
    Returns:
        dict: Stack version info or None if failed
    """
    try:
        tester = AccurateBL702Test(verbose=verbose)
        if not tester.connect():
            return None
        
        try:
            # Flush pending RX
            time.sleep(0.1)
            if tester.ser.in_waiting > 0:
                tester.ser.read(tester.ser.in_waiting)
            
            # Network init
            if not tester.network_init():
                if verbose:
                    print("Network init failed, continue...")
            
            time.sleep(0.5)
            
            # Read stack version
            stack_ver = tester.get_stack_version()
            return stack_ver
            
        finally:
            tester.disconnect()
    except Exception as e:
        if verbose:
            print(f"Failed to get BL702 stack version: {e}")
        return None

def get_blz_info(uart_device="/dev/ttyAML3", baudrate=2000000, timeout=3.0, verbose=False):
    """
    Get all BL702 information (MAC, app version, stack version)
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        verbose (bool): Enable verbose logging
        
    Returns:
        dict: Dictionary containing BL702 information or None if failed
    """
    try:
        tester = AccurateBL702Test(verbose=verbose)
        if not tester.connect():
            return None
        
        try:
            # Flush pending RX
            time.sleep(0.1)
            if tester.ser.in_waiting > 0:
                tester.ser.read(tester.ser.in_waiting)
            
            # Network init
            if not tester.network_init():
                if verbose:
                    print("Network init failed, continue...")
            
            time.sleep(0.5)
            
            info = {}
            
            # Get MAC
            try:
                info['mac'] = tester.get_mac_address()
            except Exception as e:
                info['mac'] = None
                if verbose:
                    print(f"Failed to get MAC: {e}")
            
            time.sleep(0.5)
            
            # Get application version
            try:
                info['version'] = tester.get_app_version()
            except Exception as e:
                info['version'] = None
                if verbose:
                    print(f"Failed to get app version: {e}")
            
            time.sleep(0.5)
            
            # Get stack version
            try:
                info['stack_version'] = tester.get_stack_version()
            except Exception as e:
                info['stack_version'] = None
                if verbose:
                    print(f"Failed to get stack version: {e}")
            
            return info
            
        finally:
            tester.disconnect()
    except Exception as e:
        if verbose:
            print(f"Failed to get BL702 info: {e}")
        return None

def main():
    print("Accurate BL702 Communication Test")
    print("Based on real log analysis")
    print("=" * 50)
    
    tester = AccurateBL702Test(verbose=True)
    
    try:
        # Open serial
        if not tester.connect():
            return
        
        # Flush pending RX
        time.sleep(0.1)
        if tester.ser.in_waiting > 0:
            old_data = tester.ser.read(tester.ser.in_waiting)
            print(f"Flush old data: {binascii.hexlify(old_data).decode().upper()}")
        
        # Network init
        if not tester.network_init():
            print("Network init failed, continue...")
        
        time.sleep(0.5)
        
        # Read MAC
        mac = tester.get_mac_address()
        
        time.sleep(0.5)
        
        # Read application version
        version = tester.get_app_version()

        time.sleep(0.5)

        # Read stack version
        stack_ver = tester.get_stack_version()

        time.sleep(0.5)

        # Read network params
        nwk_params = tester.get_network_parameters()
        
        # Summary
        print("\n" + "=" * 50)
        print("Results:")
        print(f"   MAC: {mac if mac else 'failed'}")
        print(f"   App version: {version if version else 'failed'}")
        print(f"   Stack version: {stack_ver if stack_ver else 'failed'}")
        print(f"   Network params: {nwk_params if nwk_params else 'failed'}")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Depends only on pyserial; minimal Spinel+HDLC to read version/channel/panid/txpower

import argparse
import struct
import sys
import time

try:
    import serial
except ImportError:
    print("Please install pyserial first: pip install pyserial", file=sys.stderr)
    sys.exit(1)

# HDLC constants
HDLC_FLAG = 0x7E
HDLC_ESC = 0x7D
HDLC_FCS_INIT = 0xFFFF
HDLC_FCS_POLY = 0x8408
HDLC_FCS_GOOD = 0xF0B8

# Spinel commands and properties
CMD_PROP_VALUE_GET = 2
RSP_PROP_VALUE_IS = 6
HEADER_DEFAULT = 0x81

PROP_NCP_VERSION = 2             # U
PROP_PHY_CHAN = 33               # C
PROP_PHY_TX_POWER = 37           # c
PROP_MAC_15_4_PANID = 54         # S
PROP_LAST_STATUS = 0             # i

STATUS_PROPERTY_NOT_FOUND = 13

def varint_encode(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

def varint_decode(buf: bytes, off: int = 0):
    value, shift, i = 0, 0, 0
    while True:
        b = buf[off + i]
        value |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            break
        shift += 7
    return value, i  # (value, bytes_consumed)

def fcs_update(fcs: int, byte: int) -> int:
    fcs ^= byte
    for _ in range(8):
        if fcs & 1:
            fcs = (fcs >> 1) ^ HDLC_FCS_POLY
        else:
            fcs >>= 1
    return fcs

def hdlc_encode(payload: bytes) -> bytes:
    fcs = HDLC_FCS_INIT
    out = bytearray([HDLC_FLAG])

    def put(b: int):
        if b in (HDLC_FLAG, HDLC_ESC):
            out.append(HDLC_ESC)
            out.append(b ^ 0x20)
        else:
            out.append(b)

    for b in payload:
        fcs = fcs_update(fcs, b)
        put(b)

    fcs ^= 0xFFFF
    put(fcs & 0xFF)
    put((fcs >> 8) & 0xFF)
    out.append(HDLC_FLAG)
    return bytes(out)

def hdlc_read_frame(ser: serial.Serial, overall_timeout_s: float = 5.0) -> bytes | None:
    start = time.time()
    buf = bytearray()
    in_frame = False
    esc = False
    fcs = HDLC_FCS_INIT

    while time.time() - start < overall_timeout_s:
        b = ser.read(1)
        if not b:
            continue
        byte = b[0]

        if not in_frame:
            if byte == HDLC_FLAG:
                in_frame = True
                buf.clear()
                fcs = HDLC_FCS_INIT
                esc = False
            continue

        if byte == HDLC_FLAG:
            if len(buf) >= 2 and fcs == HDLC_FCS_GOOD:
                # Remove the two FCS bytes
                return bytes(buf[:-2])
            # Resynchronize
            in_frame = True
            buf.clear()
            fcs = HDLC_FCS_INIT
            esc = False
            continue

        if byte == HDLC_ESC:
            esc = True
            continue

        if esc:
            byte ^= 0x20
            esc = False

        buf.append(byte)
        fcs = fcs_update(fcs, byte)

    return None

def spinel_build_get(prop_id: int, tid: int = HEADER_DEFAULT) -> bytes:
    return bytes([tid]) + varint_encode(CMD_PROP_VALUE_GET) + varint_encode(prop_id)

def spinel_parse_response(frame: bytes):
    tid = frame[0]
    cmd_id, n1 = varint_decode(frame, 1)
    payload = frame[1 + n1:]
    return tid, cmd_id, payload

def _read_prop_raw(ser: serial.Serial, prop_id: int, timeout=2.0) -> bytes:
    req = spinel_build_get(prop_id)
    ser.write(hdlc_encode(req))
    deadline = time.time() + timeout

    while time.time() < deadline:
        frame = hdlc_read_frame(ser, overall_timeout_s=max(0.0, deadline - time.time()))
        if not frame:
            continue
        tid, cmd_id, pay = spinel_parse_response(frame)
        if cmd_id != RSP_PROP_VALUE_IS:
            continue
        got_prop, n = varint_decode(pay, 0)

        # Handle LAST_STATUS first (returned when the property is unsupported, etc.)
        if got_prop == PROP_LAST_STATUS:
            status, _ = varint_decode(pay, n)
            raise RuntimeError(f"Spinel LAST_STATUS={status} for prop {prop_id}")

        if got_prop != prop_id:
            continue
        return pay[n:]  # Return the raw value bytes of this property

    raise TimeoutError(f"No response for prop {prop_id}")

def get_prop_U(ser: serial.Serial, prop_id: int, timeout=2.0) -> str:
    val = _read_prop_raw(ser, prop_id, timeout)
    zero = val.find(b'\x00')
    if zero >= 0:
        val = val[:zero]
    return val.decode('utf-8', errors='ignore')

def get_prop_C(ser: serial.Serial, prop_id: int, timeout=2.0) -> int:
    val = _read_prop_raw(ser, prop_id, timeout)
    if len(val) < 1:
        raise ValueError("prop C length < 1")
    return val[0]

def get_prop_S(ser: serial.Serial, prop_id: int, timeout=2.0) -> int:
    val = _read_prop_raw(ser, prop_id, timeout)
    if len(val) < 2:
        raise ValueError("prop S length < 2")
    return struct.unpack('<H', val[:2])[0]

def get_prop_c(ser: serial.Serial, prop_id: int, timeout=2.0) -> int:
    val = _read_prop_raw(ser, prop_id, timeout)
    if len(val) < 1:
        raise ValueError("prop c length < 1")
    return struct.unpack('<b', val[:1])[0]

# New interface functions for ptest.py to call
def get_rcp_version(uart_device="/dev/ttyAML6", baudrate=115200, timeout=2.0):
    """
    Get RCP version information
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        
    Returns:
        str: Version string or None if failed
    """
    try:
        ser = serial.Serial(uart_device, baudrate, timeout=0.2)
        try:
            version = get_prop_U(ser, PROP_NCP_VERSION, timeout=timeout)
            return version
        finally:
            ser.close()
    except Exception as e:
        print(f"Failed to get RCP version: {e}", file=sys.stderr)
        return None

def get_rcp_channel(uart_device="/dev/ttyAML6", baudrate=115200, timeout=2.0):
    """
    Get RCP channel information
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        
    Returns:
        int: Channel number or None if failed
    """
    try:
        ser = serial.Serial(uart_device, baudrate, timeout=0.2)
        try:
            channel = get_prop_C(ser, PROP_PHY_CHAN, timeout=timeout)
            return channel
        finally:
            ser.close()
    except Exception as e:
        print(f"Failed to get RCP channel: {e}", file=sys.stderr)
        return None

def get_rcp_info(uart_device="/dev/ttyAML6", baudrate=115200, timeout=2.0):
    """
    Get all RCP information (version, channel, panid, txpower)
    
    Args:
        uart_device (str): Serial device path
        baudrate (int): Baud rate
        timeout (float): Timeout in seconds
        
    Returns:
        dict: Dictionary containing RCP information or None if failed
    """
    try:
        ser = serial.Serial(uart_device, baudrate, timeout=0.2)
        try:
            info = {}
            
            # Get version
            try:
                info['version'] = get_prop_U(ser, PROP_NCP_VERSION, timeout=timeout)
            except Exception as e:
                info['version'] = None
                print(f"Failed to get version: {e}", file=sys.stderr)
            
            # Get channel
            try:
                info['channel'] = get_prop_C(ser, PROP_PHY_CHAN, timeout=timeout)
            except Exception as e:
                info['channel'] = None
                print(f"Failed to get channel: {e}", file=sys.stderr)
            
            # Get panid
            try:
                info['panid'] = get_prop_S(ser, PROP_MAC_15_4_PANID, timeout=timeout)
            except Exception as e:
                info['panid'] = None
                print(f"Failed to get panid: {e}", file=sys.stderr)
            
            # Get txpower
            try:
                info['txpower'] = get_prop_c(ser, PROP_PHY_TX_POWER, timeout=timeout)
            except Exception as e:
                info['txpower'] = None
                print(f"Failed to get txpower: {e}", file=sys.stderr)
            
            return info
        finally:
            ser.close()
    except Exception as e:
        print(f"Failed to get RCP info: {e}", file=sys.stderr)
        return None

def main():
    ap = argparse.ArgumentParser(description="Read RCP basic info via Spinel over UART (HDLC)")
    ap.add_argument("-u", "--uart", default="/dev/ttyAML6", help="Serial device, e.g. /dev/ttyAML6")
    ap.add_argument("-b", "--baud", type=int, default=115200, help="Baudrate")
    ap.add_argument("--timeout", type=float, default=2.0, help="Per-property timeout (seconds)")
    args = ap.parse_args()

    ser = serial.Serial(args.uart, args.baud, timeout=0.2)
    try:
        # version (U)
        ver = get_prop_U(ser, PROP_NCP_VERSION, timeout=args.timeout)
        print("Version:", ver)

        # channel (C)
        ch = get_prop_C(ser, PROP_PHY_CHAN, timeout=args.timeout)
        print("Channel:", ch)

        # panid (S, little-endian)
        panid = get_prop_S(ser, PROP_MAC_15_4_PANID, timeout=args.timeout)
        print("PANID: 0x%04X" % panid)

        # txpower (c, signed dBm)
        txp = get_prop_c(ser, PROP_PHY_TX_POWER, timeout=args.timeout)
        print("TxPower (dBm):", txp)

    finally:
        ser.close()

if __name__ == "__main__":
    main()
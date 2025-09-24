#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import logging
import threading
import subprocess
from typing import Optional, Dict

from zeroconf import Zeroconf, ServiceInfo


logger = logging.getLogger("Supervisor")


class ZeroconfManager:
    """
    Manage Zeroconf service registration and unregistration.

    Usage:
        zm = ZeroconfManager(
            service_type="_linuxbox._tcp.local.",
            service_name_template="HUB-{mac}._linuxbox._tcp.local.",
            service_port=8086,
            properties={"version": "v1.0.0"}
        )
        zm.start(ip_address)
        zm.update_ip(new_ip)
        zm.stop()
    """

    def __init__(
        self,
        service_type: str = "_linuxbox._tcp.local.",
        service_name_template: str = "HUB-{mac}._linuxbox._tcp.local.",
        service_port: int = 8086,
        properties: Optional[Dict[str, str]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._zeroconf: Optional[Zeroconf] = None
        self._info: Optional[ServiceInfo] = None
        self._ip: Optional[str] = None

        self._service_type = service_type
        self._service_name_template = service_name_template
        self._service_port = service_port
        self._properties = properties or {}

    def start(self, ip_address: str) -> bool:
        """Register Zeroconf service on the given IP (must be a valid IPv4)."""
        with self._lock:
            logger.debug(f"ZeroconfManager.start called with IP: {ip_address}")
            
            if not self._is_valid_ipv4(ip_address):
                logger.warning(f"Invalid IPv4 address: {ip_address}")
                return False

            try:
                addr_bytes = socket.inet_aton(ip_address)
                logger.debug(f"IP address converted to bytes: {addr_bytes}")
            except OSError as e:
                logger.error(f"Failed to convert IP address {ip_address}: {e}")
                return False

            if self._zeroconf is None:
                try:
                    self._zeroconf = Zeroconf()
                    logger.debug("Zeroconf instance created")
                except Exception as e:
                    logger.error(f"Failed to create Zeroconf instance: {e}")
                    return False

            # Unregister previous service if it exists
            if self._info is not None:
                try:
                    self._zeroconf.unregister_service(self._info)
                    logger.debug("Previous service unregistered")
                except Exception as e:
                    logger.warning(f"Failed to unregister previous service: {e}")
                self._info = None

            # Get MAC address and generate service name
            mac_address = self._get_wlan0_mac()
            service_name = self._service_name_template.format(mac=mac_address)
            logger.debug(f"Generated service name: {service_name} (MAC: {mac_address})")

            try:
                info = ServiceInfo(
                    type_=self._service_type,
                    name=service_name,
                    addresses=[addr_bytes],
                    port=self._service_port,
                    properties=self._properties,
                    server=service_name,
                    weight=0,
                    priority=0,
                )
                logger.debug("ServiceInfo created successfully")
            except Exception as e:
                logger.error(f"Failed to create ServiceInfo: {e}", exc_info=True)
                logger.error(f"ServiceInfo parameters - type: {self._service_type}, name: {service_name}, port: {self._service_port}, properties: {self._properties}")
                return False

            try:
                self._zeroconf.register_service(info)
                self._info = info
                self._ip = ip_address
                logger.info(
                    f"Zeroconf registered: {service_name} {self._service_type} at {ip_address}:{self._service_port}"
                )
                return True
            except Exception as e:
                logger.error(f"Failed to register Zeroconf service: {e}")
                return False

    def stop(self) -> None:
        """Unregister the Zeroconf service."""
        with self._lock:
            if self._zeroconf and self._info:
                try:
                    self._zeroconf.unregister_service(self._info)
                    logger.info(f"Zeroconf service unregistered: {self._info.name}")
                except Exception as e:
                    logger.warning(f"Failed to unregister Zeroconf service: {e}")
                finally:
                    self._info = None
            else:
                logger.debug("No Zeroconf service to unregister")

    def update_ip(self, ip_address: Optional[str]) -> None:
        """
        Handle IP changes:
        - valid new IP: re-register on the new IP
        - invalid/empty IP: stop advertising
        """
        with self._lock:
            if ip_address and self._is_valid_ipv4(ip_address):
                if ip_address != self._ip:
                    self.start(ip_address)
            else:
                self.stop()
                self._ip = None

    def _get_wlan0_mac(self) -> str:
        """Get MAC address of wlan0 interface (last 8 characters, uppercase)."""
        try:
            result = subprocess.run(
                ["cat", "/sys/class/net/wlan0/address"],
                capture_output=True,
                text=True,
                check=True
            )
            mac = result.stdout.strip().replace(":", "").upper()
            # Return last 8 characters of MAC address
            return mac[-8:] if len(mac) >= 8 else mac
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to a default MAC if wlan0 not available
            logger.warning("Failed to get wlan0 MAC address, using default")
            return "UNKNOWN"

    @staticmethod
    def _is_valid_ipv4(ip: Optional[str]) -> bool:
        if not ip or ip in ["", "0.0.0.0"]:
            return False
        try:
            socket.inet_aton(ip)
            return True
        except OSError:
            return False



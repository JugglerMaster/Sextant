from __future__ import annotations

import re
import socket
from typing import Callable

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def normalize_mac(mac: str) -> str:
    value = (mac or "").strip()
    if not MAC_RE.match(value):
        raise ValueError("MAC address must look like AA:BB:CC:DD:EE:FF")
    return value.replace("-", ":").upper()


def build_magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(normalize_mac(mac).replace(":", ""))
    return b"\xff" * 6 + raw * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "255.255.255.255",
    port: int = 9,
    packets: int = 3,
    sock_factory: Callable[[], socket.socket] | None = None,
) -> int:
    payload = build_magic_packet(mac)
    if not 1 <= int(port) <= 65535:
        raise ValueError("WoL port out of range")
    count = max(1, min(int(packets), 10))
    sock = (sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)))()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(count):
            sock.sendto(payload, (broadcast, int(port)))
    finally:
        sock.close()
    return count

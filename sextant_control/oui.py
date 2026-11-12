"""Bundled, small OUI lookup table for discovery.

This is intentionally not a complete IEEE registry. It covers the vendors most
often seen on a home lab or small office LAN and keeps the footprint small
enough to ship with the server. Unknown prefixes return an empty string rather
than a guess.
"""

from __future__ import annotations

import re

OUI_VENDORS: dict[str, str] = {
    # Virtualization and hypervisors
    "00:50:56": "VMware",
    "00:0C:29": "VMware",
    "00:05:69": "VMware",
    "00:1C:14": "VMware",
    "00:1C:42": "Parallels",
    "08:00:27": "Oracle VirtualBox",
    "52:54:00": "QEMU/KVM",
    "00:15:5D": "Microsoft Hyper-V",
    "00:03:FF": "Microsoft",
    "00:0D:3A": "Microsoft",
    "00:12:5A": "Microsoft",
    "00:17:FA": "Microsoft",
    "00:1D:D8": "Microsoft",
    "00:22:48": "Microsoft",
    "00:50:F2": "Microsoft",
    "7C:ED:8D": "Microsoft",
    # Single-board computers and maker devices
    "B8:27:EB": "Raspberry Pi Foundation",
    "DC:A6:32": "Raspberry Pi Trading",
    "E4:5F:01": "Raspberry Pi Trading",
    "2C:CF:67": "Raspberry Pi Trading",
    "D8:3A:DD": "Raspberry Pi Trading",
    # Phones, tablets, laptops
    "00:03:93": "Apple",
    "00:0A:27": "Apple",
    "00:0A:95": "Apple",
    "00:1B:63": "Apple",
    "00:1E:C2": "Apple",
    "00:23:DF": "Apple",
    "00:25:00": "Apple",
    "00:26:B0": "Apple",
    "3C:07:54": "Apple",
    "3C:22:FB": "Apple",
    "F0:18:98": "Apple",
    "F0:DB:E2": "Apple",
    # NAS and storage
    "00:11:32": "Synology",
    "24:5E:BE": "QNAP",
    "00:90:A9": "Western Digital",
    "00:08:9B": "Buffalo",
    # Printers
    "00:00:AA": "Xerox",
    "00:1B:A9": "Brother",
    "00:80:77": "Brother",
    "3C:2A:F4": "Brother",
    "00:1E:8F": "Canon",
    "A4:5E:60": "Epson",
    "00:26:AB": "Seiko Epson",
    "3C:52:82": "HP",
    "00:1F:29": "HP",
    "00:21:5A": "HP",
    # Network infrastructure
    "00:04:20": "Linksys",
    "00:0C:85": "Cisco",
    "00:1A:A1": "Cisco",
    "00:1D:7E": "Cisco-Linksys",
    "00:18:39": "Cisco-Linksys",
    "00:1E:2A": "Netgear",
    "00:24:B2": "Netgear",
    "00:14:6C": "Netgear",
    "00:1D:0F": "TP-Link",
    "50:C7:BF": "TP-Link",
    "00:15:6D": "Ubiquiti",
    "24:A4:3C": "Ubiquiti",
    "78:8A:20": "Ubiquiti",
    "00:0B:86": "Aruba",
    "00:1A:1E": "Aruba",
    # Media, IoT, and voice
    "00:17:88": "Philips Hue",
    "00:1A:11": "Google",
    "3C:5A:B4": "Google",
    "54:60:09": "Google",
    "F4:F5:E8": "Google",
    "00:1D:BA": "Sony",
    "00:04:1F": "Sony",
    "00:09:BF": "Nintendo",
    "00:1F:32": "Nintendo",
    # Servers and workstations
    "00:24:E8": "Dell",
    "00:1E:C9": "Dell",
    "00:21:9B": "Dell",
    "00:0E:0C": "Intel",
    "00:1B:21": "Intel",
    "00:1E:67": "Intel",
    "00:1F:3B": "Intel",
    "3C:97:0E": "Wistron",
    "00:1C:B3": "Apple",
}


def normalize_mac(mac: str | None) -> str:
    """Return a lower-case, colon-separated MAC or an empty string."""
    if not mac:
        return ""
    value = str(mac).strip().lower().replace("-", ":").replace(".", "")
    if len(value) == 12 and ":" not in value:
        value = ":".join(value[index : index + 2] for index in range(0, 12, 2))
    if not re.match(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", value):
        return ""
    if value in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        return ""
    return value


def is_locally_administered(mac: str | None) -> bool:
    """True when the locally-administered bit is set (randomized MAC)."""
    normalized = normalize_mac(mac)
    if not normalized:
        return False
    try:
        first = int(normalized[:2], 16)
    except ValueError:
        return False
    return bool(first & 0x02)


def lookup_vendor(mac: str | None) -> str:
    normalized = normalize_mac(mac)
    if not normalized:
        return ""
    return OUI_VENDORS.get(normalized[:8].upper(), "")

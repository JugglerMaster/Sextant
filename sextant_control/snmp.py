"""Minimal bounded SNMP v2c GET client using only the standard library.

Only the four system OIDs needed by Discovery are queried. The community string
is accepted as an argument, used for one datagram exchange, and never stored in
the returned observation, audit detail, or error messages.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYSTEM_OIDS = (OID_SYS_DESCR, OID_SYS_OBJECT_ID, OID_SYS_UPTIME, OID_SYS_NAME)
MAX_RESPONSE_BYTES = 65535
MAX_TIMEOUT_MS = 5000
MAX_RETRIES = 3
SNMP_BACKEND = "stdlib"


class SNMPError(RuntimeError):
    """A bounded SNMP error safe to show to an operator."""


def snmp_status() -> dict[str, Any]:
    """No optional dependency is required for the stdlib backend."""
    return {
        "available": True,
        "backend": SNMP_BACKEND,
        "optional_dependency": False,
    }


def _ber_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _ber_length(len(value)) + value


def _ber_int(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    length = (value.bit_length() + 8) // 8
    return value.to_bytes(length, "big", signed=True)


def _ber_oid(oid: str) -> bytes:
    parts = [int(part) for part in oid.strip().split(".")]
    if len(parts) < 2:
        raise SNMPError("Invalid OID.")
    out = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        stack = [part & 0x7F]
        part >>= 7
        while part:
            stack.append((part & 0x7F) | 0x80)
            part >>= 7
        out.extend(reversed(stack))
    return bytes(out)


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    if offset >= len(data):
        raise SNMPError("Truncated SNMP response.")
    tag = data[offset]
    offset += 1
    if offset >= len(data):
        raise SNMPError("Truncated SNMP length.")
    length = data[offset]
    offset += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise SNMPError("Invalid SNMP length.")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    if length < 0 or offset + length > len(data):
        raise SNMPError("Truncated SNMP value.")
    return tag, data[offset : offset + length], offset + length


def _decode_int(value: bytes) -> int:
    if not value:
        return 0
    return int.from_bytes(value, "big", signed=True)


def _decode_oid(value: bytes) -> str:
    if not value:
        return ""
    first = value[0]
    parts = [first // 40, first % 40]
    number = 0
    for byte in value[1:]:
        number = (number << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(number)
            number = 0
    return ".".join(str(part) for part in parts)


def encode_get(community: str, oids: tuple[str, ...] = SYSTEM_OIDS, request_id: int = 1) -> bytes:
    varbinds = b"".join(
        _tlv(0x30, _tlv(0x06, _ber_oid(oid)) + b"\x05\x00") for oid in oids
    )
    pdu = _tlv(
        0xA0,
        _tlv(0x02, _ber_int(request_id))
        + _tlv(0x02, b"\x00")
        + _tlv(0x02, b"\x00")
        + _tlv(0x30, varbinds),
    )
    message = _tlv(0x30, _tlv(0x02, _ber_int(1)) + _tlv(0x04, community.encode("utf-8")) + pdu)
    return message


def decode_response(data: bytes) -> dict[str, Any]:
    tag, body, _ = _read_tlv(data, 0)
    if tag != 0x30:
        raise SNMPError("Not an SNMP message.")
    offset = 0
    tag, version_value, offset = _read_tlv(body, offset)
    if tag != 0x02:
        raise SNMPError("Missing SNMP version.")
    version = _decode_int(version_value)
    tag, _community, offset = _read_tlv(body, offset)
    if tag != 0x04:
        raise SNMPError("Missing SNMP community response field.")
    tag, pdu, offset = _read_tlv(body, offset)
    if tag != 0xA2:
        raise SNMPError("Unexpected SNMP response type.")
    pdu_offset = 0
    tag, request_value, pdu_offset = _read_tlv(pdu, pdu_offset)
    request_id = _decode_int(request_value) if tag == 0x02 else 0
    tag, error_value, pdu_offset = _read_tlv(pdu, pdu_offset)
    error_status = _decode_int(error_value) if tag == 0x02 else 0
    tag, index_value, pdu_offset = _read_tlv(pdu, pdu_offset)
    error_index = _decode_int(index_value) if tag == 0x02 else 0
    tag, varbind_body, pdu_offset = _read_tlv(pdu, pdu_offset)
    if tag != 0x30:
        raise SNMPError("Missing SNMP varbind list.")
    values: dict[str, Any] = {}
    vb_offset = 0
    while vb_offset < len(varbind_body):
        tag, varbind, vb_offset = _read_tlv(varbind_body, vb_offset)
        if tag != 0x30:
            continue
        inner = 0
        tag, oid_value, inner = _read_tlv(varbind, inner)
        if tag != 0x06:
            continue
        oid = _decode_oid(oid_value)
        tag, raw, inner = _read_tlv(varbind, inner)
        if tag == 0x04:
            value: Any = raw.decode("utf-8", errors="replace")
        elif tag == 0x02:
            value = _decode_int(raw)
        elif tag == 0x43:
            value = int.from_bytes(raw, "big") if raw else 0
        elif tag == 0x06:
            value = _decode_oid(raw)
        elif tag == 0x41:
            value = ".".join(str(byte) for byte in raw)
        elif tag in {0x05, 0x80, 0x81, 0x82}:
            value = None
        else:
            value = raw.hex()
        values[oid] = value
    return {
        "version": version,
        "request_id": request_id,
        "error_status": error_status,
        "error_index": error_index,
        "values": values,
    }


def snmp_get(
    host: str,
    community: str,
    oids: tuple[str, ...] = SYSTEM_OIDS,
    *,
    timeout_ms: int = 600,
    retries: int = 1,
    port: int = 161,
    sock_factory: Any | None = None,
) -> dict[str, Any]:
    if not community:
        raise SNMPError("SNMP community is required.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise SNMPError("SNMP target must be an IP address.") from None
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_global
        or not address.is_private
    ):
        raise SNMPError("SNMP only targets private device addresses.")
    timeout = max(0.1, min(MAX_TIMEOUT_MS / 1000, int(timeout_ms) / 1000))
    attempts = max(0, min(MAX_RETRIES, int(retries))) + 1
    packet = encode_get(community, oids)
    last_error: Exception | None = None
    for _ in range(attempts):
        sock = None
        try:
            sock = (sock_factory or socket.socket)(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(packet, (str(address), int(port)))
            data, _peer = sock.recvfrom(MAX_RESPONSE_BYTES)
            result = decode_response(data)
            if int(result.get("error_status") or 0):
                raise SNMPError(f"SNMP agent returned error status {result['error_status']}.")
            return result
        except (OSError, socket.error, SNMPError) as exc:
            last_error = exc
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    if isinstance(last_error, SNMPError):
        raise last_error
    raise SNMPError("SNMP request timed out or was unreachable.")


_VENDOR_PREFIXES = {
    "1.3.6.1.4.1.9": "Cisco",
    "1.3.6.1.4.1.11": "HP",
    "1.3.6.1.4.1.311": "Microsoft",
    "1.3.6.1.4.1.6574": "Synology",
    "1.3.6.1.4.1.14988": "MikroTik",
    "1.3.6.1.4.1.41112": "Ubiquiti",
    "1.3.6.1.4.1.6876": "VMware",
    "1.3.6.1.4.1.8072": "Net-SNMP",
}


def _vendor_from_values(descr: str, object_id: str) -> str:
    for prefix, vendor in _VENDOR_PREFIXES.items():
        if object_id.startswith(prefix):
            return vendor
    lowered = descr.lower()
    for keyword, vendor in (
        ("cisco", "Cisco"),
        ("mikrotik", "MikroTik"),
        ("synology", "Synology"),
        ("qnap", "QNAP"),
        ("ubiquiti", "Ubiquiti"),
        ("hewlett", "HP"),
        ("hp ", "HP"),
        ("brother", "Brother"),
        ("epson", "Epson"),
        ("canon", "Canon"),
        ("vmware", "VMware"),
        ("microsoft", "Microsoft"),
        ("linux", "Linux"),
        ("freebsd", "FreeBSD"),
    ):
        if keyword in lowered:
            return vendor
    return ""


def _device_type_from_values(descr: str, object_id: str, vendor: str) -> str:
    text = f"{descr} {object_id} {vendor}".lower()
    if any(word in text for word in ("printer", "jetdirect", "laserjet", "epson", "brother")):
        return "printer"
    if any(word in text for word in ("switch", "router", "cisco ios", "routeros", "mikrotik")):
        return "router/switch"
    if any(word in text for word in ("synology", "qnap", "nas", "diskstation")):
        return "NAS"
    if any(word in text for word in ("vmware", "esxi", "proxmox", "hypervisor")):
        return "hypervisor"
    if "windows" in text:
        return "Windows"
    if any(word in text for word in ("linux", "unix", "freebsd", "net-snmp")):
        return "Linux/Unix"
    return "unknown"


def snmp_device(ip: str, values: dict[str, Any]) -> dict[str, Any]:
    descr = str(values.get(OID_SYS_DESCR) or "").strip()[:512]
    name = str(values.get(OID_SYS_NAME) or "").strip()[:255]
    object_id = str(values.get(OID_SYS_OBJECT_ID) or "").strip()[:255]
    uptime = values.get(OID_SYS_UPTIME)
    vendor = _vendor_from_values(descr, object_id)
    device_type = _device_type_from_values(descr, object_id, vendor)
    return {
        "ip": ip,
        "mac": "",
        "hostname": name,
        "vendor": vendor,
        "open_ports": [],
        "services": {},
        "evidence": {
            "methods": ["snmp"],
            "snmp": {
                "sys_descr": descr,
                "sys_name": name,
                "sys_object_id": object_id,
                "uptime_ticks": uptime if isinstance(uptime, int) else None,
            },
            "imported_device_type": device_type,
        },
    }


def target_ips(targets: list[str], max_targets: int = 256) -> list[str]:
    """Expand configured IP/CIDR targets with a hard cap and private-only rule."""
    resolved: list[str] = []
    seen: set[str] = set()
    for value in targets:
        text = str(value).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise SNMPError(f"Invalid SNMP target: {text[:80]}") from None
        if network.is_global or not network.is_private:
            raise SNMPError("SNMP targets must be private IP/CIDR ranges.")
        if network.num_addresses == 1:
            candidates = [network.network_address]
        else:
            candidates = network.hosts()
        for address in candidates:
            if (
                address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or address.is_global
                or not address.is_private
            ):
                continue
            key = str(address)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(key)
            if len(resolved) >= max_targets:
                return resolved
    return resolved


_BAD_CHARS = re.compile(r"[\r\n\t]")


def redact_snmp_error(value: Any) -> str:
    return _BAD_CHARS.sub(" ", str(value or ""))[:500]

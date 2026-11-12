"""Safe, bounded parsers for external discovery imports.

Nothing here touches the network. Parsers accept a bounded text payload and
return normalized device dictionaries that the discovery engine merges into the
existing inventory. Malformed structure is rejected; unsafe or unusable device
addresses are skipped and counted.
"""

from __future__ import annotations

import ipaddress
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable

from sextant_common.oui import lookup_vendor, normalize_mac

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_DEVICES = 2000
MAX_PORTS = 128

IMPORT_FORMATS = (
    "nmap-xml",
    "nmap-json",
    "arp-scan",
    "dnsmasq",
    "isc-dhcpd",
    "json",
)
_FORMAT_ALIASES = {
    "nmap": "nmap-xml",
    "nmap_xml": "nmap-xml",
    "nmap-json": "nmap-json",
    "nmap_json": "nmap-json",
    "arp": "arp-scan",
    "arp_scan": "arp-scan",
    "dnsmasq-leases": "dnsmasq",
    "dhcpd": "isc-dhcpd",
    "isc": "isc-dhcpd",
    "generic": "json",
    "generic-json": "json",
    "normalized-json": "json",
}
_DOC_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)


class DiscoveryImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ParsedImport:
    format: str
    devices: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_format(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _FORMAT_ALIASES.get(normalized, normalized)
    if normalized not in IMPORT_FORMATS:
        raise DiscoveryImportError(
            "Unsupported import format. Use: " + ", ".join(IMPORT_FORMATS)
        )
    return normalized


def _safe_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return ""
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_global
        or not address.is_private
    ):
        return ""
    if any(
        address.version == network.version and address in network
        for network in _DOC_NETWORKS
    ):
        return ""
    return str(address)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_services(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    services: dict[str, Any] = {}
    for key, item in list(value.items())[:MAX_PORTS]:
        port = str(key)[:16]
        if isinstance(item, dict):
            bounded: dict[str, Any] = {}
            for sub_key, sub_value in list(item.items())[:16]:
                if isinstance(sub_value, (str, int, float, bool)) or sub_value is None:
                    bounded[str(sub_key)[:48]] = (
                        sub_value[:256] if isinstance(sub_value, str) else sub_value
                    )
            services[port] = bounded
        elif isinstance(item, (str, int, float, bool)) or item is None:
            services[port] = item
    return services


def _normalize_device(item: dict[str, Any], fmt: str, evidence: dict[str, Any] | None = None) -> dict[str, Any] | None:
    ip = _safe_ip(str(item.get("ip") or item.get("address") or ""))
    if not ip:
        return None
    mac = normalize_mac(item.get("mac") or item.get("mac_address") or "")
    hostname = _bounded_text(
        item.get("hostname") or item.get("name") or item.get("client_hostname"), 255
    )
    vendor = _bounded_text(item.get("vendor") or item.get("manufacturer"), 255)
    if not vendor and mac:
        vendor = lookup_vendor(mac)
    raw_ports = item.get("open_ports", item.get("ports", []))
    ports: list[int] = []
    if isinstance(raw_ports, (list, tuple)):
        for value in raw_ports[:MAX_PORTS]:
            try:
                port = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
    services = _bounded_services(item.get("services"))
    for port in ports:
        services.setdefault(str(port), {"protocol": "tcp"})
    device_evidence = {
        "import_format": fmt,
        "import_source": True,
        **(evidence or {}),
    }
    for key in ("device_type", "os_guess", "os_match", "server", "ssdp", "mdns"):
        if key in item and isinstance(item[key], (str, dict, int, float, bool)):
            device_evidence[f"imported_{key}"] = item[key]
    return {
        "ip": ip,
        "mac": mac,
        "hostname": hostname,
        "vendor": vendor,
        "open_ports": sorted(ports),
        "services": services,
        "mdns": item.get("mdns") if isinstance(item.get("mdns"), dict) else {},
        "ssdp": item.get("ssdp") if isinstance(item.get("ssdp"), dict) else {},
        "evidence": device_evidence,
    }


def normalize_device(item: dict[str, Any], fmt: str, evidence: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Public wrapper used by import parsers and vendor adapters."""
    return _normalize_device(item, fmt, evidence)


def _dedupe_devices(devices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for device in devices:
        key = device.get("mac") or f"ip:{device.get('ip')}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = device
            continue
        existing_ports = set(existing.get("open_ports") or [])
        existing_ports.update(device.get("open_ports") or [])
        existing["open_ports"] = sorted(existing_ports)
        existing["services"] = {**existing.get("services", {}), **device.get("services", {})}
        existing["hostname"] = existing.get("hostname") or device.get("hostname", "")
        existing["vendor"] = existing.get("vendor") or device.get("vendor", "")
        existing["evidence"] = {**existing.get("evidence", {}), **device.get("evidence", {})}
    return list(merged.values())


def _collect(devices: list[dict[str, Any]], parsed: ParsedImport, raw: Iterable[dict[str, Any]]) -> None:
    for item in raw:
        if len(parsed.devices) >= MAX_IMPORT_DEVICES:
            parsed.truncated = True
            break
        normalized = _normalize_device(item, parsed.format)
        if normalized is None:
            parsed.skipped += 1
            continue
        devices.append(normalized)


def parse_nmap_xml(text: str) -> ParsedImport:
    parsed = ParsedImport(format="nmap-xml")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise DiscoveryImportError("DTD and ENTITY declarations are not allowed.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DiscoveryImportError(f"Malformed Nmap XML: {exc}") from None
    raw: list[dict[str, Any]] = []
    for host in root.iter("host"):
        status = host.find("status")
        if status is not None and str(status.get("state", "")).lower() != "up":
            continue
        ip = ""
        mac = ""
        mac_vendor = ""
        for address in host.findall("address"):
            kind = str(address.get("addrtype", "")).lower()
            value = str(address.get("addr", ""))
            if kind in {"ipv4", "ipv6"} and not ip:
                ip = value
            elif kind == "mac" and not mac:
                mac = value
                mac_vendor = str(address.get("vendor", ""))
        hostname = ""
        hostname_node = host.find("hostnames/hostname")
        if hostname_node is not None:
            hostname = str(hostname_node.get("name", ""))
        ports: list[int] = []
        services: dict[str, Any] = {}
        for port_node in host.findall("ports/port"):
            state = port_node.find("state")
            if state is not None and str(state.get("state", "")).lower() != "open":
                continue
            try:
                port = int(port_node.get("portid", ""))
            except (TypeError, ValueError):
                continue
            if not 1 <= port <= 65535:
                continue
            service = port_node.find("service")
            details: dict[str, Any] = {"protocol": "tcp"}
            if service is not None:
                details = {
                    "protocol": str(service.get("name") or "tcp")[:64],
                    "product": str(service.get("product") or "")[:120],
                    "version": str(service.get("version") or "")[:120],
                    "extrainfo": str(service.get("extrainfo") or "")[:120],
                }
            ports.append(port)
            services[str(port)] = details
        os_match = host.find("os/osmatch")
        raw.append(
            {
                "ip": ip,
                "mac": mac,
                "vendor": mac_vendor,
                "hostname": hostname,
                "open_ports": ports,
                "services": services,
                "os_match": str(os_match.get("name", ""))[:255] if os_match is not None else "",
            }
        )
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"hosts": len(raw), "devices": len(parsed.devices)}
    return parsed


def _json_addresses(host: dict[str, Any]) -> tuple[str, str, str]:
    ip = ""
    mac = ""
    vendor = ""
    addresses = host.get("address")
    if isinstance(addresses, dict):
        addresses = [addresses]
    if isinstance(addresses, list):
        for address in addresses:
            if not isinstance(address, dict):
                continue
            kind = str(address.get("addrtype", "")).lower()
            if kind in {"ipv4", "ipv6"} and not ip:
                ip = str(address.get("addr", ""))
            elif kind == "mac" and not mac:
                mac = str(address.get("addr", ""))
                vendor = str(address.get("vendor", ""))
    return ip, mac, vendor


def _json_hostnames(host: dict[str, Any]) -> str:
    hostnames = host.get("hostnames")
    if isinstance(hostnames, dict):
        hostnames = hostnames.get("hostname")
    if isinstance(hostnames, dict):
        hostnames = [hostnames]
    if isinstance(hostnames, list):
        for item in hostnames:
            if isinstance(item, dict) and item.get("name"):
                return str(item["name"])
            if isinstance(item, str) and item:
                return item
    return ""


def parse_nmap_json(text: str) -> ParsedImport:
    parsed = ParsedImport(format="nmap-json")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DiscoveryImportError(f"Malformed Nmap JSON: {exc}") from None
    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("hosts") or payload.get("host") or [payload]
        if isinstance(items, dict):
            items = [items]
    else:
        raise DiscoveryImportError("Nmap JSON must be an object or array.")
    raw: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        host = item.get("host") if isinstance(item.get("host"), dict) else item
        status = host.get("status")
        if isinstance(status, dict) and str(status.get("state", "")).lower() not in {"", "up"}:
            continue
        ip, mac, vendor = _json_addresses(host)
        ports_container = host.get("ports")
        if isinstance(ports_container, dict):
            port_items = ports_container.get("port") or []
        elif isinstance(ports_container, list):
            port_items = ports_container
        else:
            port_items = []
        if isinstance(port_items, dict):
            port_items = [port_items]
        ports: list[int] = []
        services: dict[str, Any] = {}
        for port_item in port_items if isinstance(port_items, list) else []:
            if not isinstance(port_item, dict):
                continue
            state = port_item.get("state")
            if isinstance(state, dict) and str(state.get("state", "")).lower() != "open":
                continue
            try:
                port = int(port_item.get("portid", ""))
            except (TypeError, ValueError):
                continue
            if not 1 <= port <= 65535:
                continue
            service = port_item.get("service") if isinstance(port_item.get("service"), dict) else {}
            ports.append(port)
            services[str(port)] = {
                "protocol": str(service.get("name") or port_item.get("protocol") or "tcp")[:64],
                "product": str(service.get("product") or "")[:120],
                "version": str(service.get("version") or "")[:120],
            }
        os_name = ""
        os_container = host.get("os")
        if isinstance(os_container, dict):
            matches = os_container.get("osmatch") or []
            if isinstance(matches, dict):
                matches = [matches]
            if isinstance(matches, list) and matches and isinstance(matches[0], dict):
                os_name = str(matches[0].get("name", ""))
        raw.append(
            {
                "ip": ip,
                "mac": mac,
                "vendor": vendor,
                "hostname": _json_hostnames(host),
                "open_ports": ports,
                "services": services,
                "os_match": os_name[:255],
            }
        )
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"hosts": len(raw), "devices": len(parsed.devices)}
    return parsed


_ARP_RE = re.compile(
    r"^\s*((?:\d{1,3}\.){3}\d{1,3})\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s*(.*)$"
)


def parse_arp_scan(text: str) -> ParsedImport:
    parsed = ParsedImport(format="arp-scan")
    raw = []
    for line in text.splitlines():
        match = _ARP_RE.match(line)
        if not match:
            continue
        vendor = re.sub(r"\s+", " ", match.group(3)).strip()
        raw.append(
            {
                "ip": match.group(1),
                "mac": match.group(2),
                "vendor": vendor[:255],
            }
        )
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"lines": len(text.splitlines()), "devices": len(parsed.devices)}
    return parsed


def parse_dnsmasq(text: str) -> ParsedImport:
    parsed = ParsedImport(format="dnsmasq")
    raw = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].startswith("#"):
            continue
        _, mac, ip, hostname = parts[:4]
        if normalize_mac(mac) == "":
            continue
        raw.append(
            {
                "ip": ip,
                "mac": mac,
                "hostname": "" if hostname == "*" else hostname,
            }
        )
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"lines": len(text.splitlines()), "devices": len(parsed.devices)}
    return parsed


_ISC_LEASE_RE = re.compile(r"lease\s+([0-9A-Fa-f:.]+)\s*\{(.*?)\}", re.DOTALL)
_ISC_MAC_RE = re.compile(r"hardware\s+ethernet\s+([0-9A-Fa-f:.-]+)\s*;", re.IGNORECASE)
_ISC_HOST_RE = re.compile(r'client-hostname\s+"([^"]*)"\s*;', re.IGNORECASE)
_ISC_STATE_RE = re.compile(r"binding\s+state\s+(\w+)\s*;", re.IGNORECASE)


def parse_isc_dhcpd(text: str) -> ParsedImport:
    parsed = ParsedImport(format="isc-dhcpd")
    raw = []
    for match in _ISC_LEASE_RE.finditer(text):
        ip, block = match.group(1), match.group(2)
        state_match = _ISC_STATE_RE.search(block)
        if state_match is not None and state_match.group(1).lower() in {"free", "abandoned"}:
            continue
        mac_match = _ISC_MAC_RE.search(block)
        host_match = _ISC_HOST_RE.search(block)
        raw.append(
            {
                "ip": ip,
                "mac": mac_match.group(1) if mac_match else "",
                "hostname": host_match.group(1) if host_match else "",
            }
        )
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"leases": len(raw), "devices": len(parsed.devices)}
    return parsed


def parse_generic_json(text: str) -> ParsedImport:
    parsed = ParsedImport(format="json")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DiscoveryImportError(f"Malformed JSON: {exc}") from None
    if isinstance(payload, dict):
        devices = payload.get("devices", payload.get("hosts", [payload]))
    elif isinstance(payload, list):
        devices = payload
    else:
        raise DiscoveryImportError("JSON must be an object or array of devices.")
    if not isinstance(devices, list):
        raise DiscoveryImportError("JSON devices must be an array.")
    raw = [item for item in devices if isinstance(item, dict)]
    _collect(parsed.devices, parsed, raw)
    parsed.devices = _dedupe_devices(parsed.devices)
    parsed.metadata = {"devices": len(parsed.devices)}
    return parsed


_PARSERS = {
    "nmap-xml": parse_nmap_xml,
    "nmap-json": parse_nmap_json,
    "arp-scan": parse_arp_scan,
    "dnsmasq": parse_dnsmasq,
    "isc-dhcpd": parse_isc_dhcpd,
    "json": parse_generic_json,
}


def parse_import(
    format: str,
    content: str | bytes,
    *,
    max_bytes: int = MAX_IMPORT_BYTES,
    max_devices: int = MAX_IMPORT_DEVICES,
) -> ParsedImport:
    fmt = normalize_format(format)
    if isinstance(content, bytes):
        raw_bytes = content
        if len(raw_bytes) > max_bytes:
            raise DiscoveryImportError(
                f"Import exceeds the {max_bytes} byte limit.", status_code=413
            )
        text = raw_bytes.decode("utf-8", errors="replace")
    else:
        text = str(content or "")
        if len(text.encode("utf-8")) > max_bytes:
            raise DiscoveryImportError(
                f"Import exceeds the {max_bytes} byte limit.", status_code=413
            )
    if not text.strip():
        raise DiscoveryImportError("Import content is empty.")
    parser = _PARSERS[fmt]
    parsed = parser(text)
    if len(parsed.devices) > max_devices:
        parsed.devices = parsed.devices[:max_devices]
        parsed.truncated = True
    parsed.format = fmt
    return parsed

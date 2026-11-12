"""CIDR-scoped, non-invasive network discovery for Sextant.

The engine is deliberately small and uses only the Python standard library:

* neighbour tables (``ip neigh`` / ``arp -a``)
* reverse DNS (PTR)
* a bundled OUI vendor table
* passive SSDP listening
* opt-in TCP connect/banner fingerprinting

It never opens raw sockets, never runs exploitation or credential checks, and
only ever expands addresses from the administrator's allowlisted CIDRs.

Scans run on one bounded background thread so the FastAPI event loop is never
blocked. Each scan is cancellable and the manager shuts down cleanly with the
application lifespan.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import ipaddress
import logging
import queue
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from .config import DiscoverySettings
from .db import Database, dumps, loads, parse_iso, utcnow, utcnow_iso
from .oui import is_locally_administered, lookup_vendor, normalize_mac

logger = logging.getLogger("sextant_control.discovery")

ACKNOWLEDGEMENT = "I am authorized to scan these networks"
ADMIN_PORTS = frozenset({"22", "3389", "5900", "5985", "5986", "8006"})
SSH_PORTS = frozenset({22})
HTTP_PORTS = frozenset({80, 631, 8080, 32400})
TLS_PORTS = frozenset({443, 8006, 5986, 8443})
DARK_AFTER_HOURS = 24
# A MAC change against a device seen within this window is ambiguous: two
# devices may be claiming the same address. Beyond it, treat the new MAC as
# identity drift (NIC replaced, VM rebuilt, or randomized address rotated).
IDENTITY_CHANGE_WINDOW = timedelta(minutes=15)
SCAN_RATE_LIMIT_SECONDS = 5.0
SCAN_QUEUE_LIMIT = 8
TLS_HANDSHAKE_TIMEOUT = 3.0


class DiscoveryError(Exception):
    """A validation or lifecycle error safe to show to an operator."""

    status_code = 400


class DiscoveryDisabled(DiscoveryError):
    status_code = 403


class DiscoveryNotAcknowledged(DiscoveryError):
    status_code = 403


class DiscoveryBusy(DiscoveryError):
    status_code = 429


class DiscoveryRateLimited(DiscoveryError):
    status_code = 429


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------


def parse_network(value: str | ipaddress._BaseNetwork) -> ipaddress._BaseNetwork:
    """Parse one CIDR with strict=False (host bits are masked)."""
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError as exc:
        raise DiscoveryError(f"Invalid CIDR: {value}") from exc


_DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)


def _network_rejection(network: ipaddress._BaseNetwork) -> str:
    if any(
        network.version == doc.version and network.overlaps(doc)
        for doc in _DOCUMENTATION_NETWORKS
    ):
        return "documentation ranges cannot be scanned"
    if network.is_multicast:
        return "multicast ranges cannot be scanned"
    if network.is_loopback:
        return "loopback ranges cannot be scanned"
    if network.is_link_local:
        return "link-local ranges cannot be scanned"
    if network.is_unspecified:
        return "unspecified ranges cannot be scanned"
    if getattr(network, "is_reserved", False):
        return "reserved ranges cannot be scanned"
    if network.version == 4 and network.prefixlen < 8:
        return "IPv4 scopes must be /8 or narrower"
    if network.version == 6 and network.prefixlen < 48:
        return "IPv6 scopes must be /48 or narrower"
    if getattr(network, "is_global", False):
        return "public internet ranges cannot be scanned"
    if not network.is_private:
        return "only private (RFC1918 / ULA) ranges can be scanned"
    if network.version == 4 and network.overlaps(ipaddress.ip_network("0.0.0.0/8")):
        return "this address range cannot be scanned"
    return ""


def validate_network(value: str | ipaddress._BaseNetwork) -> ipaddress._BaseNetwork:
    network = parse_network(value)
    reason = _network_rejection(network)
    if reason:
        raise DiscoveryError(f"{network}: {reason}.")
    return network


def validate_allowlist(values: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for value in values or ():
        network = validate_network(value)
        if network not in networks:
            networks.append(network)
    return networks


def validate_excludes(values: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    excludes: list[ipaddress._BaseNetwork] = []
    for value in values or ():
        network = parse_network(value)
        if network not in excludes:
            excludes.append(network)
    return excludes


def parse_scope(
    requested: str | Iterable[str] | None,
    allowlist: Iterable[ipaddress._BaseNetwork],
) -> list[ipaddress._BaseNetwork]:
    """Return requested networks that are inside the configured allowlist."""
    allowed = list(allowlist)
    if not allowed:
        raise DiscoveryError("No discovery CIDRs are configured.")
    if requested is None or requested == "" or requested == []:
        return allowed
    values = [requested] if isinstance(requested, str) else list(requested)
    selected: list[ipaddress._BaseNetwork] = []
    for value in values:
        network = parse_network(value)
        reason = _network_rejection(network)
        if reason:
            raise DiscoveryError(f"{network}: {reason}.")
        containing = [base for base in allowed if network.subnet_of(base)]
        if not containing:
            raise DiscoveryError(
                f"{network} is outside the configured discovery allowlist."
            )
        base = max(containing, key=lambda item: item.prefixlen)
        if network.num_addresses == 1 and network != base:
            raise DiscoveryError(
                "Scopes must be CIDRs, not individual host addresses."
            )
        if network not in selected:
            selected.append(network)
    if not selected:
        raise DiscoveryError("At least one in-scope CIDR is required.")
    return selected


def validate_methods(
    config: DiscoverySettings, requested: Iterable[str] | None
) -> list[str]:
    configured = list(config.methods)
    if requested is None or requested == []:
        methods = configured
    else:
        methods = [str(item).strip().lower() for item in requested]
    unknown = [item for item in methods if item not in configured]
    if unknown:
        raise DiscoveryError(
            "Discovery method is not enabled by SEXTANT_DISCOVERY_METHODS: "
            + ", ".join(sorted(set(unknown)))
        )
    if "tcp" in methods and not config.active_probes:
        raise DiscoveryError(
            "TCP fingerprinting requires SEXTANT_DISCOVERY_ACTIVE_PROBES=1."
        )
    return list(dict.fromkeys(methods))


def expand_targets(
    scope: Iterable[ipaddress._BaseNetwork],
    excludes: Iterable[ipaddress._BaseNetwork],
    max_hosts: int,
) -> list[str]:
    """Expand scopes, drop excluded ranges, and enforce the per-scan host cap."""
    cap = max(1, min(65536, int(max_hosts)))
    excluded = list(excludes)
    targets: list[str] = []
    total = 0
    for network in scope:
        for address in network.hosts():
            if any(address in exclude for exclude in excluded):
                continue
            total += 1
            if total > cap:
                raise DiscoveryError(
                    f"Scan scope exceeds SEXTANT_DISCOVERY_MAX_HOSTS ({cap})."
                )
            targets.append(str(address))
    return targets


def segment_for_ip(
    ip: str, scope: Iterable[ipaddress._BaseNetwork]
) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    for network in scope:
        if address in network:
            return str(network)
    return ""


def _is_excluded(
    ip: str, excludes: Iterable[ipaddress._BaseNetwork]
) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(address in exclude for exclude in excludes)


# ---------------------------------------------------------------------------
# Neighbour tables and reverse DNS
# ---------------------------------------------------------------------------


_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b[0-9a-fA-F:]{3,}\b")


def _clean_hostname(value: str) -> str:
    value = value.strip().strip("()").strip()
    if not value or value == "?" or value.lower() in {"unknown", "incomplete"}:
        return ""
    if _IP_RE.fullmatch(value):
        return ""
    return value[:255]


def _parse_arp_line(line: str) -> dict[str, str] | None:
    # Linux/BSD: gateway (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0
    match = re.search(
        r"^(?:\?\s+)?(?P<host>[^\s(]+)?\s*\((?P<ip>[^)]+)\)\s+at\s+(?P<mac>[0-9A-Fa-f:.-]+)",
        line,
    )
    if match:
        ip = match.group("ip")
        mac = normalize_mac(match.group("mac"))
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return None
        if not mac:
            return None
        return {"ip": ip, "mac": mac, "hostname": _clean_hostname(match.group("host") or ""), "state": ""}

    # Windows: 192.168.1.1   aa-bb-cc-dd-ee-ff   dynamic
    match = re.search(
        r"^\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+(?P<mac>[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})\s+(?P<state>\S+)",
        line,
    )
    if match:
        mac = normalize_mac(match.group("mac"))
        if not mac:
            return None
        return {
            "ip": match.group("ip"),
            "mac": mac,
            "hostname": "",
            "state": match.group("state").upper(),
        }
    return None


def _parse_neigh_line(line: str) -> dict[str, str] | None:
    tokens = line.split()
    if not tokens:
        return None
    ip = tokens[0]
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return _parse_arp_line(line)
    state = ""
    if tokens[-1].upper() in {
        "REACHABLE",
        "STALE",
        "DELAY",
        "PROBE",
        "FAILED",
        "INCOMPLETE",
        "PERMANENT",
        "NOARP",
    }:
        state = tokens[-1].upper()
    if state in {"FAILED", "INCOMPLETE"}:
        return None
    mac = ""
    if "lladdr" in tokens:
        index = tokens.index("lladdr")
        if index + 1 < len(tokens):
            mac = normalize_mac(tokens[index + 1])
    if not mac:
        return _parse_arp_line(line)
    return {"ip": ip, "mac": mac, "hostname": "", "state": state}


def parse_neighbor_table(text: str) -> list[dict[str, str]]:
    """Parse ``ip neigh show`` and ``arp -a`` output into unique entries."""
    entries: dict[str, dict[str, str]] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("interface:"):
            continue
        parsed = _parse_neigh_line(line)
        if parsed is None:
            continue
        try:
            ipaddress.ip_address(parsed["ip"])
        except ValueError:
            continue
        existing = entries.get(parsed["ip"])
        if existing is None or (not existing.get("hostname") and parsed.get("hostname")):
            entries[parsed["ip"]] = parsed
    return sorted(entries.values(), key=lambda item: ipaddress.ip_address(item["ip"]))


def read_neighbor_table(
    command_runner: Callable[..., Any] | None = None,
) -> list[dict[str, str]]:
    """Read the local neighbour table without a shell or elevated privileges."""
    runner = command_runner or subprocess.run
    commands = (["ip", "neigh", "show"], ["arp", "-a"], ["arp", "-an"])
    for command in commands:
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        output = getattr(result, "stdout", "") or ""
        if not output.strip():
            continue
        entries = parse_neighbor_table(output)
        if entries:
            return entries
    return []


def reverse_dns(
    ip: str,
    resolver: Callable[[str], tuple[str, list[str], list[str]]] | None = None,
) -> str:
    """Resolve one PTR record, returning an empty string on any failure."""
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return ""
    lookup = resolver or socket.gethostbyaddr
    try:
        name = lookup(ip)[0]
    except (OSError, socket.error, IndexError, TypeError):
        return ""
    name = str(name or "").strip().rstrip(".").lower()
    if not name or _IP_RE.fullmatch(name):
        return ""
    return name[:255]


# ---------------------------------------------------------------------------
# SSDP
# ---------------------------------------------------------------------------


def parse_ssdp_payload(payload: bytes | str) -> dict[str, str]:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = str(payload or "")
    text = text.replace("\r\n", "\n")
    headers: dict[str, str] = {}
    for line in text.split("\n")[1:]:
        if not line.strip():
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in {"host", "st", "usn", "server", "location", "nt", "nts", "cache-control"}:
            headers[key] = value.strip()[:500]
    if not headers.get("st") and not headers.get("usn") and not headers.get("nt"):
        return {}
    return headers


def listen_ssdp(
    timeout_seconds: float = 1.0,
    *,
    receiver: Callable[[float], list[tuple[bytes, tuple[str, int]]]] | None = None,
) -> list[dict[str, Any]]:
    """Listen passively for SSDP NOTIFY traffic (never sends a query)."""
    messages: list[tuple[bytes, tuple[str, int]]] = []
    if receiver is not None:
        try:
            messages = receiver(timeout_seconds)
        except (OSError, socket.error):
            return []
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", 1900))
            sock.settimeout(max(0.1, min(5.0, float(timeout_seconds))))
            deadline = time.monotonic() + max(0.1, min(5.0, float(timeout_seconds)))
            while time.monotonic() < deadline:
                try:
                    data, address = sock.recvfrom(8192)
                except socket.timeout:
                    break
                messages.append((data, address))
        except OSError:
            return []
        finally:
            sock.close()

    results: dict[tuple[str, str], dict[str, Any]] = {}
    for data, address in messages:
        parsed = parse_ssdp_payload(data)
        if not parsed:
            continue
        ip = str(address[0] if isinstance(address, tuple) else address)
        key = (ip, parsed.get("usn") or parsed.get("st") or parsed.get("nt") or "")
        results[key] = {"ip": ip, **parsed}
    return list(results.values())


# ---------------------------------------------------------------------------
# TCP connect and banner fingerprinting
# ---------------------------------------------------------------------------


def parse_ssh_banner(text: bytes | str) -> dict[str, str] | None:
    if isinstance(text, bytes):
        value = text.decode("utf-8", errors="replace")
    else:
        value = str(text or "")
    value = value.strip().splitlines()[0] if value.strip() else ""
    match = re.match(r"^SSH-(?P<protocol>[\d.]+)-(?P<software>\S+)", value)
    if not match:
        return None
    software = match.group("software")
    product = software
    version = ""
    if "_" in software:
        product, version = software.split("_", 1)
    return {
        "protocol": "ssh",
        "banner": value[:300],
        "ssh_protocol": match.group("protocol"),
        "product": product[:120],
        "version": version[:120],
    }


def parse_http_banner(text: bytes | str) -> dict[str, Any] | None:
    if isinstance(text, bytes):
        value = text.decode("latin-1", errors="replace")
    else:
        value = str(text or "")
    if not value:
        return None
    status_match = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", value, re.IGNORECASE)
    server_match = re.search(r"^Server:\s*(.+)$", value, re.IGNORECASE | re.MULTILINE)
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", value, re.IGNORECASE | re.DOTALL
    )
    if status_match is None and server_match is None and title_match is None:
        return None
    title = ""
    if title_match is not None:
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:200]
    result: dict[str, Any] = {"protocol": "http"}
    if status_match is not None:
        result["status"] = int(status_match.group(1))
    if server_match is not None:
        result["server"] = server_match.group(1).strip()[:200]
    if title:
        result["title"] = title
    return result


def _pem_from_der(der: bytes) -> str:
    return ssl.DER_cert_to_PEM_cert(der)


def decode_pem_certificate(pem: str) -> dict[str, Any]:
    """Decode a PEM certificate with the stdlib X.509 helper."""
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return {}
    handle = None
    path = ""
    try:
        handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        handle.write(pem)
        handle.close()
        path = handle.name
        return dict(decoder(path))
    except (OSError, ValueError):
        return {}
    finally:
        if path:
            try:
                import os

                os.unlink(path)
            except OSError:
                pass
        elif handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def _name_entries(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(value, (tuple, list)):
        return result
    for item in value:
        if not isinstance(item, (tuple, list)):
            continue
        for pair in item:
            if isinstance(pair, (tuple, list)) and len(pair) == 2:
                key, name = str(pair[0]), str(pair[1])
                result.setdefault(key, []).append(name)
    return result


def summarize_tls_certificate(cert: dict[str, Any]) -> dict[str, Any]:
    subject = _name_entries(cert.get("subject"))
    issuer = _name_entries(cert.get("issuer"))
    san = [
        str(value)
        for kind, value in (cert.get("subjectAltName") or [])
        if str(kind).lower() in {"dns", "ip address", "ip"}
    ]
    common_name = (subject.get("commonName") or [""])[0]
    issuer_name = (issuer.get("commonName") or issuer.get("organizationName") or [""])[0]
    return {
        "protocol": "tls",
        "subject": common_name[:255],
        "issuer": issuer_name[:255],
        "san": sorted(set(san))[:32],
        "not_after": str(cert.get("notAfter") or "")[:64],
    }


def _connect_tcp(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, int(port)), timeout=timeout)


def _probe_port(
    ip: str,
    port: int,
    timeout: float,
    connector: Callable[[str, int, float], socket.socket],
) -> dict[str, Any] | None:
    sock = None
    try:
        sock = connector(ip, port, timeout)
        sock.settimeout(timeout)
        if port in SSH_PORTS:
            data = sock.recv(512)
            parsed = parse_ssh_banner(data)
            return parsed or {"protocol": "ssh"}
        if port in HTTP_PORTS:
            try:
                sock.sendall(
                    f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: Sextant\r\n\r\n".encode(
                        "ascii", errors="ignore"
                    )
                )
            except (OSError, socket.error):
                pass
            data = sock.recv(8192)
            return parse_http_banner(data) or {"protocol": "http"}
        if port in TLS_PORTS:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            tls = context.wrap_socket(sock, server_hostname=ip)
            try:
                der = tls.getpeercert(binary_form=True)
                if not der:
                    return {"protocol": "tls"}
                cert = decode_pem_certificate(_pem_from_der(der))
                return summarize_tls_certificate(cert)
            finally:
                try:
                    tls.close()
                except OSError:
                    pass
            sock = None
        return {"protocol": "tcp"}
    except (OSError, socket.error, ssl.SSLError, ValueError):
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def probe_tcp_host(
    ip: str,
    ports: Iterable[int],
    *,
    timeout_seconds: float = 0.6,
    concurrency: int = 8,
    connector: Callable[[str, int, float], socket.socket] | None = None,
) -> dict[str, dict[str, Any]]:
    """Connect to curated ports and return evidence for open services."""
    selected = [int(port) for port in ports if 1 <= int(port) <= 65535]
    if not selected:
        return {}
    connect = connector or _connect_tcp
    workers = max(1, min(int(concurrency), len(selected)))
    services: dict[str, dict[str, Any]] = {}
    if workers == 1:
        for port in selected:
            result = _probe_port(ip, port, max(0.1, timeout_seconds), connect)
            if result is not None:
                services[str(port)] = result
        return services
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_probe_port, ip, port, max(0.1, timeout_seconds), connect): port
            for port in selected
        }
        for future in concurrent.futures.as_completed(futures):
            port = futures[future]
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 - one bad port must not stop the host
                result = None
            if result is not None:
                services[str(port)] = result
    return services


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _contains(value: str, *needles: str) -> bool:
    return any(needle in value for needle in needles)


def classify_device(
    *,
    hostname: str = "",
    vendor: str = "",
    open_ports: Iterable[int] = (),
    services: dict[str, Any] | None = None,
    ssdp: dict[str, Any] | None = None,
    mdns: dict[str, Any] | None = None,
    mac_randomized: bool = False,
) -> tuple[str, str, float]:
    """Return ``(device_type, os_guess, confidence)`` from coarse evidence."""
    ports = {int(port) for port in open_ports if str(port).isdigit()}
    service_map = services or {}
    ssdp_map = ssdp or {}
    mdns_map = mdns or {}
    scores: dict[str, float] = {}
    os_scores: dict[str, float] = {}

    def add(device_type: str, points: float, os_guess: str = "") -> None:
        if points <= 0:
            return
        scores[device_type] = min(0.99, scores.get(device_type, 0.0) + points)
        if os_guess:
            os_scores[os_guess] = min(0.99, os_scores.get(os_guess, 0.0) + points)

    vendor_l = str(vendor or "").lower()
    hostname_l = str(hostname or "").lower()

    if _contains(vendor_l, "synology", "qnap", "western digital", "buffalo", "nas"):
        add("NAS", 0.75, "Embedded")
    if _contains(
        vendor_l,
        "hp",
        "hewlett",
        "canon",
        "epson",
        "brother",
        "xerox",
        "ricoh",
        "kyocera",
        "lexmark",
    ):
        add("printer", 0.55, "Embedded")
    if _contains(
        vendor_l,
        "cisco",
        "linksys",
        "netgear",
        "tp-link",
        "ubiquiti",
        "aruba",
        "juniper",
        "mikrotik",
        "router",
    ):
        add("router/switch", 0.65, "Embedded")
    if _contains(vendor_l, "vmware", "parallels", "virtualbox", "qemu", "hyper-v"):
        add("hypervisor", 0.6, "Hypervisor")

    if 631 in ports or 9100 in ports:
        add("printer", 0.9 if 631 in ports and 9100 in ports else 0.7, "Embedded")
    if 8006 in ports:
        add("hypervisor", 0.85, "Proxmox VE")
    if 32400 in ports:
        add("media/IoT", 0.75, "Plex")
    if 1883 in ports:
        add("media/IoT", 0.45, "Embedded")
    if 3389 in ports:
        add("Windows", 0.65, "Windows")
    if 445 in ports or 139 in ports:
        add("Windows", 0.55, "Windows")
    if 5985 in ports or 5986 in ports:
        add("Windows", 0.6, "Windows")
    if 22 in ports:
        add("Linux/Unix", 0.4, "Linux/Unix")
        ssh = service_map.get("22") or {}
        banner = str(ssh.get("banner") or "").lower()
        if "windows" in banner:
            add("Windows", 0.5, "Windows")
        elif _contains(
            banner,
            "ubuntu",
            "debian",
            "linux",
            "openssh",
            "freebsd",
            "raspbian",
            "alpine",
        ):
            add("Linux/Unix", 0.45, "Linux/Unix")
    if 53 in ports and (80 in ports or 443 in ports):
        add("router/switch", 0.25, "Embedded")
    if 5900 in ports:
        add("Linux/Unix", 0.15)

    ssdp_text = " ".join(str(value) for value in ssdp_map.values()).lower()
    if ssdp_text:
        if "mediarenderer" in ssdp_text or "dial" in ssdp_text:
            add("media/IoT", 0.95, "Embedded")
        if "internetgatewaydevice" in ssdp_text:
            add("router/switch", 0.8, "Embedded")
        if "printer" in ssdp_text:
            add("printer", 0.8, "Embedded")

    mdns_text = " ".join(str(value) for value in mdns_map.values()).lower()
    if _contains(mdns_text, "_ipp._tcp", "_pdl-datastream", "_printer"):
        add("printer", 0.8, "Embedded")
    if _contains(mdns_text, "_airplay", "_googlecast", "_raop"):
        add("media/IoT", 0.7, "Embedded")

    if _contains(hostname_l, "printer", "epson", "brother", "canon", "hp-"):
        add("printer", 0.55, "Embedded")
    if _contains(hostname_l, "nas", "diskstation", "qnap"):
        add("NAS", 0.55, "Embedded")
    if _contains(hostname_l, "router", "gateway", "switch", "firewall"):
        add("router/switch", 0.55, "Embedded")

    if not scores:
        return "unknown", "", 0.0
    device_type, score = max(scores.items(), key=lambda item: (item[1], item[0]))
    if score < 0.5:
        return "unknown", "", round(score, 2)
    os_guess = ""
    if os_scores:
        os_guess = max(os_scores.items(), key=lambda item: (item[1], item[0]))[0]
    if device_type == "Windows" and not os_guess:
        os_guess = "Windows"
    if device_type == "Linux/Unix" and not os_guess:
        os_guess = "Linux/Unix"
    confidence = round(min(0.99, score), 2)
    if mac_randomized:
        confidence = round(max(0.0, confidence - 0.1), 2)
    return device_type, os_guess, confidence


# ---------------------------------------------------------------------------
# Merge and change detection
# ---------------------------------------------------------------------------


@dataclass
class DeviceObservation:
    ip: str
    mac: str = ""
    hostname: str = ""
    vendor: str = ""
    source: str = "server"
    segment: str = ""
    open_ports: list[int] = field(default_factory=list)
    services: dict[str, Any] = field(default_factory=dict)
    mdns: dict[str, Any] = field(default_factory=dict)
    ssdp: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


def _clean_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _dict_value(value: Any) -> dict[str, Any]:
    parsed = loads(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _device_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "mac": row["mac"],
        "ip": row["ip"],
        "hostname": row["hostname"],
        "vendor": row["vendor"],
        "device_type": row["device_type"],
        "os_guess": row["os_guess"],
        "confidence": float(row["confidence"] or 0),
        "source": row["source"],
        "segment": row["segment"],
        "open_ports": loads(row["open_ports"], []),
        "services": _dict_value(row["services"]),
        "mdns": _dict_value(row["mdns"]),
        "ssdp": _dict_value(row["ssdp"]),
        "evidence": _dict_value(row["evidence"]),
        "state": row["state"],
        "agent_id": row["agent_id"],
        "wake_target_id": row["wake_target_id"],
        "tags": loads(row["tags"], []),
        "notes": row["notes"],
        "mac_randomized": bool(row["mac_randomized"]),
        "wake_status": row["wake_status"] if "wake_status" in row.keys() else "",
        "wake_verified_at": row["wake_verified_at"] if "wake_verified_at" in row.keys() else None,
        "change_kind": row["change_kind"] if "change_kind" in row.keys() else "",
        "change": _dict_value(row["change_detail"])
        if "change_detail" in row.keys()
        else {},
        "changed_at": row["changed_at"] if "changed_at" in row.keys() else None,
        "conflict_detail": _dict_value(row["conflict_detail"])
        if "conflict_detail" in row.keys()
        else {},
        "conflict": bool(row["conflict_at"])
        if "conflict_at" in row.keys()
        else False,
    }


def _merge_event(
    database: Database,
    conn: Any,
    device_id: str,
    kind: str,
    detail: Any,
    at: str,
) -> None:
    database.record_discovery_event(device_id, kind, detail, at=at, conn=conn)


def _absorb_duplicate_devices(
    database: Database,
    conn: Any,
    device_id: str,
    *,
    ip: str,
    hostname: str,
    mac: str,
    stamp: str,
) -> None:
    """Fold obvious duplicate rows into the row that was just observed.

    MAC-less observations used to create a second row for an IP that already
    had a MAC, and rotating private MACs used to leave one row per address.
    Both are the same device, so keep the freshest row and move its history.
    """
    keep = conn.execute(
        "SELECT * FROM discovered_devices WHERE id = ?", (device_id,)
    ).fetchone()
    if keep is None:
        return
    candidates = conn.execute(
        """SELECT * FROM discovered_devices
           WHERE id != ? AND (
             (? != '' AND ip = ?) OR (? != '' AND hostname = ? COLLATE NOCASE)
           )""",
        (device_id, ip, ip, hostname, hostname),
    ).fetchall()
    keep_mac = str(keep["mac"] or "")
    for candidate in candidates:
        candidate_mac = str(candidate["mac"] or "")
        same_ip = bool(ip) and str(candidate["ip"] or "") == ip
        same_name = bool(hostname) and (
            str(candidate["hostname"] or "").lower() == hostname.lower()
        )
        previous_seen = parse_iso(candidate["last_seen"])
        stale = (
            previous_seen is None
            or utcnow() - previous_seen > IDENTITY_CHANGE_WINDOW
        )
        absorb = False
        if same_ip and not candidate_mac:
            absorb = True
        elif same_ip and not mac and candidate_mac:
            absorb = True
        elif same_name and not candidate_mac:
            absorb = True
        elif (
            same_name
            and candidate_mac
            and mac
            and candidate_mac.lower() != mac.lower()
            and (
                is_locally_administered(candidate_mac)
                or is_locally_administered(mac)
            )
        ):
            absorb = True
        elif (
            (same_ip or same_name)
            and candidate_mac
            and mac
            and candidate_mac.lower() != mac.lower()
            and stale
        ):
            absorb = True
        if not absorb:
            continue

        keep_ports = {
            int(port)
            for port in loads(keep["open_ports"], [])
            if str(port).isdigit()
        }
        candidate_ports = {
            int(port)
            for port in loads(candidate["open_ports"], [])
            if str(port).isdigit()
        }
        merged_services = {
            **_dict_value(candidate["services"]),
            **_dict_value(keep["services"]),
        }
        merged_mdns = {**_dict_value(candidate["mdns"]), **_dict_value(keep["mdns"])}
        merged_ssdp = {**_dict_value(candidate["ssdp"]), **_dict_value(keep["ssdp"])}
        merged_evidence = {
            **_dict_value(candidate["evidence"]),
            **_dict_value(keep["evidence"]),
        }
        tags = list(
            dict.fromkeys(
                [
                    *loads(candidate["tags"], []),
                    *loads(keep["tags"], []),
                ]
            )
        )[:32]
        new_mac = keep_mac or candidate_mac
        change: dict[str, Any] = {}
        if keep_mac and candidate_mac and keep_mac.lower() != candidate_mac.lower():
            change = {
                "kind": "mac",
                "from": candidate_mac,
                "to": keep_mac,
                "at": stamp,
            }
        conn.execute(
            """UPDATE discovered_devices SET
                 first_seen = ?, mac = ?, open_ports = ?, services = ?,
                 mdns = ?, ssdp = ?, evidence = ?, tags = ?,
                 notes = CASE WHEN notes = '' THEN ? ELSE notes END,
                 mac_randomized = ?, agent_id = COALESCE(agent_id, ?),
                 wake_target_id = COALESCE(wake_target_id, ?),
                 change_kind = CASE WHEN ? != '' THEN ? ELSE change_kind END,
                 change_detail = CASE WHEN ? != '' THEN ? ELSE change_detail END,
                 changed_at = CASE WHEN ? != '' THEN ? ELSE changed_at END
               WHERE id = ?""",
            (
                min(str(keep["first_seen"]), str(candidate["first_seen"])),
                new_mac,
                dumps(sorted(keep_ports | candidate_ports)),
                dumps(merged_services),
                dumps(merged_mdns),
                dumps(merged_ssdp),
                dumps(merged_evidence),
                dumps(tags),
                str(candidate["notes"] or ""),
                int(bool(keep["mac_randomized"]) or bool(candidate["mac_randomized"])),
                candidate["agent_id"],
                candidate["wake_target_id"],
                change.get("kind", ""),
                change.get("kind", ""),
                dumps(change),
                dumps(change),
                change.get("kind", ""),
                stamp if change else None,
                device_id,
            ),
        )
        conn.execute(
            "UPDATE discovery_events SET device_id = ? WHERE device_id = ?",
            (device_id, str(candidate["id"])),
        )
        conn.execute(
            "DELETE FROM discovered_devices WHERE id = ?", (str(candidate["id"]),)
        )
        if change:
            _merge_event(database, conn, device_id, "identity_changed", change, stamp)


def merge_observation(
    database: Database,
    observation: DeviceObservation,
    *,
    scan_id: str | None = None,
    now: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Normalize one observation into the device table and diff its state.

    MAC is the primary merge key. An unknown MAC may merge by stable IP and then
    hostname. Two different known MACs are never merged; the older row gets an
    ``identity_conflict`` event instead.
    """
    del scan_id  # stored for future scan-to-device provenance; events carry the diff
    stamp = now or utcnow_iso()
    mac = normalize_mac(observation.mac)
    ip = _clean_ip(observation.ip)
    if not ip and not mac:
        raise DiscoveryError("An observation needs an IP or MAC address.")
    hostname = str(observation.hostname or "").strip().rstrip(".")[:255]
    vendor = str(observation.vendor or "").strip()[:255] or lookup_vendor(mac)
    ports = sorted({int(port) for port in observation.open_ports if 1 <= int(port) <= 65535})
    services = _json_safe(observation.services or {})
    ssdp = _json_safe(observation.ssdp or {})
    mdns = _json_safe(observation.mdns or {})
    random_mac = is_locally_administered(mac)
    evidence = _json_safe(observation.evidence or {})
    device_type, os_guess, confidence = classify_device(
        hostname=hostname,
        vendor=vendor,
        open_ports=ports,
        services=services,
        ssdp=ssdp,
        mdns=mdns,
        mac_randomized=random_mac,
    )
    imported_type = str(evidence.get("imported_device_type") or "")
    if imported_type in {
        "Windows",
        "Linux/Unix",
        "printer",
        "router/switch",
        "NAS",
        "media/IoT",
        "hypervisor",
    }:
        device_type = imported_type
        confidence = max(confidence, 0.6)
        if not os_guess:
            os_guess = {
                "Windows": "Windows",
                "Linux/Unix": "Linux/Unix",
                "hypervisor": "Hypervisor",
            }.get(imported_type, "Embedded")
    tcp_scanned = "tcp" in (evidence.get("methods") or [])
    source = str(observation.source or "server")[:128]
    segment = str(observation.segment or "")[:128]

    transaction = (
        contextlib.nullcontext(conn) if conn is not None else database.transaction()
    )
    with transaction as conn:
        row = None
        created = False
        added_ports: list[int] = []
        conflict_row = None
        change_kind = ""

        def find_ip_candidate() -> Any:
            if not ip:
                return None
            return conn.execute(
                """SELECT * FROM discovered_devices
                   WHERE ip = ?
                   ORDER BY (mac != '') DESC, last_seen DESC LIMIT 1""",
                (ip,),
            ).fetchone()

        def find_hostname_candidate() -> Any:
            if not hostname:
                return None
            return conn.execute(
                """SELECT * FROM discovered_devices
                   WHERE hostname = ? COLLATE NOCASE
                   ORDER BY last_seen DESC LIMIT 1""",
                (hostname,),
            ).fetchone()

        def can_be_mac_change(candidate: Any, same_name: bool) -> bool:
            """Decide whether a different MAC is drift or a live conflict.

            A reused hostname with a private/randomized address is the same
            device rotating its MAC. A stale previous sighting looks like the
            same device with a new NIC. Two recent vendor MACs on one identity
            are a genuine conflict.
            """
            previous_mac = str(candidate["mac"] or "")
            if not previous_mac or not mac:
                return True
            if same_name and (
                is_locally_administered(previous_mac)
                or is_locally_administered(mac)
            ):
                return True
            previous_seen = parse_iso(candidate["last_seen"])
            return (
                previous_seen is None
                or utcnow() - previous_seen > IDENTITY_CHANGE_WINDOW
            )

        if mac:
            row = conn.execute(
                """SELECT * FROM discovered_devices
                   WHERE mac = ? COLLATE NOCASE AND mac != ''
                   ORDER BY last_seen DESC LIMIT 1""",
                (mac,),
            ).fetchone()

        if row is None:
            candidate = find_ip_candidate() or find_hostname_candidate()
            if candidate is not None:
                previous_mac = str(candidate["mac"] or "")
                same_name = bool(hostname) and (
                    str(candidate["hostname"] or "").lower() == hostname.lower()
                )
                if mac and previous_mac and previous_mac.lower() != mac:
                    if can_be_mac_change(candidate, same_name):
                        row = candidate
                        change_kind = "mac"
                    else:
                        conflict_row = candidate
                else:
                    row = candidate

        if row is None:
            created = True
            device_id = "dev_" + secrets.token_hex(8)
            new_conflict = {}
            if conflict_row is not None:
                new_conflict = {
                    "kind": "mac",
                    "ip": ip,
                    "known_mac": str(conflict_row["mac"] or ""),
                    "observed_mac": mac,
                    "known_device_id": str(conflict_row["id"]),
                }
            conn.execute(
                """INSERT INTO discovered_devices
                   (id, first_seen, last_seen, mac, ip, hostname, vendor,
                    device_type, os_guess, confidence, source, segment,
                    open_ports, services, mdns, ssdp, evidence, state,
                    tags, notes, mac_randomized, conflict_detail, conflict_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'new', '[]', '', ?, ?, ?)""",
                (
                    device_id,
                    stamp,
                    stamp,
                    mac,
                    ip,
                    hostname,
                    vendor,
                    device_type,
                    os_guess,
                    confidence,
                    source,
                    segment,
                    dumps(ports),
                    dumps(services),
                    dumps(mdns),
                    dumps(ssdp),
                    dumps(evidence),
                    int(random_mac),
                    dumps(new_conflict),
                    stamp if new_conflict else None,
                ),
            )
            _merge_event(
                database,
                conn,
                device_id,
                "new_device",
                {"ip": ip, "mac": mac, "hostname": hostname},
                stamp,
            )
            old_ports: set[int] = set()
            old_services: dict[str, Any] = {}
            survivor_mac = mac
        else:
            device_id = str(row["id"])
            old_ports = {int(port) for port in loads(row["open_ports"], []) if str(port).isdigit()}
            old_services = _dict_value(row["services"])
            existing_mac = str(row["mac"] or "")
            previous_ip = str(row["ip"] or "")
            previous_hostname = str(row["hostname"] or "")
            merged_mac = mac or existing_mac
            merged_hostname = hostname or previous_hostname
            merged_vendor = vendor or str(row["vendor"] or "")
            # A TCP-enabled scan is authoritative for the configured port set.
            # Passive-observation scans preserve previously fingerprinted ports.
            merged_ports = sorted(set(ports) if tcp_scanned else old_ports | set(ports))
            merged_services = services if tcp_scanned else {**old_services, **services}
            merged_mdns = {**_dict_value(row["mdns"]), **mdns}
            merged_ssdp = {**_dict_value(row["ssdp"]), **ssdp}
            merged_evidence = {**_dict_value(row["evidence"]), **evidence}

            if not change_kind:
                if mac and existing_mac and existing_mac.lower() != mac:
                    change_kind = "mac"
                elif ip and previous_ip and previous_ip != ip:
                    change_kind = "ip"
                elif (
                    hostname
                    and previous_hostname
                    and previous_hostname.lower() != hostname.lower()
                ):
                    change_kind = "hostname"
            change_detail: dict[str, Any] = {}
            if change_kind:
                change_detail = {
                    "kind": change_kind,
                    "from": {
                        "mac": existing_mac,
                        "ip": previous_ip,
                        "hostname": previous_hostname,
                    }.get(change_kind, ""),
                    "to": {"mac": mac, "ip": ip, "hostname": hostname}.get(
                        change_kind, ""
                    ),
                    "at": stamp,
                }

            # A device seen in a later observation is no longer "new". Operator
            # decisions (ignored/promoted) are preserved.
            next_state = str(row["state"])
            if next_state == "new" and not change_kind:
                next_state = "known"

            conn.execute(
                """UPDATE discovered_devices SET
                     last_seen = ?, mac = ?, ip = ?, hostname = ?, vendor = ?,
                     device_type = ?, os_guess = ?, confidence = ?, source = ?,
                     segment = ?, open_ports = ?, services = ?, mdns = ?,
                     ssdp = ?, evidence = ?, mac_randomized = ?, state = ?,
                     change_kind = ?, change_detail = ?, changed_at = ?,
                     conflict_detail = '{}', conflict_at = NULL
                   WHERE id = ?""",
                (
                    stamp,
                    merged_mac,
                    ip or row["ip"],
                    merged_hostname,
                    merged_vendor,
                    device_type if device_type != "unknown" else row["device_type"],
                    os_guess or row["os_guess"],
                    max(confidence, float(row["confidence"] or 0))
                    if device_type == row["device_type"]
                    else confidence,
                    source,
                    segment or row["segment"],
                    dumps(merged_ports),
                    dumps(merged_services),
                    dumps(merged_mdns),
                    dumps(merged_ssdp),
                    dumps(merged_evidence),
                    int(random_mac or bool(row["mac_randomized"])),
                    next_state,
                    change_kind,
                    dumps(change_detail),
                    stamp if change_kind else None,
                    device_id,
                ),
            )
            if change_kind:
                _merge_event(
                    database,
                    conn,
                    device_id,
                    "identity_changed",
                    change_detail,
                    stamp,
                )
            survivor_mac = merged_mac
            added_ports = sorted(set(ports) - old_ports)
            for port in added_ports:
                _merge_event(
                    database,
                    conn,
                    device_id,
                    "new_port",
                    {"port": port},
                    stamp,
                )
            for port in sorted(set(ports) & old_ports):
                previous = old_services.get(str(port))
                current = merged_services.get(str(port))
                if previous is not None and current is not None and previous != current:
                    _merge_event(
                        database,
                        conn,
                        device_id,
                        "service_changed",
                        {"port": port, "from": previous, "to": current},
                        stamp,
                    )

        if conflict_row is not None:
            detail = {
                "kind": "mac",
                "ip": ip,
                "known_mac": str(conflict_row["mac"] or ""),
                "observed_mac": mac,
                "observed_device_id": device_id,
            }
            conn.execute(
                """UPDATE discovered_devices SET conflict_detail = ?, conflict_at = ?
                   WHERE id = ?""",
                (dumps(detail), stamp, str(conflict_row["id"])),
            )
            _merge_event(
                database,
                conn,
                str(conflict_row["id"]),
                "identity_conflict",
                detail,
                stamp,
            )

        _absorb_duplicate_devices(
            database,
            conn,
            device_id,
            ip=ip,
            hostname=hostname,
            mac=survivor_mac,
            stamp=stamp,
        )

        agent = None
        if mac:
            agent = conn.execute(
                "SELECT * FROM agents WHERE mac = ? COLLATE NOCASE LIMIT 1",
                (mac,),
            ).fetchone()
        wake = None
        if mac:
            wake = conn.execute(
                "SELECT * FROM wake_targets WHERE mac = ? COLLATE NOCASE LIMIT 1",
                (mac,),
            ).fetchone()
        conn.execute(
            "UPDATE discovered_devices SET agent_id = ?, wake_target_id = ? WHERE id = ?",
            (
                str(agent["id"]) if agent is not None else None,
                str(wake["id"]) if wake is not None else None,
                device_id,
            ),
        )
        if agent is not None:
            label = hostname or ip or mac
            if created:
                database.record_agent_event(
                    str(agent["id"]),
                    "discovery",
                    "info",
                    f"Discovered device {label}",
                    {"device_id": device_id, "ip": ip, "mac": mac},
                    at=stamp,
                    conn=conn,
                )
            for port in added_ports:
                database.record_agent_event(
                    str(agent["id"]),
                    "discovery",
                    "info",
                    f"Discovered new port {port} on {label}",
                    {"device_id": device_id, "ip": ip, "port": port},
                    at=stamp,
                    conn=conn,
                )
        final = conn.execute(
            "SELECT * FROM discovered_devices WHERE id = ?", (device_id,)
        ).fetchone()
    return _device_dict(final)


def merge_observations(
    database: Database,
    observations: Iterable[DeviceObservation],
    *,
    scan_id: str | None = None,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Merge a report atomically: all device rows and events commit together."""
    stamp = now or utcnow_iso()
    merged: list[dict[str, Any]] = []
    with database.transaction() as conn:
        for observation in observations:
            merged.append(
                merge_observation(
                    database,
                    observation,
                    scan_id=scan_id,
                    now=stamp,
                    conn=conn,
                )
            )
    return merged


def detect_identity_conflicts(
    database: Database, device_id: str | None = None
) -> list[dict[str, Any]]:
    """Return current identity conflicts.

    Historical ``identity_conflict`` events are not conflicts: once a device is
    merged after an IP or MAC change, the old event is just history. Conflicts
    come from rows whose current identities disagree.
    """
    conflicts: list[dict[str, Any]] = []
    with database.connection() as conn:
        rows = conn.execute("SELECT * FROM discovered_devices").fetchall()

    by_mac: dict[str, list[Any]] = {}
    by_ip: dict[str, list[Any]] = {}
    by_hostname: dict[str, list[Any]] = {}
    for row in rows:
        if row["mac"]:
            by_mac.setdefault(str(row["mac"]).lower(), []).append(row)
            if row["ip"]:
                by_ip.setdefault(str(row["ip"]), []).append(row)
            if row["hostname"]:
                by_hostname.setdefault(str(row["hostname"]).lower(), []).append(row)

    grouped_ids: set[str] = set()

    def add_conflict(
        kind: str,
        key: str,
        group: list[Any],
        *,
        detail: dict[str, Any] | None = None,
        at: str | None = None,
    ) -> None:
        ids = sorted({str(row["id"]) for row in group})
        grouped_ids.update(ids)
        conflicts.append(
            {
                "type": kind,
                "key": key,
                "device_ids": ids,
                "devices": [_device_dict(row) for row in group],
                "at": at,
                "detail": detail or {},
            }
        )

    for mac, group in by_mac.items():
        ips = sorted({str(row["ip"]) for row in group if row["ip"]})
        if len(ips) > 1:
            add_conflict(
                "mac_multiple_ips", mac, group, detail={"mac": mac, "ips": ips}
            )
    for ip, group in by_ip.items():
        macs = sorted({str(row["mac"]).lower() for row in group if row["mac"]})
        if len(macs) > 1:
            add_conflict(
                "ip_multiple_macs", ip, group, detail={"ip": ip, "macs": macs}
            )
    for hostname, group in by_hostname.items():
        macs = sorted({str(row["mac"]).lower() for row in group if row["mac"]})
        vendors = sorted({str(row["vendor"]) for row in group if row["vendor"]})
        if len(macs) > 1:
            add_conflict(
                "hostname_multiple_identities",
                hostname,
                group,
                detail={
                    "hostname": hostname,
                    "macs": macs,
                    "vendors": vendors,
                },
            )
    for row in rows:
        row_id = str(row["id"])
        if row_id in grouped_ids or not str(row["conflict_at"] or ""):
            continue
        detail = loads(row["conflict_detail"], {})
        add_conflict(
            "identity_conflict",
            row_id,
            [row],
            detail=detail if isinstance(detail, dict) else {},
            at=str(row["conflict_at"]),
        )
    if device_id:
        conflicts = [
            conflict
            for conflict in conflicts
            if device_id in conflict.get("device_ids", [])
        ]
    conflicts.sort(key=lambda item: (item["type"], item["key"]))
    return conflicts


def verify_reachability(
    ip: str,
    ports: Iterable[int] = (),
    *,
    mac: str = "",
    timeout_ms: int = 600,
    concurrency: int = 8,
    neighbor_reader: Callable[[], list[dict[str, str]]] | None = None,
    prober: Callable[..., dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Bounded TCP/neighbour reachability check used by wake verification."""
    address = _clean_ip(ip)
    if not address:
        raise DiscoveryError("A valid device IP address is required.")
    parsed = ipaddress.ip_address(address)
    if (
        parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_global
        or not parsed.is_private
    ):
        raise DiscoveryError("Wake verification only targets private device addresses.")
    reader = neighbor_reader or read_neighbor_table
    entries = reader()
    neighbor_match = False
    normalized_mac = normalize_mac(mac)
    for entry in entries:
        if str(entry.get("ip", "")) == address:
            neighbor_match = True
            break
        if normalized_mac and normalize_mac(entry.get("mac", "")) == normalized_mac:
            neighbor_match = True
            break
    open_ports: list[int] = []
    services: dict[str, Any] = {}
    if ports:
        probe = prober or probe_tcp_host
        services = probe(
            address,
            list(ports),
            timeout_seconds=max(0.1, int(timeout_ms) / 1000.0),
            concurrency=max(1, min(8, int(concurrency))),
        )
        open_ports = sorted(int(port) for port in services)
    reachable = neighbor_match or bool(open_ports)
    method = "neighbor" if neighbor_match else ("tcp" if open_ports else "")
    return {
        "reachable": reachable,
        "method": method,
        "neighbor": neighbor_match,
        "open_ports": open_ports,
        "services": services,
        "checked_at": utcnow_iso(),
    }


def record_went_dark(
    database: Database,
    segments: Iterable[str],
    seen_device_ids: Iterable[str],
    *,
    now: datetime | None = None,
    threshold_hours: int = DARK_AFTER_HOURS,
) -> list[str]:
    """Emit one ``went_dark`` event per device and last-seen timestamp."""
    current = now or utcnow()
    cutoff = current - timedelta(hours=max(1, int(threshold_hours)))
    cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    seen = {str(item) for item in seen_device_ids}
    segments = [str(item) for item in segments]
    with database.transaction() as conn:
        if segments:
            placeholders = ",".join("?" for _ in segments)
            rows = conn.execute(
                f"""SELECT * FROM discovered_devices
                    WHERE last_seen < ? AND segment IN ({placeholders})""",
                [cutoff_iso, *segments],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM discovered_devices WHERE last_seen < ?", (cutoff_iso,)
            ).fetchall()
        marked: list[str] = []
        for row in rows:
            device_id = str(row["id"])
            if device_id in seen:
                continue
            inserted = database.record_discovery_event(
                device_id,
                "went_dark",
                {"last_seen": row["last_seen"]},
                at=current.isoformat(timespec="seconds").replace("+00:00", "Z"),
                conn=conn,
            )
            if inserted is not None:
                marked.append(device_id)
        return marked


# ---------------------------------------------------------------------------
# Scan execution
# ---------------------------------------------------------------------------


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _make_observation(
    ip: str,
    *,
    entry: dict[str, str] | None = None,
    hostname: str = "",
    scope: Iterable[ipaddress._BaseNetwork],
    services: dict[str, Any] | None = None,
    ssdp: dict[str, Any] | None = None,
    mdns: dict[str, Any] | None = None,
    methods: Iterable[str],
    evidence: dict[str, Any] | None = None,
) -> DeviceObservation:
    mac = normalize_mac((entry or {}).get("mac", ""))
    observed_hostname = hostname or (entry or {}).get("hostname", "")
    segment = segment_for_ip(ip, scope)
    open_ports = sorted(int(port) for port in (services or {}))
    method_set = set(methods)
    base_evidence = {
        "methods": sorted(method_set),
        "neighbor": bool(entry),
        "reverse_dns": bool(observed_hostname),
        "ssdp": bool(ssdp),
        "tcp": bool(services),
    }
    if evidence:
        base_evidence.update(evidence)
    return DeviceObservation(
        ip=ip,
        mac=mac,
        hostname=observed_hostname,
        vendor=lookup_vendor(mac),
        source="server",
        segment=segment,
        open_ports=open_ports,
        services=services or {},
        mdns=mdns or {},
        ssdp=ssdp or {},
        evidence=base_evidence,
    )


def run_scan(
    database: Database,
    config: DiscoverySettings,
    scan: Any,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run one validated scan. Safe to call from the background worker."""
    if not config.enabled:
        raise DiscoveryDisabled("Discovery is disabled.")
    acknowledgement = database.get_discovery_settings()
    if acknowledgement is None or not acknowledgement["acknowledged_at"]:
        raise DiscoveryNotAcknowledged(
            "Discovery authorization acknowledgement is required."
        )
    cancel_event = cancel_event or threading.Event()
    scope_values = loads(_row_get(scan, "scope", "[]"), [])
    method_values = loads(_row_get(scan, "methods", "[]"), [])
    allowlist = validate_allowlist(config.cidrs)
    excludes = validate_excludes(config.exclude)
    scope = parse_scope(scope_values, allowlist)
    methods = validate_methods(config, method_values)
    targets = expand_targets(scope, excludes, config.max_hosts)
    target_set = set(targets)
    excluded = excludes
    timeout_seconds = max(0.1, config.timeout_ms / 1000.0)

    observations: dict[str, DeviceObservation] = {}
    seen_ips: set[str] = set()

    neighbour_entries: list[dict[str, str]] = []
    if "neigh" in methods:
        neighbour_entries = read_neighbor_table()
    neighbour_by_ip = {entry["ip"]: entry for entry in neighbour_entries}

    hostname_by_ip: dict[str, str] = {}
    if "dns" in methods:
        for entry in neighbour_entries:
            ip = entry["ip"]
            if ip not in target_set or ip in seen_ips:
                continue
            name = reverse_dns(ip)
            if name:
                hostname_by_ip[ip] = name

    ssdp_by_ip: dict[str, dict[str, Any]] = {}
    if "ssdp" in methods:
        for item in listen_ssdp(max(0.2, min(2.0, timeout_seconds * 2))):
            ip = _clean_ip(item.get("ip", ""))
            if not ip or ip not in target_set or _is_excluded(ip, excluded):
                continue
            ssdp_by_ip.setdefault(ip, {})
            for key, value in item.items():
                if key != "ip":
                    ssdp_by_ip[ip][key] = value

    tcp_services: dict[str, dict[str, Any]] = {}
    if "tcp" in methods and config.active_probes and targets:
        def probe(ip: str) -> tuple[str, dict[str, Any]]:
            # The outer executor owns the configured concurrency budget. Probing
            # ports sequentially inside each host keeps the total worker count
            # bounded at SEXTANT_DISCOVERY_CONCURRENCY.
            return ip, probe_tcp_host(
                ip,
                config.probe_ports,
                timeout_seconds=timeout_seconds,
                concurrency=1,
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(config.concurrency, len(targets)))
        ) as pool:
            futures = [pool.submit(probe, ip) for ip in targets]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                if cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    ip, services = future.result()
                except Exception:  # noqa: BLE001 - a failed host is not fatal
                    continue
                if services:
                    tcp_services[ip] = services
                if progress is not None and (index % 10 == 0 or index == len(futures)):
                    progress(min(99, int(index * 100 / max(1, len(futures)))), index)
        if cancel_event.is_set():
            return {"status": "cancelled", "hosts_found": len(observations), "error": ""}

    candidate_ips = set(neighbour_by_ip) | set(tcp_services) | set(ssdp_by_ip) | set(hostname_by_ip)
    ordered = sorted(
        (
            ip
            for ip in candidate_ips
            if ip in target_set and not _is_excluded(ip, excluded)
        ),
        key=ipaddress.ip_address,
    )
    total = len(ordered)
    for index, ip in enumerate(ordered, start=1):
        if cancel_event.is_set():
            return {"status": "cancelled", "hosts_found": len(observations), "error": ""}
        services = tcp_services.get(ip, {})
        ssdp = ssdp_by_ip.get(ip, {})
        entry = neighbour_by_ip.get(ip)
        if entry is None and not services and not ssdp and not hostname_by_ip.get(ip):
            continue
        seen_ips.add(ip)
        observations[ip] = _make_observation(
            ip,
            entry=entry,
            hostname=hostname_by_ip.get(ip, ""),
            scope=scope,
            services=services,
            ssdp=ssdp,
            methods=methods,
        )
        if progress is not None and (index % 10 == 0 or index == total):
            progress(min(99, int(index * 100 / max(1, total))), index)

    merged_ids: list[str] = []
    for ip in sorted(observations, key=ipaddress.ip_address):
        if cancel_event.is_set():
            return {"status": "cancelled", "hosts_found": len(set(merged_ids)), "error": ""}
        device = merge_observation(database, observations[ip])
        merged_ids.append(device["id"])

    record_went_dark(
        database,
        {str(network) for network in scope},
        set(merged_ids),
        threshold_hours=DARK_AFTER_HOURS,
    )
    if progress is not None:
        progress(100, total)
    return {
        "status": "succeeded",
        "hosts_found": len(set(merged_ids)),
        "error": "",
    }


# ---------------------------------------------------------------------------
# Background manager
# ---------------------------------------------------------------------------


class DiscoveryManager:
    """One bounded worker thread and one in-flight scan per process."""

    def __init__(
        self,
        database: Database,
        config: DiscoverySettings,
        *,
        executor: Callable[..., dict[str, Any]] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.executor = executor or run_scan
        self.on_change = on_change
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=SCAN_QUEUE_LIMIT)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active_id: str | None = None
        self._last_start = 0.0
        self._cancel_events: dict[str, threading.Event] = {}

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="sextant-discovery", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5)

    @property
    def active_scan_id(self) -> str | None:
        with self._lock:
            return self._active_id

    # -- submission ------------------------------------------------------

    def submit(self, scan_id: str) -> None:
        with self._lock:
            if self._active_id is not None:
                raise DiscoveryBusy("A discovery scan is already running.")
            now = time.monotonic()
            if now - self._last_start < SCAN_RATE_LIMIT_SECONDS:
                retry = max(1, int(SCAN_RATE_LIMIT_SECONDS - (now - self._last_start)))
                raise DiscoveryRateLimited(
                    f"Scan rate limit reached; retry in {retry}s."
                )
            self._active_id = scan_id
            self._last_start = now
            self._cancel_events[scan_id] = threading.Event()
        try:
            self._queue.put_nowait(scan_id)
        except queue.Full:
            with self._lock:
                self._active_id = None
                self._cancel_events.pop(scan_id, None)
            raise DiscoveryBusy("The discovery queue is full; try again later.") from None

    def cancel(self, scan_id: str) -> bool:
        found = self.database.request_discovery_scan_cancel(scan_id)
        with self._lock:
            event = self._cancel_events.get(scan_id)
            if event is not None:
                event.set()
        return found

    # -- worker ----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=30)
            except queue.Empty:
                self._maybe_schedule()
                continue
            try:
                if item is None:
                    break
                self._execute(item)
            finally:
                self._queue.task_done()

    def _execute(self, scan_id: str) -> None:
        try:
            scan = self.database.get_discovery_scan(scan_id)
            if scan is None:
                return
            if bool(_row_get(scan, "cancel_requested", 0)):
                self.database.update_discovery_scan(
                    scan_id,
                    status="cancelled",
                    finished_at=utcnow_iso(),
                    error="Cancelled before the scan started.",
                )
                self._audit("discovery.scan_cancelled", scan, {"stage": "queued"})
                return
            self.database.update_discovery_scan(
                scan_id, status="running", cancel_requested=False
            )
            cancel_event = self._cancel_events.get(scan_id) or threading.Event()

            def progress(percent: int, scanned: int) -> None:
                self.database.update_discovery_scan(
                    scan_id, progress=max(0, min(100, int(percent))), hosts_scanned=scanned
                )

            try:
                result = self.executor(
                    self.database, self.config, scan, cancel_event, progress
                )
            except DiscoveryError as exc:
                result = {"status": "failed", "hosts_found": 0, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - worker must survive
                logger.exception("Discovery scan failed")
                result = {
                    "status": "failed",
                    "hosts_found": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                }
            status = str(result.get("status") or "succeeded")
            if status not in {"succeeded", "failed", "cancelled"}:
                status = "failed"
            if cancel_event.is_set() and status == "succeeded":
                status = "cancelled"
            self.database.update_discovery_scan(
                scan_id,
                status=status,
                finished_at=utcnow_iso(),
                hosts_found=max(0, int(result.get("hosts_found") or 0)),
                error=str(result.get("error") or "")[:4000],
                progress=100,
            )
            self._audit(
                "discovery.scan_cancelled" if status == "cancelled" else "discovery.scan_finished",
                scan,
                {
                    "status": status,
                    "hosts_found": int(result.get("hosts_found") or 0),
                    "error": str(result.get("error") or "")[:500],
                },
            )
            self.database.prune_discovery(self.config.retention_days)
            if self.on_change is not None:
                try:
                    self.on_change()
                except Exception:  # noqa: BLE001 - notification must not break the worker
                    logger.debug("discovery change notification failed", exc_info=True)
        finally:
            with self._lock:
                if self._active_id == scan_id:
                    self._active_id = None
                self._cancel_events.pop(scan_id, None)

    def _audit(self, action: str, scan: Any, detail: dict[str, Any]) -> None:
        self.database.audit(
            actor=str(_row_get(scan, "initiated_by", "system") or "system"),
            action=action,
            target=str(_row_get(scan, "id", "")),
            detail=detail,
        )

    def _maybe_schedule(self) -> None:
        if self.config.interval_hours <= 0 or not self.config.enabled:
            return
        ack = self.database.get_discovery_settings()
        if ack is None or not ack["acknowledged_at"]:
            return
        if self.active_scan_id is not None:
            return
        try:
            allowlist = validate_allowlist(self.config.cidrs)
            if not allowlist:
                return
            latest, _ = self.database.list_discovery_scans(limit=1)
        except DiscoveryError:
            return
        if latest:
            started = parse_iso(latest[0]["started_at"])
            if started is not None and utcnow() - started < timedelta(
                hours=self.config.interval_hours
            ):
                return
        scope = [str(network) for network in allowlist]
        methods = list(self.config.methods)
        scan = self.database.create_discovery_scan(scope, methods, "scheduler")
        try:
            self.submit(str(scan["id"]))
        except DiscoveryError:
            self.database.update_discovery_scan(
                str(scan["id"]),
                status="failed",
                finished_at=utcnow_iso(),
                error="Scheduled scan could not be queued.",
            )
            return
        self.database.audit(
            actor="scheduler",
            action="discovery.scan_started",
            target=str(scan["id"]),
            detail={"scope": scope, "methods": methods, "scheduled": True},
        )

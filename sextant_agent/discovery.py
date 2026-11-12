"""Bounded agent-side discovery for the local VLAN.

Only the Python standard library is used. The scanner accepts a CIDR scope from
the server, validates it again locally, and never targets an address outside
that scope. It reuses the shared OUI table and intentionally does not import
server/database modules.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sextant_common.oui import is_locally_administered, lookup_vendor, normalize_mac

AGENT_METHODS = ("neigh", "dns", "oui", "ssdp", "tcp")
SSH_PORTS = frozenset({22})
HTTP_PORTS = frozenset({80, 631, 8080, 32400})
TLS_PORTS = frozenset({443, 8006, 5986, 8443})
MAX_DEVICES = 512
MAX_PORTS = 64
MAX_SCOPE_CIDRS = 16
DEFAULT_SCAN_BUDGET_SECONDS = 120.0

_DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class AgentDiscoveryError(ValueError):
    """A local validation failure safe to return to the server."""


def parse_network(value: str) -> ipaddress._BaseNetwork:
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError as exc:
        raise AgentDiscoveryError(f"Invalid CIDR: {value}") from exc


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


def validate_scope(values: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    if isinstance(values, str):
        values = [values]
    scope: list[ipaddress._BaseNetwork] = []
    for value in values or ():
        network = parse_network(value)
        reason = _network_rejection(network)
        if reason:
            raise AgentDiscoveryError(f"{network}: {reason}.")
        if network not in scope:
            scope.append(network)
    if not scope:
        raise AgentDiscoveryError("At least one discovery CIDR is required.")
    if len(scope) > MAX_SCOPE_CIDRS:
        raise AgentDiscoveryError(f"At most {MAX_SCOPE_CIDRS} discovery CIDRs are allowed.")
    return scope


def validate_excludes(values: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    excludes: list[ipaddress._BaseNetwork] = []
    for value in values or ():
        network = parse_network(value)
        if network not in excludes:
            excludes.append(network)
    return excludes


def validate_methods(values: Iterable[str]) -> list[str]:
    methods = [str(item).strip().lower() for item in values or ()]
    unknown = [item for item in methods if item not in AGENT_METHODS]
    if unknown:
        raise AgentDiscoveryError(
            "Unsupported discovery method(s): " + ", ".join(sorted(set(unknown)))
        )
    if not methods:
        raise AgentDiscoveryError("At least one discovery method is required.")
    return list(dict.fromkeys(methods))


def validate_ports(values: Iterable[Any]) -> list[int]:
    ports: list[int] = []
    for value in values or ():
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    if not ports:
        raise AgentDiscoveryError("At least one TCP port is required.")
    if len(ports) > MAX_PORTS:
        raise AgentDiscoveryError(f"At most {MAX_PORTS} TCP ports are allowed.")
    return ports


def expand_targets(
    scope: Iterable[ipaddress._BaseNetwork],
    excludes: Iterable[ipaddress._BaseNetwork],
    max_hosts: int,
) -> list[str]:
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
                raise AgentDiscoveryError(f"Scan scope exceeds the local host cap ({cap}).")
            targets.append(str(address))
    return targets


def ip_in_scope(ip: str, scope: Iterable[ipaddress._BaseNetwork]) -> bool:
    try:
        address = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    return any(address in network for network in scope)


def _clean_hostname(value: str) -> str:
    value = value.strip().strip("()").strip()
    if not value or value == "?" or value.lower() in {"unknown", "incomplete"}:
        return ""
    if _IP_RE.fullmatch(value):
        return ""
    return value[:255]


def _parse_arp_line(line: str) -> dict[str, str] | None:
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
        return {
            "ip": ip,
            "mac": mac,
            "hostname": _clean_hostname(match.group("host") or ""),
            "state": "",
        }
    match = re.search(
        r"^\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+"
        r"(?P<mac>[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})\s+(?P<state>\S+)",
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
    entries: dict[str, dict[str, str]] = {}
    for line in str(text or "").splitlines():
        parsed = _parse_neigh_line(line.strip())
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
    runner = command_runner or subprocess.run
    for command in (["ip", "neigh", "show"], ["arp", "-a"], ["arp", "-an"]):
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
        if key in {"host", "st", "usn", "server", "location", "nt", "nts"}:
            headers[key] = value.strip()[:500]
    if not headers.get("st") and not headers.get("usn") and not headers.get("nt"):
        return {}
    return headers


def listen_ssdp(
    timeout_seconds: float = 1.0,
    *,
    receiver: Callable[[float], list[tuple[bytes, tuple[str, int]]]] | None = None,
) -> list[dict[str, Any]]:
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


def parse_ssh_banner(text: bytes | str) -> dict[str, str] | None:
    value = (
        text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text or "")
    )
    value = value.strip().splitlines()[0] if value.strip() else ""
    match = re.match(r"^SSH-(?P<protocol>[\d.]+)-(?P<software>\S+)", value)
    if not match:
        return None
    software = match.group("software")
    product, version = (software.split("_", 1) + [""])[:2] if "_" in software else (software, "")
    return {
        "protocol": "ssh",
        "banner": value[:300],
        "ssh_protocol": match.group("protocol"),
        "product": product[:120],
        "version": version[:120],
    }


def parse_http_banner(text: bytes | str) -> dict[str, Any] | None:
    value = (
        text.decode("latin-1", errors="replace") if isinstance(text, bytes) else str(text or "")
    )
    if not value:
        return None
    status_match = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", value, re.IGNORECASE)
    server_match = re.search(r"^Server:\s*(.+)$", value, re.IGNORECASE | re.MULTILINE)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", value, re.IGNORECASE | re.DOTALL)
    if status_match is None and server_match is None and title_match is None:
        return None
    result: dict[str, Any] = {"protocol": "http"}
    if status_match is not None:
        result["status"] = int(status_match.group(1))
    if server_match is not None:
        result["server"] = server_match.group(1).strip()[:200]
    if title_match is not None:
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:200]
        if title:
            result["title"] = title
    return result


def decode_pem_certificate(pem: str) -> dict[str, Any]:
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return {}
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


def _name_entries(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(value, (tuple, list)):
        return result
    for item in value:
        if not isinstance(item, (tuple, list)):
            continue
        for pair in item:
            if isinstance(pair, (tuple, list)) and len(pair) == 2:
                result.setdefault(str(pair[0]), []).append(str(pair[1]))
    return result


def summarize_tls_certificate(cert: dict[str, Any]) -> dict[str, Any]:
    subject = _name_entries(cert.get("subject"))
    issuer = _name_entries(cert.get("issuer"))
    san = [
        str(value)
        for kind, value in (cert.get("subjectAltName") or [])
        if str(kind).lower() in {"dns", "ip address", "ip"}
    ]
    return {
        "protocol": "tls",
        "subject": (subject.get("commonName") or [""])[0][:255],
        "issuer": (
            (issuer.get("commonName") or issuer.get("organizationName") or [""])[0]
        )[:255],
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
            return parse_ssh_banner(data) or {"protocol": "ssh"}
        if port in HTTP_PORTS:
            try:
                sock.sendall(
                    f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: Sextant-Agent\r\n\r\n".encode(
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
                return summarize_tls_certificate(
                    decode_pem_certificate(ssl.DER_cert_to_PEM_cert(der))
                )
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
    connector: Callable[[str, int, float], socket.socket] | None = None,
) -> dict[str, dict[str, Any]]:
    connect = connector or _connect_tcp
    services: dict[str, dict[str, Any]] = {}
    for port in [int(item) for item in ports]:
        result = _probe_port(ip, port, max(0.1, timeout_seconds), connect)
        if result is not None:
            services[str(port)] = result
    return services


def run_agent_scan(
    *,
    scope: Iterable[str],
    methods: Iterable[str],
    ports: Iterable[int],
    timeout_ms: int = 600,
    concurrency: int = 16,
    max_hosts: int = 512,
    excludes: Iterable[str] = (),
    os_name: str | None = None,
    deadline_seconds: float | None = None,
    command_runner: Callable[..., Any] | None = None,
    resolver: Callable[[str], tuple[str, list[str], list[str]]] | None = None,
    ssdp_receiver: Callable[[float], list[tuple[bytes, tuple[str, int]]]] | None = None,
    tcp_connector: Callable[[str, int, float], socket.socket] | None = None,
) -> dict[str, Any]:
    platform = (os_name or sys.platform or "").lower()
    if platform.startswith("win") or platform.startswith("cygwin") or platform.startswith("msys"):
        return {
            "status": "unsupported",
            "error": "Agent discovery is supported on Linux only.",
            "devices": [],
            "collected_at": _utc_stamp(),
            "segment": "",
            "truncated": False,
        }

    try:
        networks = validate_scope(scope)
        method_list = validate_methods(methods)
        port_list = validate_ports(ports) if "tcp" in method_list else []
        excluded = validate_excludes(excludes)
        cap = max(1, min(1024, int(max_hosts)))
        targets = expand_targets(networks, excluded, cap)
        timeout_seconds = max(0.1, min(5.0, int(timeout_ms) / 1000.0))
        workers = max(1, min(128, int(concurrency)))
    except (AgentDiscoveryError, TypeError, ValueError) as exc:
        return {
            "status": "failed",
            "error": str(exc)[:500],
            "devices": [],
            "collected_at": _utc_stamp(),
            "segment": "",
            "truncated": False,
        }

    target_set = set(targets)
    deadline = time.monotonic() + max(
        2.0,
        min(300.0, float(deadline_seconds or DEFAULT_SCAN_BUDGET_SECONDS)),
    )
    neighbours: dict[str, dict[str, str]] = {}
    if "neigh" in method_list:
        for entry in read_neighbor_table(command_runner):
            ip = entry.get("ip", "")
            if (
                ip in target_set
                and not _is_excluded(ip, excluded)
                and time.monotonic() < deadline
            ):
                neighbours[ip] = entry

    hostnames: dict[str, str] = {}
    if "dns" in method_list:
        for ip in neighbours:
            if time.monotonic() >= deadline:
                break
            name = reverse_dns(ip, resolver)
            if name:
                hostnames[ip] = name

    ssdp_by_ip: dict[str, dict[str, Any]] = {}
    if "ssdp" in method_list:
        for item in listen_ssdp(min(2.0, max(0.2, timeout_seconds * 2)), receiver=ssdp_receiver):
            ip = str(item.get("ip", ""))
            if ip in target_set and not _is_excluded(ip, excluded):
                ssdp_by_ip.setdefault(ip, {})
                for key, value in item.items():
                    if key != "ip":
                        ssdp_by_ip[ip][key] = value

    services_by_ip: dict[str, dict[str, Any]] = {}
    if "tcp" in method_list and port_list and targets:
        probe_targets = [ip for ip in targets if not _is_excluded(ip, excluded)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    probe_tcp_host,
                    ip,
                    port_list,
                    timeout_seconds=timeout_seconds,
                    connector=tcp_connector,
                ): ip
                for ip in probe_targets
            }
            for future in concurrent.futures.as_completed(futures):
                if time.monotonic() >= deadline:
                    for pending in futures:
                        pending.cancel()
                    break
                ip = futures[future]
                try:
                    services = future.result()
                except Exception:  # noqa: BLE001 - one host never aborts the scan
                    continue
                if services:
                    services_by_ip[ip] = services

    candidate_ips = set(neighbours) | set(services_by_ip) | set(ssdp_by_ip) | set(hostnames)
    devices: list[dict[str, Any]] = []
    truncated = False
    for ip in sorted(candidate_ips, key=ipaddress.ip_address):
        if time.monotonic() >= deadline:
            truncated = True
            break
        if ip not in target_set or _is_excluded(ip, excluded):
            continue
        entry = neighbours.get(ip, {})
        services = services_by_ip.get(ip, {})
        ssdp = ssdp_by_ip.get(ip, {})
        hostname = hostnames.get(ip, "") or entry.get("hostname", "")
        mac = normalize_mac(entry.get("mac", ""))
        vendor = lookup_vendor(mac)
        evidence = {
            "methods": method_list,
            "neighbor": bool(entry),
            "reverse_dns": bool(hostnames.get(ip)),
            "ssdp": bool(ssdp),
            "tcp": bool(services),
            "randomized_mac": is_locally_administered(mac),
        }
        devices.append(
            {
                "ip": ip,
                "mac": mac,
                "hostname": hostname[:255],
                "vendor": vendor[:255],
                "open_ports": sorted(int(port) for port in services),
                "services": services,
                "mdns": {},
                "ssdp": ssdp,
                "evidence": evidence,
            }
        )
        if len(devices) >= MAX_DEVICES:
            truncated = True
            break

    return {
        "status": "succeeded",
        "error": "",
        "devices": devices,
        "collected_at": _utc_stamp(),
        "segment": str(networks[0]) if networks else "",
        "truncated": truncated,
    }


def discovery_allowed(config: Any, remote_policy: dict[str, Any] | None = None) -> bool:
    policy = remote_policy if isinstance(remote_policy, dict) else {}
    return bool(getattr(config, "allow_discovery", False)) and bool(
        policy.get("allow_discovery", False)
    )


def _is_excluded(ip: str, excludes: Iterable[ipaddress._BaseNetwork]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(address in exclude for exclude in excludes)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

"""Read-only discovery integration adapters and write-only secret storage.

Live vendor endpoints are implemented conservatively and marked unverified. ESXi
is fixture/export-only because a safe live SOAP client is outside this phase.
Secrets are held in process memory only; they are never written to SQLite,
audit rows, responses, or logs.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from sextant_common.oui import lookup_vendor, normalize_mac
from sextant_control.discovery_imports import normalize_device


class IntegrationError(ValueError):
    pass


INTEGRATION_KINDS = ("snmp", "unifi", "routeros", "opnsense", "proxmox", "esxi")


class IntegrationSecretStore:
    """Thread-safe, in-memory-only write-only secret holder."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: dict[str, dict[str, str]] = {}

    def set(self, kind: str, values: dict[str, str] | None, *, merge: bool = True) -> None:
        kind = str(kind).lower()
        cleaned = {
            str(key): str(value)
            for key, value in (values or {}).items()
            if value not in (None, "")
        }
        with self._lock:
            if not merge:
                self._secrets.pop(kind, None)
            if cleaned:
                current = self._secrets.get(kind, {}) if merge else {}
                current.update(cleaned)
                self._secrets[kind] = current

    def get(self, kind: str) -> dict[str, str]:
        with self._lock:
            return dict(self._secrets.get(str(kind).lower(), {}))

    def configured(self, kind: str, fields: tuple[str, ...] | None = None) -> bool:
        values = self.get(kind)
        if fields is None:
            return bool(values)
        return any(values.get(field) for field in fields)

    def clear(self, kind: str) -> None:
        with self._lock:
            self._secrets.pop(str(kind).lower(), None)


def secret_fields(kind: str) -> tuple[str, ...]:
    return _secret_fields(kind)


def _secret_fields(kind: str) -> tuple[str, ...]:
    return {
        "snmp": ("community",),
        "unifi": ("api_token", "password"),
        "routeros": ("password",),
        "opnsense": ("api_key", "api_secret"),
        "proxmox": ("token_secret", "password"),
        "esxi": ("password",),
    }.get(kind, ())


def _base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise IntegrationError("base_url is required.")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"https", "http"}:
        raise IntegrationError("base_url must use https:// (or http:// on localhost).")
    if not parsed.hostname:
        raise IntegrationError("base_url must include a hostname.")
    if parsed.username or parsed.password:
        raise IntegrationError("Do not put credentials in base_url.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise IntegrationError("Non-local vendor connectors must use https://.")
    if parsed.query or parsed.fragment:
        raise IntegrationError("base_url must not contain a query or fragment.")
    return raw[:1024]


def validate_integration_config(kind: str, config: dict[str, Any] | None) -> dict[str, Any]:
    kind = str(kind).lower()
    if kind not in INTEGRATION_KINDS:
        raise IntegrationError("Unknown discovery integration.")
    source = config if isinstance(config, dict) else {}
    if kind == "snmp":
        targets = source.get("targets") or []
        if not isinstance(targets, list) or len(targets) > 256:
            raise IntegrationError("SNMP targets must be an array of at most 256 IP/CIDR values.")
        normalized_targets: list[str] = []
        for target in targets:
            text = str(target).strip()
            try:
                network = ipaddress.ip_network(text, strict=False)
            except ValueError:
                raise IntegrationError(f"Invalid SNMP target: {text[:80]}") from None
            if network.is_global or not network.is_private:
                raise IntegrationError("SNMP targets must be private IP/CIDR ranges.")
            normalized_targets.append(str(network))
        return {
            "targets": normalized_targets,
            "use_discovered": bool(source.get("use_discovered", True)),
            "timeout_ms": max(100, min(5000, int(source.get("timeout_ms", 600)))),
            "retries": max(0, min(3, int(source.get("retries", 1)))),
            "max_targets": max(1, min(1024, int(source.get("max_targets", 256)))),
            "verify_tls": True,
        }
    normalized: dict[str, Any] = {
        "base_url": _base_url(source.get("base_url")),
        "verify_tls": bool(source.get("verify_tls", True)),
        "timeout_ms": max(100, min(30000, int(source.get("timeout_ms", 5000)))),
        "max_response_bytes": max(
            1024, min(4_000_000, int(source.get("max_response_bytes", 1_000_000)))
        ),
    }
    for key in ("username", "token_name", "site", "label"):
        if source.get(key) not in (None, ""):
            normalized[key] = str(source[key]).strip()[:128]
    if kind == "unifi":
        normalized.setdefault("site", "default")
    if kind == "proxmox":
        normalized.setdefault("auth_method", "token")
    if kind == "esxi":
        normalized["fixture_only"] = True
    return normalized


def _bounded_evidence(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:256] if isinstance(value, str) else value
    if isinstance(value, list):
        return [_bounded_evidence(item) for item in value[:32]]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _bounded_evidence(item)
            for key, item in list(value.items())[:32]
        }
    return str(value)[:256]


def _adapter_device(
    *,
    kind: str,
    ip: Any,
    mac: Any = "",
    hostname: Any = "",
    vendor: Any = "",
    ports: Any = None,
    services: Any = None,
    evidence: Any = None,
) -> dict[str, Any] | None:
    evidence_block = {
        "adapter": kind,
        "adapter_verified": False,
        **(evidence if isinstance(evidence, dict) else {}),
    }
    item = {
        "ip": ip,
        "mac": mac,
        "hostname": hostname,
        "vendor": vendor or (lookup_vendor(normalize_mac(mac)) if mac else ""),
        "open_ports": ports or [],
        "services": services or {},
        "evidence": evidence_block,
    }
    normalized = normalize_device(item, f"adapter:{kind}", evidence_block)
    if normalized is None:
        return None
    return normalized


def parse_unifi(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise IntegrationError("UniFi response did not contain a data array.")
    devices: list[dict[str, Any]] = []
    for item in data[:5000]:
        if not isinstance(item, dict):
            continue
        mac = item.get("mac") or item.get("macAddress") or ""
        ip = item.get("ip") or item.get("ipAddress") or ""
        name = item.get("name") or item.get("hostname") or ""
        vendor = item.get("oui") or "Ubiquiti"
        device_type = str(item.get("type") or "").lower()
        ports = []
        services = {}
        port_table = item.get("port_table")
        if isinstance(port_table, list):
            evidence_ports = [
                {"port_idx": port.get("port_idx"), "up": port.get("up")}
                for port in port_table[:128]
                if isinstance(port, dict)
            ]
        else:
            evidence_ports = []
        normalized = _adapter_device(
            kind="unifi",
            ip=ip,
            mac=mac,
            hostname=name,
            vendor=vendor,
            ports=ports,
            services=services,
            evidence={
                "unifi_type": device_type,
                "port_table": evidence_ports,
                "imported_device_type": "router/switch",
            },
        )
        if normalized:
            devices.append(normalized)
    return devices


def parse_routeros_arp(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise IntegrationError("RouterOS response did not contain an array.")
    devices: list[dict[str, Any]] = []
    for item in data[:5000]:
        if not isinstance(item, dict):
            continue
        normalized = _adapter_device(
            kind="routeros",
            ip=item.get("address") or item.get("ip-address") or "",
            mac=item.get("mac-address") or item.get("mac") or "",
            hostname=item.get("host-name") or item.get("hostname") or item.get("comment") or "",
            vendor="MikroTik",
            evidence={
                "interface": item.get("interface"),
                "status": item.get("status"),
                "dynamic": item.get("dynamic"),
                "imported_device_type": "router/switch",
            },
        )
        if normalized:
            devices.append(normalized)
    return devices


def parse_opnsense_arp(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("rows") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        raise IntegrationError("OPNsense response did not contain rows.")
    devices: list[dict[str, Any]] = []
    for item in data[:5000]:
        if not isinstance(item, dict):
            continue
        normalized = _adapter_device(
            kind="opnsense",
            ip=item.get("ip") or item.get("address") or "",
            mac=item.get("mac") or item.get("macaddress") or "",
            hostname=item.get("hostname") or item.get("host") or "",
            vendor=item.get("vendor") or item.get("manufacturer") or "",
            evidence={
                "interface": item.get("interface") or item.get("intf_description"),
                "manufacturer": item.get("manufacturer"),
                "imported_device_type": "router/switch",
            },
        )
        if normalized:
            devices.append(normalized)
    return devices


def parse_proxmox_nodes(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise IntegrationError("Proxmox response did not contain a data array.")
    devices: list[dict[str, Any]] = []
    for item in data[:5000]:
        if not isinstance(item, dict):
            continue
        normalized = _adapter_device(
            kind="proxmox",
            ip=item.get("ip") or item.get("address") or "",
            mac=item.get("mac") or "",
            hostname=item.get("name") or item.get("node") or "",
            vendor="Proxmox",
            evidence={
                "node": item.get("node") or item.get("name"),
                "status": item.get("status"),
                "maxmem": item.get("maxmem"),
                "maxcpu": item.get("maxcpu"),
                "imported_device_type": "hypervisor",
            },
        )
        if normalized:
            devices.append(normalized)
    return devices


def parse_esxi_hosts(payload: Any) -> list[dict[str, Any]]:
    """Parse a bounded normalized ESXi export; no live SOAP connector is implemented."""
    data = payload.get("hosts") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise IntegrationError("ESXi fixture/export must contain a hosts array.")
    devices: list[dict[str, Any]] = []
    for item in data[:5000]:
        if not isinstance(item, dict):
            continue
        normalized = _adapter_device(
            kind="esxi",
            ip=item.get("ip") or item.get("address") or "",
            mac=item.get("mac") or "",
            hostname=item.get("name") or item.get("hostname") or "",
            vendor="VMware",
            evidence={
                "esxi_version": item.get("version"),
                "esxi_build": item.get("build"),
                "fixture_only": True,
                "imported_device_type": "hypervisor",
            },
        )
        if normalized:
            devices.append(normalized)
    return devices


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpFetcher:
    """Small read-only JSON fetcher with bounded time and response size."""

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        self.config = config
        self.secrets = secrets
        self.timeout = max(0.1, min(30.0, int(config.get("timeout_ms", 5000)) / 1000))
        self.max_bytes = max(1024, min(4_000_000, int(config.get("max_response_bytes", 1_000_000))))

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"https", "http"}:
            raise IntegrationError("Connector URL must use http(s).")
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", **(headers or {})},
            method="GET",
        )
        handlers: list[Any] = [_NoRedirect()]
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            if not self.config.get("verify_tls", True):
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise IntegrationError(f"{parsed.hostname} returned HTTP {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise IntegrationError(f"{parsed.hostname} is unreachable: {type(exc).__name__}.") from None
        if len(raw) > self.max_bytes:
            raise IntegrationError(f"{parsed.hostname} response exceeded the size limit.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise IntegrationError(f"{parsed.hostname} returned invalid JSON.") from None


def _auth_headers(kind: str, config: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if kind == "unifi":
        if secrets.get("api_token"):
            headers["X-API-KEY"] = secrets["api_token"]
        elif secrets.get("password"):
            raw = f"{config.get('username', '')}:{secrets['password']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif kind == "routeros":
        if secrets.get("password"):
            raw = f"{config.get('username', '')}:{secrets['password']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif kind == "opnsense":
        if secrets.get("api_key") and secrets.get("api_secret"):
            raw = f"{secrets['api_key']}:{secrets['api_secret']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif kind == "proxmox":
        if secrets.get("token_secret"):
            headers["Authorization"] = (
                f"PVEAPIToken={config.get('token_name', '')}={secrets['token_secret']}"
            )
        elif secrets.get("password"):
            raw = f"{config.get('username', '')}:{secrets['password']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif kind == "esxi":
        if secrets.get("password"):
            raw = f"{config.get('username', '')}:{secrets['password']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    return headers


@dataclass
class AdapterResult:
    kind: str
    devices: list[dict[str, Any]]
    live_supported: bool = True
    verified: bool = False
    detail: str = ""


_ADAPTERS: dict[str, dict[str, Any]] = {
    "unifi": {
        "label": "UniFi controller",
        "path": "/api/s/{site}/stat/device",
        "parser": parse_unifi,
        "live_supported": True,
    },
    "routeros": {
        "label": "RouterOS",
        "path": "/rest/ip/arp",
        "parser": parse_routeros_arp,
        "live_supported": True,
    },
    "opnsense": {
        "label": "OPNsense",
        "path": "/api/diagnostics/interface/getArp",
        "parser": parse_opnsense_arp,
        "live_supported": True,
    },
    "proxmox": {
        "label": "Proxmox",
        "path": "/api2/json/nodes",
        "parser": parse_proxmox_nodes,
        "live_supported": True,
    },
    "esxi": {
        "label": "VMware ESXi",
        "path": "",
        "parser": parse_esxi_hosts,
        "live_supported": False,
    },
}


def adapter_descriptor(kind: str) -> dict[str, Any]:
    meta = _ADAPTERS.get(kind)
    if meta is None:
        raise IntegrationError("Unknown vendor adapter.")
    return {
        "kind": kind,
        "label": meta["label"],
        "live_supported": bool(meta["live_supported"]),
        "verified": False,
        "secret_fields": list(_secret_fields(kind)),
    }


def fetch_adapter_devices(
    kind: str,
    config: dict[str, Any],
    secrets: dict[str, str],
    fetcher: Callable[[str, dict[str, str]], Any] | None = None,
) -> AdapterResult:
    meta = _ADAPTERS.get(kind)
    if meta is None:
        raise IntegrationError("Unknown vendor adapter.")
    if not meta["live_supported"]:
        raise IntegrationError(
            f"{meta['label']} is fixture-only in this release; a live connector is unverified."
        )
    if not secrets:
        raise IntegrationError("No credentials are configured for this connector.")
    base = config["base_url"]
    path = str(meta["path"]).format(site=config.get("site", "default"))
    url = base + path
    headers = _auth_headers(kind, config, secrets)
    fetch = fetcher or HttpFetcher(config, secrets).get_json
    payload = fetch(url, headers)
    devices = meta["parser"](payload)
    return AdapterResult(
        kind=kind,
        devices=devices,
        live_supported=True,
        verified=False,
        detail=f"{len(devices)} devices parsed; live vendor endpoint is unverified.",
    )


def test_adapter(
    kind: str,
    config: dict[str, Any],
    secrets: dict[str, str],
    fetcher: Callable[[str, dict[str, str]], Any] | None = None,
) -> AdapterResult:
    return fetch_adapter_devices(kind, config, secrets, fetcher)

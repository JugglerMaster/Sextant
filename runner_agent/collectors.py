from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .config import AgentConfig

PSEUDO_FS = {
    "proc",
    "sysfs",
    "devtmpfs",
    "devpts",
    "tmpfs",
    "cgroup",
    "cgroup2",
    "pstore",
    "securityfs",
    "debugfs",
    "tracefs",
    "configfs",
    "fusectl",
    "mqueue",
    "hugetlbfs",
    "bpf",
    "autofs",
    "binfmt_misc",
    "efivarfs",
    "rpc_pipefs",
    "nsfs",
    "squashfs",
}

WINDOWS_FAST_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  [pscustomobject]@{
    mount = [string]$_.DeviceID
    fs = [string]$_.FileSystem
    total = [int64]$_.Size
    free = [int64]$_.FreeSpace
  }
})
$net = Get-NetIPConfiguration | Where-Object {
  $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway -ne $null
} | Select-Object -First 1
$ip = ''
$mac = ''
if ($net) {
  $ip = [string](@($net.IPv4Address)[0].IPAddress)
  $mac = [string]$net.NetAdapter.MacAddress
}
[pscustomobject]@{
  hostname = [string]$env:COMPUTERNAME
  os_version = (( [string]$os.Caption ) + ' ' + ( [string]$os.Version )).Trim()
  arch = [string]$env:PROCESSOR_ARCHITECTURE
  ip = $ip
  mac = $mac
  cpu_pct = [double]$cpu.LoadPercentage
  mem_total = ([int64]$os.TotalVisibleMemorySize * 1024)
  mem_used = (([int64]$os.TotalVisibleMemorySize - [int64]$os.FreePhysicalMemory) * 1024)
  uptime_seconds = [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds
  disks = $disks
} | ConvertTo-Json -Depth 4 -Compress
"""

WINDOWS_INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$services = @(Get-CimInstance Win32_Service | ForEach-Object {
  [pscustomobject]@{
    name = [string]$_.Name
    display_name = [string]$_.DisplayName
    status = [string]$_.State
    startup = [string]$_.StartMode
    watched = $false
  }
})
$startup = New-Object System.Collections.ArrayList
$runKeys = @(
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
)
foreach ($key in $runKeys) {
  $item = Get-ItemProperty -Path $key
  if ($item) {
    $item.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
      [void]$startup.Add([pscustomobject]@{
        name = [string]$_.Name
        command = [string]$_.Value
        source = [string]$key
        enabled = $true
      })
    }
  }
}
$startupDirs = @(
  (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup')
)
foreach ($dir in $startupDirs) {
  Get-ChildItem -LiteralPath $dir -File | ForEach-Object {
    [void]$startup.Add([pscustomobject]@{
      name = [string]$_.Name
      command = [string]$_.FullName
      source = [string]$dir
      enabled = $true
    })
  }
}
Get-CimInstance Win32_StartupCommand | ForEach-Object {
  [void]$startup.Add([pscustomobject]@{
    name = [string]$_.Name
    command = [string]$_.Command
    source = ([string]$_.Location + ' / ' + [string]$_.User)
    enabled = $true
  })
}
$gpuNames = @(Get-CimInstance Win32_VideoController | ForEach-Object { [string]$_.Name })
[pscustomobject]@{
  cpu_model = [string]$cpu.Name
  services = $services
  startup = @($startup)
  gpu_names = $gpuNames
} | ConvertTo-Json -Depth 5 -Compress
"""


@dataclass
class _CpuSample:
    idle: int
    total: int


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _run(args: list[str], timeout: int = 10, input_text: str | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _powershell_script(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str] | None:
    exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    return _run(
        [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
        timeout=timeout,
        input_text=script,
    )


def _parse_json_object(result: subprocess.CompletedProcess[str] | None) -> dict[str, Any]:
    if result and result.returncode == 0 and result.stdout.strip():
        try:
            parsed = json.loads(result.stdout.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def machine_identity() -> str:
    """Return a stable host identity when the OS exposes one."""
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)[:128]
        except (OSError, ImportError):
            pass
    else:
        for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            try:
                value = path.read_text(encoding="utf-8", errors="replace").strip()
                if value:
                    return value[:128]
            except OSError:
                continue
    # Stable fallback for unusual images/containers without machine-id.
    fallback = f"{socket.gethostname()}|{_uuid_mac()}".encode("utf-8", errors="replace")
    return hashlib.sha256(fallback).hexdigest()


def parse_cpu_stat(text: str) -> _CpuSample | None:
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            if len(parts) < 5:
                return None
            values = [int(value) for value in parts[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return _CpuSample(idle=idle, total=sum(values))
    return None


def parse_proc_meminfo(text: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable")
    if available is None:
        available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    return total, max(0, total - available)


def parse_nvidia_smi(text: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue

        def number(value: str) -> float:
            try:
                return float(value)
            except ValueError:
                return 0.0

        used_mib = number(parts[2])
        total_mib = number(parts[3])
        gpus.append(
            {
                "name": parts[0] or "NVIDIA GPU",
                "util_pct": max(0.0, min(100.0, number(parts[1]))),
                "mem_used": int(used_mib * 1024 * 1024),
                "mem_total": int(total_mib * 1024 * 1024),
                "temp_c": max(0.0, min(200.0, number(parts[4]))),
            }
        )
    return gpus


def parse_nvidia_processes(text: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",", 2)]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            used_mib = float(parts[2])
        except ValueError:
            continue
        processes.append(
            {
                "pid": pid,
                "name": parts[1][:255],
                "mem_used": int(used_mib * 1024 * 1024),
            }
        )
    return processes


def parse_systemctl_units(text: str) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("●*").strip()
        if not line or line.startswith("UNIT "):
            continue
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        unit, _load, active, sub = parts[:4]
        description = parts[4] if len(parts) > 4 else ""
        if active == "active" and sub == "running":
            status = "running"
        elif active == "failed" or sub == "failed":
            status = "failed"
        elif active == "activating" or sub == "start":
            status = "starting"
        elif active == "deactivating" or sub == "stop":
            status = "stopping"
        else:
            status = sub or active or "unknown"
        services.append(
            {
                "name": unit,
                "display_name": description,
                "status": status,
                "startup": "",
                "watched": False,
            }
        )
    return services


def parse_unit_files(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) >= 2 and parts[0].endswith(".service"):
            result[parts[0]] = parts[1]
    return result


def parse_startup_desktop(text: str) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    in_desktop = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[Desktop Entry]":
            in_desktop = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_desktop = False
            continue
        if not in_desktop or "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    if not fields:
        return None
    return fields


def _base_service(name: str) -> str:
    lowered = (name or "").strip().casefold()
    if lowered.endswith(".service"):
        lowered = lowered[: -len(".service")]
    return lowered


class MetricsCollector:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.watch = {_base_service(name) for name in config.watch_services if name.strip()}
        self._prev_cpu: _CpuSample | None = None
        self._prev_cpu_at = 0.0
        self._service_inventory: dict[str, Any] | None = None
        self._service_inventory_at = 0.0
        self._service_inventory_collected_at = ""
        self._service_forced = False
        self._package_payload: dict[str, Any] | None = None
        self._package_hash = ""
        self._package_collected_at = ""
        self._package_last_collect = 0.0
        self._package_last_full = 0.0
        self._package_forced = False
        self._package_full_forced = False
        self._package_previous_hash = ""
        self._package_rows: list[dict[str, str]] = []
        self._package_previous_rows: list[dict[str, str]] | None = None
        self._package_revision = 0
        self._package_full_pending = False
        self._machine_id: str | None = None
        self.profile: dict[str, Any] = {}

    def set_profile(self, profile: dict[str, Any]) -> None:
        if not isinstance(profile, dict):
            return
        self.profile = profile
        self._service_forced = True
        self._package_forced = True

    def _module(self, name: str, default: bool = True) -> bool:
        modules = self.profile.get("modules") if isinstance(self.profile.get("modules"), dict) else {}
        return bool(modules.get(name, default))

    def set_watch_services(self, names: Iterable[str]) -> None:
        watch = {_base_service(str(name)) for name in names if str(name).strip()}
        if watch != self.watch:
            self.watch = watch
            self.request_inventory_refresh()

    def request_inventory_refresh(self) -> None:
        """Force service and package inventory refresh on the next snapshot."""
        self._service_forced = True
        self._package_forced = True
        self._package_full_forced = True

    def request_package_full(self) -> None:
        self._package_forced = True
        self._package_full_forced = True
        self._package_full_pending = True

    def ack_package_payload(self, revision: int | None = None) -> None:
        """Mark a successfully delivered package payload as acknowledged."""
        if self._package_payload is not None and revision is not None:
            self._package_revision = max(0, int(revision))
            self._package_previous_rows = list(self._package_rows)
            if self._package_payload.get("full"):
                self._package_last_full = time.monotonic()
                self._package_full_forced = False
                self._package_full_pending = False
        self._package_payload = None

    def snapshot(self) -> dict[str, Any]:
        if not self._module("telemetry"):
            return {
                "hostname": socket.gethostname(),
                "os": "windows" if platform.system() == "Windows" else "linux",
                "os_version": "",
                "arch": platform.machine()[:64],
                "ip": "",
                "mac": "",
                "cpu_pct": 0.0,
                "mem_total": 0,
                "mem_used": 0,
                "mem_pct": 0.0,
                "uptime_seconds": 0,
                "disks": [],
                "gpus": [],
                "services": [],
                "startup": [],
                "packages": [],
                "package_managers": [],
                "package_inventory_hash": "",
                "package_inventory_full": False,
                "package_inventory_collected_at": "",
                "extra": {},
                "agent_version": __version__,
                "machine_id": self._machine_identity(),
            }
        system = platform.system()
        if system == "Windows":
            data = self._windows_snapshot()
        else:
            data = self._linux_snapshot()
        self._ensure_package_inventory()
        data["agent_version"] = __version__
        data["machine_id"] = self._machine_identity()
        data["package_inventory_hash"] = self._package_hash
        data["package_inventory_collected_at"] = self._package_collected_at
        data["service_inventory_collected_at"] = self._service_inventory_collected_at
        data["package_inventory_full"] = bool(self._package_payload and self._package_payload.get("full"))
        payload = self._package_payload
        data["package_inventory_mode"] = payload.get("mode", "full") if payload else ("delta" if self._package_revision else "full")
        data["package_inventory_base_revision"] = payload.get("base_revision", self._package_revision) if payload else self._package_revision
        data["package_inventory_revision"] = self._package_revision + (1 if payload else 0)
        data["package_inventory_deletes"] = payload.get("deletes", []) if payload else []
        if payload is not None:
            data["packages"] = payload.get("packages", [])
            data["package_managers"] = payload.get("package_managers", [])
        return data

    def _machine_identity(self) -> str:
        if self._machine_id is None:
            self._machine_id = machine_identity()
        return self._machine_id

    def _ensure_service_inventory(self, system: str) -> dict[str, Any]:
        interval = max(60, int(getattr(self.config, "service_inventory_interval", 0) or getattr(self.config, "inventory_interval", 3600)))
        expired = (time.monotonic() - self._service_inventory_at) >= interval
        if self._service_inventory is None or self._service_forced or expired:
            self._service_inventory = self._collect_service_inventory(system)
            self._service_inventory_at = time.monotonic()
            self._service_inventory_collected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._service_forced = False
        return self._service_inventory

    def _collect_service_inventory(self, system: str) -> dict[str, Any]:
        if system == "Windows":
            return self._collect_windows_inventory()
        services, unit_files = self._linux_services()
        return {
            "services": services if self._module("services") else [],
            "startup": self._linux_startup(unit_files) if self._module("startup") else [],
            "gpu_processes": self._nvidia_processes() if self._module("gpu") else [],
            "cpu_model": _linux_cpu_model(),
            "kernel": platform.release(),
            "os_version": _linux_os_version(),
            "arch": platform.machine(),
        }

    def _ensure_package_inventory(self) -> None:
        if not self._module("packages"):
            self._package_hash = ""
            self._package_collected_at = ""
            self._package_payload = None
            return
        from .packages import collect_inventory, detect_managers

        now = time.monotonic()
        interval = max(300, int(getattr(self.config, "package_inventory_interval", 86400)))
        full_interval = max(3600, int(getattr(self.config, "package_full_interval", 604800)))
        due = self._package_last_collect == 0 or self._package_forced or (now - self._package_last_collect) >= interval
        if not due:
            return
        full_due = (
            self._package_previous_rows is None
            or self._package_last_full == 0
            or self._package_full_forced
            or self._package_full_pending
            or (now - self._package_last_full) >= full_interval
        )
        rows = collect_inventory()
        managers = detect_managers()
        digest = hashlib.sha256()
        for entry in sorted(rows, key=lambda item: (item.get("manager", ""), item.get("package", ""))):
            digest.update(
                f"{entry.get('manager','')}|{entry.get('package','')}|{entry.get('version','')}|{entry.get('arch','')}\n".encode()
            )
        new_hash = digest.hexdigest()
        changed = new_hash != self._package_previous_hash
        self._package_hash = new_hash
        self._package_collected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._package_last_collect = now
        self._package_forced = False
        if full_due:
            self._package_payload = {
                "mode": "full",
                "packages": rows,
                "package_managers": managers,
                "deletes": [],
                "base_revision": 0,
                "full": True,
            }
        elif changed:
            previous = {
                (row.get("manager", ""), row.get("package", "")): row
                for row in (self._package_previous_rows or [])
            }
            current = {(row.get("manager", ""), row.get("package", "")): row for row in rows}
            upserts = [
                row
                for key, row in current.items()
                if key not in previous
                or previous[key].get("version") != row.get("version")
                or previous[key].get("arch") != row.get("arch")
            ]
            deletes = [[manager, package] for manager, package in previous if (manager, package) not in current]
            self._package_payload = (
                {
                    "mode": "delta",
                    "packages": upserts,
                    "package_managers": managers,
                    "deletes": deletes,
                    "base_revision": self._package_revision,
                    "full": False,
                }
                if upserts or deletes
                else None
            )
        else:
            self._package_payload = None
        self._package_rows = rows
        self._package_previous_hash = new_hash

    # --- Linux -----------------------------------------------------------
    def _linux_snapshot(self) -> dict[str, Any]:
        inventory = self._ensure_service_inventory("Linux")
        cpu = self._linux_cpu_pct()
        total_mem, used_mem = self._linux_memory()
        gpus = self._nvidia_gpus() if self._module("gpu") else []
        ip, mac = _linux_network()
        return {
            "hostname": socket.gethostname(),
            "os": "linux",
            "os_version": inventory["os_version"],
            "arch": inventory["arch"],
            "ip": ip,
            "mac": mac,
            "cpu_pct": cpu,
            "mem_total": total_mem,
            "mem_used": used_mem,
            "mem_pct": (used_mem / total_mem * 100.0) if total_mem else 0.0,
            "uptime_seconds": _linux_uptime(),
            "disks": _linux_disks(),
            "gpus": gpus,
            "services": inventory["services"],
            "startup": inventory["startup"],
            "packages": inventory.get("packages", []),
            "package_managers": inventory.get("package_managers", []),
            "extra": {
                "cpu_model": inventory["cpu_model"],
                "kernel": inventory["kernel"],
                "gpu_source": "nvidia-smi" if gpus else "none",
                "gpu_processes": inventory["gpu_processes"],
            },
        }

    def _linux_cpu_pct(self) -> float:
        try:
            sample = parse_cpu_stat(Path("/proc/stat").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return 0.0
        if sample is None:
            return 0.0
        if self._prev_cpu is None:
            self._prev_cpu = sample
            self._prev_cpu_at = time.monotonic()
            return 0.0
        total_delta = sample.total - self._prev_cpu.total
        idle_delta = sample.idle - self._prev_cpu.idle
        self._prev_cpu = sample
        self._prev_cpu_at = time.monotonic()
        if total_delta <= 0:
            return 0.0
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))

    @staticmethod
    def _linux_memory() -> tuple[int, int]:
        try:
            return parse_proc_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return 0, 0

    def _linux_services(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not shutil.which("systemctl"):
            return [], {}
        units_result = _run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
            timeout=12,
        )
        files_result = _run(
            ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
            timeout=12,
        )
        services = parse_systemctl_units(units_result.stdout) if units_result and units_result.stdout else []
        unit_files = parse_unit_files(files_result.stdout) if files_result and files_result.stdout else {}
        by_name = {service["name"]: service for service in services}
        for unit, startup in unit_files.items():
            if unit not in by_name:
                by_name[unit] = {
                    "name": unit,
                    "display_name": "",
                    "status": "unknown",
                    "startup": startup,
                    "watched": False,
                }
            else:
                by_name[unit]["startup"] = startup

        for service in by_name.values():
            service["watched"] = _base_service(service["name"]) in self.watch

        # Keep the payload bounded: monitored services, active/failed units, then a sample
        # of known services. The full list remains available via systemctl on the host.
        def priority(item: dict[str, Any]) -> tuple[int, int, str]:
            return (
                0 if item["watched"] else 1,
                0 if item["status"] in {"running", "failed", "starting"} else 1,
                item["name"].casefold(),
            )

        selected = sorted(by_name.values(), key=priority)[:300]
        for service in selected:
            if service["status"] == "unknown" and service["watched"]:
                active = _run(["systemctl", "is-active", service["name"]], timeout=5)
                service["status"] = (active.stdout.strip() if active and active.stdout else "unknown") or "unknown"
        return selected, unit_files

    def _linux_startup(self, unit_files: dict[str, str]) -> list[dict[str, Any]]:
        startup: list[dict[str, Any]] = []
        for unit, state in sorted(unit_files.items()):
            if state.casefold() == "enabled":
                startup.append(
                    {
                        "name": unit,
                        "command": f"systemctl enable {unit}",
                        "source": "systemd",
                        "enabled": True,
                    }
                )
        dirs = [Path("/etc/xdg/autostart")]
        home_dirs = [Path("/root")]
        try:
            home_dirs.extend(path for path in Path("/home").glob("*") if path.is_dir())
        except OSError:
            pass
        dirs.extend(home / ".config" / "autostart" for home in home_dirs)
        seen_startup: set[tuple[str, str]] = set()
        for directory in dirs:
            try:
                entries = sorted(directory.glob("*.desktop"))
            except OSError:
                continue
            for entry in entries[:100]:
                try:
                    fields = parse_startup_desktop(entry.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if not fields:
                    continue
                name = fields.get("Name", entry.stem)[:255]
                command = fields.get("Exec", "")[:2048]
                key = (name.casefold(), command.casefold())
                if key in seen_startup:
                    continue
                seen_startup.add(key)
                hidden = fields.get("Hidden", "").casefold() == "true"
                enabled = fields.get("X-GNOME-Autostart-enabled", "true").casefold() != "false"
                startup.append(
                    {
                        "name": name,
                        "command": command,
                        "source": str(directory),
                        "enabled": enabled and not hidden,
                    }
                )
                if len(startup) >= 300:
                    return startup
        return startup

    def _nvidia_gpus(self) -> list[dict[str, Any]]:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return []
        result = _run(
            [
                exe,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=10,
        )
        if not result or result.returncode != 0:
            return []
        return parse_nvidia_smi(result.stdout)

    def _nvidia_processes(self) -> list[dict[str, Any]]:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return []
        result = _run(
            [exe, "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            timeout=10,
        )
        if not result or result.returncode != 0:
            return []
        return parse_nvidia_processes(result.stdout)[:64]

    # --- Windows ---------------------------------------------------------
    def _collect_windows_inventory(self) -> dict[str, Any]:
        result = _powershell_script(WINDOWS_INVENTORY_SCRIPT, timeout=45)
        data = _parse_json_object(result)
        return {
            "services": self._normalize_windows_services(_as_list(data.get("services")))
            if self._module("services")
            else [],
            "startup": self._normalize_windows_startup(_as_list(data.get("startup")))
            if self._module("startup")
            else [],
            "gpu_processes": self._nvidia_processes() if self._module("gpu") else [],
            "cpu_model": str(data.get("cpu_model") or "")[:255],
            "gpu_names": [
                str(name)[:255] for name in _as_list(data.get("gpu_names")) if str(name).strip()
            ]
            if self._module("gpu")
            else [],
            "ps_error": (result.stderr.strip()[:500] if result and result.returncode != 0 else ""),
        }

    def _windows_snapshot(self) -> dict[str, Any]:
        inventory = self._ensure_service_inventory("Windows")
        result = _powershell_script(WINDOWS_FAST_SCRIPT, timeout=45)
        data = _parse_json_object(result)
        total_mem = int(data.get("mem_total") or 0)
        used_mem = int(data.get("mem_used") or 0)
        nvidia_gpus = self._nvidia_gpus() if self._module("gpu") else []
        gpus = nvidia_gpus or [
            {
                "name": str(name)[:255] or "GPU",
                "util_pct": 0.0,
                "mem_used": 0,
                "mem_total": 0,
                "temp_c": 0.0,
            }
            for name in inventory["gpu_names"]
            if str(name).strip()
        ][:32]
        fast_error = result.stderr.strip()[:500] if result and result.returncode != 0 else ""
        return {
            "hostname": str(data.get("hostname") or socket.gethostname()),
            "os": "windows",
            "os_version": str(data.get("os_version") or platform.platform())[:255],
            "arch": str(data.get("arch") or platform.machine())[:64],
            "ip": str(data.get("ip") or _windows_ip())[:128],
            "mac": str(data.get("mac") or _windows_mac())[:64],
            "cpu_pct": max(0.0, min(100.0, float(data.get("cpu_pct") or 0))),
            "mem_total": total_mem,
            "mem_used": used_mem,
            "mem_pct": (used_mem / total_mem * 100.0) if total_mem else 0.0,
            "uptime_seconds": int(data.get("uptime_seconds") or 0),
            "disks": _windows_disks(_as_list(data.get("disks"))),
            "gpus": gpus,
            "services": inventory["services"],
            "startup": inventory["startup"],
            "packages": inventory.get("packages", []),
            "package_managers": inventory.get("package_managers", []),
            "extra": {
                "cpu_model": inventory["cpu_model"],
                "kernel": platform.release(),
                "gpu_source": "nvidia-smi" if nvidia_gpus else "wmi",
                "gpu_processes": inventory["gpu_processes"],
                "ps_error": inventory["ps_error"] or fast_error,
            },
        }

    def _normalize_windows_startup(self, raw: Iterable[Any]) -> list[dict[str, Any]]:
        startup: list[dict[str, Any]] = []
        seen_startup: set[tuple[str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))[:255]
            command = str(item.get("command", ""))[:2048]
            key = (name.casefold(), command.casefold())
            if key in seen_startup:
                continue
            seen_startup.add(key)
            startup.append(
                {
                    "name": name,
                    "command": command,
                    "source": str(item.get("source", ""))[:128],
                    "enabled": bool(item.get("enabled", True)),
                }
            )
            if len(startup) >= 300:
                break
        return startup

    def _normalize_windows_services(self, raw: Iterable[Any]) -> list[dict[str, Any]]:
        services: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            status = str(item.get("status") or "unknown").strip().casefold()
            if status == "running":
                status = "running"
            elif status == "stopped":
                status = "stopped"
            services.append(
                {
                    "name": name[:255],
                    "display_name": str(item.get("display_name") or "")[:255],
                    "status": status[:64],
                    "startup": str(item.get("startup") or "")[:64],
                    "watched": _base_service(name) in self.watch,
                }
            )

        def priority(service: dict[str, Any]) -> tuple[int, int, str]:
            return (
                0 if service["watched"] else 1,
                0 if service["status"] == "running" else 1,
                service["name"].casefold(),
            )

        return sorted(services, key=priority)[:300]


def _windows_disks(raw: Iterable[Any]) -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        total = int(item.get("total") or 0)
        free = int(item.get("free") or 0)
        used = max(0, total - free)
        disks.append(
            {
                "mount": str(item.get("mount") or "?")[:255],
                "total": total,
                "used": used,
                "free": free,
                "percent": round((used / total * 100.0) if total else 0.0, 1),
                "fs": str(item.get("fs") or "")[:64],
            }
        )
    return disks


def _linux_disks() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return disks
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ").replace("\\011", "\t")
        fstype = parts[2]
        if fstype in PSEUDO_FS or mount in seen:
            continue
        try:
            stats = os.statvfs(mount)
        except OSError:
            continue
        total = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        if total <= 0:
            continue
        used = max(0, total - free)
        seen.add(mount)
        disks.append(
            {
                "mount": mount[:255],
                "total": total,
                "used": used,
                "free": free,
                "percent": round(used / total * 100.0, 1),
                "fs": fstype[:64],
            }
        )
        if len(disks) >= 64:
            break
    return disks


def _linux_os_version() -> str:
    try:
        release = platform.freedesktop_os_release()
        return (release.get("PRETTY_NAME") or platform.platform())[:255]
    except (OSError, AttributeError):
        return platform.platform()[:255]


def _linux_cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()[:255]
    except OSError:
        pass
    return platform.processor()[:255]


def _linux_uptime() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _linux_network() -> tuple[str, str]:
    interface = ""
    try:
        for line in Path("/proc/net/route").read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 8 and fields[1] == "00000000":
                interface = fields[0]
                break
    except OSError:
        pass
    if not interface and os.path.isdir("/sys/class/net"):
        for name in os.listdir("/sys/class/net"):
            if name != "lo":
                interface = name
                break
    mac = ""
    if interface:
        try:
            mac = Path(f"/sys/class/net/{interface}/address").read_text(encoding="utf-8").strip().upper()
        except OSError:
            mac = ""
    if not mac:
        mac = _uuid_mac()
    return _outbound_ip(), mac


def _outbound_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""


def _uuid_mac() -> str:
    value = uuid.getnode()
    return ":".join(f"{(value >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


def _windows_ip() -> str:
    return _outbound_ip()


def _windows_mac() -> str:
    try:
        result = _run(["getmac", "/fo", "csv", "/nh"], timeout=10)
        if result and result.stdout:
            for line in result.stdout.splitlines():
                fields = [field.strip('"') for field in line.split(",")]
                if fields and ":" not in fields[0]:
                    candidate = fields[0].replace("-", ":").upper()
                    if len(candidate.split(":")) == 6:
                        return candidate
    except OSError:
        pass
    return _uuid_mac()


def enrollment_identity(config: AgentConfig) -> dict[str, Any]:
    """Lightweight identity for enrollment, without waiting for a full sample."""
    if platform.system() == "Windows":
        ip, mac = _windows_ip(), _windows_mac()
        os_name = "windows"
        os_version = platform.platform()
    else:
        ip, mac = _linux_network()
        os_name = "linux"
        os_version = _linux_os_version()
    return {
        "hostname": socket.gethostname(),
        "name": config.name or socket.gethostname(),
        "machine_id": machine_identity(),
        "os": os_name,
        "os_version": os_version[:255],
        "arch": platform.machine()[:64],
        "ip": ip[:128],
        "mac": mac[:64],
        "agent_version": __version__,
        "profile": config.profile,
        "tags": config.tags,
        "watch_services": config.watch_services,
    }

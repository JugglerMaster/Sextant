from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_config_path() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(base) / "SextantAgent" / "config.json"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/etc/sextant-agent/config.json")
    return Path.home() / ".config" / "sextant-agent" / "config.json"


@dataclass
class AgentConfig:
    server_url: str
    agent_id: str
    agent_token: str
    name: str = ""
    verify_tls: bool = True
    ca_file: str = ""
    interval: int = 30
    inventory_interval: int = 3600
    service_inventory_interval: int = 0
    package_inventory_interval: int = 86400
    package_full_interval: int = 604800
    profile: str = "workstation"
    timeout: int = 25
    watch_services: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    allow_shell: bool = True
    allow_power: bool = True
    allow_service: bool = True
    allow_packages: bool = True
    allow_self_update: bool = True
    allow_docker: bool = False
    allow_docker_exec: bool = False
    allow_docker_compose: bool = False
    allow_docker_prune: bool = False
    docker_socket: str = "unix:///var/run/docker.sock"
    docker_command: str = "docker"
    docker_timeout: int = 60
    docker_inventory_interval: int = 300
    max_output_bytes: int = 200_000

    def validate(self) -> None:
        if not self.server_url.startswith(("https://", "http://")):
            raise ValueError("server_url must start with http:// or https://")
        if not self.agent_id or not self.agent_token:
            raise ValueError("agent_id and agent_token are required")
        self.interval = max(10, min(int(self.interval), 3600))
        self.inventory_interval = max(60, min(int(self.inventory_interval), 86400))
        if int(self.service_inventory_interval) <= 0:
            self.service_inventory_interval = self.inventory_interval
        self.service_inventory_interval = max(60, min(int(self.service_inventory_interval), 86400))
        self.package_inventory_interval = max(300, min(int(self.package_inventory_interval), 604800))
        self.package_full_interval = max(3600, min(int(self.package_full_interval), 2592000))
        self.timeout = max(5, min(int(self.timeout), 300))
        self.docker_timeout = max(5, min(int(self.docker_timeout), 300))
        self.docker_inventory_interval = max(60, min(int(self.docker_inventory_interval), 86400))
        self.docker_socket = str(self.docker_socket or "").strip()[:512]
        self.docker_command = str(self.docker_command or "docker").strip()[:512] or "docker"
        self.max_output_bytes = max(1024, min(int(self.max_output_bytes), 1_000_000))

    def redacted(self) -> dict[str, Any]:
        data = asdict(self)
        data["agent_token"] = data["agent_token"][:12] + "..." if data["agent_token"] else ""
        return data


def load_config(path: Path | str | None = None) -> AgentConfig:
    config_path = Path(path) if path else default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agent config must be a JSON object")
    known = {field_name for field_name in AgentConfig.__dataclass_fields__}
    config = AgentConfig(**{key: value for key, value in raw.items() if key in known})
    config.validate()
    try:
        mode = config_path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO) and os.name != "nt":
            print(f"warning: {config_path} is accessible by group/other; run: chmod 600 {config_path}", file=sys.stderr)
    except OSError:
        pass
    return config


def save_config(config: AgentConfig, path: Path | str | None = None) -> Path:
    config.validate()
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(config), indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".sextant-agent-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, config_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return config_path

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    mode: Literal["auto", "local", "ad"] = "auto"


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=254)
    role: Literal["admin", "operator", "viewer"] = "operator"
    password: str = Field(min_length=1, max_length=1024)


class UserUpdateIn(BaseModel):
    role: Literal["admin", "operator", "viewer"] | None = None
    active: bool | None = None


class AdminPasswordResetIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=1024)


class AgentUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    tags: list[str] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    watch_services: list[str] | None = Field(default=None, max_length=64)
    profile: str | None = Field(default=None, max_length=64)
    allow_shell: bool | None = None
    allow_power: bool | None = None
    allow_service: bool | None = None
    allow_packages: bool | None = None


class EnrollmentTokenIn(BaseModel):
    label: str = Field(default="", max_length=120)
    expires_minutes: int | None = Field(default=None, ge=5, le=1440)


class StorePackageIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    vendor: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    kind: Literal["app", "agent", "driver"] = "app"
    platforms: list[str] = Field(default_factory=list, max_length=8)


class PackageRuleIn(BaseModel):
    manager: Literal["apt", "dnf", "winget", "choco"]
    package: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9+._:@-]{0,127}$")
    desired_state: Literal["present", "absent", "latest", "version"]
    version_spec: str = Field(default="", max_length=128)


class AgentEnrollIn(BaseModel):
    enrollment_token: str = Field(min_length=16, max_length=512)
    hostname: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=255)
    machine_id: str = Field(default="", max_length=128)
    os: str = Field(min_length=1, max_length=64)
    os_version: str = Field(default="", max_length=255)
    arch: str = Field(default="", max_length=64)
    ip: str = Field(default="", max_length=128)
    mac: str = Field(default="", max_length=64)
    agent_version: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=32)
    watch_services: list[str] = Field(default_factory=list, max_length=64)
    notes: str = Field(default="", max_length=2000)
    profile: str = Field(default="", max_length=64)


class ProfileModules(BaseModel):
    telemetry: bool = True
    services: bool = True
    startup: bool = True
    gpu: bool = True
    packages: bool = True
    shell: bool = True
    power: bool = True
    service_control: bool = True
    package_management: bool = True
    self_update: bool = True


class ProfileCollection(BaseModel):
    service_inventory_seconds: int = Field(default=21600, ge=60, le=86400)
    package_inventory_seconds: int = Field(default=86400, ge=300, le=604800)
    package_full_seconds: int = Field(default=604800, ge=3600, le=2592000)


class ProfileActions(BaseModel):
    allow_shell: bool = True
    allow_power: bool = True
    allow_service: bool = True
    allow_packages: bool = True


class ProfileUpdate(BaseModel):
    allow_self_update: bool = True
    channel: Literal["stable", "beta"] = "stable"


class ProfileSettings(BaseModel):
    heartbeat_seconds: int = Field(default=60, ge=10, le=3600)
    modules: ProfileModules = Field(default_factory=ProfileModules)
    collection: ProfileCollection = Field(default_factory=ProfileCollection)
    actions: ProfileActions = Field(default_factory=ProfileActions)
    update: ProfileUpdate = Field(default_factory=ProfileUpdate)
    max_output_bytes: int = Field(default=200000, ge=1024, le=1000000)


class AgentProfileIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    settings: ProfileSettings = Field(default_factory=ProfileSettings)


class AgentProfilePatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    settings: ProfileSettings | None = None


class ProfileTagIn(BaseModel):
    profile: str = Field(min_length=1, max_length=64)


class RemoteInstallIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(default="", max_length=255)
    profile: str = Field(default="workstation", max_length=64)
    watch_services: list[str] = Field(default_factory=list, max_length=64)


class DiskMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mount: str = Field(default="", max_length=255)
    total: int = Field(default=0, ge=0)
    used: int = Field(default=0, ge=0)
    free: int = Field(default=0, ge=0)
    percent: float = Field(default=0, ge=0, le=100)
    fs: str = Field(default="", max_length=64)


class GpuMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(default="GPU", max_length=255)
    util_pct: float = Field(default=0, ge=0, le=100)
    mem_used: int = Field(default=0, ge=0)
    mem_total: int = Field(default=0, ge=0)
    temp_c: float = Field(default=0, ge=0, le=200)


class ServiceMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)
    status: str = Field(default="unknown", max_length=64)
    startup: str = Field(default="", max_length=64)
    watched: bool = False


class StartupMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(default="", max_length=255)
    command: str = Field(default="", max_length=2048)
    source: str = Field(default="", max_length=128)
    enabled: bool = True


class PackageMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    manager: str = Field(default="", max_length=32)
    package: str = Field(min_length=1, max_length=128)
    version: str = Field(default="", max_length=128)
    arch: str = Field(default="", max_length=32)
    source: str = Field(default="", max_length=128)


class PackageManagerMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=32)
    version: str = Field(default="", max_length=128)


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hostname: str = Field(default="", max_length=255)
    machine_id: str = Field(default="", max_length=128)
    os: str = Field(default="", max_length=64)
    os_version: str = Field(default="", max_length=255)
    arch: str = Field(default="", max_length=64)
    ip: str = Field(default="", max_length=128)
    mac: str = Field(default="", max_length=64)
    agent_version: str = Field(default="", max_length=64)
    cpu_pct: float = Field(default=0, ge=0, le=100)
    mem_pct: float = Field(default=0, ge=0, le=100)
    mem_total: int = Field(default=0, ge=0)
    mem_used: int = Field(default=0, ge=0)
    uptime_seconds: int = Field(default=0, ge=0)
    disks: list[DiskMetric] = Field(default_factory=list, max_length=128)
    gpus: list[GpuMetric] = Field(default_factory=list, max_length=32)
    services: list[ServiceMetric] = Field(default_factory=list, max_length=500)
    startup: list[StartupMetric] = Field(default_factory=list, max_length=500)
    packages: list[PackageMetric] = Field(default_factory=list, max_length=5000)
    package_managers: list[PackageManagerMetric] = Field(default_factory=list, max_length=10)
    package_inventory_hash: str = Field(default="", max_length=64)
    package_inventory_full: bool = False
    package_inventory_collected_at: str = Field(default="", max_length=64)
    package_inventory_mode: Literal["full", "delta"] = "full"
    package_inventory_base_revision: int = Field(default=0, ge=0)
    package_inventory_revision: int = Field(default=0, ge=0)
    package_inventory_deletes: list[list[str]] = Field(default_factory=list, max_length=5000)
    service_inventory_collected_at: str = Field(default="", max_length=64)
    extra: dict[str, Any] = Field(default_factory=dict)


class CommandIn(BaseModel):
    kind: Literal["shell", "power", "service", "refresh", "package"]
    args: dict[str, Any] = Field(default_factory=dict)


class AgentWakeIn(BaseModel):
    broadcast: str = Field(default="255.255.255.255", max_length=64)
    port: int = Field(default=9, ge=1, le=65535)
    packets: int = Field(default=3, ge=1, le=10)


class AlertSnoozeIn(BaseModel):
    minutes: int = Field(ge=5, le=10080)


class WakeTargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mac: str = Field(min_length=17, max_length=32)
    ip: str = Field(default="", max_length=128)
    broadcast: str = Field(default="255.255.255.255", max_length=64)
    port: int = Field(default=9, ge=1, le=65535)


class WakeTargetWakeIn(BaseModel):
    broadcast: str | None = Field(default=None, max_length=64)
    port: int | None = Field(default=None, ge=1, le=65535)
    packets: int = Field(default=3, ge=1, le=10)


class CommandResultIn(BaseModel):
    status: Literal["succeeded", "failed"]
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=1_000_000)
    stderr: str = Field(default="", max_length=1_000_000)
    error: str = Field(default="", max_length=4000)
    started_at: str = Field(default="", max_length=64)
    completed_at: str = Field(default="", max_length=64)


class CommandOutputIn(BaseModel):
    stream: Literal["stdout", "stderr"]
    seq: int = Field(ge=1, le=2_000_000_000)
    text: str = Field(min_length=1, max_length=1_000_000)

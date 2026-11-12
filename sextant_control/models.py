from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from .docker_rules import (
    CONTAINER_ID_PATTERN,
    CPUS_PATTERN,
    IMAGE_ID_PATTERN,
    MEMORY_PATTERN,
    NETWORK_ID_PATTERN,
    RENAME_PATTERN,
    RESTART_POLICY_PATTERN,
    REVISION_HASH_PATTERN,
    STACK_SLUG_PATTERN,
    USER_PATTERN,
    VOLUME_NAME_PATTERN,
    WORKDIR_PATTERN,
)

DOCKER_REASONS = Literal[
    "",
    "disabled",
    "unsupported_os",
    "not_installed",
    "socket_missing",
    "permission_denied",
    "daemon_unreachable",
    "compose_missing",
    "probe_error",
]


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
    allow_docker: bool | None = None
    allow_docker_exec: bool | None = None
    allow_docker_compose: bool | None = None
    allow_docker_prune: bool | None = None
    allow_discovery: bool | None = None


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
    docker: bool = False
    discovery: bool = False


class ProfileCollection(BaseModel):
    service_inventory_seconds: int = Field(default=21600, ge=60, le=86400)
    package_inventory_seconds: int = Field(default=86400, ge=300, le=604800)
    package_full_seconds: int = Field(default=604800, ge=3600, le=2592000)
    docker_inventory_seconds: int = Field(default=300, ge=60, le=86400)


class ProfileActions(BaseModel):
    allow_shell: bool = True
    allow_power: bool = True
    allow_service: bool = True
    allow_packages: bool = True
    allow_docker: bool = False
    allow_docker_exec: bool = False
    allow_docker_compose: bool = False
    allow_docker_prune: bool = False


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


class DockerCountsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    containers: int = Field(default=0, ge=0, le=1_000_000)
    running: int = Field(default=0, ge=0, le=1_000_000)
    paused: int = Field(default=0, ge=0, le=1_000_000)
    stopped: int = Field(default=0, ge=0, le=1_000_000)
    unhealthy: int = Field(default=0, ge=0, le=1_000_000)
    images: int = Field(default=0, ge=0, le=1_000_000)
    volumes: int = Field(default=0, ge=0, le=1_000_000)
    networks: int = Field(default=0, ge=0, le=1_000_000)


class DockerSummaryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    reason: DOCKER_REASONS = ""
    engine_version: str = Field(default="", max_length=64)
    api_version: str = Field(default="", max_length=64)
    compose_version: str = Field(default="", max_length=64)
    compose_available: bool = False
    rootless: bool = False
    storage_driver: str = Field(default="", max_length=64)
    cgroup_driver: str = Field(default="", max_length=64)
    docker_root_dir: str = Field(default="", max_length=255)
    counts: DockerCountsIn = Field(default_factory=DockerCountsIn)
    inventory_hash: str = Field(default="", max_length=64)
    inventory_revision: int = Field(default=0, ge=0, le=2_000_000_000)
    collected_at: str = Field(default="", max_length=64)


class DockerContainerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=255)
    image: str = Field(default="", max_length=512)
    image_id: str = Field(default="", max_length=128)
    command: str = Field(default="", max_length=2048)
    state: str = Field(default="", max_length=32)
    status: str = Field(default="", max_length=255)
    health: str = Field(default="", max_length=32)
    created_at: str = Field(default="", max_length=64)
    started_at: str = Field(default="", max_length=64)
    finished_at: str = Field(default="", max_length=64)
    exit_code: int | None = Field(default=None, ge=-1, le=1_000_000)
    restart_policy: str = Field(default="", max_length=64)
    memory_limit: int = Field(default=0, ge=0, le=10**15)
    nano_cpus: int = Field(default=0, ge=0, le=10**15)
    pids_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    ports: list[str] = Field(default_factory=list, max_length=128)
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    mounts: list[str] = Field(default_factory=list, max_length=128)
    networks: list[str] = Field(default_factory=list, max_length=64)
    compose_project: str = Field(default="", max_length=255)
    compose_service: str = Field(default="", max_length=255)
    compose_config_hash: str = Field(default="", max_length=128)


class DockerImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    repo_tags: list[str] = Field(default_factory=list, max_length=64)
    repo_digests: list[str] = Field(default_factory=list, max_length=64)
    size: int = Field(default=0, ge=0, le=10**15)
    shared_size: int = Field(default=0, ge=0, le=10**15)
    containers: int = Field(default=0, ge=0, le=1_000_000)
    dangling: bool = False
    created_at: str = Field(default="", max_length=64)


class DockerVolumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    driver: str = Field(default="", max_length=64)
    scope: str = Field(default="", max_length=32)
    mountpoint: str = Field(default="", max_length=1024)
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    in_use: bool = False


class DockerNetworkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=255)
    driver: str = Field(default="", max_length=64)
    scope: str = Field(default="", max_length=32)
    internal: bool = False
    attachable: bool = False
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    container_count: int = Field(default=0, ge=0, le=1_000_000)


class DockerContainerLifecycleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "container.start",
        "container.stop",
        "container.restart",
        "container.pause",
        "container.unpause",
        "container.kill",
    ]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)


class DockerContainerRenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.rename"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)
    new_name: str = Field(pattern=RENAME_PATTERN)


class DockerContainerRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.remove"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)
    force: bool = False
    remove_volumes: bool = False


class DockerContainerUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.update"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)
    restart_policy: str = Field(default="", max_length=64)
    memory: str = Field(default="", max_length=32)
    cpus: str = Field(default="", max_length=16)
    pids_limit: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("restart_policy")
    @classmethod
    def _restart_policy(cls, value: str) -> str:
        if value and not re.match(RESTART_POLICY_PATTERN, value):
            raise ValueError("invalid restart policy")
        return value

    @field_validator("memory")
    @classmethod
    def _memory(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.match(MEMORY_PATTERN, value):
            raise ValueError("memory must be a Docker memory value such as 512m or 1g")
        return value

    @field_validator("cpus")
    @classmethod
    def _cpus(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(CPUS_PATTERN, value):
            raise ValueError("cpus must be a positive number such as 0.5 or 2")
        return value

    @model_validator(mode="after")
    def _at_least_one(self) -> "DockerContainerUpdateIn":
        if not self.restart_policy and not self.memory and not self.cpus and self.pids_limit is None:
            raise ValueError("container.update requires at least one limit")
        return self


class DockerContainerLogsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.logs"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)
    tail: Literal[100, 500, 1000] = 500
    timestamps: bool = False


class DockerContainerInspectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.inspect"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)


class DockerImageRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["image.remove"]
    image_id: str = Field(pattern=IMAGE_ID_PATTERN)


class DockerVolumeRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["volume.remove"]
    volume_name: str = Field(pattern=VOLUME_NAME_PATTERN)


class DockerNetworkRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["network.remove"]
    network_id: str = Field(pattern=NETWORK_ID_PATTERN)


class DockerContainerCloneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["container.clone"]
    container_id: str = Field(pattern=CONTAINER_ID_PATTERN)
    new_name: str = Field(pattern=RENAME_PATTERN)
    image_id: str = Field(pattern=IMAGE_ID_PATTERN)
    command: list[str] = Field(default_factory=list, max_length=64)
    restart_policy: str = Field(default="", max_length=64)
    memory: str = Field(default="", max_length=32)
    cpus: str = Field(default="", max_length=16)
    pids_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    network_ids: list[str] = Field(default_factory=list, max_length=16)
    acknowledge_drift: bool = False

    @field_validator("command")
    @classmethod
    def _clone_command(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item or len(item) > 2048 or "\x00" in item:
                raise ValueError("invalid command argument")
        return value

    @field_validator("restart_policy")
    @classmethod
    def _clone_restart(cls, value: str) -> str:
        if value and not re.match(RESTART_POLICY_PATTERN, value):
            raise ValueError("invalid restart policy")
        return value

    @field_validator("memory")
    @classmethod
    def _clone_memory(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.match(MEMORY_PATTERN, value):
            raise ValueError("invalid memory limit")
        return value

    @field_validator("cpus")
    @classmethod
    def _clone_cpus(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(CPUS_PATTERN, value):
            raise ValueError("invalid CPU limit")
        return value

    @field_validator("network_ids")
    @classmethod
    def _clone_networks(cls, value: list[str]) -> list[str]:
        for item in value:
            if not re.match(NETWORK_ID_PATTERN, item):
                raise ValueError("invalid network ID")
        return list(dict.fromkeys(value))


DockerActionPayload = Annotated[
    Union[
        DockerContainerLifecycleIn,
        DockerContainerRenameIn,
        DockerContainerRemoveIn,
        DockerContainerUpdateIn,
        DockerContainerLogsIn,
        DockerContainerInspectIn,
        DockerContainerCloneIn,
        DockerImageRemoveIn,
        DockerVolumeRemoveIn,
        DockerNetworkRemoveIn,
    ],
    Field(discriminator="action"),
]


class DockerActionIn(RootModel[DockerActionPayload]):
    root: DockerActionPayload


class DockerExecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell: Literal["sh", "bash", "ash"] = "sh"
    user: str = Field(default="", max_length=32)
    workdir: str = Field(default="", max_length=256)
    command: str = Field(min_length=1, max_length=8000)
    timeout: int = Field(default=60, ge=5, le=300)
    tty: bool = False

    @field_validator("user")
    @classmethod
    def _user(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(USER_PATTERN, value):
            raise ValueError("user must match [A-Za-z0-9_.-]{1,32}")
        return value

    @field_validator("workdir")
    @classmethod
    def _workdir(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(WORKDIR_PATTERN, value):
            raise ValueError("workdir must be an absolute path")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("command must not contain a NUL byte")
        return value


class DockerCleanupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "stopped_containers",
        "dangling_images",
        "unused_images",
        "unused_volumes",
        "unused_networks",
        "build_cache",
    ]
    include_compose: bool = False


class DockerStackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=STACK_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    files: dict[str, str] = Field(min_length=1, max_length=16)
    message: str = Field(default="", max_length=2000)

    @field_validator("files")
    @classmethod
    def _files(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("at most 16 files are allowed")
        for name, content in value.items():
            if not name or len(name) > 128:
                raise ValueError("invalid file name")
            if len(content.encode("utf-8")) > 1_000_000:
                raise ValueError(f"{name} is too large")
        return value


class DockerStackPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    files: dict[str, str] | None = Field(default=None, min_length=1, max_length=16)
    message: str = Field(default="", max_length=2000)


class DockerDeployIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(default="", max_length=64)
    agent_ids: list[str] = Field(default_factory=list, max_length=20)
    revision_hash: str = Field(default="", max_length=64)
    project_name: str = Field(default="", max_length=63, pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,62})?$")
    action: Literal["up", "down", "restart", "pull"] = "up"
    pull_before_up: bool = False
    remove_volumes: bool = False

    @field_validator("agent_id")
    @classmethod
    def _agent_id(cls, value: str) -> str:
        if value and not re.match(r"^agt_[A-Za-z0-9]+$", value):
            raise ValueError("invalid agent ID")
        return value

    @field_validator("agent_ids")
    @classmethod
    def _agent_ids(cls, value: list[str]) -> list[str]:
        if len(value) > 20:
            raise ValueError("at most 20 Docker hosts can be selected")
        for item in value:
            if not re.match(r"^agt_[A-Za-z0-9]+$", item):
                raise ValueError("invalid agent ID")
        return list(dict.fromkeys(value))

    @field_validator("revision_hash")
    @classmethod
    def _revision_hash(cls, value: str) -> str:
        if value and not re.match(REVISION_HASH_PATTERN, value):
            raise ValueError("invalid revision hash")
        return value

    @model_validator(mode="after")
    def _targets(self) -> "DockerDeployIn":
        if not self.agent_id and not self.agent_ids:
            raise ValueError("agent_id or agent_ids is required")
        if self.agent_id and self.agent_ids:
            raise ValueError("provide either agent_id or agent_ids, not both")
        return self


class DockerConsoleOpenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell: Literal["sh", "bash", "ash"] = "sh"
    user: str = Field(default="", max_length=32)
    workdir: str = Field(default="", max_length=256)
    rows: int = Field(default=32, ge=10, le=200)
    cols: int = Field(default=100, ge=20, le=300)

    @field_validator("user")
    @classmethod
    def _console_user(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(USER_PATTERN, value):
            raise ValueError("user must match [A-Za-z0-9_.-]{1,32}")
        return value

    @field_validator("workdir")
    @classmethod
    def _console_workdir(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(WORKDIR_PATTERN, value):
            raise ValueError("workdir must be an absolute path")
        return value


class DockerConsoleInputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: str = Field(min_length=1, max_length=262144)


class DockerConsoleResizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: int = Field(ge=10, le=200)
    cols: int = Field(ge=20, le=300)


class DockerConsoleOutputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: str = Field(min_length=1, max_length=262144)


class DockerConsoleClosedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exit_code: int | None = Field(default=None, ge=-1, le=1_000_000)
    error: str = Field(default="", max_length=4000)


class DockerSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compose_dir: str = Field(min_length=1, max_length=1024)


class DockerInventoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: DockerSummaryIn
    containers: list[DockerContainerIn] = Field(default_factory=list, max_length=1000)
    images: list[DockerImageIn] = Field(default_factory=list, max_length=2000)
    volumes: list[DockerVolumeIn] = Field(default_factory=list, max_length=1000)
    networks: list[DockerNetworkIn] = Field(default_factory=list, max_length=500)
    collected_at: str = Field(default="", max_length=64)
    hash: str = Field(default="", max_length=64)
    revision: int = Field(default=0, ge=0, le=2_000_000_000)


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
    docker: DockerSummaryIn | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CommandIn(BaseModel):
    kind: Literal["shell", "power", "service", "refresh", "package", "docker", "discovery"]
    args: dict[str, Any] = Field(default_factory=dict)


class BulkActionIn(BaseModel):
    action: Literal[
        "refresh",
        "refresh_telemetry",
        "wake",
        "power",
        "reboot",
        "shutdown",
        "service",
        "service_start",
        "service_stop",
        "service_restart",
        "profile",
        "profile_apply",
    ]
    agent_ids: list[str] = Field(min_length=1, max_length=200)
    args: dict[str, Any] = Field(default_factory=dict)


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)


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


class DiscoveryDevicePatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    state: Literal["new", "known", "ignored", "promoted"] | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [str(tag).strip() for tag in value if str(tag).strip()]
        if len(cleaned) > 32:
            raise ValueError("at most 32 tags are allowed")
        for tag in cleaned:
            if len(tag) > 64:
                raise ValueError("tags must be 64 characters or fewer")
        return list(dict.fromkeys(cleaned))


class DiscoveryScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: list[str] = Field(default_factory=list, max_length=16)
    methods: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item or len(item) > 64:
                raise ValueError("each scope must be a CIDR of 64 characters or fewer")
        return list(dict.fromkeys(value))

    @field_validator("methods")
    @classmethod
    def _methods(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item).strip().lower() for item in value if str(item).strip()))


class DiscoverySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledged: bool
    acknowledgement: str = Field(default="", max_length=500)


class DiscoveryReportDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip: str = Field(min_length=1, max_length=64)
    mac: str = Field(default="", max_length=64)
    hostname: str = Field(default="", max_length=255)
    vendor: str = Field(default="", max_length=255)
    open_ports: list[int] = Field(default_factory=list, max_length=64)
    services: dict[str, Any] = Field(default_factory=dict)
    mdns: dict[str, Any] = Field(default_factory=dict)
    ssdp: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ip")
    @classmethod
    def _ip(cls, value: str) -> str:
        import ipaddress

        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            raise ValueError("invalid IP address") from None

    @field_validator("open_ports")
    @classmethod
    def _ports(cls, value: list[int]) -> list[int]:
        for port in value:
            if not 1 <= int(port) <= 65535:
                raise ValueError("TCP ports must be between 1 and 65535")
        return list(dict.fromkeys(int(port) for port in value))

    @field_validator("services", "mdns", "ssdp", "evidence")
    @classmethod
    def _bounded_evidence(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("evidence objects are limited to 64 keys")
        if len(json.dumps(value, separators=(",", ":"), default=str)) > 8192:
            raise ValueError("evidence object exceeds 8192 bytes")
        return value


class DiscoveryReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_id: str = Field(pattern=r"^dsc_[A-Za-z0-9]+$")
    source: str = Field(default="", max_length=128)
    collected_at: str = Field(default="", max_length=64)
    segment: str = Field(default="", max_length=128)
    devices: list[DiscoveryReportDeviceIn] = Field(default_factory=list, max_length=512)
    truncated: bool = False
    error: str = Field(default="", max_length=4000)


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

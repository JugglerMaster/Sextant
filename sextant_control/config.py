from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_STATIC_DIR = PACKAGE_ROOT / "static"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, parsed)


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class LdapSettings:
    server: str
    base_dn: str
    bind_dn: str
    bind_password: str
    user_filter: str = "(sAMAccountName={username})"
    admin_groups: tuple[str, ...] = ()
    operator_groups: tuple[str, ...] = ()
    viewer_groups: tuple[str, ...] = ()
    default_role: str = "viewer"
    use_ssl: bool = False
    ca_cert: str = ""
    port: int = 389
    timeout: int = 8
    domain: str = ""
    username_attr: str = "sAMAccountName"
    display_name_attr: str = "displayName"
    email_attr: str = "mail"
    group_attr: str = "memberOf"

    @property
    def effective_port(self) -> int:
        return 636 if self.use_ssl and self.port == 389 else self.port


DISCOVERY_METHODS = ("neigh", "dns", "oui", "ssdp", "tcp")
DISCOVERY_DEFAULT_PORTS = (
    22,
    53,
    80,
    139,
    443,
    445,
    631,
    1883,
    3389,
    5900,
    5985,
    5986,
    8006,
    8080,
    8443,
    9100,
    32400,
)


def _int_range(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _ports(value: str | None) -> tuple[int, ...]:
    if value is None:
        return DISCOVERY_DEFAULT_PORTS
    ports: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            port = int(part)
        except ValueError:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return tuple(ports)


@dataclass(frozen=True)
class DiscoverySettings:
    enabled: bool = False
    cidrs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    methods: tuple[str, ...] = ("neigh", "dns", "oui", "ssdp")
    probe_ports: tuple[int, ...] = DISCOVERY_DEFAULT_PORTS
    active_probes: bool = False
    interval_hours: int = 0
    max_hosts: int = 1024
    concurrency: int = 32
    timeout_ms: int = 600
    retention_days: int = 90
    agent_relay: bool = False
    export_limit: int = 10000

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "DiscoverySettings":
        methods = tuple(
            item
            for item in (part.strip().lower() for part in _split_csv(env.get("SEXTANT_DISCOVERY_METHODS")))
            if item in DISCOVERY_METHODS
        )
        if not methods:
            methods = ("neigh", "dns", "oui", "ssdp")
        return cls(
            enabled=_bool(env.get("SEXTANT_DISCOVERY_ENABLED"), False),
            cidrs=_split_csv(env.get("SEXTANT_DISCOVERY_CIDRS")),
            exclude=_split_csv(env.get("SEXTANT_DISCOVERY_EXCLUDE")),
            methods=methods,
            probe_ports=_ports(env.get("SEXTANT_DISCOVERY_PROBE_PORTS")),
            active_probes=_bool(env.get("SEXTANT_DISCOVERY_ACTIVE_PROBES"), False),
            interval_hours=_int_range(
                env.get("SEXTANT_DISCOVERY_INTERVAL_HOURS"), 0, 0, 168
            ),
            max_hosts=_int_range(
                env.get("SEXTANT_DISCOVERY_MAX_HOSTS"), 1024, 1, 65536
            ),
            concurrency=_int_range(
                env.get("SEXTANT_DISCOVERY_CONCURRENCY"), 32, 1, 128
            ),
            timeout_ms=_int_range(
                env.get("SEXTANT_DISCOVERY_TIMEOUT_MS"), 600, 100, 5000
            ),
            retention_days=_int_range(
                env.get("SEXTANT_DISCOVERY_RETENTION_DAYS"), 90, 1, 3650
            ),
            agent_relay=_bool(env.get("SEXTANT_DISCOVERY_AGENT_RELAY"), False),
            export_limit=_int_range(
                env.get("SEXTANT_DISCOVERY_EXPORT_LIMIT"), 10000, 1, 100000
            ),
        )


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    secret_key: bytes
    session_hours: int = 12
    cookie_name: str = "sextant_session"
    cookie_secure: bool = False
    offline_after_seconds: int = 90
    alert_ack_ttl_hours: int = 168
    notify_enabled: bool = True
    notify_interval_seconds: int = 20
    notification_retention_days: int = 90
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_thread_id: str = ""
    telegram_events: tuple[str, ...] = ("offline", "disk", "memory", "gpu", "service")
    telegram_min_severity: str = "warning"
    telegram_recovery: bool = True
    telegram_api_base: str = "https://api.telegram.org"
    telegram_timeout_seconds: int = 10
    enrollment_ttl_minutes: int = 30
    max_body_bytes: int = 2_000_000
    command_output_limit: int = 200_000
    max_queued_commands_per_agent: int = 50
    offline_command_policy: str = "queue"
    dispatched_grace_seconds: int = 120
    queued_command_ttl_hours: int = 24
    session_cleanup_days: int = 7
    command_retention_days: int = 90
    audit_retention_days: int = 365
    telemetry_retention_days: int = 7
    telemetry_history: bool = True
    trust_proxy: bool = False
    admin_user: str = "admin"
    admin_password: str | None = None
    ldap: LdapSettings | None = None
    public_url: str = ""
    allowed_hosts: tuple[str, ...] = ()
    store_enabled: bool = True
    store_max_blob_mb: int = 512
    store_max_total_gb: int = 20
    update_feed: str = ""
    update_check_hours: int = 24
    update_release_root: str = "/opt/sextant/releases"
    update_current_link: str = "/opt/sextant/current"
    update_restart_trigger: str = "/var/lib/sextant/restart-request"
    update_download_max_mb: int = 256
    graceful_shutdown_seconds: int = 10
    docker_enabled: bool = True
    docker_inventory_max_bytes: int = 2_000_000
    docker_inventory_stale_seconds: int = 900
    docker_compose_dir: str = ""
    docker_compose_dir_from_env: bool = False
    docker_max_file_bytes: int = 262144
    docker_max_files: int = 16
    docker_max_total_bytes: int = 1_048_576
    docker_max_bundle_bytes: int = 4_194_304
    docker_deployment_retention_days: int = 90
    docker_console_poll_seconds: int = 25
    docker_console_idle_seconds: int = 900
    docker_console_max_sessions: int = 2
    docker_console_max_input_bytes: int = 65536
    docker_console_max_output_bytes: int = 1_048_576
    ssh_key_path: str = ""
    ssh_key_source: str = "managed"
    ssh_system_user: str = ""
    ssh_config_path: str = ""
    ssh_install_enabled: bool = True
    windows_install_enabled: bool = True
    windows_install_ca_file: str = ""
    windows_install_timeout_seconds: int = 600
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    extra: dict = field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "sextant.sqlite3"

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def bootstrap_file(self) -> Path:
        return self.data_dir / "bootstrap-admin.txt"

    @property
    def docker_compose_root(self) -> Path:
        if self.docker_compose_dir:
            return Path(self.docker_compose_dir).expanduser().resolve()
        return (self.data_dir / "docker" / "compose").resolve()

    @property
    def update_release_path(self) -> Path:
        return Path(self.update_release_root).expanduser().resolve()

    @property
    def update_current_path(self) -> Path:
        return Path(self.update_current_link).expanduser()

    @property
    def update_restart_path(self) -> Path:
        return Path(self.update_restart_trigger).expanduser()

    @property
    def update_state_path(self) -> Path:
        return self.data_dir / "update-state.json"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        data_dir = Path(env.get("SEXTANT_DATA_DIR", "data")).expanduser().resolve()
        static_dir = Path(env.get("SEXTANT_STATIC_DIR", str(DEFAULT_STATIC_DIR))).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            data_dir.chmod(0o700)
        except OSError:
            pass

        secret_key = _load_secret(data_dir, env.get("SEXTANT_SECRET_KEY"))
        ldap = _ldap_from_env(env)
        offline_policy = env.get("SEXTANT_OFFLINE_COMMAND_POLICY", "queue").strip().lower()
        if offline_policy not in {"queue", "reject"}:
            offline_policy = "queue"
        public_url = env.get("SEXTANT_PUBLIC_URL", "").rstrip("/")
        allowed_hosts = _split_csv(env.get("SEXTANT_ALLOWED_HOSTS"))
        if not allowed_hosts and public_url:
            host = urlsplit(public_url).hostname
            if host:
                allowed_hosts = (host,)
        notify_events = _split_csv(
            env.get(
                "SEXTANT_TELEGRAM_EVENTS",
                "offline,disk,memory,gpu,service",
            )
        )
        notify_events = tuple(
            event
            for event in (item.strip().lower() for item in notify_events)
            if event in {"offline", "disk", "memory", "gpu", "service"}
        )
        min_severity = env.get("SEXTANT_TELEGRAM_MIN_SEVERITY", "warning").strip().lower()
        if min_severity not in {"info", "warning", "critical"}:
            min_severity = "warning"
        telegram_api_base = env.get("SEXTANT_TELEGRAM_API_BASE", "https://api.telegram.org").strip().rstrip("/")
        if not telegram_api_base.startswith(("https://", "http://")):
            telegram_api_base = "https://api.telegram.org"
        api_parts = urlsplit(telegram_api_base)
        if api_parts.scheme != "https" and api_parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            # Never send a bot token over cleartext to a remote host.
            telegram_api_base = "https://api.telegram.org"

        return cls(
            data_dir=data_dir,
            static_dir=static_dir,
            secret_key=secret_key,
            session_hours=min(8784, _int(env.get("SEXTANT_SESSION_HOURS"), 12)),
            cookie_name=env.get("SEXTANT_COOKIE_NAME", "sextant_session").strip() or "sextant_session",
            cookie_secure=_bool(env.get("SEXTANT_COOKIE_SECURE"), False),
            offline_after_seconds=_int(env.get("SEXTANT_OFFLINE_AFTER_SECONDS"), 90, 15),
            alert_ack_ttl_hours=min(8760, _int(env.get("SEXTANT_ALERT_ACK_TTL_HOURS"), 168, 1)),
            notify_enabled=_bool(env.get("SEXTANT_NOTIFY_ENABLED"), True),
            notify_interval_seconds=min(3600, _int(env.get("SEXTANT_NOTIFY_INTERVAL_SECONDS"), 20, 5)),
            notification_retention_days=min(3650, _int(env.get("SEXTANT_NOTIFICATION_RETENTION_DAYS"), 90, 1)),
            telegram_enabled=_bool(env.get("SEXTANT_TELEGRAM_ENABLED"), True),
            telegram_bot_token=env.get("SEXTANT_TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=env.get("SEXTANT_TELEGRAM_CHAT_ID", "").strip(),
            telegram_thread_id=env.get("SEXTANT_TELEGRAM_THREAD_ID", "").strip(),
            telegram_events=notify_events,
            telegram_min_severity=min_severity,
            telegram_recovery=_bool(env.get("SEXTANT_TELEGRAM_RECOVERY"), True),
            telegram_api_base=telegram_api_base,
            telegram_timeout_seconds=min(60, _int(env.get("SEXTANT_TELEGRAM_TIMEOUT_SECONDS"), 10, 2)),
            enrollment_ttl_minutes=_int(env.get("SEXTANT_ENROLLMENT_TTL_MINUTES"), 30),
            max_body_bytes=_int(env.get("SEXTANT_MAX_BODY_BYTES"), 2_000_000, 1024),
            command_output_limit=_int(env.get("SEXTANT_COMMAND_OUTPUT_LIMIT"), 200_000, 1024),
            max_queued_commands_per_agent=_int(env.get("SEXTANT_MAX_QUEUED_COMMANDS_PER_AGENT"), 50, 1),
            offline_command_policy=offline_policy,
            dispatched_grace_seconds=_int(env.get("SEXTANT_DISPATCHED_GRACE_SECONDS"), 120, 10),
            queued_command_ttl_hours=_int(env.get("SEXTANT_QUEUED_COMMAND_TTL_HOURS"), 24, 1),
            session_cleanup_days=_int(env.get("SEXTANT_SESSION_CLEANUP_DAYS"), 7, 1),
            command_retention_days=_int(env.get("SEXTANT_COMMAND_RETENTION_DAYS"), 90, 1),
            audit_retention_days=_int(env.get("SEXTANT_AUDIT_RETENTION_DAYS"), 365, 1),
            telemetry_retention_days=min(365, _int(env.get("SEXTANT_TELEMETRY_RETENTION_DAYS"), 7, 1)),
            telemetry_history=_bool(env.get("SEXTANT_TELEMETRY_HISTORY"), True),
            trust_proxy=_bool(env.get("SEXTANT_TRUST_PROXY"), False),
            admin_user=env.get("SEXTANT_ADMIN_USER", "admin").strip() or "admin",
            admin_password=env.get("SEXTANT_ADMIN_PASSWORD") or None,
            ldap=ldap,
            public_url=public_url,
            allowed_hosts=allowed_hosts,
            store_enabled=_bool(env.get("SEXTANT_STORE_ENABLED"), True),
            store_max_blob_mb=_int(env.get("SEXTANT_STORE_MAX_BLOB_MB"), 512, 1),
            store_max_total_gb=_int(env.get("SEXTANT_STORE_MAX_TOTAL_GB"), 20, 1),
            update_feed=env.get("SEXTANT_UPDATE_FEED", "").strip(),
            update_check_hours=_int_range(
                env.get("SEXTANT_UPDATE_CHECK_HOURS"), 24, 1, 168
            ),
            update_release_root=(
                env.get("SEXTANT_UPDATE_RELEASE_ROOT")
                or env.get("SEXTANT_RELEASE_ROOT")
                or "/opt/sextant/releases"
            ).strip()
            or "/opt/sextant/releases",
            update_current_link=(
                env.get("SEXTANT_UPDATE_CURRENT_LINK")
                or env.get("SEXTANT_CURRENT_LINK")
                or "/opt/sextant/current"
            ).strip()
            or "/opt/sextant/current",
            update_restart_trigger=(
                env.get("SEXTANT_UPDATE_RESTART_TRIGGER")
                or env.get("SEXTANT_RESTART_TRIGGER")
                or "/var/lib/sextant/restart-request"
            ).strip()
            or "/var/lib/sextant/restart-request",
            update_download_max_mb=_int_range(
                env.get("SEXTANT_UPDATE_DOWNLOAD_MAX_MB"), 256, 1, 4096
            ),
            graceful_shutdown_seconds=_int_range(
                env.get("SEXTANT_GRACEFUL_SHUTDOWN_SECONDS")
                or env.get("SEXTANT_SHUTDOWN_TIMEOUT"),
                10,
                1,
                300,
            ),
            docker_enabled=_bool(env.get("SEXTANT_DOCKER_ENABLED"), True),
            docker_inventory_max_bytes=max(
                1024, _int(env.get("SEXTANT_DOCKER_INVENTORY_MAX_BYTES"), 2_000_000, 1024)
            ),
            docker_inventory_stale_seconds=max(
                60, _int(env.get("SEXTANT_DOCKER_INVENTORY_STALE_SECONDS"), 900, 60)
            ),
            docker_compose_dir=env.get("SEXTANT_DOCKER_COMPOSE_DIR", "").strip(),
            docker_compose_dir_from_env=bool(env.get("SEXTANT_DOCKER_COMPOSE_DIR", "").strip()),
            docker_max_file_bytes=max(
                1024, _int(env.get("SEXTANT_DOCKER_MAX_FILE_BYTES"), 262144, 1024)
            ),
            docker_max_files=max(1, min(64, _int(env.get("SEXTANT_DOCKER_MAX_FILES"), 16, 1))),
            docker_max_total_bytes=max(
                1024, _int(env.get("SEXTANT_DOCKER_MAX_TOTAL_BYTES"), 1_048_576, 1024)
            ),
            docker_max_bundle_bytes=max(
                1024, _int(env.get("SEXTANT_DOCKER_MAX_BUNDLE_BYTES"), 4_194_304, 1024)
            ),
            docker_deployment_retention_days=max(
                1, _int(env.get("SEXTANT_DOCKER_DEPLOYMENT_RETENTION_DAYS"), 90, 1)
            ),
            docker_console_poll_seconds=min(
                60, _int(env.get("SEXTANT_DOCKER_CONSOLE_POLL_SECONDS"), 25, 5)
            ),
            docker_console_idle_seconds=min(
                3600, _int(env.get("SEXTANT_DOCKER_CONSOLE_IDLE_SECONDS"), 900, 60)
            ),
            docker_console_max_sessions=max(
                1, min(8, _int(env.get("SEXTANT_DOCKER_CONSOLE_MAX_SESSIONS"), 2, 1))
            ),
            docker_console_max_input_bytes=max(
                1024, _int(env.get("SEXTANT_DOCKER_CONSOLE_MAX_INPUT_BYTES"), 65536, 1024)
            ),
            docker_console_max_output_bytes=max(
                4096, _int(env.get("SEXTANT_DOCKER_CONSOLE_MAX_OUTPUT_BYTES"), 1_048_576, 4096)
            ),
            ssh_key_path=env.get("SEXTANT_SSH_KEY_PATH", "").strip() or str(data_dir / "ssh" / "install_key"),
            ssh_key_source=(env.get("SEXTANT_SSH_KEY_SOURCE", "managed").strip().lower() or "managed"),
            ssh_system_user=env.get("SEXTANT_SSH_SYSTEM_USER", "").strip(),
            ssh_config_path=env.get("SEXTANT_SSH_CONFIG_PATH", "").strip(),
            ssh_install_enabled=_bool(env.get("SEXTANT_SSH_INSTALL_ENABLED"), True),
            windows_install_enabled=_bool(env.get("SEXTANT_WINDOWS_INSTALL_ENABLED"), True),
            windows_install_ca_file=env.get("SEXTANT_WINDOWS_INSTALL_CA_FILE", "").strip(),
            windows_install_timeout_seconds=_int_range(
                env.get("SEXTANT_WINDOWS_INSTALL_TIMEOUT_SECONDS"), 600, 60, 3600
            ),
            discovery=DiscoverySettings.from_env(env),
        )


def _load_secret(data_dir: Path, configured: str | None) -> bytes:
    if configured:
        raw = configured.encode("utf-8")
        if len(raw) < 32:
            raise ValueError(
                "SEXTANT_SECRET_KEY must be at least 32 bytes of high-entropy data. "
                "Use a password manager or `python -c 'import secrets; print(secrets.token_hex(32))'`."
            )
        return raw
    secret_file = data_dir / "secret.key"
    if secret_file.exists():
        raw = secret_file.read_bytes().strip()
        if raw:
            try:
                secret_file.chmod(0o600)
            except OSError:
                pass
            return raw
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_hex(48).encode("ascii")
    secret_file.write_bytes(raw + b"\n")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    return raw


def _ldap_from_env(env: dict[str, str]) -> LdapSettings | None:
    if not _bool(env.get("SEXTANT_LDAP_ENABLED"), False):
        return None
    missing = [
        key
        for key in ("SEXTANT_LDAP_SERVER", "SEXTANT_LDAP_BASE_DN", "SEXTANT_LDAP_BIND_DN", "SEXTANT_LDAP_BIND_PASSWORD")
        if not env.get(key)
    ]
    if missing:
        raise ValueError(f"LDAP is enabled but missing: {', '.join(missing)}")
    return LdapSettings(
        server=env["SEXTANT_LDAP_SERVER"],
        base_dn=env["SEXTANT_LDAP_BASE_DN"],
        bind_dn=env["SEXTANT_LDAP_BIND_DN"],
        bind_password=env["SEXTANT_LDAP_BIND_PASSWORD"],
        user_filter=env.get("SEXTANT_LDAP_USER_FILTER", "(sAMAccountName={username})"),
        admin_groups=_split_csv(env.get("SEXTANT_LDAP_ADMIN_GROUPS")),
        operator_groups=_split_csv(env.get("SEXTANT_LDAP_OPERATOR_GROUPS")),
        viewer_groups=_split_csv(env.get("SEXTANT_LDAP_VIEWER_GROUPS")),
        default_role=env.get("SEXTANT_LDAP_DEFAULT_ROLE", "viewer"),
        use_ssl=_bool(env.get("SEXTANT_LDAP_USE_SSL"), False),
        ca_cert=env.get("SEXTANT_LDAP_CA_CERT", ""),
        port=_int(env.get("SEXTANT_LDAP_PORT"), 389),
        timeout=_int(env.get("SEXTANT_LDAP_TIMEOUT"), 8, 2),
        domain=env.get("SEXTANT_LDAP_DOMAIN", "").strip(),
        username_attr=env.get("SEXTANT_LDAP_USERNAME_ATTR", "sAMAccountName").strip()
        or "sAMAccountName",
        display_name_attr=env.get("SEXTANT_LDAP_DISPLAY_NAME_ATTR", "displayName").strip()
        or "displayName",
        email_attr=env.get("SEXTANT_LDAP_EMAIL_ATTR", "mail").strip() or "mail",
        group_attr=env.get("SEXTANT_LDAP_GROUP_ATTR", "memberOf").strip() or "memberOf",
    )

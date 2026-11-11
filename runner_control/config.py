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

    @property
    def effective_port(self) -> int:
        return 636 if self.use_ssl and self.port == 389 else self.port


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    secret_key: bytes
    session_hours: int = 12
    cookie_name: str = "rc_session"
    cookie_secure: bool = False
    offline_after_seconds: int = 90
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
    update_check_hours: int = 6
    ssh_key_path: str = ""
    ssh_install_enabled: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "runner-control.sqlite3"

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def bootstrap_file(self) -> Path:
        return self.data_dir / "bootstrap-admin.txt"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        data_dir = Path(env.get("RC_DATA_DIR", "data")).expanduser().resolve()
        static_dir = Path(env.get("RC_STATIC_DIR", str(DEFAULT_STATIC_DIR))).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            data_dir.chmod(0o700)
        except OSError:
            pass

        secret_key = _load_secret(data_dir, env.get("RC_SECRET_KEY"))
        ldap = _ldap_from_env(env)
        offline_policy = env.get("RC_OFFLINE_COMMAND_POLICY", "queue").strip().lower()
        if offline_policy not in {"queue", "reject"}:
            offline_policy = "queue"
        public_url = env.get("RC_PUBLIC_URL", "").rstrip("/")
        allowed_hosts = _split_csv(env.get("RC_ALLOWED_HOSTS"))
        if not allowed_hosts and public_url:
            host = urlsplit(public_url).hostname
            if host:
                allowed_hosts = (host,)

        return cls(
            data_dir=data_dir,
            static_dir=static_dir,
            secret_key=secret_key,
            session_hours=min(8784, _int(env.get("RC_SESSION_HOURS"), 12)),
            cookie_name=env.get("RC_COOKIE_NAME", "rc_session").strip() or "rc_session",
            cookie_secure=_bool(env.get("RC_COOKIE_SECURE"), False),
            offline_after_seconds=_int(env.get("RC_OFFLINE_AFTER_SECONDS"), 90, 15),
            enrollment_ttl_minutes=_int(env.get("RC_ENROLLMENT_TTL_MINUTES"), 30),
            max_body_bytes=_int(env.get("RC_MAX_BODY_BYTES"), 2_000_000, 1024),
            command_output_limit=_int(env.get("RC_COMMAND_OUTPUT_LIMIT"), 200_000, 1024),
            max_queued_commands_per_agent=_int(env.get("RC_MAX_QUEUED_COMMANDS_PER_AGENT"), 50, 1),
            offline_command_policy=offline_policy,
            dispatched_grace_seconds=_int(env.get("RC_DISPATCHED_GRACE_SECONDS"), 120, 10),
            queued_command_ttl_hours=_int(env.get("RC_QUEUED_COMMAND_TTL_HOURS"), 24, 1),
            session_cleanup_days=_int(env.get("RC_SESSION_CLEANUP_DAYS"), 7, 1),
            command_retention_days=_int(env.get("RC_COMMAND_RETENTION_DAYS"), 90, 1),
            audit_retention_days=_int(env.get("RC_AUDIT_RETENTION_DAYS"), 365, 1),
            telemetry_retention_days=min(365, _int(env.get("RC_TELEMETRY_RETENTION_DAYS"), 7, 1)),
            telemetry_history=_bool(env.get("RC_TELEMETRY_HISTORY"), True),
            trust_proxy=_bool(env.get("RC_TRUST_PROXY"), False),
            admin_user=env.get("RC_ADMIN_USER", "admin").strip() or "admin",
            admin_password=env.get("RC_ADMIN_PASSWORD") or None,
            ldap=ldap,
            public_url=public_url,
            allowed_hosts=allowed_hosts,
            store_enabled=_bool(env.get("RC_STORE_ENABLED"), True),
            store_max_blob_mb=_int(env.get("RC_STORE_MAX_BLOB_MB"), 512, 1),
            store_max_total_gb=_int(env.get("RC_STORE_MAX_TOTAL_GB"), 20, 1),
            update_feed=env.get("RC_UPDATE_FEED", "").strip(),
            update_check_hours=_int(env.get("RC_UPDATE_CHECK_HOURS"), 6, 1),
            ssh_key_path=env.get("RC_SSH_KEY_PATH", "").strip() or str(data_dir / "ssh" / "install_key"),
            ssh_install_enabled=_bool(env.get("RC_SSH_INSTALL_ENABLED"), True),
        )


def _load_secret(data_dir: Path, configured: str | None) -> bytes:
    if configured:
        raw = configured.encode("utf-8")
        if len(raw) < 32:
            raise ValueError(
                "RC_SECRET_KEY must be at least 32 bytes of high-entropy data. "
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
    if not _bool(env.get("RC_LDAP_ENABLED"), False):
        return None
    missing = [
        key
        for key in ("RC_LDAP_SERVER", "RC_LDAP_BASE_DN", "RC_LDAP_BIND_DN", "RC_LDAP_BIND_PASSWORD")
        if not env.get(key)
    ]
    if missing:
        raise ValueError(f"LDAP is enabled but missing: {', '.join(missing)}")
    return LdapSettings(
        server=env["RC_LDAP_SERVER"],
        base_dn=env["RC_LDAP_BASE_DN"],
        bind_dn=env["RC_LDAP_BIND_DN"],
        bind_password=env["RC_LDAP_BIND_PASSWORD"],
        user_filter=env.get("RC_LDAP_USER_FILTER", "(sAMAccountName={username})"),
        admin_groups=_split_csv(env.get("RC_LDAP_ADMIN_GROUPS")),
        operator_groups=_split_csv(env.get("RC_LDAP_OPERATOR_GROUPS")),
        viewer_groups=_split_csv(env.get("RC_LDAP_VIEWER_GROUPS")),
        default_role=env.get("RC_LDAP_DEFAULT_ROLE", "viewer"),
        use_ssl=_bool(env.get("RC_LDAP_USE_SSL"), False),
        ca_cert=env.get("RC_LDAP_CA_CERT", ""),
        port=_int(env.get("RC_LDAP_PORT"), 389),
        timeout=_int(env.get("RC_LDAP_TIMEOUT"), 8, 2),
    )

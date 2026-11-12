"""UI-editable system settings layered over the environment defaults.

The environment remains the bootstrap source of truth. Values saved here are
stored as one JSON document and applied on top of ``Settings`` at startup and
whenever an administrator saves the form. Secrets never live in that JSON:

* the LDAP bind password is a 0600 file under ``data_dir/secrets``;
* SSH private keys are 0600 files under the configured key path.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # POSIX only; the Windows server still accepts every other setting.
    import pwd
except ImportError:  # pragma: no cover - exercised on Windows CI
    pwd = None  # type: ignore[assignment]

from .config import LdapSettings, Settings

SERVER_KEYS = (
    "public_url",
    "allowed_hosts",
    "session_hours",
    "cookie_secure",
    "trust_proxy",
    "offline_after_seconds",
    "enrollment_ttl_minutes",
)
DOMAIN_KEYS = (
    "enabled",
    "domain",
    "server",
    "port",
    "use_ssl",
    "base_dn",
    "bind_dn",
    "user_filter",
    "username_attr",
    "display_name_attr",
    "email_attr",
    "group_attr",
    "admin_groups",
    "operator_groups",
    "viewer_groups",
    "default_role",
    "ca_cert",
    "timeout",
)
SSH_KEYS = (
    "source",
    "system_user",
    "key_path",
    "config_path",
    "install_enabled",
)
RESTART_ONLY_KEYS = frozenset({"allowed_hosts", "cookie_secure"})
_ATTR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,127}$")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253})[A-Za-z0-9]$")


def bind_password_path(settings: Settings) -> Path:
    return settings.data_dir / "secrets" / "ldap_bind_password"


def read_bind_password(settings: Settings) -> str:
    path = bind_password_path(settings)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return settings.ldap.bind_password if settings.ldap is not None else ""


def write_bind_password(settings: Settings, password: str) -> None:
    path = bind_password_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not password:
        path.unlink(missing_ok=True)
        return
    path.write_text(password + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _managed_key_path(settings: Settings) -> Path:
    return settings.data_dir / "ssh" / "install_key"


def _home_for_user(username: str) -> Path:
    if pwd is None:
        return Path.home()
    try:
        return Path(pwd.getpwnam(username).pw_dir)
    except KeyError:
        return Path("~" + username).expanduser()


def resolve_ssh_key_path(settings: Settings) -> str:
    explicit = str(settings.ssh_key_path or "").strip()
    managed = str(_managed_key_path(settings))
    if (
        settings.ssh_key_source != "system_user"
        or not settings.ssh_system_user
        or pwd is None
    ):
        return explicit or managed
    home = _home_for_user(settings.ssh_system_user)
    if explicit and explicit != managed:
        return str(Path(explicit).expanduser())
    for name in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"):
        candidate = home / ".ssh" / name
        if candidate.exists():
            return str(candidate)
    return str(home / ".ssh" / "id_ed25519")


def resolve_ssh_config_path(settings: Settings) -> str:
    explicit = str(settings.ssh_config_path or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    if (
        settings.ssh_key_source != "system_user"
        or not settings.ssh_system_user
        or pwd is None
    ):
        return ""
    candidate = _home_for_user(settings.ssh_system_user) / ".ssh" / "config"
    return str(candidate) if candidate.exists() else ""


def build_ldap_settings(
    base: Settings,
    domain: dict[str, Any] | None,
    password: str | None = None,
) -> LdapSettings | None:
    if not domain or not domain.get("enabled"):
        return None
    return LdapSettings(
        server=str(domain.get("server") or ""),
        base_dn=str(domain.get("base_dn") or ""),
        bind_dn=str(domain.get("bind_dn") or ""),
        bind_password=read_bind_password(base) if password is None else password,
        user_filter=str(domain.get("user_filter") or "(sAMAccountName={username})"),
        admin_groups=tuple(domain.get("admin_groups") or ()),
        operator_groups=tuple(domain.get("operator_groups") or ()),
        viewer_groups=tuple(domain.get("viewer_groups") or ()),
        default_role=str(domain.get("default_role") or "viewer"),
        use_ssl=bool(domain.get("use_ssl")),
        ca_cert=str(domain.get("ca_cert") or ""),
        port=int(domain.get("port") or 389),
        timeout=int(domain.get("timeout") or 8),
        domain=str(domain.get("domain") or ""),
        username_attr=str(domain.get("username_attr") or "sAMAccountName"),
        display_name_attr=str(domain.get("display_name_attr") or "displayName"),
        email_attr=str(domain.get("email_attr") or "mail"),
        group_attr=str(domain.get("group_attr") or "memberOf"),
    )


def apply_overrides(base: Settings, values: dict[str, Any] | None) -> Settings:
    """Return Settings with saved UI overrides applied."""
    values = values or {}
    server = values.get("server") or {}
    domain = values.get("domain")
    ssh = values.get("ssh") or {}
    changes: dict[str, Any] = {}

    for key in SERVER_KEYS:
        if key in server:
            changes[key] = tuple(server[key]) if key == "allowed_hosts" else server[key]

    if domain is None:
        changes["ldap"] = base.ldap
    else:
        changes["ldap"] = build_ldap_settings(base, domain)

    if "install_enabled" in ssh:
        changes["ssh_install_enabled"] = bool(ssh["install_enabled"])
    if "source" in ssh:
        changes["ssh_key_source"] = str(ssh["source"])
    if "system_user" in ssh:
        changes["ssh_system_user"] = str(ssh["system_user"])
    if "key_path" in ssh:
        changes["ssh_key_path"] = str(ssh["key_path"])
    if "config_path" in ssh:
        changes["ssh_config_path"] = str(ssh["config_path"])

    provisional = replace(base, **changes)
    changes["ssh_key_path"] = resolve_ssh_key_path(provisional)
    changes["ssh_config_path"] = resolve_ssh_config_path(provisional)
    return replace(base, **changes)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,]", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError("Expected a list of strings.")
    return list(dict.fromkeys(str(item).strip() for item in parts if str(item).strip()))


def _group_list(value: Any) -> list[str]:
    """Like _string_list but keeps DN commas intact."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n;]", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError("Expected a list of group DNs.")
    return list(dict.fromkeys(str(item).strip() for item in parts if str(item).strip()))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def _as_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Public URL must be an http:// or https:// URL.")
    return text


def _as_attr(value: Any, label: str, default: str) -> str:
    text = str(value or "").strip() or default
    if not _ATTR_RE.match(text):
        raise ValueError(f"{label} is not a valid LDAP attribute name.")
    return text


def _derive_base_dn(domain: str) -> str:
    labels = [part for part in domain.strip(".").split(".") if part]
    return ",".join(f"DC={label}" for label in labels)


def _domain_from_base_dn(base_dn: str) -> str:
    labels = re.findall(r"DC=([^,]+)", base_dn or "", flags=re.IGNORECASE)
    return ".".join(label.strip() for label in labels).lower()


def normalize_patch(
    base: Settings,
    stored: dict[str, Any] | None,
    patch: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a partial UI patch and return (persisted, secret operations)."""
    current = stored or {}
    merged: dict[str, Any] = {
        "server": dict(current.get("server") or {}),
        "domain": dict(current.get("domain") or {}),
        "ssh": dict(current.get("ssh") or {}),
    }
    secrets: dict[str, Any] = {}

    if "server" in patch:
        raw = patch["server"] or {}
        if not isinstance(raw, dict):
            raise ValueError("Server settings must be an object.")
        if "public_url" in raw:
            merged["server"]["public_url"] = _as_url(raw["public_url"])
        if "allowed_hosts" in raw:
            merged["server"]["allowed_hosts"] = _string_list(raw["allowed_hosts"])
        if "session_hours" in raw:
            merged["server"]["session_hours"] = _as_int(
                raw["session_hours"], "Session hours", 1, 8784
            )
        for key in ("cookie_secure", "trust_proxy"):
            if key in raw:
                merged["server"][key] = _as_bool(raw[key])
        if "offline_after_seconds" in raw:
            merged["server"]["offline_after_seconds"] = _as_int(
                raw["offline_after_seconds"], "Offline threshold", 15, 86400
            )
        if "enrollment_ttl_minutes" in raw:
            merged["server"]["enrollment_ttl_minutes"] = _as_int(
                raw["enrollment_ttl_minutes"], "Enrollment token lifetime", 1, 10080
            )

    if "domain" in patch:
        raw = patch["domain"] or {}
        if not isinstance(raw, dict):
            raise ValueError("Domain settings must be an object.")
        for key in DOMAIN_KEYS:
            if key in raw:
                merged["domain"][key] = raw[key]
        for key in ("admin_groups", "operator_groups", "viewer_groups"):
            if key in merged["domain"]:
                merged["domain"][key] = _group_list(merged["domain"][key])
        if "enabled" in merged["domain"]:
            merged["domain"]["enabled"] = _as_bool(merged["domain"]["enabled"])
        if "use_ssl" in merged["domain"]:
            merged["domain"]["use_ssl"] = _as_bool(merged["domain"]["use_ssl"])
        if "port" in merged["domain"]:
            merged["domain"]["port"] = _as_int(merged["domain"]["port"], "LDAP port", 1, 65535)
        if "timeout" in merged["domain"]:
            merged["domain"]["timeout"] = _as_int(
                merged["domain"]["timeout"], "LDAP timeout", 2, 60
            )
        domain_name = str(merged["domain"].get("domain") or "").strip().lower()
        if domain_name and not _DOMAIN_RE.match(domain_name):
            raise ValueError("Domain name is not a valid DNS domain.")
        merged["domain"]["domain"] = domain_name
        if not str(merged["domain"].get("base_dn") or "").strip() and domain_name:
            merged["domain"]["base_dn"] = _derive_base_dn(domain_name)
        for key, label, default in (
            ("username_attr", "Username attribute", "sAMAccountName"),
            ("display_name_attr", "Display name attribute", "displayName"),
            ("email_attr", "Email attribute", "mail"),
            ("group_attr", "Group attribute", "memberOf"),
        ):
            merged["domain"][key] = _as_attr(merged["domain"].get(key), label, default)
        user_filter = str(
            merged["domain"].get("user_filter") or "(sAMAccountName={username})"
        ).strip()
        if user_filter.count("{username}") != 1:
            raise ValueError("User filter must contain {username} exactly once.")
        merged["domain"]["user_filter"] = user_filter
        role = str(merged["domain"].get("default_role") or "viewer")
        if role not in {"admin", "operator", "viewer"}:
            raise ValueError("Default role must be admin, operator, or viewer.")
        merged["domain"]["default_role"] = role
        if "bind_password" in raw:
            password = str(raw.get("bind_password") or "")
            if password:
                secrets["bind_password"] = password
        if raw.get("clear_bind_password"):
            secrets["bind_password"] = ""
        if merged["domain"].get("enabled"):
            missing = [
                label
                for key, label in (
                    ("server", "LDAP server"),
                    ("base_dn", "Base DN"),
                    ("bind_dn", "Bind DN"),
                )
                if not str(merged["domain"].get(key) or "").strip()
            ]
            if missing:
                raise ValueError(f"Directory login needs: {', '.join(missing)}.")
            if not (read_bind_password(base) or secrets.get("bind_password")):
                raise ValueError("Set a bind password before enabling directory login.")
        ca_cert = str(merged["domain"].get("ca_cert") or "").strip()
        if ca_cert and not Path(ca_cert).expanduser().exists():
            raise ValueError("The CA certificate path does not exist on the server.")
        merged["domain"]["ca_cert"] = ca_cert

    if "ssh" in patch:
        raw = patch["ssh"] or {}
        if not isinstance(raw, dict):
            raise ValueError("SSH settings must be an object.")
        for key in SSH_KEYS:
            if key in raw:
                merged["ssh"][key] = raw[key]
        source = str(merged["ssh"].get("source") or "managed")
        if source not in {"managed", "system_user"}:
            raise ValueError("SSH key source must be managed or system_user.")
        merged["ssh"]["source"] = source
        merged["ssh"]["system_user"] = str(merged["ssh"].get("system_user") or "").strip()
        if source == "system_user":
            if pwd is None:
                raise ValueError(
                    "System-user SSH configuration is only available on Linux and macOS."
                )
            if not merged["ssh"]["system_user"]:
                raise ValueError("Choose the system user whose SSH configuration to use.")
            try:
                pwd.getpwnam(merged["ssh"]["system_user"])
            except KeyError as exc:
                raise ValueError("That system user does not exist on the server.") from exc
        key_path = str(merged["ssh"].get("key_path") or "").strip()
        if key_path:
            key_path = str(Path(key_path).expanduser())
            if ".." in Path(key_path).parts:
                raise ValueError("SSH key path cannot contain '..'.")
        merged["ssh"]["key_path"] = key_path
        config_path = str(merged["ssh"].get("config_path") or "").strip()
        if config_path:
            config_path = str(Path(config_path).expanduser())
            if not Path(config_path).exists():
                raise ValueError("The SSH config path does not exist on the server.")
        merged["ssh"]["config_path"] = config_path
        if "install_enabled" in merged["ssh"]:
            merged["ssh"]["install_enabled"] = _as_bool(merged["ssh"]["install_enabled"])
        if raw.get("clear_key"):
            secrets["ssh_private_key"] = ""
        if raw.get("private_key"):
            if source != "managed":
                raise ValueError("Upload a key only in Sextant-managed mode.")
            key = str(raw["private_key"])
            if "PRIVATE KEY-----" not in key or len(key) > 65536:
                raise ValueError("That does not look like a PEM private key.")
            secrets["ssh_private_key"] = key

    return merged, secrets


def serialize(
    base: Settings,
    stored: dict[str, Any] | None,
    *,
    updated_at: str = "",
    updated_by: str = "",
) -> dict[str, Any]:
    effective = apply_overrides(base, stored or {})
    ldap = effective.ldap
    key_path = effective.ssh_key_path
    key_info = ssh_key_info(key_path)
    domain_values = (stored or {}).get("domain") or {}
    return {
        "server": {
            "public_url": effective.public_url,
            "allowed_hosts": list(effective.allowed_hosts),
            "session_hours": effective.session_hours,
            "cookie_secure": effective.cookie_secure,
            "trust_proxy": effective.trust_proxy,
            "offline_after_seconds": effective.offline_after_seconds,
            "enrollment_ttl_minutes": effective.enrollment_ttl_minutes,
            "data_dir": str(effective.data_dir),
        },
        "domain": {
            "enabled": ldap is not None,
            "domain": (ldap.domain or _domain_from_base_dn(ldap.base_dn))
            if ldap
            else str(domain_values.get("domain") or ""),
            "server": ldap.server if ldap else str(domain_values.get("server") or ""),
            "port": ldap.port if ldap else int(domain_values.get("port") or 389),
            "use_ssl": ldap.use_ssl if ldap else bool(domain_values.get("use_ssl")),
            "base_dn": ldap.base_dn if ldap else str(domain_values.get("base_dn") or ""),
            "bind_dn": ldap.bind_dn if ldap else str(domain_values.get("bind_dn") or ""),
            "bind_password_set": bool(read_bind_password(base)),
            "user_filter": (
                ldap.user_filter
                if ldap
                else str(domain_values.get("user_filter") or "(sAMAccountName={username})")
            ),
            "username_attr": ldap.username_attr if ldap else "sAMAccountName",
            "display_name_attr": ldap.display_name_attr if ldap else "displayName",
            "email_attr": ldap.email_attr if ldap else "mail",
            "group_attr": ldap.group_attr if ldap else "memberOf",
            "admin_groups": list(ldap.admin_groups) if ldap else list(domain_values.get("admin_groups") or []),
            "operator_groups": list(ldap.operator_groups)
            if ldap
            else list(domain_values.get("operator_groups") or []),
            "viewer_groups": list(ldap.viewer_groups)
            if ldap
            else list(domain_values.get("viewer_groups") or []),
            "default_role": ldap.default_role if ldap else str(domain_values.get("default_role") or "viewer"),
            "ca_cert": ldap.ca_cert if ldap else str(domain_values.get("ca_cert") or ""),
            "timeout": ldap.timeout if ldap else int(domain_values.get("timeout") or 8),
        },
        "ssh": {
            "source": effective.ssh_key_source,
            "system_user": effective.ssh_system_user,
            "key_path": str((stored or {}).get("ssh", {}).get("key_path") or ""),
            "config_path": effective.ssh_config_path,
            "install_enabled": effective.ssh_install_enabled,
            "effective_key_path": key_path,
            "key_present": key_info["present"],
            "key_readable": key_info["readable"],
            "key_fingerprint": key_info["fingerprint"],
            "public_key": key_info["public_key"],
        },
        "meta": {
            "ldap3_available": importlib.util.find_spec("ldap3") is not None,
            "updated_at": updated_at,
            "updated_by": updated_by,
            "restart_required": [],
        },
    }


def ssh_key_info(path: str) -> dict[str, Any]:
    key_path = Path(path).expanduser() if path else None
    present = bool(key_path and key_path.is_file())
    result: dict[str, Any] = {
        "present": present,
        "readable": bool(present and os.access(key_path, os.R_OK)),
        "fingerprint": "",
        "public_key": "",
    }
    if not present or key_path is None:
        return result
    public_file = key_path.with_name(key_path.name + ".pub")
    if public_file.is_file():
        try:
            result["public_key"] = public_file.read_text(encoding="utf-8").strip().splitlines()[0][:4096]
        except (OSError, IndexError):
            pass
    try:
        proc = subprocess.run(
            ["ssh-keygen", "-lf", str(key_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["fingerprint"] = proc.stdout.strip()[:512]
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def write_ssh_private_key(settings: Settings, key: str) -> Path:
    path = Path(settings.ssh_key_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not key:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".pub").unlink(missing_ok=True)
        return path
    text = key.strip() + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path

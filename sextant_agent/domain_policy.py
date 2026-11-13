"""Apply a server-defined domain login policy to a joined Linux host.

The policy only adds domain access. It never edits local accounts, passwords,
authorized keys, or ``/etc/sudoers``. Before and after every change it verifies
that protected local users keep the SSH and sudo access they had, and it rolls
the managed files back when a check fails.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .executor import run_capture

MANAGED_SSHD = Path("/etc/ssh/sshd_config.d/90-sextant-domain.conf")
MANAGED_SUDOERS = Path("/etc/sudoers.d/90-sextant-domain")
SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSHD_CONFIG_DIR = Path("/etc/ssh/sshd_config.d")
SSSD_CONFIG = Path("/etc/sssd/sssd.conf")
PAM_FILES = (
    Path("/etc/pam.d/common-session"),
    Path("/etc/pam.d/common-session-noninteractive"),
)
PAM_MARKER = "# Managed by Sextant domain login policy"
PAM_HOME_LINE = "session optional pam_mkhomedir.so skel=/etc/skel umask=0077"
SSHD_BLOCK_BEGIN = "# BEGIN Sextant domain login policy"
SSHD_BLOCK_END = "# END Sextant domain login policy"
DEFAULT_PROTECTED_USERS = ("dadisc01",)
PROTECTED_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
PAM_MODULE_DIRS = (
    Path("/lib/security"),
    Path("/usr/lib/security"),
    Path("/lib/x86_64-linux-gnu/security"),
    Path("/usr/lib/x86_64-linux-gnu/security"),
    Path("/lib/aarch64-linux-gnu/security"),
    Path("/usr/lib/aarch64-linux-gnu/security"),
)


def _result(
    status: str,
    *,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
    }


def _write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    _write_bytes(path, text.encode("utf-8"), mode)


def _backup(path: Path) -> Path:
    backup = path.with_name(path.name + ".sextant-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    return backup


class _Snapshot:
    """Remember the exact bytes of touched files so a failed check rolls back."""

    def __init__(self) -> None:
        self._items: dict[Path, tuple[bool, bytes, int]] = {}

    def remember(self, path: Path) -> None:
        if path in self._items:
            return
        try:
            self._items[path] = (True, path.read_bytes(), path.stat().st_mode & 0o777)
        except OSError:
            self._items[path] = (False, b"", 0o644)

    def restore(self) -> None:
        for path, (exists, data, mode) in self._items.items():
            try:
                if exists:
                    _write_bytes(path, data, mode)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        self._items.clear()


def _run(args: list[str], timeout: int = 30) -> dict[str, Any]:
    return run_capture(args, timeout=timeout, max_output=50_000)


def _realm_output(domain: str = "") -> str:
    realm = shutil.which("realm") or "/usr/sbin/realm"
    result = _run([realm, "list", *([domain] if domain else [])], timeout=20)
    return (result.get("stdout") or "") + (result.get("stderr") or "")


def _realm_joined(domain: str) -> tuple[bool, str]:
    output = _realm_output(domain)
    return ("configured: kerberos-member" in output, output)


def _detect_domain() -> str:
    """Return the configured realm when exactly one host is joined."""
    output = _realm_output()
    candidates: list[str] = []
    current = ""
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(" "):
            if current and "configured: kerberos-member" in stripped:
                candidates.append(current)
        else:
            current = (
                stripped
                if re.match(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9.-]+$", stripped)
                else ""
            )
    candidates = list(dict.fromkeys(candidates))
    return candidates[0] if len(candidates) == 1 else ""


def _protected_users(policy: dict[str, Any]) -> list[str]:
    raw = policy.get("protected_users")
    if raw is None:
        raw = list(DEFAULT_PROTECTED_USERS)
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    if not isinstance(raw, (list, tuple)):
        raw = list(DEFAULT_PROTECTED_USERS)
    users: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name and PROTECTED_USER_RE.match(name) and name not in users:
            users.append(name)
    return users[:32]


def _group_exists(name: str) -> bool:
    getent = shutil.which("getent")
    if not getent:
        return True
    result = _run([getent, "group", name], timeout=10)
    return result.get("status") == "succeeded" and bool(
        str(result.get("stdout") or "").strip()
    )


def _local_user_exists(name: str) -> bool:
    getent = shutil.which("getent")
    if not getent:
        return False
    result = _run([getent, "passwd", name], timeout=10)
    return result.get("status") == "succeeded" and bool(
        str(result.get("stdout") or "").strip()
    )


def _sudo_allowed(name: str) -> bool:
    sudo = shutil.which("sudo")
    if not sudo or not _local_user_exists(name):
        return False
    result = _run([sudo, "-n", "-l", "-U", name], timeout=20)
    return result.get("status") == "succeeded"


def _user_groups(name: str) -> list[str]:
    identifier = shutil.which("id")
    if not identifier:
        return []
    result = _run([identifier, "-nG", name], timeout=10)
    if result.get("status") != "succeeded":
        return []
    return str(result.get("stdout") or "").split()


def _sshd_effective_config() -> dict[str, str] | None:
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    result = _run([sshd, "-T"], timeout=20)
    if result.get("status") != "succeeded":
        return None
    config: dict[str, str] = {}
    for line in str(result.get("stdout") or "").splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        key = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        config[key] = f"{config[key]} {value}".strip() if key in config else value
    return config


def _ssh_allows_user(name: str, config: dict[str, str] | None) -> bool | None:
    if config is None:
        return None
    allow_users = str(config.get("allowusers") or "").split()
    allow_groups = str(config.get("allowgroups") or "").split()
    if not allow_users and not allow_groups:
        return True
    for pattern in allow_users:
        if fnmatch.fnmatch(name.lower(), pattern.lower()):
            return True
    groups = _user_groups(name) if allow_groups else []
    for pattern in allow_groups:
        if any(fnmatch.fnmatch(group.lower(), pattern.lower()) for group in groups):
            return True
    return False


def _disable_allow_lists(messages: list[str], snapshot: _Snapshot) -> None:
    files = [SSHD_CONFIG]
    if SSHD_CONFIG_DIR.is_dir():
        files.extend(
            sorted(
                path
                for path in SSHD_CONFIG_DIR.glob("*.conf")
                if path.name != MANAGED_SSHD.name
            )
        )
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        changed = False
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^(AllowUsers|AllowGroups)\b", stripped, re.IGNORECASE):
                lines.append(f"# Disabled by Sextant domain login policy: {stripped}")
                changed = True
            else:
                lines.append(line)
        if changed:
            _backup(path)
            snapshot.remember(path)
            _write(path, "\n".join(lines) + "\n", mode=path.stat().st_mode & 0o777)
            messages.append(f"Removed allow-list restrictions from {path}.")


def _sshd_uses_dropin(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("include "):
            pattern = stripped.split(None, 1)[1]
            if "sshd_config.d" in pattern:
                return True
    return False


def _strip_sshd_block(text: str) -> str:
    lines: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == SSHD_BLOCK_BEGIN:
            skipping = True
            continue
        if skipping:
            if stripped == SSHD_BLOCK_END:
                skipping = False
            continue
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def _insert_sshd_block(text: str, directives: list[str]) -> str:
    text = _strip_sshd_block(text)
    lines = text.splitlines()
    insert_at = len(lines)
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("match "):
            insert_at = index
            break
    block = [SSHD_BLOCK_BEGIN, *directives, SSHD_BLOCK_END]
    combined = [*lines[:insert_at], *block, *lines[insert_at:]]
    return "\n".join(combined).rstrip() + "\n"


def _write_managed_ssh(
    directives: list[str], messages: list[str], snapshot: _Snapshot
) -> None:
    text = SSHD_CONFIG.read_text(encoding="utf-8")
    if _sshd_uses_dropin(text):
        snapshot.remember(MANAGED_SSHD)
        _write(
            MANAGED_SSHD,
            "# Managed by Sextant domain login policy\n" + "\n".join(directives) + "\n",
            mode=0o644,
        )
        messages.append(f"Wrote {MANAGED_SSHD}.")
        if SSHD_BLOCK_BEGIN in text:
            snapshot.remember(SSHD_CONFIG)
            _write(SSHD_CONFIG, _strip_sshd_block(text), mode=SSHD_CONFIG.stat().st_mode & 0o777)
            messages.append(f"Removed the old managed block from {SSHD_CONFIG}.")
        return
    snapshot.remember(SSHD_CONFIG)
    _write(
        SSHD_CONFIG,
        _insert_sshd_block(text, directives),
        mode=SSHD_CONFIG.stat().st_mode & 0o777,
    )
    snapshot.remember(MANAGED_SSHD)
    MANAGED_SSHD.unlink(missing_ok=True)
    messages.append(f"Updated the managed block in {SSHD_CONFIG}.")


def _remove_managed_ssh(messages: list[str], snapshot: _Snapshot) -> None:
    snapshot.remember(MANAGED_SSHD)
    MANAGED_SSHD.unlink(missing_ok=True)
    try:
        text = SSHD_CONFIG.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if SSHD_BLOCK_BEGIN in text:
        snapshot.remember(SSHD_CONFIG)
        _write(SSHD_CONFIG, _strip_sshd_block(text), mode=SSHD_CONFIG.stat().st_mode & 0o777)
    messages.append("Removed the managed SSH domain-login configuration.")


def _apply_ssh(policy: dict[str, Any], messages: list[str]) -> str:
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    preflight = _run([sshd, "-t"], timeout=20)
    if preflight.get("status") != "succeeded":
        return (
            "Refusing to change SSH: the current sshd configuration is already "
            "invalid: "
            + ((preflight.get("stderr") or preflight.get("stdout") or "").strip()[:2000])
        )
    before = _sshd_effective_config()
    protected = _protected_users(policy)
    snapshot = _Snapshot()
    password_auth = bool(policy.get("password_authentication", True))
    ssh_login = bool(policy.get("ssh_login", True))
    if not ssh_login:
        _remove_managed_ssh(messages, snapshot)
    else:
        _disable_allow_lists(messages, snapshot)
        directives = [
            "UsePAM yes",
            f"PasswordAuthentication {'yes' if password_auth else 'no'}",
            f"KbdInteractiveAuthentication {'yes' if password_auth else 'no'}",
        ]
        _write_managed_ssh(directives, messages, snapshot)

    checked = _run([sshd, "-t"], timeout=20)
    if checked.get("status") != "succeeded":
        snapshot.restore()
        return (
            "sshd configuration check failed; SSH changes were rolled back: "
            + ((checked.get("stderr") or checked.get("stdout") or "").strip()[:2000])
        )

    after = _sshd_effective_config()
    if before is None or after is None:
        snapshot.restore()
        return (
            "Could not verify the effective sshd configuration; SSH changes were "
            "rolled back. Local SSH access was not changed."
        )
    for name in protected:
        if not _local_user_exists(name):
            continue
        was_allowed = _ssh_allows_user(name, before)
        now_allowed = _ssh_allows_user(name, after)
        if was_allowed and not now_allowed:
            snapshot.restore()
            return (
                f"Refusing to apply: local user {name} would lose SSH access. "
                "SSH changes were rolled back."
            )

    reloaded = False
    for unit in ("ssh", "sshd"):
        reload_result = _run(["systemctl", "reload", unit], timeout=30)
        if reload_result.get("status") == "succeeded":
            messages.append(f"Reloaded {unit}.service.")
            reloaded = True
            break
    if not reloaded:
        messages.append("Could not reload ssh/sshd; restart it manually.")
    return ""


def _pam_mkhomedir_available() -> bool:
    return any((directory / "pam_mkhomedir.so").exists() for directory in PAM_MODULE_DIRS)


def _apply_pam(policy: dict[str, Any], messages: list[str]) -> None:
    enabled = bool(policy.get("create_home", True))
    if enabled and not _pam_mkhomedir_available():
        messages.append(
            "pam_mkhomedir.so is not installed; skipped home-directory creation."
        )
        return
    for path in PAM_FILES:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        managed = False
        kept: list[str] = []
        skip_next = False
        for line in lines:
            if skip_next:
                skip_next = False
                if "pam_mkhomedir" in line or "pam_oddjob_mkhomedir" in line:
                    managed = True
                    continue
            if line.strip() == PAM_MARKER:
                skip_next = True
                managed = True
                continue
            kept.append(line)
        if not enabled:
            if managed:
                _backup(path)
                _write(
                    path,
                    "\n".join(kept).rstrip() + "\n",
                    mode=path.stat().st_mode & 0o777,
                )
                messages.append(f"Disabled Sextant home-directory creation in {path}.")
            continue
        if any(
            "pam_mkhomedir" in line or "pam_oddjob_mkhomedir" in line
            for line in kept
        ):
            continue
        _backup(path)
        _write(
            path,
            "\n".join(kept).rstrip() + f"\n{PAM_MARKER}\n{PAM_HOME_LINE}\n",
            mode=path.stat().st_mode & 0o777,
        )
        messages.append(f"Enabled home-directory creation in {path}.")


def _apply_sudo(
    policy: dict[str, Any],
    admin_group: str,
    messages: list[str],
    protected: list[str],
) -> str:
    before = {
        name: _sudo_allowed(name)
        for name in protected
        if _local_user_exists(name)
    }
    snapshot = _Snapshot()
    enabled = bool(policy.get("sudo_domain_admins", True))
    if not enabled:
        snapshot.remember(MANAGED_SUDOERS)
        MANAGED_SUDOERS.unlink(missing_ok=True)
        messages.append("Removed the managed domain sudoers file.")
    else:
        if not _group_exists(admin_group):
            return f"Domain group not found locally: {admin_group}"
        escaped = admin_group.replace(" ", r"\ ")
        content = (
            "# Managed by Sextant domain login policy\n"
            f"%{escaped} ALL=(ALL:ALL) ALL\n"
        )
        snapshot.remember(MANAGED_SUDOERS)
        _write(MANAGED_SUDOERS, content, mode=0o440)
        visudo = shutil.which("visudo") or "/usr/sbin/visudo"
        checked = _run([visudo, "-cf", str(MANAGED_SUDOERS)], timeout=20)
        if checked.get("status") != "succeeded":
            snapshot.restore()
            return (
                "sudoers validation failed; the managed file was removed: "
                + ((checked.get("stderr") or checked.get("stdout") or "").strip()[:2000])
            )
        full = _run([visudo, "-c"], timeout=20)
        if full.get("status") != "succeeded":
            snapshot.restore()
            return (
                "The full sudoers configuration failed validation; the managed "
                "file was removed: "
                + ((full.get("stderr") or full.get("stdout") or "").strip()[:2000])
            )
        messages.append(f"Granted sudo to {admin_group}.")

    for name, was_allowed in before.items():
        if was_allowed and not _sudo_allowed(name):
            snapshot.restore()
            return (
                f"Refusing to apply: local user {name} would lose sudo access. "
                "The managed sudoers change was rolled back."
            )
    return ""


def _apply_sssd(policy: dict[str, Any], messages: list[str]) -> str:
    if not SSSD_CONFIG.exists():
        return "SSSD configuration is missing."
    text = SSSD_CONFIG.read_text(encoding="utf-8", errors="replace")
    desired = "True" if not bool(policy.get("short_names", False)) else "False"
    if re.search(r"(?m)^use_fully_qualified_names\s*=", text):
        updated = re.sub(
            r"(?m)^use_fully_qualified_names\s*=.*$",
            f"use_fully_qualified_names = {desired}",
            text,
        )
    else:
        # Insert into the first domain section.
        match = re.search(r"(?m)^\[domain/[^\]]+\]\s*$", text)
        if not match:
            return "No [domain/...] section found in sssd.conf."
        insert_at = match.end()
        updated = (
            text[:insert_at]
            + f"\nuse_fully_qualified_names = {desired}"
            + text[insert_at:]
        )
    if updated == text:
        return ""
    _backup(SSSD_CONFIG)
    snapshot = _Snapshot()
    snapshot.remember(SSSD_CONFIG)
    _write(SSSD_CONFIG, updated, mode=0o600)
    restarted = _run(["systemctl", "restart", "sssd"], timeout=60)
    if restarted.get("status") != "succeeded":
        snapshot.restore()
        _run(["systemctl", "restart", "sssd"], timeout=60)
        return "SSSD restart failed; the previous sssd.conf was restored."
    messages.append(
        f"Set SSSD use_fully_qualified_names = {desired} and restarted sssd."
    )
    return ""


def apply_login_policy(policy: dict[str, Any], domain: str = "") -> dict[str, Any]:
    domain = str(domain or "").strip().lower()
    if not domain:
        domain = _detect_domain()
    if not domain:
        return _result(
            "failed",
            error="This host is not joined to exactly one Active Directory domain.",
        )
    joined, realm_output = _realm_joined(domain)
    if not joined:
        return _result(
            "failed",
            stdout=realm_output[-4000:],
            error=f"This host is not joined to {domain}.",
        )
    domain_users = f"domain users@{domain}"
    domain_admins = f"domain admins@{domain}"
    if not _group_exists(domain_users):
        return _result(
            "failed",
            error=f"Domain group not found locally: {domain_users}",
        )
    protected = _protected_users(policy)
    messages: list[str] = [f"Domain: {domain}"]
    if protected:
        messages.append(
            "Protected local users: " + ", ".join(protected)
        )
    error = _apply_ssh(policy, messages)
    if error:
        return _result("failed", stdout="\n".join(messages), error=error)
    _apply_pam(policy, messages)
    error = _apply_sudo(policy, domain_admins, messages, protected)
    if error:
        return _result("failed", stdout="\n".join(messages), error=error)
    error = _apply_sssd(policy, messages)
    if error:
        return _result("failed", stdout="\n".join(messages), error=error)
    messages.append("Domain login policy applied.")
    return _result("succeeded", exit_code=0, stdout="\n".join(messages))

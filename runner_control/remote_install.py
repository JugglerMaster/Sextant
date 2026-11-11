from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable

SERVICE_UNIT = """[Unit]
Description=Runner Control agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
NoNewPrivileges=true
UMask=0077
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
ExecStart=/opt/runner-agent/current/bin/runner-agent run --config /etc/runner-agent/config.json
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=2
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"""


class RemoteInstallError(RuntimeError):
    pass


def _validate_target(host: str, port: int, user: str) -> None:
    allowed_host = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:")
    if not host or any(ch not in allowed_host for ch in host):
        raise RemoteInstallError("Host must be an IP address or DNS name.")
    if not 1 <= int(port) <= 65535:
        raise RemoteInstallError("Port must be between 1 and 65535.")
    if not user or not (user[0].isalpha() or user[0] == "_"):
        raise RemoteInstallError("SSH user must start with a letter or underscore.")
    if any(not (ch.isalnum() or ch in "._-") for ch in user):
        raise RemoteInstallError("SSH user contains invalid characters.")


def _ssh_base(key_path: str, host: str, port: int, user: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key_path,
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=15",
        f"{user}@{host}",
    ]


def _scp_base(key_path: str, port: int) -> list[str]:
    return [
        "scp",
        "-i",
        key_path,
        "-P",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=15",
    ]


def _run(args: list[str], *, input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteInstallError(f"Could not run {args[0]}: {exc}") from exc


def _require_binaries() -> None:
    for name in ("ssh", "scp"):
        if shutil.which(name) is None:
            raise RemoteInstallError(f"{name} is not installed on the server.")


def _key_check(key_path: str) -> None:
    path = Path(key_path)
    if not path.is_file():
        raise RemoteInstallError(
            f"SSH key not found at {key_path}. Place one at RC_SSH_KEY_PATH or the default data/ssh/install_key."
        )
    if os.name != "nt":
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RemoteInstallError(f"SSH key {key_path} must not be group/other accessible (chmod 600).")


def test_connection(
    *,
    key_path: str,
    host: str,
    port: int,
    user: str,
    timeout: int = 60,
) -> str:
    _validate_target(host, port, user)
    _require_binaries()
    _key_check(key_path)
    script = """
set -euo pipefail
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required")'
command -v systemctl >/dev/null
sudo -n true
python3 --version
echo "sudo=passwordless systemd=present"
"""
    result = _run(
        [*_ssh_base(key_path, host, port, user), "bash -s"],
        input_text=script,
        timeout=timeout,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise RemoteInstallError(output[-2000:] or "SSH prerequisite check failed.")
    return output[-4000:]


def run_install(
    *,
    key_path: str,
    host: str,
    port: int,
    user: str,
    server_url: str,
    token: str,
    name: str,
    profile: str,
    watch_services: list[str],
    wheel_path: str,
    log: Callable[[str], None] = lambda _line: None,
    timeout: int = 300,
) -> None:
    _validate_target(host, port, user)
    _require_binaries()
    _key_check(key_path)
    wheel = Path(wheel_path)
    if not wheel.is_file():
        raise RemoteInstallError("Agent wheel is missing from the package store.")

    remote_wheel = f"/tmp/runner-control-{wheel.name}"
    remote_unit = "/tmp/runner-agent.service"
    log(f"Checking prerequisites on {user}@{host}:{port}")
    log(test_connection(key_path=key_path, host=host, port=port, user=user, timeout=60))

    log("Uploading agent wheel and service unit")
    scp = [*_scp_base(key_path, port)]
    uploaded = _run([*scp, str(wheel), f"{user}@{host}:{remote_wheel}"], timeout=120)
    if uploaded.returncode != 0:
        raise RemoteInstallError((uploaded.stderr or uploaded.stdout)[-2000:])
    unit_file = Path("/tmp") / f"runner-agent-unit-{os.getpid()}.service"
    unit_file.write_text(SERVICE_UNIT, encoding="utf-8")
    try:
        uploaded = _run([*scp, str(unit_file), f"{user}@{host}:{remote_unit}"], timeout=60)
        if uploaded.returncode != 0:
            raise RemoteInstallError((uploaded.stderr or uploaded.stdout)[-2000:])
    finally:
        unit_file.unlink(missing_ok=True)

    enroll_args = [
        "/opt/runner-agent/.venv/bin/runner-agent",
        "enroll",
        "--config",
        "/etc/runner-agent/config.json",
        "--server",
        server_url,
        "--token",
        token,
        "--name",
        name,
        "--profile",
        profile,
    ]
    for service in watch_services[:64]:
        enroll_args.extend(["--watch-service", service])
    enroll_command = "sudo -n " + " ".join(shlex.quote(part) for part in enroll_args)

    script = f"""set -euo pipefail
WHEEL={shlex.quote(remote_wheel)}
UNIT={shlex.quote(remote_unit)}
sudo -n install -d -m 0755 /opt/runner-agent
sudo -n install -d -m 0700 /etc/runner-agent
if [ ! -x /opt/runner-agent/.venv/bin/python ]; then
  sudo -n python3 -m venv /opt/runner-agent/.venv
fi
sudo -n /opt/runner-agent/.venv/bin/pip install --no-deps --upgrade "$WHEEL"
sudo -n ln -sfn .venv /opt/runner-agent/current
{enroll_command}
sudo -n chmod 0600 /etc/runner-agent/config.json
sudo -n install -m 0644 "$UNIT" /etc/systemd/system/runner-agent.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now runner-agent
sudo -n systemctl is-active runner-agent
rm -f "$WHEEL" "$UNIT"
"""
    log("Installing and enrolling the agent")
    result = _run(
        [*_ssh_base(key_path, host, port, user), "bash -s"],
        input_text=script,
        timeout=timeout,
    )
    output = (result.stdout + result.stderr).strip()
    if output:
        log(output[-20_000:])
    if result.returncode != 0:
        raise RemoteInstallError(output[-4000:] or "Remote install failed.")
    log("Remote install completed.")

"""Domain join helpers for the Windows and Linux agent.

The password is passed separately by the server command dispatcher and is
never placed in argv, the persisted command row, or log output. Windows reads
it from a child-only environment variable; Linux uses a PTY prompt for realmd
or adcli's stdin-password mode.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:  # POSIX only; the module is imported on Windows too.
    import pty
except ImportError:  # pragma: no cover - exercised on Windows
    pty = None  # type: ignore[assignment]

from .executor import run_capture

ChunkCallback = Callable[[str, str], None]
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253})[A-Za-z0-9]$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
_COMPUTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")


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


def _validate(args: dict[str, Any], *, need_password: bool) -> tuple[dict[str, str], str]:
    domain = str(args.get("domain") or "").strip()
    username = str(args.get("username") or "").strip()
    password = str(args.get("password") or "")
    ou = str(args.get("ou") or "").strip()
    computer_name = str(args.get("computer_name") or "").strip()
    server = str(args.get("server") or "").strip()
    if not _DOMAIN_RE.match(domain):
        raise ValueError("Enter a valid AD domain name.")
    if not username or len(username) > 255 or any(ord(ch) < 32 for ch in username):
        raise ValueError("Enter a valid domain username.")
    if need_password and not password:
        raise ValueError("A domain password is required.")
    if len(password) > 4096:
        raise ValueError("The password is too long.")
    if ou and (len(ou) > 1024 or any(ord(ch) < 32 for ch in ou)):
        raise ValueError("Enter a valid OU distinguished name.")
    if computer_name and not _COMPUTER_RE.match(computer_name):
        raise ValueError("Computer name must be 1-15 letters, numbers, or hyphens.")
    if server and not _HOST_RE.match(server):
        raise ValueError("Enter a valid domain controller host.")
    return (
        {
            "domain": domain,
            "username": username,
            "password": password,
            "ou": ou,
            "computer_name": computer_name,
            "server": server,
        },
        domain,
    )


def _redact(text: str, password: str) -> str:
    if password and password in text:
        return text.replace(password, "[redacted]")
    return text


def _run_windows_join(
    values: dict[str, str],
    password: str,
    *,
    reboot: bool,
    timeout: int,
    max_output: int,
    on_chunk: ChunkCallback | None,
) -> dict[str, Any]:
    login = values["username"]
    if "\\" not in login and "@" not in login:
        login = f"{values['domain']}\\{login}"
    env = os.environ.copy()
    env.update(
        {
            "SEXTANT_DOMAIN_NAME": values["domain"],
            "SEXTANT_DOMAIN_USER": login,
            "SEXTANT_DOMAIN_PASSWORD": password,
            "SEXTANT_DOMAIN_OU": values["ou"],
            "SEXTANT_DOMAIN_COMPUTER": values["computer_name"],
            "SEXTANT_DOMAIN_REBOOT": "1" if reboot else "0",
        }
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$secure = ConvertTo-SecureString $env:SEXTANT_DOMAIN_PASSWORD -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($env:SEXTANT_DOMAIN_USER, $secure)
$params = @{
  DomainName = $env:SEXTANT_DOMAIN_NAME
  Credential = $cred
  Force = $true
  Restart = $false
  ErrorAction = 'Stop'
}
if ($env:SEXTANT_DOMAIN_OU) { $params.OUPath = $env:SEXTANT_DOMAIN_OU }
if ($env:SEXTANT_DOMAIN_COMPUTER) { $params.NewName = $env:SEXTANT_DOMAIN_COMPUTER }
Add-Computer @params
$cs = Get-CimInstance Win32_ComputerSystem
Write-Output ("Domain: " + $cs.Domain)
Write-Output ("PartOfDomain: " + $cs.PartOfDomain)
if ($env:SEXTANT_DOMAIN_REBOOT -eq '1') {
  shutdown.exe /r /t 30 /c "Sextant domain join reboot"
  Write-Output "Reboot scheduled in 30 seconds."
} else {
  Write-Output "Reboot required to finish applying domain membership."
}
"""
    try:
        result = run_capture(
            [
                shutil.which("powershell") or shutil.which("pwsh") or "powershell",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "-",
            ],
            timeout=timeout,
            input_text="[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script,
            max_output=max_output,
            env=env,
            on_chunk=on_chunk,
        )
    except OSError as exc:
        return _result("failed", error=f"Could not start PowerShell: {exc}")
    result["stdout"] = _redact(str(result.get("stdout") or ""), password)
    result["stderr"] = _redact(str(result.get("stderr") or ""), password)
    return result


def _run_pty(
    args: list[str],
    password: str,
    *,
    timeout: int,
    max_output: int,
    env: dict[str, str] | None = None,
    on_chunk: ChunkCallback | None = None,
) -> dict[str, Any]:
    if pty is None:
        return _result("failed", error="Interactive password prompts are not available on this platform.")
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            args,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        os.close(master)
        os.close(slave)
        return _result("failed", error=f"Could not start {args[0]}: {exc}")
    finally:
        try:
            os.close(slave)
        except OSError:
            pass

    deadline = time.monotonic() + max(5, timeout)
    chunks: list[str] = []
    total = 0
    sent_password = False
    timed_out = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], min(remaining, 0.5))
            if ready:
                try:
                    raw = os.read(master, 4096)
                except OSError:
                    break
                if not raw:
                    break
                text = raw.decode("utf-8", "replace")
                total += len(text)
                if total <= max_output:
                    chunks.append(text)
                    visible = _redact(text, password)
                    if on_chunk is not None:
                        try:
                            on_chunk("stdout", visible)
                        except Exception:
                            pass
                if not sent_password and "password" in text.lower():
                    os.write(master, (password + "\n").encode("utf-8"))
                    sent_password = True
            if process.poll() is not None and not ready:
                break
        if timed_out:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                process.kill()
        exit_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        exit_code = None
        timed_out = True
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    output = _redact("".join(chunks), password)
    if timed_out:
        return _result(
            "failed",
            exit_code=exit_code,
            stdout=output,
            error="The directory join timed out.",
        )
    status = "succeeded" if exit_code == 0 else "failed"
    return _result(
        status,
        exit_code=exit_code,
        stdout=output,
        error="" if exit_code == 0 else "The directory join failed.",
    )


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return values


def _flush_dns(on_chunk: ChunkCallback | None = None) -> list[str]:
    """Best-effort local resolver cache flush before a join."""
    messages: list[str] = []
    if sys.platform.startswith("win"):
        result = run_capture(
            ["ipconfig", "/flushdns"],
            timeout=15,
            max_output=20_000,
        )
        if result.get("status") == "succeeded":
            messages.append("Flushed the Windows DNS resolver cache.")
        return messages
    candidates = (
        (["resolvectl", "flush-caches"], "resolvectl"),
        (["systemd-resolve", "--flush-caches"], "systemd-resolve"),
        (["nscd", "-i", "hosts"], "nscd"),
    )
    for args, label in candidates:
        if shutil.which(args[0]) is None:
            continue
        result = run_capture(args, timeout=15, max_output=20_000, on_chunk=on_chunk)
        if result.get("status") == "succeeded":
            messages.append(f"Flushed the DNS resolver cache with {label}.")
            break
    if not messages:
        messages.append("No local DNS cache tool was available; continuing without a cache flush.")
    return messages


def _install_packages(on_chunk: ChunkCallback | None) -> dict[str, Any]:
    release = _os_release()
    family = f"{release.get('ID', '')} {release.get('ID_LIKE', '')}".lower()
    if "debian" in family or "ubuntu" in family:
        commands = [
            (
                ["apt-get", "-o", "APT::Sandbox::User=root", "update"],
                {"DEBIAN_FRONTEND": "noninteractive"},
            ),
            (
                [
                    "apt-get",
                    "-o",
                    "APT::Sandbox::User=root",
                    "install",
                    "-y",
                    "realmd",
                    "sssd",
                    "sssd-tools",
                    "adcli",
                    "krb5-user",
                    "samba-common-bin",
                    "oddjob",
                    "oddjob-mkhomedir",
                ],
                {"DEBIAN_FRONTEND": "noninteractive"},
            ),
        ]
    elif any(token in family for token in ("rhel", "fedora", "centos", "rocky", "almalinux")):
        commands = [
            (
                [
                    "dnf",
                    "install",
                    "-y",
                    "realmd",
                    "sssd",
                    "adcli",
                    "krb5-workstation",
                    "samba-common-tools",
                    "oddjob",
                    "oddjob-mkhomedir",
                ],
                {},
            )
        ]
    elif "suse" in family or "opensuse" in family:
        commands = [
            (
                [
                    "zypper",
                    "--non-interactive",
                    "install",
                    "realmd",
                    "sssd",
                    "adcli",
                    "krb5-client",
                    "samba-client",
                ],
                {},
            )
        ]
    elif "arch" in family:
        commands = [
            (
                ["pacman", "-S", "--noconfirm", "realmd", "sssd", "adcli", "krb5"],
                {},
            )
        ]
    else:
        return _result(
            "failed",
            error="Unsupported Linux distribution for automatic prerequisite installation.",
        )
    outputs: list[str] = []
    for args, extra_env in commands:
        if shutil.which(args[0]) is None:
            return _result(
                "failed",
                stdout="\n".join(outputs),
                error=f"{args[0]} is not installed; install the domain packages manually.",
            )
        env = os.environ.copy()
        env.update(extra_env)
        result = run_capture(
            args,
            timeout=600,
            max_output=200_000,
            env=env,
            on_chunk=on_chunk,
        )
        outputs.append(f"$ {' '.join(args)}\n{result.get('stdout') or ''}{result.get('stderr') or ''}")
        if result.get("status") != "succeeded":
            return _result(
                "failed",
                exit_code=result.get("exit_code"),
                stdout="\n".join(outputs)[-200_000:],
                error=result.get("error") or "Package installation failed.",
            )
    return _result("succeeded", exit_code=0, stdout="\n".join(outputs)[-200_000:])


def _run_linux_join(
    values: dict[str, str],
    password: str,
    *,
    method: str,
    install_prerequisites: bool,
    reboot: bool,
    timeout: int,
    max_output: int,
    on_chunk: ChunkCallback | None,
) -> dict[str, Any]:
    realm = shutil.which("realm")
    adcli = shutil.which("adcli")
    if install_prerequisites:
        installed = _install_packages(on_chunk)
        if installed.get("status") != "succeeded":
            return installed
        realm = shutil.which("realm")
        adcli = shutil.which("adcli")
    if method == "realm" and not realm:
        return _result("failed", error="realmd is not installed. Install realm/SSSD or choose auto.")
    if method == "adcli" and not adcli:
        return _result("failed", error="adcli is not installed. Install adcli or choose auto.")
    if method not in {"auto", "realm", "adcli"}:
        return _result("failed", error="Linux join method must be auto, realm, or adcli.")

    # realmd does not accept an explicit DC. If the operator supplied one, use
    # adcli so the override is honored.
    use_realm = realm and method in {"auto", "realm"}
    if method == "auto" and values["server"] and adcli:
        use_realm = False
    if use_realm:
        args = [realm, "join", f"--user={values['username']}"]
        if values["ou"]:
            args.append(f"--computer-ou={values['ou']}")
        if values["computer_name"]:
            args.append(f"--computer-name={values['computer_name']}")
        args.append(values["domain"])
        result = _run_pty(
            args,
            password,
            timeout=timeout,
            max_output=max_output,
            env=os.environ.copy(),
            on_chunk=on_chunk,
        )
        if result.get("status") != "succeeded":
            return result
        listed = run_capture(
            [realm, "list", values["domain"]],
            timeout=30,
            max_output=max_output,
        )
        result["stdout"] = (
            str(result.get("stdout") or "").rstrip()
            + "\n"
            + str(listed.get("stdout") or listed.get("stderr") or "").strip()
        )
        if reboot:
            subprocess.Popen(["shutdown", "-r", "+1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result["stdout"] += "\nReboot scheduled in one minute."
        return result

    if adcli:
        args = [
            adcli,
            "join",
            "--stdin-password",
            f"--domain={values['domain']}",
            f"--login-user={values['username']}",
        ]
        if values["ou"]:
            args.append(f"--computer-ou={values['ou']}")
        if values["computer_name"]:
            args.append(f"--computer-name={values['computer_name']}")
        if values["server"]:
            args.append(f"--domain-controller={values['server']}")
        result = run_capture(
            args,
            timeout=timeout,
            input_text=password + "\n",
            max_output=max_output,
            env=os.environ.copy(),
            on_chunk=on_chunk,
        )
        result["stdout"] = _redact(str(result.get("stdout") or ""), password)
        result["stderr"] = _redact(str(result.get("stderr") or ""), password)
        if result.get("status") == "succeeded":
            systemctl = shutil.which("systemctl")
            if systemctl:
                started = run_capture(
                    [systemctl, "enable", "--now", "sssd"],
                    timeout=60,
                    max_output=max_output,
                )
                result["stdout"] = (
                    str(result.get("stdout") or "").rstrip()
                    + "\n"
                    + str(started.get("stdout") or started.get("stderr") or "").strip()
                )
            result["stdout"] += (
                "\nJoined with adcli. Verify SSSD and PAM configuration for directory logins."
            )
            if reboot:
                subprocess.Popen(
                    ["shutdown", "-r", "+1"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result["stdout"] += "\nReboot scheduled in one minute."
        return result

    return _result(
        "failed",
        error="Neither realmd nor adcli is installed. Install the domain packages first.",
    )


def _run_linux_status(*, timeout: int, max_output: int) -> dict[str, Any]:
    outputs: list[str] = []
    realm = shutil.which("realm")
    adcli = shutil.which("adcli")
    if realm:
        listed = run_capture([realm, "list"], timeout=timeout, max_output=max_output)
        outputs.append(f"$ realm list\n{listed.get('stdout') or listed.get('stderr') or ''}".strip())
    if adcli:
        tested = run_capture(
            [adcli, "testjoin"],
            timeout=timeout,
            max_output=max_output,
        )
        outputs.append(
            f"$ adcli testjoin\n{tested.get('stdout') or tested.get('stderr') or ''}".strip()
        )
    systemctl = shutil.which("systemctl")
    if systemctl:
        active = run_capture(
            [systemctl, "is-active", "sssd"],
            timeout=timeout,
            max_output=max_output,
        )
        outputs.append(f"$ systemctl is-active sssd\n{active.get('stdout') or active.get('stderr') or ''}".strip())
    if not outputs:
        return _result("failed", error="Neither realmd nor adcli is installed.")
    return _result("succeeded", exit_code=0, stdout="\n\n".join(outputs)[-max_output:])


def _run_windows_status(*, timeout: int, max_output: int) -> dict[str, Any]:
    script = r"""
$cs = Get-CimInstance Win32_ComputerSystem
Write-Output ("Domain: " + $cs.Domain)
Write-Output ("PartOfDomain: " + $cs.PartOfDomain)
if ($cs.PartOfDomain) { nltest /dsgetdc:$($cs.Domain) }
"""
    return run_capture(
        [
            shutil.which("powershell") or shutil.which("pwsh") or "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "-",
        ],
        timeout=timeout,
        input_text="[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script,
        max_output=max_output,
    )


def run_domain(
    args: dict[str, Any],
    *,
    allow_packages: bool = False,
    timeout: int = 300,
    max_output: int = 200_000,
    on_chunk: ChunkCallback | None = None,
) -> dict[str, Any]:
    action = str(args.get("action") or "join").strip().lower()
    if action not in {"join", "status"}:
        return _result("failed", error="Domain action must be join or status.")
    if action == "status":
        try:
            if sys.platform.startswith("win"):
                return _run_windows_status(timeout=min(timeout, 60), max_output=max_output)
            return _run_linux_status(timeout=min(timeout, 60), max_output=max_output)
        except OSError as exc:
            return _result("failed", error=f"Could not check domain status: {exc}")

    try:
        values, _domain = _validate(args, need_password=True)
    except ValueError as exc:
        return _result("failed", error=str(exc))
    install_prerequisites = bool(args.get("install_prerequisites"))
    reboot = bool(args.get("reboot"))
    flush_dns = bool(args.get("flush_dns", True))
    if install_prerequisites and not allow_packages:
        return _result(
            "failed",
            error="Package installation is blocked by this agent's profile or server policy.",
        )
    dns_messages = _flush_dns(on_chunk) if flush_dns else []
    try:
        if sys.platform.startswith("win"):
            result = _run_windows_join(
                values,
                values["password"],
                reboot=reboot,
                timeout=timeout,
                max_output=max_output,
                on_chunk=on_chunk,
            )
        else:
            result = _run_linux_join(
                values,
                values["password"],
                method=str(args.get("method") or "auto").strip().lower(),
                install_prerequisites=install_prerequisites,
                reboot=reboot,
                timeout=timeout,
                max_output=max_output,
                on_chunk=on_chunk,
            )
        if dns_messages:
            result["stdout"] = "\n".join(dns_messages) + "\n" + str(result.get("stdout") or "")
        return result
    except OSError as exc:
        return _result("failed", error=f"Domain join failed to start: {exc}")

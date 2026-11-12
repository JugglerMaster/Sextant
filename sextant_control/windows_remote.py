"""Credential-based Windows agent install over WinRM/HTTPS.

The optional ``windows-remote`` extra supplies pywinrm. Credentials and the
one-time enrollment token exist only in the job request/thread and are redacted
from every log/error path. The transport is HTTPS-only and validates the target
certificate by default.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import ipaddress
from pathlib import Path
from typing import Any, Callable

WINDOWS_INSTALL_SCRIPT = Path(__file__).with_name("windows_install.ps1")
MAX_WHEEL_BYTES = 50 * 1024 * 1024
UPLOAD_CHUNK_CHARS = 48_000
REMOTE_ROOT = r"C:\ProgramData\SextantAgent\install"


class WindowsRemoteError(RuntimeError):
    pass


def windows_remote_available() -> bool:
    try:
        __import__("winrm")
        return True
    except ImportError:
        return False


def _load_winrm() -> Any:
    try:
        import winrm  # type: ignore[import-not-found]
    except ImportError:
        raise WindowsRemoteError(
            "Windows remote install requires the optional 'windows-remote' extra "
            "(pip install 'sextant[windows-remote]')."
        ) from None
    return winrm


def _validate_target(host: str, port: int, user: str) -> None:
    allowed_host = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:")
    if not host or any(ch not in allowed_host for ch in host):
        raise WindowsRemoteError("Host must be an IP address or DNS name.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_global or not address.is_private):
        raise WindowsRemoteError("Windows remote install only targets private addresses.")
    if not 1 <= int(port) <= 65535:
        raise WindowsRemoteError("Port must be between 1 and 65535.")
    if not user or len(user) > 128:
        raise WindowsRemoteError("A Windows username is required.")
    if any(not (ch.isalnum() or ch in "._@\\-") for ch in user):
        raise WindowsRemoteError("Windows username contains invalid characters.")


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _redact(text: str, secrets: list[str]) -> str:
    value = str(text or "")
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***")
    return value[-4000:]


def _installer_script() -> str:
    try:
        return WINDOWS_INSTALL_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        raise WindowsRemoteError("The packaged Windows installer script is missing.") from None


class WinRMClient:
    """Small wrapper around pywinrm with bounded chunked file upload."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        *,
        ca_file: str = "",
        timeout: int = 60,
    ) -> None:
        _validate_target(host, port, user)
        if not password:
            raise WindowsRemoteError("A Windows password is required.")
        winrm = _load_winrm()
        target = f"https://{host}:{int(port)}/wsman"
        kwargs: dict[str, Any] = {
            "auth": (user, password),
            "transport": "ntlm",
            "server_cert_validation": "validate",
            "operation_timeout_sec": max(10, min(300, int(timeout))),
            "read_timeout_sec": max(20, min(330, int(timeout) + 10)),
        }
        if ca_file:
            try:
                signature = inspect.signature(winrm.Session)
            except (TypeError, ValueError):
                signature = None
            if signature is None or "ca_trust_path" not in signature.parameters:
                raise WindowsRemoteError(
                    "This pywinrm build does not support a custom CA file. "
                    "Install the CA in the server trust store instead."
                )
            kwargs["ca_trust_path"] = ca_file
        try:
            self.session = winrm.Session(target, **kwargs)
        except Exception as exc:  # never include the credential in the message
            raise WindowsRemoteError(f"Could not initialize WinRM: {type(exc).__name__}") from None

    def run_ps(self, script: str) -> str:
        try:
            result = self.session.run_ps(script)
        except Exception as exc:
            raise WindowsRemoteError(f"WinRM command failed: {type(exc).__name__}") from None
        status = int(getattr(result, "status_code", 1))
        stdout = str(getattr(result, "std_out", "") or "")
        stderr = str(getattr(result, "std_err", "") or "")
        if status != 0:
            raise WindowsRemoteError((stderr or stdout or f"PowerShell exited {status}")[-4000:])
        return stdout

    def upload_file(self, remote_path: str, data: bytes) -> None:
        if len(data) > MAX_WHEEL_BYTES:
            raise WindowsRemoteError("Remote file exceeds the upload limit.")
        b64_path = remote_path + ".b64"
        self.run_ps(f"[IO.File]::WriteAllText({_ps_quote(b64_path)}, '')")
        encoded = base64.b64encode(data).decode("ascii")
        for offset in range(0, len(encoded), UPLOAD_CHUNK_CHARS):
            chunk = encoded[offset : offset + UPLOAD_CHUNK_CHARS]
            self.run_ps(
                f"[IO.File]::AppendAllText({_ps_quote(b64_path)}, {_ps_quote(chunk)})"
            )
        self.run_ps(
            "[IO.File]::WriteAllBytes("
            f"{_ps_quote(remote_path)}, [Convert]::FromBase64String("
            f"[IO.File]::ReadAllText({_ps_quote(b64_path)}))); "
            f"Remove-Item -LiteralPath {_ps_quote(b64_path)} -Force"
        )


def _client_factory(
    host: str,
    port: int,
    user: str,
    password: str,
    ca_file: str,
    timeout: int,
) -> WinRMClient:
    return WinRMClient(
        host,
        port,
        user,
        password,
        ca_file=ca_file,
        timeout=timeout,
    )


def test_connection(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    ca_file: str = "",
    timeout: int = 60,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    _validate_target(host, port, user)
    if not password:
        raise WindowsRemoteError("A Windows password is required.")
    factory = client_factory or _client_factory
    client = factory(host, port, user, password, ca_file, timeout)
    script = r"""
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The WinRM session is not elevated.'
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
} else {
    throw 'Python 3.11+ is required.'
}
Write-Output 'admin=ok python=ok'
"""
    output = client.run_ps(script).strip()
    return _redact(output, [password])


def run_install(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    server_url: str,
    token: str,
    name: str,
    profile: str,
    watch_services: list[str],
    wheel_path: str,
    ca_file: str = "",
    timeout: int = 600,
    log: Callable[[str], None] = lambda _line: None,
    client_factory: Callable[..., Any] | None = None,
) -> None:
    _validate_target(host, port, user)
    if not password:
        raise WindowsRemoteError("A Windows password is required.")
    wheel = Path(wheel_path)
    if not wheel.is_file():
        raise WindowsRemoteError("Agent wheel is missing from the package store.")
    if wheel.stat().st_size > MAX_WHEEL_BYTES:
        raise WindowsRemoteError("Agent wheel exceeds the Windows upload limit.")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    installer = _installer_script()
    factory = client_factory or _client_factory
    client = factory(host, port, user, password, ca_file, timeout)
    redactions = [password, token]
    remote_wheel = REMOTE_ROOT + r"\sextant-agent.whl"
    remote_installer = REMOTE_ROOT + r"\install-windows-agent.ps1"
    try:
        log(_redact(f"Preparing WinRM session on {user}@{host}:{port}", redactions))
        client.run_ps("New-Item -ItemType Directory -Path " + _ps_quote(REMOTE_ROOT) + " -Force | Out-Null")
        log("Uploading agent wheel and installer")
        client.upload_file(remote_wheel, wheel.read_bytes())
        client.upload_file(remote_installer, installer.encode("utf-8"))
        service_args = " ".join(
            "-WatchServices " + ",".join(_ps_quote(item) for item in watch_services[:64])
            if watch_services
            else ""
        )
        script = f"""$ErrorActionPreference = 'Stop'
$env:SEXTANT_ENROLL_TOKEN = {_ps_quote(token)}
try {{
  & {_ps_quote(remote_installer)} -Server {_ps_quote(server_url)} -Token $env:SEXTANT_ENROLL_TOKEN -Name {_ps_quote(name)} -SourcePath {_ps_quote(remote_wheel)} -Profile {_ps_quote(profile)} -ExpectedSha256 {_ps_quote(digest)}{(' ' + service_args) if service_args else ''}
}} finally {{
  Remove-Item Env:SEXTANT_ENROLL_TOKEN -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath {_ps_quote(remote_wheel)} -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath {_ps_quote(remote_installer)} -Force -ErrorAction SilentlyContinue
}}
"""
        output = client.run_ps(script)
        if output:
            log(_redact(output, redactions))
    except WindowsRemoteError as exc:
        raise WindowsRemoteError(_redact(str(exc), redactions)) from None
    except Exception as exc:  # never echo a credential or token
        raise WindowsRemoteError(_redact(f"{type(exc).__name__}: {exc}", redactions)) from None
    log("Windows agent install and enrollment completed.")

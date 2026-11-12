from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from . import updates

DEFAULT_RELEASE_ROOT = Path("/opt/sextant/releases")
DEFAULT_CURRENT_LINK = Path("/opt/sextant/current")
DEFAULT_RESTART_TRIGGER = Path("/var/lib/sextant/restart-request")
USER_AGENT = f"sextant/{__version__}"
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+~-]{0,63}$")
_GITHUB_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}


class UpdateError(RuntimeError):
    """A safe, user-facing self-update failure."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(updates.sanitize_error(message))
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_version(version: str) -> str:
    value = str(version or "").strip().lstrip("vV")
    if not value or not _VERSION_RE.fullmatch(value):
        raise UpdateError("Invalid release version.", 400)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(
        args,
        completed.returncode,
        completed.stdout.decode("utf-8", errors="replace"),
        completed.stderr.decode("utf-8", errors="replace"),
    )


def _is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def _host_allowed(host: str | None, allowed_hosts: set[str]) -> bool:
    if not host:
        return False
    host = host.casefold().rstrip(".")
    if host in allowed_hosts:
        return True
    for allowed in allowed_hosts:
        if allowed.startswith(".") and host.endswith(allowed):
            return True
    return False


def validate_download_url(
    url: str,
    *,
    allowed_hosts: set[str],
    initial_scheme: str = "https",
) -> str:
    """Reject unexpected schemes, credentials, and redirect hosts."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        raise UpdateError("The release artifact URL uses an unsupported scheme.", 400)
    if parsed.username or parsed.password:
        raise UpdateError("The release artifact URL contains credentials.", 400)
    if not parsed.hostname:
        raise UpdateError("The release artifact URL has no host.", 400)
    if initial_scheme == "https" and parsed.scheme != "https":
        raise UpdateError("The release artifact download attempted an insecure redirect.", 400)
    if not _host_allowed(parsed.hostname, allowed_hosts):
        raise UpdateError("The release artifact URL redirected to an unexpected host.", 400)
    return str(url)


def _allowed_hosts(url: str) -> set[str]:
    host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    if not host:
        return set()
    if host in _GITHUB_HOSTS or host.endswith(".githubusercontent.com") or host.endswith(".github.com"):
        return set(_GITHUB_HOSTS)
    return {host}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str], initial_scheme: str) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.initial_scheme = initial_scheme

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_download_url(
            newurl,
            allowed_hosts=self.allowed_hosts,
            initial_scheme=self.initial_scheme,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_artifact(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    max_bytes: int,
    timeout: int = 60,
) -> dict[str, Any]:
    """Download with stdlib, bounded by size, and verify SHA-256 and size."""
    if expected_size <= 0:
        raise UpdateError("The release does not publish the artifact size.", 400)
    if expected_size > max_bytes:
        raise UpdateError("The release artifact is larger than the configured download limit.", 400)
    parsed = urllib.parse.urlsplit(str(url or ""))
    allowed = _allowed_hosts(url)
    validate_download_url(
        url,
        allowed_hosts=allowed,
        initial_scheme=parsed.scheme if parsed.scheme in {"http", "https"} else "https",
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler(allowed, parsed.scheme))
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    digest = hashlib.sha256()
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            validate_download_url(
                final_url,
                allowed_hosts=allowed,
                initial_scheme=parsed.scheme,
            )
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise UpdateError(
                            "The release artifact is larger than the configured download limit.",
                            400,
                        )
                except ValueError:
                    pass
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateError(
                            "The release artifact exceeded the configured download limit.",
                            400,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
    except UpdateError:
        destination.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        destination.unlink(missing_ok=True)
        if 300 <= int(exc.code) < 400:
            raise UpdateError(
                "The release artifact redirect was rejected (unexpected scheme or host).",
                400,
            ) from exc
        raise UpdateError(f"Download failed: {exc}", 502) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}", 502) from exc
    actual_sha256 = digest.hexdigest()
    if total != expected_size:
        destination.unlink(missing_ok=True)
        raise UpdateError(
            f"Downloaded artifact size {total} does not match the expected {expected_size}.",
            400,
        )
    if actual_sha256.casefold() != str(expected_sha256).casefold():
        destination.unlink(missing_ok=True)
        raise UpdateError("Downloaded artifact failed SHA-256 verification.", 400)
    return {"size": total, "sha256": actual_sha256}


def _venv_bin(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def stage_venv(
    version: str,
    wheel: Path,
    release_root: Path,
    *,
    python: str | None = None,
    timeout: int = 900,
) -> Path:
    version = validate_version(version)
    release_root = Path(release_root)
    release_dir = release_root / version
    venv_dir = release_dir / "venv"
    release_dir.mkdir(parents=True, exist_ok=True)
    if venv_dir.exists():
        import shutil

        shutil.rmtree(venv_dir)
    created = _run([python or sys.executable, "-m", "venv", str(venv_dir)], timeout=300)
    if created.returncode != 0:
        raise UpdateError(
            f"Could not create the staging venv: {created.stderr[-500:]}",
            500,
        )
    pip = _venv_bin(venv_dir, "pip")
    # Carry installed optional extras into the new release venv. The server
    # can only inspect its own environment, which is exactly what we want.
    extras: list[str] = []
    for module, requirement in (
        ("ldap3", "ldap3>=2.9,<3"),
        ("winrm", "pywinrm>=0.5,<1"),
    ):
        if importlib.util.find_spec(module) is not None:
            extras.append(requirement)
    installed = _run(
        [
            str(pip),
            "install",
            "--disable-pip-version-check",
            "--no-input",
            str(wheel),
            *extras,
        ],
        timeout=timeout,
    )
    if installed.returncode != 0:
        import shutil

        shutil.rmtree(release_dir, ignore_errors=True)
        raise UpdateError(
            f"Could not install the staged wheel: {installed.stderr[-800:]}",
            500,
        )
    return venv_dir


def health_check(venv_dir: Path, expected_version: str, *, timeout: int = 60) -> tuple[bool, str]:
    """Run the staged CLI and verify the imported package version."""
    exe = _venv_bin(venv_dir, "sextant")
    if not exe.exists():
        return False, f"Staged CLI is missing: {exe}"
    checked = _run([str(exe), "--help"], timeout=timeout)
    if checked.returncode != 0:
        return False, f"sextant --help failed: {checked.stderr[-500:]}"
    python = _venv_bin(venv_dir, "python")
    version_check = _run(
        [
            str(python),
            "-c",
            "from sextant_control import __version__; print(__version__)",
        ],
        timeout=timeout,
    )
    if version_check.returncode != 0:
        return False, f"Staged import failed: {version_check.stderr[-500:]}"
    actual = version_check.stdout.strip().lstrip("vV")
    expected = str(expected_version).strip().lstrip("vV")
    if actual != expected:
        return False, f"Staged version {actual or 'unknown'} does not match {expected}."
    return True, ""


def _link_target(path: Path) -> Path | None:
    try:
        raw = os.readlink(path)
    except OSError:
        try:
            return path.resolve()
        except OSError:
            return None
    target = Path(raw)
    if not target.is_absolute():
        target = path.parent / target
    try:
        return target.resolve()
    except OSError:
        return target


def switch_current(current_link: Path, target: Path) -> Path | None:
    """Atomically repoint the stable current symlink and return its old target."""
    current_link = Path(current_link)
    target = Path(target)
    if not target.is_dir():
        raise UpdateError(f"Staged release is missing: {target}", 500)
    current_link.parent.mkdir(parents=True, exist_ok=True)
    previous = _link_target(current_link) if os.path.lexists(current_link) else None
    temporary = current_link.parent / (
        f".current-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        if os.path.lexists(temporary):
            temporary.unlink()
        temporary.symlink_to(target.resolve(), target_is_directory=True)
        os.replace(temporary, current_link)
    except OSError as exc:
        try:
            if os.path.lexists(temporary):
                temporary.unlink()
        except OSError:
            pass
        raise UpdateError(f"Could not switch the current release: {exc}", 500) from exc
    return previous


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def restart_pending(trigger_path: Path) -> bool:
    try:
        return Path(trigger_path).exists()
    except OSError:
        return False


def request_restart(
    trigger_path: Path,
    *,
    version: str,
    action: str,
    previous_version: str = "",
) -> Path:
    """Write an atomic restart trigger for the root-owned systemd path unit."""
    trigger_path = Path(trigger_path)
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "requested_at": _now(),
        "action": action,
        "version": str(version or "")[:64],
        "previous_version": str(previous_version or "")[:64],
    }
    temporary = trigger_path.with_name(f".{trigger_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, trigger_path)
    try:
        trigger_path.chmod(0o600)
    except OSError:
        pass
    return trigger_path


def _within(path: Path, root: Path) -> bool:
    try:
        resolved = Path(path).resolve()
        root_resolved = Path(root).resolve()
        return os.path.commonpath([str(resolved), str(root_resolved)]) == str(root_resolved)
    except (OSError, ValueError):
        return False


def apply_update(
    info: dict[str, Any],
    *,
    version: str = "",
    release_root: Path,
    current_link: Path,
    trigger_path: Path,
    state_path: Path,
    max_bytes: int,
    current_version: str | None = None,
    download_timeout: int = 60,
    downloader: Callable[..., dict[str, Any]] | None = None,
    stager: Callable[..., Path] | None = None,
    health: Callable[..., tuple[bool, str]] | None = None,
    switcher: Callable[[Path, Path], Path | None] | None = None,
    trigger: Callable[..., Path] | None = None,
) -> dict[str, Any]:
    downloader = downloader or download_artifact
    stager = stager or stage_venv
    health = health or health_check
    switcher = switcher or switch_current
    trigger = trigger or request_restart
    target = validate_version(version or str(info.get("version") or ""))
    available = bool(info.get("update_available", info.get("available", False)))
    if not available:
        raise UpdateError("No newer release is available.", 409)
    latest = str(info.get("latest_version") or info.get("version") or "").strip()
    if latest and updates.parse_version(target) != updates.parse_version(latest):
        raise UpdateError(
            f"Target version {target} is not the latest available release ({latest}).",
            409,
        )
    running = str(current_version or info.get("current_version") or __version__)
    if updates.parse_version(target) <= updates.parse_version(running):
        raise UpdateError(f"Version {target} is already installed.", 409)
    asset = info.get("selected_asset")
    if not isinstance(asset, dict):
        asset = updates.select_server_asset(
            info.get("assets") or [], target
        )
    if not asset and info.get("sha256"):
        asset = updates.normalize_asset(
            {
                "name": info.get("filename") or info.get("artifact_name") or "",
                "url": info.get("download_url") or info.get("artifact_url") or "",
                "sha256": info.get("sha256"),
                "size": info.get("size") or 0,
            },
            version=target,
        )
    if not asset:
        raise UpdateError("The release does not contain a server wheel asset.", 409)
    url = str(asset.get("url") or "").strip()
    sha256 = str(asset.get("sha256") or "").strip().casefold()
    try:
        size = int(asset.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if not url:
        raise UpdateError("The server wheel asset has no download URL.", 409)
    if not sha256:
        raise UpdateError(
            "The release does not publish a SHA-256 digest for the server wheel.",
            409,
        )
    if size <= 0:
        raise UpdateError("The release does not publish the server wheel size.", 409)
    if size > max_bytes:
        raise UpdateError(
            "The server wheel is larger than the configured download limit.", 409
        )

    release_root = Path(release_root)
    release_dir = release_root / target
    filename = Path(str(asset.get("name") or f"sextant-{target}-py3-none-any.whl")).name
    with tempfile.TemporaryDirectory(prefix="sextant-server-update-") as temp:
        wheel = Path(temp) / filename
        downloader(
            url,
            wheel,
            expected_size=size,
            expected_sha256=sha256,
            max_bytes=max_bytes,
            timeout=download_timeout,
        )
        try:
            venv_dir = stager(target, wheel, release_root)
        except UpdateError:
            import shutil

            shutil.rmtree(release_dir, ignore_errors=True)
            raise
        ok, detail = health(venv_dir, target)
        if not ok:
            import shutil

            shutil.rmtree(release_dir, ignore_errors=True)
            raise UpdateError(
                f"Staged server failed its health check; the current release is unchanged. {detail}",
                500,
            )

    previous = switcher(current_link, venv_dir)
    state = {
        "version": target,
        "previous_version": running,
        "previous_target": str(previous) if previous else "",
        "current_target": str(venv_dir.resolve()),
        "sha256": sha256,
        "size": size,
        "filename": filename,
        "applied_at": _now(),
    }
    try:
        write_state(state_path, state)
    except OSError as exc:
        if previous is not None:
            try:
                switcher(current_link, previous)
            except UpdateError:
                pass
        raise UpdateError(
            f"Could not record the update state; the current release was left unchanged: {exc}",
            500,
        ) from exc
    restart_requested = True
    restart_error = ""
    try:
        trigger(
            trigger_path,
            version=target,
            action="apply",
            previous_version=running,
        )
    except OSError as exc:
        restart_requested = False
        restart_error = updates.sanitize_error(exc)
    return {
        "status": "switched" if restart_requested else "restart_required",
        "version": target,
        "previous_version": running,
        "previous_target": str(previous) if previous else "",
        "current_target": str(venv_dir.resolve()),
        "sha256": sha256,
        "size": size,
        "restart_required": True,
        "restarting": restart_requested,
        "restart_requested": restart_requested,
        "restart_trigger": str(trigger_path),
        "restart_error": restart_error,
        "instructions": (
            "The new release is live after the service restarts."
            if restart_requested
            else "Restart the sextant service manually to start the new release."
        ),
    }


def rollback_update(
    *,
    release_root: Path,
    current_link: Path,
    trigger_path: Path,
    state_path: Path,
    trigger: Callable[..., Path] | None = None,
    switcher: Callable[[Path, Path], Path | None] | None = None,
) -> dict[str, Any]:
    trigger = trigger or request_restart
    switcher = switcher or switch_current
    state = read_state(state_path)
    previous_raw = str(state.get("previous_target") or "").strip()
    if not previous_raw:
        raise UpdateError("No previous release is recorded for rollback.", 409)
    previous = Path(previous_raw)
    if not _within(previous, release_root):
        raise UpdateError("The recorded previous release is outside the release root.", 409)
    if not previous.is_dir():
        raise UpdateError("The previous release is no longer on disk.", 409)
    current = _link_target(current_link)
    if current is not None and current == previous:
        raise UpdateError("The current release is already the recorded previous release.", 409)
    switcher(current_link, previous)
    rolled_back_from = str(state.get("version") or "")
    new_state = {
        **state,
        "version": str(state.get("previous_version") or ""),
        "previous_version": rolled_back_from,
        "previous_target": str(current) if current else "",
        "current_target": str(previous.resolve()),
        "rolled_back_at": _now(),
    }
    try:
        write_state(state_path, new_state)
    except OSError as exc:
        if current is not None:
            try:
                switcher(current_link, current)
            except UpdateError:
                pass
        raise UpdateError(
            f"Could not record the rollback state; the current release was left unchanged: {exc}",
            500,
        ) from exc
    restart_requested = True
    restart_error = ""
    try:
        trigger(
            trigger_path,
            version=str(new_state.get("version") or ""),
            action="rollback",
            previous_version=rolled_back_from,
        )
    except OSError as exc:
        restart_requested = False
        restart_error = updates.sanitize_error(exc)
    return {
        "status": "rolled_back" if restart_requested else "restart_required",
        "version": str(new_state.get("version") or ""),
        "rolled_back_from": rolled_back_from,
        "previous_target": str(previous.resolve()),
        "restart_required": True,
        "restarting": restart_requested,
        "restart_requested": restart_requested,
        "restart_trigger": str(trigger_path),
        "restart_error": restart_error,
        "instructions": (
            "The previous release is live after the service restarts."
            if restart_requested
            else "Restart the sextant service manually to start the previous release."
        ),
    }

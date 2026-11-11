from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .client import AgentClient
from .config import AgentConfig

DEFAULT_LINUX_INSTALL_DIR = Path("/opt/sextant-agent")
DEFAULT_WINDOWS_INSTALL_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "SextantAgent"


def default_install_dir() -> Path:
    if os.name == "nt":
        return DEFAULT_WINDOWS_INSTALL_DIR
    return DEFAULT_LINUX_INSTALL_DIR


def check(config: AgentConfig) -> dict[str, Any]:
    client = AgentClient(config)
    info = client.get_update(__version__)
    info.setdefault("current_version", __version__)
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _stage_venv(version: str, wheel: Path, install_dir: Path) -> Path:
    release_dir = install_dir / "releases" / version
    venv_dir = release_dir / "venv"
    release_dir.mkdir(parents=True, exist_ok=True)
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    created = _run([sys.executable, "-m", "venv", str(venv_dir)])
    if created.returncode != 0:
        raise RuntimeError(f"Could not create the staging venv: {created.stderr[-500:]}")
    pip = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    installed = _run([str(pip), "install", "--no-deps", str(wheel)])
    if installed.returncode != 0:
        raise RuntimeError(f"Could not install the staged wheel: {installed.stderr[-800:]}")
    return venv_dir


def _health_check(venv_dir: Path, config_path: Path) -> bool:
    exe = venv_dir / ("Scripts/sextant-agent.exe" if os.name == "nt" else "bin/sextant-agent")
    if not exe.exists():
        return False
    checked = _run([str(exe), "show", "--config", str(config_path)], timeout=60)
    return checked.returncode == 0


def _switch_current(install_dir: Path, version: str) -> None:
    current = install_dir / "current"
    legacy = install_dir / ".venv"
    if not current.exists() and legacy.exists():
        current.symlink_to(".venv")
    target = Path("releases") / version / "venv"
    temporary = install_dir / f".current-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, current)


def _prune_releases(install_dir: Path, keep: int = 3) -> None:
    releases = install_dir / "releases"
    if not releases.exists():
        return
    items = sorted(
        [path for path in releases.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in items[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def apply_update(
    config: AgentConfig,
    info: dict[str, Any],
    *,
    install_dir: Path | None = None,
    config_path: Path | None = None,
    restart: bool = False,
) -> dict[str, Any]:
    if not config.allow_self_update:
        return {"status": "disabled", "error": "allow_self_update is false in the agent config."}
    if not info.get("available"):
        return {"status": "current", "version": info.get("latest_version") or __version__}

    version = str(info.get("version", ""))
    sha256 = str(info.get("sha256", ""))
    size = int(info.get("size") or 0)
    download_url = str(info.get("download_url", ""))
    if not version or not sha256 or not download_url:
        return {"status": "failed", "error": "Server update metadata is incomplete."}

    install_dir = Path(install_dir) if install_dir else default_install_dir()
    config_path = Path(config_path) if config_path else _config_path()
    client = AgentClient(config)
    with tempfile.TemporaryDirectory(prefix="sextant-agent-update-") as temp:
        wheel = Path(temp) / str(info.get("filename") or f"sextant-{version}-py3-none-any.whl")
        client.download(download_url, wheel, max_bytes=max(1024 * 1024, size + 1024 * 1024))
        actual_size = wheel.stat().st_size
        actual_sha = _sha256(wheel)
        if actual_size != size or actual_sha != sha256:
            return {
                "status": "failed",
                "error": "Downloaded artifact failed SHA-256/size verification.",
                "expected_sha256": sha256,
                "actual_sha256": actual_sha,
            }
        venv_dir = _stage_venv(version, wheel, install_dir)
        if not _health_check(venv_dir, config_path):
            shutil.rmtree(venv_dir.parent, ignore_errors=True)
            return {"status": "failed", "error": "Staged agent failed its health check; current version left unchanged."}

    if os.name == "nt":
        return {
            "status": "staged",
            "version": version,
            "restart_required": True,
            "instructions": (
                "The new version is staged under the install directory. "
                "Update the SextantAgent scheduled task to point at the new release and restart it. "
                "Automatic Windows switching is not enabled yet."
            ),
        }

    _switch_current(install_dir, version)
    _prune_releases(install_dir)
    result: dict[str, Any] = {
        "status": "switched",
        "version": version,
        "install_dir": str(install_dir),
        "restart_required": True,
    }
    if restart:
        restarted = _run(["systemctl", "restart", "sextant-agent"], timeout=60)
        result["restart_required"] = restarted.returncode != 0
        if restarted.returncode != 0:
            result["restart_error"] = restarted.stderr[-500:]
    return result


def _config_path() -> Path:
    from .config import default_config_path

    return default_config_path()


def report(info: dict[str, Any]) -> str:
    if info.get("available"):
        return (
            f"Update available: {info.get('current_version')} -> {info.get('version')} "
            f"({info.get('filename')}, {info.get('size')} bytes)"
        )
    if info.get("latest_version"):
        return f"Agent is current ({info.get('latest_version')})."
    return "No agent release is available in the store."

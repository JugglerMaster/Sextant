from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import __version__
from .client import AgentClient
from .config import AgentConfig

DEFAULT_LINUX_INSTALL_DIR = Path("/opt/sextant-agent")
DEFAULT_WINDOWS_INSTALL_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "SextantAgent"
WINDOWS_TASK_NAME = "SextantAgent"


def default_install_dir() -> Path:
    if _is_windows():
        return DEFAULT_WINDOWS_INSTALL_DIR
    return DEFAULT_LINUX_INSTALL_DIR


def _is_windows() -> bool:
    return os.name == "nt"


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
        _decode_output(completed.stdout),
        _decode_output(completed.stderr),
    )


def _decode_output(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "mbcs" if _is_windows() else "utf-8"
        return data.decode(encoding, errors="replace")


def _stage_venv(version: str, wheel: Path, install_dir: Path) -> Path:
    release_dir = install_dir / "releases" / version
    venv_dir = release_dir / "venv"
    release_dir.mkdir(parents=True, exist_ok=True)
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    created = _run([sys.executable, "-m", "venv", str(venv_dir)])
    if created.returncode != 0:
        raise RuntimeError(f"Could not create the staging venv: {created.stderr[-500:]}")
    pip = venv_dir / ("Scripts/pip.exe" if _is_windows() else "bin/pip")
    installed = _run([str(pip), "install", "--no-deps", str(wheel)])
    if installed.returncode != 0:
        raise RuntimeError(f"Could not install the staged wheel: {installed.stderr[-800:]}")
    return venv_dir


def _health_check(venv_dir: Path, config_path: Path) -> bool:
    exe = venv_dir / ("Scripts/sextant-agent.exe" if _is_windows() else "bin/sextant-agent")
    if not exe.exists():
        return False
    checked = _run([str(exe), "show", "--config", str(config_path)], timeout=60)
    return checked.returncode == 0


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


def _create_windows_junction(link: Path, target: Path) -> None:
    result = _run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], timeout=30)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "mklink failed")[-500:])


def _remove_windows_junction(path: Path) -> None:
    if not os.path.lexists(path):
        return
    result = _run(["cmd.exe", "/d", "/c", "rmdir", str(path)], timeout=30)
    if result.returncode == 0:
        return
    try:
        os.rmdir(path)
    except OSError:
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError(f"Could not remove {path}: {exc}") from exc


def _ensure_windows_current(install_dir: Path) -> Path:
    current = install_dir / "current"
    if os.path.lexists(current):
        return current
    legacy = install_dir / "venv"
    if not legacy.exists():
        raise RuntimeError(
            f"Neither {current} nor the legacy {legacy} exists; reinstall the agent."
        )
    _create_windows_junction(current, legacy.resolve())
    return current


def _switch_windows_current(install_dir: Path, version: str) -> Path | None:
    current = install_dir / "current"
    target = install_dir / "releases" / version / "venv"
    if not target.exists():
        raise RuntimeError(f"Staged release is missing: {target}")
    _ensure_windows_current(install_dir)
    previous = _link_target(current) if os.path.lexists(current) else None
    _remove_windows_junction(current)
    try:
        _create_windows_junction(current, target.resolve())
    except Exception:
        if previous is not None:
            _create_windows_junction(current, previous)
        raise
    return previous


def _query_windows_task() -> tuple[str, str, str] | None:
    """Return (command, arguments, working_directory) for the agent task."""
    result = _run(["schtasks.exe", "/Query", "/TN", WINDOWS_TASK_NAME, "/XML"], timeout=30)
    if result.returncode != 0:
        return None
    if not result.stdout.strip():
        raise RuntimeError("schtasks returned no XML for the SextantAgent task.")
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError as exc:
        raise RuntimeError(f"Could not parse the SextantAgent task XML: {exc}") from exc
    exec_element = next(
        (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "Exec"),
        None,
    )
    if exec_element is None:
        return None
    values: dict[str, str] = {}
    for child in exec_element:
        values[child.tag.rsplit("}", 1)[-1]] = (child.text or "").strip()
    command = values.get("Command", "")
    if not command:
        return None
    return command, values.get("Arguments", ""), values.get("WorkingDirectory", "")


def _update_windows_task_action(install_dir: Path) -> tuple[bool, str]:
    try:
        task = _query_windows_task()
    except RuntimeError as exc:
        return False, str(exc)
    if task is None:
        return True, "Scheduled task SextantAgent was not found; switch completed, restart manually."
    command, arguments, _working_directory = task
    desired = install_dir / "current" / "Scripts" / "sextant-agent.exe"
    if command.strip('"').casefold() == str(desired).casefold():
        return True, "Scheduled task already uses current."
    run = f'"{desired}"{(" " + arguments) if arguments else ""}'
    result = _run(
        ["schtasks.exe", "/Change", "/TN", WINDOWS_TASK_NAME, "/TR", run],
        timeout=30,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "schtasks /Change failed")[-500:]
    return True, "Scheduled task action updated."


def _restart_windows_task() -> tuple[bool, str]:
    _run(["schtasks.exe", "/End", "/TN", WINDOWS_TASK_NAME], timeout=30)
    time.sleep(1)
    result = _run(["schtasks.exe", "/Run", "/TN", WINDOWS_TASK_NAME], timeout=30)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "schtasks /Run failed")[-500:]
    return True, ""


def _switch_current(install_dir: Path, version: str) -> None:
    if _is_windows():
        _switch_windows_current(install_dir, version)
        return
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
    current_target = _link_target(install_dir / "current")
    items = sorted(
        [path for path in releases.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in items[keep:]:
        if current_target is not None:
            try:
                if (stale / "venv").resolve() == current_target:
                    continue
            except OSError:
                pass
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

    if _is_windows():
        try:
            _ensure_windows_current(install_dir)
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
        task_ok, task_message = _update_windows_task_action(install_dir)
        if not task_ok:
            return {"status": "failed", "error": task_message}
        try:
            _switch_current(install_dir, version)
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
        _prune_releases(install_dir)
        result: dict[str, Any] = {
            "status": "switched",
            "version": version,
            "install_dir": str(install_dir),
            "restart_required": True,
            "task": task_message,
        }
        if restart:
            restarted, restart_error = _restart_windows_task()
            result["restart_required"] = not restarted
            if not restarted:
                result["restart_error"] = restart_error
        else:
            result["instructions"] = "Restart the SextantAgent scheduled task to start the new version."
        return result

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

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

from .config import AgentConfig

PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@-]{0,127}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~-]{0,127}$")
MANAGERS = {"apt", "dnf", "winget", "choco"}
ACTIONS = {"install", "remove", "upgrade"}
MAX_ACTIONS = 25


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    try:
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
    except (OSError, subprocess.TimeoutExpired):
        return None


def detect_managers() -> list[dict[str, str]]:
    detected: list[dict[str, str]] = []
    checks = [
        ("apt", "dpkg-query", ["--version"]),
        ("dnf", "dnf", ["--version"]),
        ("winget", "winget", ["--version"]),
        ("choco", "choco", ["--version"]),
    ]
    for name, binary, version_args in checks:
        exe = shutil.which(binary)
        if not exe:
            continue
        result = _run([exe, *version_args], timeout=10)
        version = ""
        if result and result.stdout:
            version = result.stdout.splitlines()[0].strip()[:128]
        detected.append({"name": name, "version": version})
    return detected


def _parse_winget(output: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(("name ", "---", "no installed package")):
            continue
        parts = [part.strip() for part in re.split(r"\s{2,}", stripped) if part.strip()]
        if len(parts) < 3:
            continue
        package_id = parts[1]
        if not PACKAGE_RE.match(package_id):
            continue
        packages.append(
            {
                "manager": "winget",
                "package": package_id[:128],
                "version": parts[2][:128],
                "arch": "",
                "source": parts[4][:128] if len(parts) > 4 else "",
            }
        )
    return packages


def collect_inventory(limit: int = 5000) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if shutil.which("dpkg-query"):
        result = _run(
            ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\n"],
            timeout=30,
        )
        if result and result.stdout:
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                package = parts[0].strip()
                if not package or package.endswith((":amd64", ":arm64")):
                    continue
                entries.append(
                    {
                        "manager": "apt",
                        "package": package[:128],
                        "version": parts[1].strip()[:128],
                        "arch": (parts[2].strip() if len(parts) > 2 else "")[:32],
                        "source": "",
                    }
                )
    if shutil.which("winget"):
        result = _run(
            ["winget", "list", "--accept-source-agreements", "--disable-interactivity"],
            timeout=60,
        )
        if result and result.stdout:
            entries.extend(_parse_winget(result.stdout))
    return entries[:limit]


def _command(manager: str, action: str, package: str, version: str) -> list[str] | None:
    if not PACKAGE_RE.match(package):
        return None
    if version and not VERSION_RE.match(version):
        return None
    target = f"{package}={version}" if manager == "apt" and version else package
    if manager == "dnf" and version:
        target = f"{package}-{version}"
    if manager == "apt":
        if action == "install":
            return ["apt-get", "-o", "APT::Sandbox::User=root", "install", "-y", "--no-install-recommends", "-o", "DPkg::Lock::Timeout=60", target]
        if action == "remove":
            return ["apt-get", "-o", "APT::Sandbox::User=root", "remove", "-y", "-o", "DPkg::Lock::Timeout=60", package]
        if action == "upgrade":
            return ["apt-get", "-o", "APT::Sandbox::User=root", "install", "-y", "--only-upgrade", "-o", "DPkg::Lock::Timeout=60", package]
    if manager == "dnf":
        if action == "install":
            return ["dnf", "install", "-y", target]
        if action == "remove":
            return ["dnf", "remove", "-y", package]
        if action == "upgrade":
            return ["dnf", "upgrade", "-y", package]
    if manager == "winget":
        if action == "install":
            return [
                "winget",
                "install",
                "--id",
                package,
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ]
        if action == "remove":
            return ["winget", "uninstall", "--id", package, "--exact", "--silent", "--disable-interactivity"]
        if action == "upgrade":
            return [
                "winget",
                "upgrade",
                "--id",
                package,
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ]
    if manager == "choco":
        if action == "install":
            cmd = ["choco", "install", "-y", "--no-progress", package]
            if version:
                cmd.extend(["--version", version])
            return cmd
        if action == "remove":
            return ["choco", "uninstall", "-y", "--no-progress", package]
        if action == "upgrade":
            return ["choco", "upgrade", "-y", "--no-progress", package]
    return None


def normalize_actions(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("actions must be a non-empty list")
    if len(raw) > MAX_ACTIONS:
        raise ValueError(f"at most {MAX_ACTIONS} package actions are allowed per job")
    normalized: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each action must be an object")
        manager = str(item.get("manager", "")).lower()
        action = str(item.get("action", "")).lower()
        package = str(item.get("package", "")).strip()
        version = str(item.get("version") or "").strip()
        if manager not in MANAGERS:
            raise ValueError(f"unsupported manager: {manager}")
        if action not in ACTIONS:
            raise ValueError(f"unsupported action: {action}")
        if not PACKAGE_RE.match(package):
            raise ValueError("invalid package name")
        if version and not VERSION_RE.match(version):
            raise ValueError("invalid version")
        normalized.append({"manager": manager, "action": action, "package": package, "version": version})
    return normalized


def apply_actions(
    raw_actions: Any,
    config: AgentConfig,
    remote_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = remote_policy if isinstance(remote_policy, dict) else {}
    if not config.allow_packages or not bool(policy.get("allow_packages", False)):
        return {
            "status": "failed",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": "Package management is disabled by local or server policy.",
        }
    try:
        actions = normalize_actions(raw_actions)
    except ValueError as exc:
        return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": str(exc)}

    from .executor import run_capture

    results: list[dict[str, Any]] = []
    lines: list[str] = []
    errors: list[str] = []
    failed = False
    env = dict(os.environ)
    env.update({"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none"})
    for action in actions:
        command = _command(action["manager"], action["action"], action["package"], action["version"])
        if command is None:
            results.append({**action, "status": "failed", "error": "unsupported command"})
            failed = True
            continue
        result = run_capture(command, timeout=300, max_output=min(config.max_output_bytes, 200_000), env=env)
        if result["status"] != "succeeded":
            failed = True
            if result.get("error"):
                errors.append(f"{action['package']}: {result['error']}")
        results.append({**action, "status": result["status"], "exit_code": result["exit_code"]})
        header = f"[{action['manager']}] {action['action']} {action['package']}"
        lines.append(header)
        if result.get("stdout"):
            lines.append(result["stdout"])
        if result.get("stderr"):
            lines.append(result["stderr"])
    return {
        "status": "failed" if failed else "succeeded",
        "exit_code": 1 if failed else 0,
        "stdout": "\n".join(lines)[: config.max_output_bytes],
        "stderr": "\n".join(errors)[: config.max_output_bytes],
        "error": "; ".join(errors)[:4000],
        "results": results,
    }

#!/usr/bin/env python3
r"""Bootstrap installer for the Sextant agent.

Fetches the Sextant source straight from the repository and runs the
platform-appropriate agent installer. This lets you install and enroll the
agent from a single command without a pre-existing source checkout.

Usage (Linux):
    curl -fsSL https://raw.githubusercontent.com/JugglerMaster/Sextant/main/deploy/install-agent-bootstrap.py -o /tmp/sextant-install.py
    sudo python3 /tmp/sextant-install.py <server-url> <enrollment-token> <name>

Usage (Windows, elevated PowerShell):
    curl.exe -fsSL https://raw.githubusercontent.com/JugglerMaster/Sextant/main/deploy/install-agent-bootstrap.py -o $env:TEMP\sextant-install.py
    python $env:TEMP\sextant-install.py <server-url> <enrollment-token> <name>

The agent is installed to /opt/sextant-agent (Linux) or
C:\ProgramData\SextantAgent (Windows) and enrolled with the one-time token.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPO = "JugglerMaster/Sextant"
DEFAULT_TAG = "main"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the Sextant source and install/enroll the agent."
    )
    parser.add_argument("server", help="Sextant server base URL, e.g. https://ops.example.com")
    parser.add_argument("token", help="One-time enrollment token")
    parser.add_argument("name", help="Display name for the agent")
    parser.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help=f"Git tag or branch to install from (default: {DEFAULT_TAG})",
    )
    parser.add_argument(
        "--source-dir",
        default="",
        help="Use an existing local source tree instead of downloading it",
    )
    parser.add_argument(
        "--profile",
        default="workstation",
        help="Profile slug selected at enrollment (default: workstation)",
    )
    parser.add_argument(
        "--watch-service",
        action="append",
        default=[],
        metavar="SERVICE",
        help="Service name to monitor (repeatable)",
    )
    parser.add_argument("--agent-tags", default="", help="Comma-separated dashboard tags")
    parser.add_argument("--ca-file", default="", help="Private CA bundle for TLS verification")
    parser.add_argument("--repo", default=REPO, help=f"GitHub owner/repo (default: {REPO})")
    parser.add_argument("--no-self-update", action="store_true", help="Disable agent self-update")
    parser.add_argument("--allow-discovery", action="store_true", help="Allow server-requested local VLAN discovery")
    parser.add_argument("--no-packages", action="store_true", help="Disable managed package actions")
    return parser.parse_args(argv)


def is_windows() -> bool:
    return os.name == "nt"


def is_root() -> bool:
    if is_windows():
        return False
    return os.geteuid() == 0


def is_admin() -> bool:
    if not is_windows():
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def tarball_url(repo: str, tag: str) -> str:
    ref = f"refs/heads/{tag}" if tag in ("main", "master") else f"refs/tags/{tag}"
    return f"https://github.com/{repo}/archive/{ref}.tar.gz"


def download_source(repo: str, tag: str, source_dir: str) -> tuple[str, str | None]:
    """Return (source_tree, work_dir). work_dir is None when using --source-dir."""
    if source_dir:
        if not os.path.isdir(source_dir):
            fail(f"--source-dir {source_dir!r} does not exist.")
        if not os.path.isfile(os.path.join(source_dir, "pyproject.toml")):
            fail(f"--source-dir {source_dir!r} must contain pyproject.toml.")
        return source_dir, None

    print(f"Downloading {tag} source from {tarball_url(repo, tag)} ...")
    work = tempfile.mkdtemp(prefix="sextant-install-")
    tarball = os.path.join(work, "source.tar.gz")
    try:
        with urllib.request.urlopen(tarball_url(repo, tag), timeout=120) as resp:
            with open(tarball, "wb") as out:
                shutil.copyfileobj(resp, out)
    except Exception as exc:
        fail(f"Could not download the source tarball: {exc}")

    print("Extracting ...")
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(path=work, members=tf.getmembers())
    except Exception as exc:
        fail(f"Could not extract the source tarball: {exc}")

    # The tarball unpacks into a single top-level directory (e.g. Sextant-main/).
    for entry in sorted(os.listdir(work)):
        candidate = os.path.join(work, entry)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "pyproject.toml")):
            return candidate, work
    fail(f"Could not locate the source tree in {work!r}.")


def run_linux(source_dir: str, args: argparse.Namespace, watch_services: list[str]) -> None:
    installer = os.path.join(source_dir, "deploy", "install-linux-agent.sh")
    if not os.path.isfile(installer):
        fail(f"Could not find {installer!r} in the source tree.")
    env = os.environ.copy()
    env["SOURCE_DIR"] = source_dir
    env["PROFILE"] = args.profile
    if watch_services:
        env["WATCH_SERVICES"] = ",".join(watch_services)
    if args.agent_tags:
        env["AGENT_TAGS"] = args.agent_tags
    if args.ca_file:
        env["CA_FILE"] = args.ca_file
    env["ALLOW_PACKAGES"] = "0" if args.no_packages else "1"
    env["NO_SELF_UPDATE"] = "1" if args.no_self_update else "0"
    env["ALLOW_DISCOVERY"] = "1" if args.allow_discovery else "0"
    print(f"Running {installer} as root ...")
    subprocess.run(["bash", installer, args.server, args.token, args.name], env=env, check=True)


def run_windows(source_dir: str, args: argparse.Namespace, watch_services: list[str]) -> None:
    installer = os.path.join(source_dir, "deploy", "install-windows-agent.ps1")
    if not os.path.isfile(installer):
        fail(f"Could not find {installer!r} in the source tree.")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        installer,
        "-Server",
        args.server,
        "-Token",
        args.token,
        "-Name",
        args.name,
        "-SourcePath",
        source_dir,
        "-Profile",
        args.profile,
    ]
    if watch_services:
        cmd.extend(["-WatchServices", ",".join(watch_services)])
    if args.ca_file:
        cmd.extend(["-CaFile", args.ca_file])
    if args.no_self_update:
        cmd.append("-NoSelfUpdate")
    if args.allow_discovery:
        cmd.append("-AllowDiscovery")
    if args.no_packages:
        cmd.append("-NoPackages")
    print(f"Running {installer} ...")
    subprocess.run(cmd, check=True)


def reexec_as_root(args: argparse.Namespace) -> None:
    script_path = os.path.abspath(sys.argv[0])
    if script_path == "-" or not os.path.isfile(script_path):
        fail(
            "This script must be run as root. Re-run with sudo, e.g. "
            "sudo python3 /tmp/sextant-install.py <server> <token> <name>"
        )
    print("Not root; re-running via sudo ...")
    try:
        subprocess.run(["sudo", sys.executable, script_path] + sys.argv[1:], check=True)
    except subprocess.CalledProcessError as exc:
        fail(f"Could not re-run as root (sudo {exc.returncode}).")
    sys.exit(0)


def main() -> None:
    if sys.version_info < (3, 11):
        fail(f"Python 3.11+ is required (got {sys.version.split()[0]}).")

    args = parse_args()

    if is_windows() and not is_admin():
        fail(
            "This script must be run from an elevated (Administrator) PowerShell. "
            "Open PowerShell as Administrator and run it again."
        )
    if not is_windows() and not is_root():
        reexec_as_root(args)

    source_dir, work_dir = download_source(args.repo, args.tag, args.source_dir)
    try:
        if is_windows():
            run_windows(source_dir, args, args.watch_service)
        else:
            run_linux(source_dir, args, args.watch_service)
    finally:
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    print("Done. The agent is installed and enrolled.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from .config import AgentConfig

TRUNCATION_NOTE = "\n\n[output truncated by runner-agent]"


def truncate_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(TRUNCATION_NOTE))] + TRUNCATION_NOTE


def _popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {"start_new_session": True}


def run_capture(
    args: list[str],
    timeout: int,
    input_text: str | None = None,
    max_output: int = 200_000,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    cap = max(1024, int(max_output))
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **_popen_kwargs(),
        )
    except OSError as exc:
        return {
            "status": "failed",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"Could not start process: {exc}",
        }

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    totals = {"stdout": 0, "stderr": 0}
    exceeded = threading.Event()
    lock = threading.Lock()

    def reader(stream, name: str, chunks: list[bytes]) -> None:
        while True:
            try:
                data = stream.read(8192)
            except (OSError, ValueError):
                break
            if not data:
                break
            with lock:
                room = cap - totals[name]
                if room > 0:
                    chunks.append(data[:room])
                    totals[name] += min(len(data), room)
                if totals[name] >= cap:
                    exceeded.set()

    out_thread = threading.Thread(target=reader, args=(process.stdout, "stdout", stdout_chunks), daemon=True)
    err_thread = threading.Thread(target=reader, args=(process.stderr, "stderr", stderr_chunks), daemon=True)
    out_thread.start()
    err_thread.start()

    if input_text is not None and process.stdin is not None:
        def write_stdin() -> None:
            try:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        threading.Thread(target=write_stdin, daemon=True).start()

    timed_out = False
    output_exceeded = False
    deadline = time.monotonic() + max(1, int(timeout))
    while process.poll() is None:
        if exceeded.is_set():
            output_exceeded = True
            _kill_tree(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_tree(process)
            break
        time.sleep(0.05)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    out_thread.join(timeout=5)
    err_thread.join(timeout=5)
    if exceeded.is_set():
        output_exceeded = True
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if output_exceeded:
        stdout = truncate_output(stdout + TRUNCATION_NOTE, cap)
        stderr = truncate_output(stderr + TRUNCATION_NOTE, cap)

    exit_code = process.returncode
    status = "succeeded" if exit_code == 0 and not timed_out and not output_exceeded else "failed"
    error = ""
    if output_exceeded:
        error = f"Command output exceeded the {cap}-byte limit and was terminated."
    elif timed_out:
        error = f"Command timed out after {timeout}s and was terminated."
    elif exit_code not in (0, None):
        error = f"Command exited with code {exit_code}."
    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": truncate_output(stdout, cap),
        "stderr": truncate_output(stderr, cap),
        "error": error,
    }


def _kill_tree(process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _powershell():
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _powershell_capture(script: str, timeout: int, max_output: int) -> dict[str, Any]:
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    return run_capture(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
        timeout,
        input_text=prefix + script,
        max_output=max_output,
    )


def run_shell(shell: str, command: str, timeout: int, max_output: int) -> dict[str, Any]:
    shell = shell.lower()
    if not command.strip():
        return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": "Empty command."}
    if sys.platform.startswith("win"):
        if shell == "powershell":
            return _powershell_capture(command, timeout, max_output)
        if shell == "cmd":
            return run_capture(["cmd.exe", "/d", "/s", "/c", command], timeout, max_output=max_output)
        if shell == "bash":
            bash = shutil.which("bash")
            if not bash:
                return {
                    "status": "failed",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "error": "bash is not installed on this Windows host.",
                }
            return run_capture([bash, "-lc", command], timeout, max_output=max_output)
    else:
        if shell == "bash":
            bash = shutil.which("bash") or "/bin/bash"
            return run_capture([bash, "-lc", command], timeout, max_output=max_output)
        if shell == "powershell":
            pwsh = shutil.which("pwsh")
            if not pwsh:
                return {
                    "status": "failed",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "error": "PowerShell (pwsh) is not installed on this host.",
                }
            return run_capture(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                timeout,
                input_text=command,
                max_output=max_output,
            )
        if shell == "cmd":
            return {
                "status": "failed",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": "cmd.exe is only available on Windows.",
            }
    return {
        "status": "failed",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "error": f"Unsupported shell: {shell}",
    }


def build_power_command(action: str, delay: int, os_name: str | None = None) -> list[str]:
    target_os = os_name or ("windows" if sys.platform.startswith("win") else "linux")
    if target_os == "windows":
        if action == "cancel":
            return ["shutdown.exe", "/a"]
        flag = "/s" if action == "shutdown" else "/r"
        return ["shutdown.exe", flag, "/t", str(max(0, int(delay))), "/c", "Runner Control power request"]
    shutdown = shutil.which("shutdown") or "/sbin/shutdown"
    if action == "cancel":
        return [shutdown, "-c"]
    flag = "-h" if action == "shutdown" else "-r"
    if delay <= 0:
        return [shutdown, flag, "now"]
    minutes = max(1, math.ceil(delay / 60))
    return [shutdown, flag, f"+{minutes}"]


def run_power(action: str, delay: int, timeout: int = 15, max_output: int = 200_000) -> dict[str, Any]:
    if action not in {"shutdown", "reboot", "cancel"}:
        return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": "Invalid power action."}
    command = build_power_command(action, delay)
    result = run_capture(command, timeout, max_output=max_output)
    if result["status"] == "succeeded":
        if action == "cancel":
            result["stdout"] = (result["stdout"] + "\nScheduled power action cancelled.").strip()
        else:
            result["stdout"] = (
                result["stdout"] + f"\n{action.capitalize()} scheduled in {max(0, int(delay))} second(s)."
            ).strip()
    return result


def build_service_command(action: str, name: str, os_name: str | None = None) -> list[str] | tuple[str, str]:
    target_os = os_name or ("windows" if sys.platform.startswith("win") else "linux")
    if target_os == "windows":
        safe = name.replace("'", "''")
        scripts = {
            "start": f"Start-Service -Name '{safe}' -ErrorAction Stop",
            "stop": f"Stop-Service -Name '{safe}' -Force -ErrorAction Stop",
            "restart": f"Restart-Service -Name '{safe}' -Force -ErrorAction Stop",
            "enable": f"Set-Service -Name '{safe}' -StartupType Automatic -ErrorAction Stop",
            "disable": f"Set-Service -Name '{safe}' -StartupType Disabled -ErrorAction Stop",
            "status": f"Get-Service -Name '{safe}' | Format-List Name,DisplayName,Status,StartType",
        }
        return ("powershell", scripts[action])
    systemctl = shutil.which("systemctl") or "/bin/systemctl"
    if action == "status":
        return [systemctl, "status", "--no-pager", "--lines=10", name]
    return [systemctl, action, name]


def run_service(action: str, name: str, timeout: int = 60, max_output: int = 200_000) -> dict[str, Any]:
    if action not in {"start", "stop", "restart", "enable", "disable", "status"}:
        return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": "Invalid service action."}
    command = build_service_command(action, name)
    if isinstance(command, tuple):
        return _powershell_capture(command[1], timeout, max_output)
    return run_capture(command, timeout, max_output=max_output)


def execute_command(
    command: dict[str, Any],
    config: AgentConfig,
    remote_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = remote_policy if isinstance(remote_policy, dict) else {}
    allow_shell = config.allow_shell and bool(policy.get("allow_shell", True))
    allow_power = config.allow_power and bool(policy.get("allow_power", True))
    allow_service = config.allow_service and bool(policy.get("allow_service", True))
    kind = str(command.get("kind", ""))
    args = command.get("args") or {}
    if not isinstance(args, dict):
        return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": "Invalid args."}
    if kind == "shell":
        if not allow_shell:
            return {
                "status": "failed",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": "Shell commands are disabled by local or server policy.",
            }
        return run_shell(
            str(args.get("shell", "bash")),
            str(args.get("command", "")),
            int(args.get("timeout", 60)),
            config.max_output_bytes,
        )
    if kind == "power":
        if not allow_power:
            return {
                "status": "failed",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": "Power commands are disabled by local or server policy.",
            }
        return run_power(str(args.get("action", "")), int(args.get("delay", 30)), max_output=config.max_output_bytes)
    if kind == "service":
        if not allow_service:
            return {
                "status": "failed",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": "Service commands are disabled by local or server policy.",
            }
        return run_service(str(args.get("action", "")), str(args.get("name", "")), max_output=config.max_output_bytes)
    if kind == "package":
        from .packages import apply_actions

        return apply_actions(args.get("actions"), config, policy)
    if kind == "refresh":
        return {
            "status": "succeeded",
            "exit_code": 0,
            "stdout": "Metrics refresh requested.",
            "stderr": "",
            "error": "",
        }
    return {"status": "failed", "exit_code": None, "stdout": "", "stderr": "", "error": "Unsupported command kind."}

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from sextant_agent.config import AgentConfig
from sextant_agent.docker import MAX_CONTAINERS, MAX_INVENTORY_BYTES, DockerCollector

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
import time

args = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\n")

if os.environ.get("FAKE_DOCKER_FAIL") == "daemon":
    print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", file=sys.stderr)
    raise SystemExit(1)
if os.environ.get("FAKE_DOCKER_FAIL") == "permission":
    print("permission denied while trying to connect to the Docker daemon socket", file=sys.stderr)
    raise SystemExit(1)

if args[:1] == ["version"]:
    print(json.dumps({"Version": "27.5.1", "ApiVersion": "1.47", "Os": "linux", "Arch": "amd64"}))
    raise SystemExit(0)
if args[:1] == ["info"]:
    print(json.dumps({
        "Driver": "overlay2",
        "CgroupDriver": "systemd",
        "DockerRootDir": "/var/lib/docker",
        "Containers": 2,
        "ContainersRunning": 1,
        "ContainersPaused": 0,
        "ContainersStopped": 1,
        "Images": 2,
        "SecurityOptions": ["name=seccomp,profile=builtin"],
    }))
    raise SystemExit(0)
if args[:2] == ["compose", "version"]:
    if os.environ.get("FAKE_DOCKER_COMPOSE_FAIL") == "1":
        print("docker: 'compose' is not a docker command", file=sys.stderr)
        raise SystemExit(1)
    print("2.32.4")
    raise SystemExit(0)
if args[:1] == ["compose"]:
    if os.environ.get("FAKE_COMPOSE_FAIL"):
        print("compose operation failed", file=sys.stderr)
        raise SystemExit(1)
    print("compose ok")
    raise SystemExit(0)
if args[:1] == ["ps"]:
    count = int(os.environ.get("FAKE_DOCKER_CONTAINERS", "2"))
    for index in range(count):
        state = "running" if index % 2 == 0 else "exited"
        status = "Up 2 hours (healthy)" if state == "running" else "Exited (0) 3 hours ago"
        print(json.dumps({
            "ID": ("%064x" % (index + 1)),
            "Names": f"demo-{index}",
            "Image": "nginx:latest",
            "Command": "nginx -g daemon off;",
            "State": state,
            "Status": status,
            "CreatedAt": "2026-11-11 10:00:00 +0000 UTC",
            "Ports": "0.0.0.0:8080->80/tcp, :::8080->80/tcp",
            "Labels": "com.docker.compose.project=web,com.docker.compose.service=nginx,com.docker.compose.config-hash=abc123",
            "Mounts": "web_data:/data,/var/lib/docker/volumes/web/_data",
            "Networks": "web_default,bridge",
        }))
    raise SystemExit(0)
if args[:2] == ["image", "ls"]:
    for index in range(2):
        print(json.dumps({
            "ID": f"sha256:{index + 1:064x}",
            "Repository": "nginx" if index == 0 else "<none>",
            "Tag": "latest" if index == 0 else "<none>",
            "Digest": "<none>",
            "CreatedAt": "2026-11-11 10:00:00 +0000 UTC",
            "Size": "187MB",
            "SharedSize": "0B",
            "Containers": "1" if index == 0 else "0",
        }))
    raise SystemExit(0)
if args[:2] == ["volume", "ls"]:
    print(json.dumps({
        "Name": "web_data",
        "Driver": "local",
        "Scope": "local",
        "Mountpoint": "/var/lib/docker/volumes/web_data/_data",
        "Labels": "com.docker.compose.project=web",
    }))
    raise SystemExit(0)
if args[:2] == ["network", "ls"]:
    print(json.dumps({
        "ID": "a" * 64,
        "Name": "web_default",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": "false",
        "Attachable": "false",
        "Labels": "com.docker.compose.project=web",
    }))
    raise SystemExit(0)
if args[:2] == ["inspect", "--format"]:
    template = args[2]
    if "|" in template:
        for target in args[3:]:
            print(
                f"{target}|2026-11-11T10:00:00Z|2026-11-11T12:00:00Z|0|unless-stopped|0|536870912|1500000000|128"
            )
        raise SystemExit(0)
    target = args[-1]
    if target.startswith("f"):
        print("no such container", file=sys.stderr)
        raise SystemExit(1)
    print(("a" * 64) if target.startswith("a") else target)
    raise SystemExit(0)
if args[:1] == ["inspect"]:
    target = args[-1]
    print(json.dumps([{
        "Id": target,
        "Config": {"Env": ["SECRET_TOKEN=never-return-this"], "Image": "nginx:latest"},
        "State": {"Status": "running", "ExitCode": 0},
    }]))
    raise SystemExit(0)
if args[:2] == ["image", "inspect"]:
    print("sha256:" + "b" * 64)
    raise SystemExit(0)
if args[:2] == ["volume", "inspect"]:
    print("web_data")
    raise SystemExit(0)
if args[:2] == ["network", "inspect"]:
    print("c" * 64)
    raise SystemExit(0)
if args[:1] in (["rm"], ["rename"], ["update"], ["start"], ["stop"], ["restart"], ["pause"], ["unpause"], ["kill"]):
    print(args[-1])
    raise SystemExit(0)
if args[:1] == ["logs"]:
    print("log line one\nlog line two")
    raise SystemExit(0)
if args[:1] == ["exec"]:
    if os.environ.get("FAKE_DOCKER_SLEEP"):
        time.sleep(float(os.environ["FAKE_DOCKER_SLEEP"]))
    print("uid=0(root) gid=0(root) groups=0(root)")
    raise SystemExit(0)
if args[:2] == ["image", "rm"]:
    print("Untagged: nginx:latest")
    raise SystemExit(0)
if args[:2] == ["image", "prune"]:
    print("Total reclaimed space: 12.5MB")
    raise SystemExit(0)
if args[:2] == ["volume", "rm"]:
    print(args[-1])
    raise SystemExit(0)
if args[:2] == ["volume", "prune"]:
    print("Total reclaimed space: 2MB")
    raise SystemExit(0)
if args[:2] == ["network", "rm"]:
    print(args[-1])
    raise SystemExit(0)
if args[:2] == ["network", "prune"]:
    print("Total reclaimed space: 1MB")
    raise SystemExit(0)
if args[:2] == ["builder", "prune"]:
    print("Total reclaimed space: 3.5GB")
    raise SystemExit(0)
print("unsupported command", file=sys.stderr)
raise SystemExit(2)
'''


def make_config(tmp_path: Path, **overrides) -> AgentConfig:
    script = tmp_path / "docker"
    script.write_text(FAKE_DOCKER, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    socket_path = tmp_path / "docker.sock"
    socket_path.write_text("", encoding="utf-8")
    values = {
        "server_url": "http://example.test",
        "agent_id": "agt_test",
        "agent_token": "token",
        "allow_docker": True,
        "docker_command": str(script),
        "docker_socket": str(socket_path),
        "docker_timeout": 10,
        "docker_inventory_interval": 60,
    }
    values.update(overrides)
    return AgentConfig(**values)


def collector(config: AgentConfig) -> DockerCollector:
    value = DockerCollector(config)
    value.set_profile(
        {
            "modules": {"docker": True},
            "collection": {"docker_inventory_seconds": 60},
            "actions": {"allow_docker": True},
        }
    )
    value.set_policy({"allow_docker": True})
    return value


def test_disabled_is_reported_without_running_docker(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path, allow_docker=False)
    value = collector(config)
    summary = value.summary()
    assert summary["available"] is False
    assert summary["reason"] == "disabled"
    assert value.collect_inventory(force=True) is None


def test_windows_reports_unsupported_os(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    value = collector(config)
    monkeypatch.setattr("sextant_agent.docker.platform.system", lambda: "Windows")
    summary = value.summary()
    assert summary["available"] is False
    assert summary["reason"] == "unsupported_os"

    disabled = collector(make_config(tmp_path, allow_docker=False))
    assert disabled.summary()["reason"] == "unsupported_os"


def test_missing_command_reports_not_installed(tmp_path: Path):
    config = make_config(tmp_path, docker_command=str(tmp_path / "missing-docker"))
    summary = collector(config).summary()
    assert summary["reason"] == "not_installed"


def test_missing_socket_reports_socket_missing(tmp_path: Path):
    config = make_config(tmp_path, docker_socket=str(tmp_path / "missing.sock"))
    summary = collector(config).summary()
    assert summary["reason"] == "socket_missing"


def test_permission_denied_socket(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    real_access = os.access

    def access(path, mode):
        if str(path) == config.docker_socket:
            return False
        return real_access(path, mode)

    monkeypatch.setattr("sextant_agent.docker.os.access", access)
    summary = collector(config).summary()
    assert summary["reason"] == "permission_denied"


def test_daemon_unreachable(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "daemon")
    summary = collector(config).summary()
    assert summary["available"] is False
    assert summary["reason"] == "daemon_unreachable"


def test_compose_missing_is_available_with_reason(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_COMPOSE_FAIL", "1")
    summary = collector(config).summary()
    assert summary["available"] is True
    assert summary["compose_available"] is False
    assert summary["reason"] == "compose_missing"


def test_summary_and_inventory_parsing(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = collector(config)

    summary = value.summary()
    assert summary["available"] is True
    assert summary["engine_version"] == "27.5.1"
    assert summary["api_version"] == "1.47"
    assert summary["compose_version"] == "2.32.4"
    assert summary["rootless"] is False
    assert summary["counts"]["containers"] == 2

    payload = value.collect_inventory(force=True)
    assert payload is not None
    assert payload["revision"] == 1
    assert len(payload["hash"]) == 64
    assert len(payload["containers"]) == 2
    assert payload["containers"][0]["compose_project"] == "web"
    assert payload["containers"][0]["compose_service"] == "nginx"
    assert payload["containers"][0]["health"] == "healthy"
    assert payload["containers"][0]["exit_code"] == 0
    assert payload["containers"][1]["exit_code"] == 0
    assert payload["summary"]["counts"]["running"] == 1
    assert payload["summary"]["counts"]["stopped"] == 1
    assert payload["summary"]["counts"]["unhealthy"] == 0
    assert payload["images"][0]["repo_tags"] == ["nginx:latest"]
    assert payload["images"][1]["dangling"] is True
    assert payload["volumes"][0]["name"] == "web_data"
    assert payload["networks"][0]["name"] == "web_default"
    assert len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) <= MAX_INVENTORY_BYTES

    assert payload["containers"][0]["restart_policy"] == "unless-stopped"
    assert payload["containers"][0]["memory_limit"] == 536870912
    assert payload["containers"][0]["nano_cpus"] == 1500000000
    assert payload["containers"][0]["pids_limit"] == 128

    commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(command[:1] == ["ps"] for command in commands)
    assert any(command[:2] == ["inspect", "--format"] for command in commands)
    assert not any("env" in command for command in commands)
    serialized = json.dumps(payload)
    assert "DOCKER_HOST" not in serialized
    assert "environment" not in serialized.lower()

    second = value.collect_inventory(force=True)
    assert second is not None
    assert second["revision"] == 2
    assert second["hash"] == payload["hash"] or second["hash"] != ""


def test_row_cap_is_enforced(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_CONTAINERS", str(MAX_CONTAINERS + 25))
    payload = collector(config).collect_inventory(force=True)
    assert payload is not None
    assert len(payload["containers"]) == MAX_CONTAINERS
    assert payload["summary"]["counts"]["containers"] == MAX_CONTAINERS
    assert len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) <= MAX_INVENTORY_BYTES


def _action_collector(tmp_path: Path, **overrides) -> DockerCollector:
    config = make_config(
        tmp_path,
        allow_docker_exec=True,
        allow_docker_prune=True,
        **overrides,
    )
    value = DockerCollector(config)
    value.set_profile(
        {
            "modules": {"docker": True},
            "collection": {"docker_inventory_seconds": 60},
            "actions": {
                "allow_docker": True,
                "allow_docker_exec": True,
                "allow_docker_prune": True,
            },
        }
    )
    value.set_policy(
        {
            "allow_docker": True,
            "allow_docker_exec": True,
            "allow_docker_prune": True,
        }
    )
    return value


def _logged_commands(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_container_lifecycle_argv(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = collector(config)
    result = value.execute_action("container.restart", {"container_id": "a" * 64})
    assert result["status"] == "succeeded"
    commands = _logged_commands(log)
    assert ["restart", "a" * 64] in commands


def test_container_rename_update_and_remove_argv(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = collector(config)
    assert value.execute_action(
        "container.rename", {"container_id": "a" * 64, "new_name": "web-2"}
    )["status"] == "succeeded"
    assert value.execute_action(
        "container.update",
        {
            "container_id": "a" * 64,
            "restart_policy": "always",
            "memory": "512m",
            "cpus": "1.5",
            "pids_limit": 128,
        },
    )["status"] == "succeeded"
    assert value.execute_action(
        "container.remove",
        {"container_id": "a" * 64, "force": True, "remove_volumes": True},
    )["status"] == "succeeded"
    commands = _logged_commands(log)
    assert ["rename", "a" * 64, "web-2"] in commands
    assert any(command[:1] == ["update"] and "--memory" in command for command in commands)
    assert any(command[:1] == ["rm"] and "-f" in command and "-v" in command for command in commands)


def test_exec_argv_and_no_host_shell_execution(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path, allow_docker_exec=True)
    log = tmp_path / "docker.log"
    marker = tmp_path / "pwned"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = collector(config)
    value.set_policy({"allow_docker": True, "allow_docker_exec": True})
    result = value.execute_action(
        "container.exec_one_shot",
        {
            "container_id": "a" * 64,
            "shell": "sh",
            "user": "root",
            "workdir": "/srv",
            "command": f"echo hi; touch {marker}",
            "timeout": 20,
        },
    )
    assert result["status"] == "succeeded"
    assert not marker.exists()
    commands = _logged_commands(log)
    exec_command = next(command for command in commands if command[:1] == ["exec"])
    assert exec_command[:2] == ["exec", "-i"]
    assert "-u" in exec_command and "root" in exec_command
    assert "-w" in exec_command and "/srv" in exec_command
    assert f"echo hi; touch {marker}" in exec_command
    assert exec_command[-2:] == ["-lc", f"echo hi; touch {marker}"]


def test_exec_timeout_is_forwarded(tmp_path: Path, monkeypatch):
    value = collector(make_config(tmp_path, allow_docker_exec=True))
    value.set_policy({"allow_docker": True, "allow_docker_exec": True})
    seen: list[int | None] = []

    def fake_run(command, args, *, env, max_output, timeout=None):
        if args[:2] == ["inspect", "--format"]:
            return {"status": "succeeded", "exit_code": 0, "stdout": "a" * 64, "stderr": "", "error": ""}
        seen.append(timeout)
        return {"status": "succeeded", "exit_code": 0, "stdout": "ok", "stderr": "", "error": ""}

    monkeypatch.setattr(value, "_run", fake_run)
    result = value.execute_action(
        "container.exec_one_shot",
        {"container_id": "a" * 64, "shell": "sh", "command": "id", "timeout": 45},
    )
    assert result["status"] == "succeeded"
    assert seen == [45]


def test_exec_and_prune_gates(tmp_path: Path):
    value = collector(make_config(tmp_path, allow_docker_exec=False, allow_docker_prune=False))
    value.set_policy({"allow_docker": True, "allow_docker_exec": False, "allow_docker_prune": False})
    denied_exec = value.execute_action(
        "container.exec_one_shot",
        {"container_id": "a" * 64, "shell": "sh", "command": "id", "timeout": 20},
    )
    assert denied_exec["status"] == "failed"
    assert "exec" in denied_exec["error"].lower()
    denied_cleanup = value.execute_action("cleanup", {"scope": "dangling_images"})
    assert denied_cleanup["status"] == "failed"
    assert "cleanup" in denied_cleanup["error"].lower()


def test_inspect_masks_environment(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    value = collector(config)
    result = value.execute_action("container.inspect", {"container_id": "a" * 64})
    assert result["status"] == "succeeded"
    assert "SECRET_TOKEN" not in result["stdout"]
    assert "never-return-this" not in result["stdout"]
    assert "<masked>" in result["stdout"]


def test_unknown_target_is_rejected(tmp_path: Path):
    value = collector(make_config(tmp_path))
    result = value.execute_action("container.stop", {"container_id": "f" * 64})
    assert result["status"] == "failed"
    assert "not found" in result["error"].lower()


def test_cleanup_reclaimed_and_compose_exclusion(tmp_path: Path, monkeypatch):
    value = collector(make_config(tmp_path, allow_docker_prune=True))
    value.set_policy({"allow_docker": True, "allow_docker_prune": True})
    pruned = value.execute_action("cleanup", {"scope": "dangling_images"})
    assert pruned["status"] == "succeeded"
    assert "12.5MB" in pruned["stdout"]

    default = value.execute_action("cleanup", {"scope": "stopped_containers"})
    assert default["status"] == "succeeded"
    assert "Removed 0 stopped containers." in default["stdout"]

    included = value.execute_action(
        "cleanup", {"scope": "stopped_containers", "include_compose": True}
    )
    assert included["status"] == "succeeded"
    assert "Removed 1 stopped container." in included["stdout"]


def test_mutation_guard_rejects_overlap(tmp_path: Path):
    value = collector(make_config(tmp_path))
    assert value._mutation_lock.acquire(timeout=1)
    try:
        result = value.execute_action("container.stop", {"container_id": "a" * 64})
    finally:
        value._mutation_lock.release()
    assert result["status"] == "failed"
    assert "mutation" in result["error"].lower()


def test_volume_and_network_usage_is_derived_from_containers(tmp_path: Path):
    payload = collector(make_config(tmp_path)).collect_inventory(force=True)
    assert payload is not None
    assert payload["volumes"][0]["in_use"] is True
    assert payload["networks"][0]["container_count"] == 2
    assert payload["containers"][0]["compose_project"] == "web"


def test_profile_and_policy_can_disable_local_docker(tmp_path: Path):
    value = collector(make_config(tmp_path))
    value.set_profile({"modules": {"docker": False}})
    assert value.summary()["reason"] == "disabled"

    value.set_profile({"modules": {"docker": True}})
    value.set_policy({"allow_docker": False})
    assert value.summary()["reason"] == "disabled"

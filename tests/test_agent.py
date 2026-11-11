from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from sextant_agent.agent import AgentRunner
from sextant_agent.collectors import (
    MetricsCollector,
    _windows_disks,
    enrollment_identity,
    machine_identity,
    parse_cpu_stat,
    parse_nvidia_processes,
    parse_nvidia_smi,
    parse_proc_meminfo,
    parse_startup_desktop,
    parse_systemctl_units,
    parse_unit_files,
)
from sextant_agent.config import AgentConfig, load_config, save_config
from sextant_agent.executor import (
    build_power_command,
    build_service_command,
    execute_command,
    run_capture,
    run_shell,
    truncate_output,
)


def test_parse_proc_meminfo():
    text = "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nBuffers: 20 kB\nCached: 80 kB\n"
    total, used = parse_proc_meminfo(text)
    assert total == 1000 * 1024
    assert used == 600 * 1024


def test_parse_cpu_stat():
    first = parse_cpu_stat("cpu  100 0 50 850 0 0 0 0 0 0\n")
    second = parse_cpu_stat("cpu  120 0 60 900 0 0 0 0 0 0\n")
    assert first is not None and second is not None
    assert second.total - first.total == 80
    assert second.idle - first.idle == 50


def test_parse_nvidia_smi():
    gpus = parse_nvidia_smi("NVIDIA RTX 4090, 97, 20480, 24564, 71\nNVIDIA A100, [N/A], 0, 40960, 40\n")
    assert len(gpus) == 2
    assert gpus[0]["name"] == "NVIDIA RTX 4090"
    assert gpus[0]["util_pct"] == 97
    assert gpus[0]["mem_used"] == 20480 * 1024 * 1024
    assert gpus[1]["util_pct"] == 0


def test_parse_systemctl_and_unit_files():
    units = parse_systemctl_units(
        "  UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        "  ssh.service loaded active running OpenBSD Secure Shell server\n"
        "  nginx.service loaded failed failed A high performance web server\n"
        "● broken.service loaded failed failed Broken\n"
        "  socket.socket loaded active listening A socket\n"
    )
    assert [item["name"] for item in units] == ["ssh.service", "nginx.service", "broken.service"]
    assert units[0]["status"] == "running"
    assert units[1]["status"] == "failed"

    files = parse_unit_files("ssh.service enabled\nnginx.service disabled\nalias.service alias\nother.timer enabled\n")
    assert files == {"ssh.service": "enabled", "nginx.service": "disabled", "alias.service": "alias"}


def test_parse_startup_desktop():
    fields = parse_startup_desktop(
        "[Desktop Entry]\nType=Application\nName=Backup\nExec=/usr/bin/backup --now\nHidden=false\n"
        "X-GNOME-Autostart-enabled=true\n\n[Desktop Action]\nName=nope\n"
    )
    assert fields["Name"] == "Backup"
    assert fields["Exec"] == "/usr/bin/backup --now"
    assert "Type" in fields
    assert "Name" in fields


def test_config_save_and_load(tmp_path: Path):
    path = tmp_path / "agent.json"
    config = AgentConfig(
        server_url="https://ops.example.com",
        agent_id="agt_123",
        agent_token="sextant_agent_secret",
        name="box",
        watch_services=["sshd"],
        tags=["lab"],
    )
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.agent_id == "agt_123"
    assert loaded.watch_services == ["sshd"]
    assert json.loads(path.read_text())["agent_token"] == "sextant_agent_secret"
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    redacted = loaded.redacted()
    assert redacted["agent_token"].endswith("...")
    assert "secret" not in redacted["agent_token"]


def test_config_rejects_bad_url():
    with pytest.raises(ValueError):
        AgentConfig(server_url="ftp://x", agent_id="a", agent_token="b").validate()


def test_power_command_builders():
    linux = build_power_command("shutdown", 30, os_name="linux")
    assert linux[-1] == "+1"
    assert build_power_command("reboot", 0, os_name="linux")[-1] == "now"
    assert build_power_command("cancel", 0, os_name="linux")[-1] == "-c"
    windows = build_power_command("shutdown", 30, os_name="windows")
    assert windows[:2] == ["shutdown.exe", "/s"]
    assert windows[3] == "30"
    assert build_power_command("reboot", 0, os_name="windows")[1] == "/r"
    assert build_power_command("cancel", 0, os_name="windows")[1] == "/a"


def test_service_command_builders():
    windows = build_service_command("start", "Spooler", os_name="windows")
    assert isinstance(windows, tuple) and windows[0] == "powershell"
    assert "Start-Service" in windows[1]
    assert "Spooler" in windows[1]
    linux = build_service_command("restart", "nginx.service", os_name="linux")
    assert isinstance(linux, list)
    assert linux[-2:] == ["restart", "nginx.service"]


def test_run_shell_and_timeout():
    result = run_shell("bash", "printf 'hello world'", 5, 1000)
    assert result["status"] == "succeeded"
    assert result["stdout"] == "hello world"
    assert result["exit_code"] == 0

    timed = run_shell("bash", "sleep 3", 1, 1000)
    assert timed["status"] == "failed"
    assert "timed out" in timed["error"].lower()


def test_executor_chunk_callback_streams_stdout_and_stderr_before_completion():
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b", allow_shell=True)
    chunks: list[tuple[str, str]] = []
    completed = threading.Event()

    def on_chunk(stream: str, text: str) -> None:
        assert not completed.is_set(), "chunk was emitted only after the command finished"
        chunks.append((stream, text))

    result = execute_command(
        {
            "kind": "shell",
            "args": {
                "shell": "bash",
                "command": "printf 'first\\n'; printf 'err\\n' >&2; sleep 0.2; printf 'last\\n'",
                "timeout": 5,
            },
        },
        config,
        on_chunk=on_chunk,
    )
    completed.set()

    assert result["status"] == "succeeded"
    stdout = "".join(text for stream, text in chunks if stream == "stdout")
    stderr = "".join(text for stream, text in chunks if stream == "stderr")
    assert "first" in stdout and "last" in stdout
    assert "err" in stderr
    assert stdout == result["stdout"]
    assert stderr == result["stderr"]


def test_executor_streams_small_output_before_process_exit():
    chunks: list[tuple[float, str, str]] = []
    started = time.monotonic()

    result = run_capture(
        ["bash", "-lc", "printf 'first\\n'; sleep 1.5; printf 'second\\n'"],
        5,
        on_chunk=lambda stream, text: chunks.append((time.monotonic() - started, stream, text)),
    )

    assert result["status"] == "succeeded"
    assert chunks
    first_at = min(at for at, stream, _text in chunks if stream == "stdout")
    last_at = max(at for at, stream, _text in chunks if stream == "stdout")
    # read1() must return the first line while the process is still sleeping.
    assert first_at < 1.0, f"first chunk arrived at {first_at:.2f}s"
    assert last_at >= 1.2, f"second chunk arrived at {last_at:.2f}s"


def test_executor_chunk_callback_respects_max_output_bytes():
    chunks: list[tuple[str, str]] = []
    result = run_capture(
        ["bash", "-lc", "head -c 20000 /dev/zero | tr '\\0' 'x'"],
        5,
        max_output=1024,
        on_chunk=lambda stream, text: chunks.append((stream, text)),
    )

    streamed_stdout = "".join(text for stream, text in chunks if stream == "stdout")
    assert len(streamed_stdout) <= 1024
    assert len(result["stdout"]) <= 1024
    assert "truncated" in result["stdout"]
    assert result["status"] == "failed"


def test_execute_command_policies_and_refresh():
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b", allow_shell=False)
    denied = execute_command({"kind": "shell", "args": {"shell": "bash", "command": "echo hi"}}, config)
    assert denied["status"] == "failed"
    assert "disabled" in denied["error"]

    local_allowed = AgentConfig(server_url="https://x", agent_id="a", agent_token="b", allow_shell=True)
    server_denied = execute_command(
        {"kind": "shell", "args": {"shell": "bash", "command": "echo hi"}},
        local_allowed,
        {"allow_shell": False},
    )
    assert server_denied["status"] == "failed"
    assert "disabled" in server_denied["error"]

    refresh = execute_command({"kind": "refresh", "args": {}}, config)
    assert refresh["status"] == "succeeded"

    unknown = execute_command({"kind": "nonsense", "args": {}}, config)
    assert unknown["status"] == "failed"


def test_machine_identity_and_enrollment_fields():
    identity = machine_identity()
    assert identity
    assert len(identity) <= 128
    config = AgentConfig(
        server_url="https://x",
        agent_id="a",
        agent_token="b",
        name="box",
        watch_services=["ssh"],
    )
    enrolled = enrollment_identity(config)
    assert enrolled["machine_id"] == identity
    assert enrolled["watch_services"] == ["ssh"]


def test_truncate_output():
    text = "x" * 100
    out = truncate_output(text, 40)
    assert len(out) <= 40
    assert "truncated" in out


def test_agent_runner_smoke(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="agt_1", agent_token="token")
    runner = AgentRunner(config)

    def fake_snapshot():
        return {"hostname": "box"}

    def fake_heartbeat(snapshot):
        assert snapshot["hostname"] == "box"
        return {"commands": []}

    monkeypatch.setattr(runner.collector, "snapshot", fake_snapshot)
    monkeypatch.setattr(runner.client, "heartbeat", fake_heartbeat)
    assert runner.run(once=True) == 0


def test_enrollment_identity_shape():
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b", name="named")
    identity = enrollment_identity(config)
    assert identity["name"] == "named"
    assert identity["os"] in {"linux", "windows", "darwin"}
    assert identity["agent_version"]


def test_parse_nvidia_processes():
    processes = parse_nvidia_processes("1234, python3, 1024\n99, /opt/miner/run, 4096\nbad,row\n")
    assert processes == [
        {"pid": 1234, "name": "python3", "mem_used": 1024 * 1024 * 1024},
        {"pid": 99, "name": "/opt/miner/run", "mem_used": 4096 * 1024 * 1024},
    ]


def test_windows_metric_normalization():
    disks = _windows_disks(
        [
            {"mount": "C:", "fs": "NTFS", "total": 1000, "free": 250},
            {"mount": "D:", "fs": "NTFS", "total": 0, "free": 0},
        ]
    )
    assert disks[0]["used"] == 750
    assert disks[0]["percent"] == 75.0
    assert disks[1]["percent"] == 0

    config = AgentConfig(
        server_url="https://x",
        agent_id="a",
        agent_token="b",
        watch_services=["Spooler"],
    )
    collector = MetricsCollector(config)
    services = collector._normalize_windows_services(
        [
            {"name": "Spooler", "display_name": "Print Spooler", "status": "Stopped", "startup": "Auto"},
            {"name": "Other", "display_name": "Other", "status": "Running", "startup": "Manual"},
            {"name": "", "status": "Running"},
        ]
    )
    assert services[0]["name"] == "Spooler"
    assert services[0]["watched"] is True
    assert services[0]["status"] == "stopped"
    assert services[1]["watched"] is False


REQUIRED_SNAPSHOT_KEYS = {
    "hostname",
    "os",
    "os_version",
    "arch",
    "ip",
    "mac",
    "cpu_pct",
    "mem_total",
    "mem_used",
    "mem_pct",
    "uptime_seconds",
    "disks",
    "gpus",
    "services",
    "startup",
    "extra",
    "agent_version",
    "machine_id",
}


def _stub_linux_collector(monkeypatch, collector: MetricsCollector) -> dict[str, int]:
    calls: dict[str, int] = {}

    def bump(name: str) -> None:
        calls[name] = calls.get(name, 0) + 1

    def fake_services():
        bump("services")
        return (
            [
                {
                    "name": "sshd.service",
                    "display_name": "SSH",
                    "status": "running",
                    "startup": "enabled",
                    "watched": False,
                }
            ],
            {"sshd.service": "enabled"},
        )

    def fake_startup(unit_files):
        bump("startup")
        assert "sshd.service" in unit_files
        return [
            {
                "name": "sshd.service",
                "command": "systemctl enable sshd.service",
                "source": "systemd",
                "enabled": True,
            }
        ]

    def fake_processes():
        bump("processes")
        return [{"pid": 42, "name": "trainer", "mem_used": 1024}]

    def fake_memory():
        bump("memory")
        return 8 * 1024 * 1024, 2 * 1024 * 1024

    def fake_gpus():
        bump("gpus")
        return []

    monkeypatch.setattr(collector, "_linux_services", fake_services)
    monkeypatch.setattr(collector, "_linux_startup", fake_startup)
    monkeypatch.setattr(collector, "_nvidia_processes", fake_processes)
    monkeypatch.setattr(collector, "_linux_memory", fake_memory)
    monkeypatch.setattr(collector, "_nvidia_gpus", fake_gpus)
    return calls


def test_inventory_cached_across_snapshots(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    collector = MetricsCollector(config)
    calls = _stub_linux_collector(monkeypatch, collector)

    first = collector.snapshot()
    second = collector.snapshot()
    collector.snapshot()

    assert calls["services"] == 1
    assert calls["startup"] == 1
    assert calls["processes"] == 1
    assert first["services"] == second["services"]
    assert first["startup"] == second["startup"]


def test_fast_metrics_refresh_every_snapshot(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    collector = MetricsCollector(config)
    calls = _stub_linux_collector(monkeypatch, collector)

    collector.snapshot()
    collector.snapshot()
    collector.snapshot()

    assert calls["memory"] == 3
    assert calls["gpus"] == 3
    assert calls["services"] == 1


def test_request_inventory_refresh_forces_recollection(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    collector = MetricsCollector(config)
    calls = _stub_linux_collector(monkeypatch, collector)

    collector.snapshot()
    collector.snapshot()
    assert calls["services"] == 1

    collector.request_inventory_refresh()
    collector.snapshot()
    assert calls["services"] == 2
    assert calls["startup"] == 2
    assert calls["processes"] == 2


def test_inventory_interval_expiry(monkeypatch):
    config = AgentConfig(
        server_url="https://x", agent_id="a", agent_token="b", inventory_interval=60
    )
    collector = MetricsCollector(config)
    calls = _stub_linux_collector(monkeypatch, collector)
    now = [1000.0]
    monkeypatch.setattr("sextant_agent.collectors.time.monotonic", lambda: now[0])

    collector.snapshot()
    now[0] += 59
    collector.snapshot()
    assert calls["services"] == 1

    now[0] += 2
    collector.snapshot()
    assert calls["services"] == 2


def test_agent_refresh_command_requests_inventory(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    runner = AgentRunner(config)
    requests: list[bool] = []
    monkeypatch.setattr(
        runner.collector, "request_inventory_refresh", lambda: requests.append(True)
    )
    monkeypatch.setattr(runner.client, "send_result", lambda *args, **kwargs: None)

    runner._execute({"id": "cmd-1", "kind": "refresh", "args": {}})
    assert requests == [True]


def test_inventory_interval_config(tmp_path: Path):
    low = AgentConfig(
        server_url="https://x", agent_id="a", agent_token="b", inventory_interval=1
    )
    low.validate()
    assert low.inventory_interval == 60

    high = AgentConfig(
        server_url="https://x", agent_id="a", agent_token="b", inventory_interval=10_000_000
    )
    high.validate()
    assert high.inventory_interval == 86400

    path = tmp_path / "agent.json"
    saved = AgentConfig(
        server_url="https://x", agent_id="a", agent_token="b", inventory_interval=120
    )
    save_config(saved, path)
    loaded = load_config(path)
    assert loaded.inventory_interval == 120
    assert json.loads(path.read_text())["inventory_interval"] == 120

    raw = json.loads(path.read_text())
    raw.pop("inventory_interval")
    path.write_text(json.dumps(raw))
    assert load_config(path).inventory_interval == 3600


def test_snapshot_payload_shape_first_and_later(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    collector = MetricsCollector(config)
    _stub_linux_collector(monkeypatch, collector)

    first = collector.snapshot()
    second = collector.snapshot()
    for data in (first, second):
        assert REQUIRED_SNAPSHOT_KEYS <= set(data)
        assert {"cpu_model", "kernel", "gpu_source", "gpu_processes"} <= set(data["extra"])
        assert data["agent_version"]
        assert data["machine_id"]
    assert first["services"]
    assert first["startup"]
    assert second["services"] == first["services"]


def test_windows_snapshot_caches_inventory(monkeypatch):
    config = AgentConfig(server_url="https://x", agent_id="a", agent_token="b")
    collector = MetricsCollector(config)
    calls = {"inventory": 0, "fast": 0, "gpus": 0, "processes": 0}
    inventory_payload = {
        "cpu_model": "Test CPU",
        "gpu_names": ["Test GPU"],
        "services": [
            {
                "name": "Spooler",
                "display_name": "Print Spooler",
                "status": "Running",
                "startup": "Auto",
            }
        ],
        "startup": [
            {"name": "Agent", "command": "agent.exe", "source": "Run", "enabled": True}
        ],
    }
    fast_payload = {
        "hostname": "winbox",
        "os_version": "Windows Test",
        "arch": "AMD64",
        "ip": "192.0.2.10",
        "mac": "AA:BB:CC:DD:EE:FF",
        "cpu_pct": 12.5,
        "mem_total": 1024,
        "mem_used": 512,
        "uptime_seconds": 99,
        "disks": [{"mount": "C:", "fs": "NTFS", "total": 100, "free": 40}],
    }

    def fake_powershell(script, timeout=20):
        if "Win32_Service" in script:
            calls["inventory"] += 1
            payload = inventory_payload
        else:
            calls["fast"] += 1
            payload = fast_payload
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )

    def fake_gpus():
        calls["gpus"] += 1
        return []

    def fake_processes():
        calls["processes"] += 1
        return []

    monkeypatch.setattr("sextant_agent.collectors._powershell_script", fake_powershell)
    monkeypatch.setattr(collector, "_nvidia_gpus", fake_gpus)
    monkeypatch.setattr(collector, "_nvidia_processes", fake_processes)

    first = collector._windows_snapshot()
    second = collector._windows_snapshot()

    assert calls == {"inventory": 1, "fast": 2, "gpus": 2, "processes": 1}
    assert first["services"] == second["services"]
    assert second["hostname"] == "winbox"
    assert second["extra"]["cpu_model"] == "Test CPU"
    assert second["gpus"][0]["name"] == "Test GPU"

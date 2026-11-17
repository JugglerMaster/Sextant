from __future__ import annotations

from sextant_agent.collectors import MetricsCollector
from sextant_agent.config import AgentConfig
from sextant_agent.executor import run_power, run_service


def _collector() -> MetricsCollector:
    return MetricsCollector(AgentConfig(server_url="https://x", agent_id="a", agent_token="b"))


def test_run_power_invalid_action():
    result = run_power("hibernate", 30)
    assert result["status"] == "failed"
    assert result["exit_code"] is None
    assert "Invalid power action." in result["error"]


def test_run_power_success_appends_message(monkeypatch):
    monkeypatch.setattr(
        "sextant_agent.executor.build_power_command",
        lambda action, delay, os_name=None: ["/sbin/shutdown", "-h", "+1"],
    )
    monkeypatch.setattr(
        "sextant_agent.executor.run_capture",
        lambda args, timeout, **kw: {"status": "succeeded", "exit_code": 0, "stdout": "", "stderr": "", "error": ""},
    )
    result = run_power("shutdown", 30)
    assert result["status"] == "succeeded"
    assert "Shutdown" in result["stdout"]
    assert "30" in result["stdout"]


def test_run_power_cancel_appends_cancelled(monkeypatch):
    monkeypatch.setattr(
        "sextant_agent.executor.build_power_command",
        lambda action, delay, os_name=None: ["/sbin/shutdown", "-c"],
    )
    monkeypatch.setattr(
        "sextant_agent.executor.run_capture",
        lambda args, timeout, **kw: {"status": "succeeded", "exit_code": 0, "stdout": "", "stderr": "", "error": ""},
    )
    result = run_power("cancel", 30)
    assert result["status"] == "succeeded"
    assert "cancelled" in result["stdout"].lower()


def test_run_power_failed_passes_through(monkeypatch):
    monkeypatch.setattr(
        "sextant_agent.executor.build_power_command",
        lambda action, delay, os_name=None: ["/sbin/shutdown", "-h", "+1"],
    )
    monkeypatch.setattr(
        "sextant_agent.executor.run_capture",
        lambda args, timeout, **kw: {"status": "failed", "exit_code": 1, "stdout": "", "stderr": "err", "error": "boom"},
    )
    result = run_power("shutdown", 30)
    assert result["status"] == "failed"
    assert "err" in result["stderr"]


def test_run_service_invalid_action():
    result = run_service("pause", "nginx")
    assert result["status"] == "failed"
    assert result["exit_code"] is None
    assert "Invalid service action." in result["error"]


def test_run_service_linux_list_command(monkeypatch):
    monkeypatch.setattr(
        "sextant_agent.executor.build_service_command",
        lambda action, name, os_name=None: ["/bin/systemctl", "start", "nginx.service"],
    )
    monkeypatch.setattr(
        "sextant_agent.executor.run_capture",
        lambda args, timeout, **kw: {"status": "succeeded", "exit_code": 0, "stdout": "", "stderr": "", "error": ""},
    )
    result = run_service("start", "nginx.service")
    assert result["status"] == "succeeded"


def test_run_service_windows_tuple_command(monkeypatch):
    monkeypatch.setattr(
        "sextant_agent.executor.build_service_command",
        lambda action, name, os_name=None: ("powershell", "Start-Service -Name 'Spooler'"),
    )
    monkeypatch.setattr(
        "sextant_agent.executor._powershell_capture",
        lambda script, timeout, max_output, on_chunk=None: {"status": "succeeded", "exit_code": 0, "stdout": "", "stderr": "", "error": ""},
    )
    result = run_service("start", "Spooler")
    assert result["status"] == "succeeded"


def test_normalize_windows_startup_dedupes_and_limits():
    collector = _collector()
    items = [
        {"name": "App A", "command": "C:\\a.exe", "source": "s1", "enabled": True},
        {"name": "app a", "command": "c:\\a.exe", "source": "s2", "enabled": False},
        {"name": "App B", "command": "C:\\b.exe", "source": "s3", "enabled": True},
        "not-a-dict",
        {"name": "App A", "command": "C:\\a.exe", "source": "s4", "enabled": True},
    ]
    result = collector._normalize_windows_startup(items)
    # Deduplication is by (name.casefold(), command.casefold()); non-dict items are skipped.
    assert len(result) == 2
    assert result[0]["name"] == "App A"
    assert result[0]["command"] == "C:\\a.exe"
    assert result[1]["name"] == "App B"
    assert result[1]["command"] == "C:\\b.exe"
    assert all(isinstance(r, dict) for r in result)


def test_normalize_windows_startup_truncates_long_strings():
    collector = _collector()
    items = [
        {"name": "x" * 300, "command": "y" * 3000, "source": "s" * 200, "enabled": True},
    ]
    result = collector._normalize_windows_startup(items)
    assert len(result) == 1
    assert len(result[0]["name"]) == 255
    assert len(result[0]["command"]) == 2048
    assert len(result[0]["source"]) == 128


def test_normalize_windows_startup_caps_at_300():
    collector = _collector()
    items = [{"name": f"n{i}", "command": f"c{i}", "source": "s", "enabled": True} for i in range(400)]
    result = collector._normalize_windows_startup(items)
    assert len(result) == 300


def test_normalize_windows_startup_empty():
    collector = _collector()
    assert collector._normalize_windows_startup([]) == []

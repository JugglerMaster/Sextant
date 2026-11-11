from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

from sextant_control.docker_store import ComposeLibrary
from sextant_agent.docker import DockerCollector
from tests.test_docker_agent import _logged_commands, make_config


def compose_library(tmp_path: Path) -> ComposeLibrary:
    library = ComposeLibrary(
        tmp_path / "server-compose",
        max_file_bytes=262144,
        max_files=16,
        max_total_bytes=1048576,
        max_bundle_bytes=4194304,
    )
    library.ensure()
    return library


def compose_bundle(tmp_path: Path, compose: str | None = None, env: str = "TOKEN=abc\n"):
    library = compose_library(tmp_path)
    files = {
        "compose.yaml": compose or "services:\n  web:\n    image: nginx:latest\n",
        ".env": env,
    }
    revision, _manifest = library.save_revision("web", files)
    data = library.build_bundle("web", revision)
    headers = {
        "x-bundle-sha256": hashlib.sha256(data).hexdigest(),
        "content-length": str(len(data)),
        "x-compose-revision": revision,
        "x-compose-pull": "0",
        "x-compose-remove-volumes": "0",
    }
    return library, revision, data, headers


def compose_collector(tmp_path: Path, *, allow_compose: bool = True) -> DockerCollector:
    config = make_config(
        tmp_path,
        allow_docker_compose=allow_compose,
        docker_compose_dir=str(tmp_path / "agent-stacks"),
    )
    value = DockerCollector(config)
    value.set_profile(
        {
            "modules": {"docker": True},
            "actions": {
                "allow_docker": True,
                "allow_docker_compose": allow_compose,
            },
        }
    )
    value.set_policy(
        {
            "allow_docker": True,
            "allow_docker_compose": allow_compose,
        }
    )
    return value


def args(deployment_id: str = "dep_abc", revision: str = "a" * 64, project: str = "web"):
    return {
        "action": "compose.up",
        "deployment_id": deployment_id,
        "stack_id": "stk_abc",
        "revision_hash": revision,
        "project_name": project,
    }


def manual_bundle(files: dict[str, bytes], *, mode: str = "file") -> tuple[bytes, dict[str, str], str]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if mode == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                info.size = 0
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    revision = ComposeLibrary.revision_hash(files)
    headers = {
        "x-bundle-sha256": hashlib.sha256(data).hexdigest(),
        "content-length": str(len(data)),
    }
    return data, headers, revision


def test_compose_up_config_and_pull_order(tmp_path: Path, monkeypatch):
    _library, revision, data, headers = compose_bundle(tmp_path)
    headers["x-compose-pull"] = "1"
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = compose_collector(tmp_path)
    result = value.execute_compose("compose.up", args(revision=revision), data, headers)
    assert result["status"] == "succeeded", result
    commands = _logged_commands(log)
    compose_commands = [command for command in commands if command[:1] == ["compose"]]
    assert compose_commands[0][-2:] == ["config", "--quiet"]
    assert any("pull" in command for command in compose_commands)
    assert any("up" in command and "-d" in command and "--remove-orphans" in command for command in compose_commands)
    final = tmp_path / "agent-stacks" / "web" / revision
    assert (final / "compose.yaml").is_file()
    assert (final / ".env").read_text() == "TOKEN=abc\n"


def test_compose_down_volumes_are_opt_in(tmp_path: Path, monkeypatch):
    _library, revision, data, headers = compose_bundle(tmp_path)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    value = compose_collector(tmp_path)
    result = value.execute_compose("compose.down", args(revision=revision), data, headers)
    assert result["status"] == "succeeded"
    commands = _logged_commands(log)
    assert any(command[:1] == ["compose"] and "down" in command for command in commands)
    assert not any("--volumes" in command for command in commands)

    headers["x-compose-remove-volumes"] = "1"
    result = value.execute_compose("compose.down", args(revision=revision), data, headers)
    assert result["status"] == "succeeded"
    commands = _logged_commands(log)
    assert any("--volumes" in command for command in commands)


def test_compose_rejects_build_services(tmp_path: Path):
    files = {
        "compose.yaml": b"services:\n  web:\n    build: .\n",
        ".env": b"TOKEN=abc\n",
    }
    data, headers, revision = manual_bundle(files)
    value = compose_collector(tmp_path)
    result = value.execute_compose("compose.up", args(revision=revision), data, headers)
    assert result["status"] == "failed"
    assert "build" in result["error"].lower()
    assert not (tmp_path / "agent-stacks" / "web" / revision).exists()


def test_compose_rejects_traversal_symlink_and_oversize(tmp_path: Path):
    value = compose_collector(tmp_path)
    payload = b"services: {}\n"

    data, _headers, revision = manual_bundle({"compose.yaml": payload, "ok.yaml": b"services: {}\n"})
    # Replace the tar member name check with an explicitly hostile archive.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("compose.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        info = tarfile.TarInfo("../escape.yaml")
        info.size = len(b"escape")
        archive.addfile(info, io.BytesIO(b"escape"))
    data = buffer.getvalue()
    headers = {"x-bundle-sha256": hashlib.sha256(data).hexdigest(), "content-length": str(len(data))}
    result = value.execute_compose("compose.up", args(revision=revision), data, headers)
    assert result["status"] == "failed"
    assert "traversal" in result["error"].lower() or "invalid" in result["error"].lower()

    symlink, symlink_headers, revision = manual_bundle({"compose.yaml": payload}, mode="symlink")
    result = value.execute_compose("compose.up", args(revision=revision), symlink, symlink_headers)
    assert result["status"] == "failed"
    assert "regular files" in result["error"].lower() or "symlink" in result["error"].lower()

    oversize = b"x" * 262145
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("compose.yaml")
        info.size = len(oversize)
        archive.addfile(info, io.BytesIO(oversize))
    data = buffer.getvalue()
    headers = {"x-bundle-sha256": hashlib.sha256(data).hexdigest(), "content-length": str(len(data))}
    result = value.execute_compose("compose.up", args(), data, headers)
    assert result["status"] == "failed"
    assert "size" in result["error"].lower()


def test_compose_rejects_hardlink(tmp_path: Path):
    payload = b"services: {}\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("compose.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("linked.yaml")
        link.type = tarfile.LNKTYPE
        link.linkname = "compose.yaml"
        link.size = 0
        archive.addfile(link)
    data = buffer.getvalue()
    headers = {"x-bundle-sha256": hashlib.sha256(data).hexdigest(), "content-length": str(len(data))}
    result = compose_collector(tmp_path).execute_compose("compose.up", args(), data, headers)
    assert result["status"] == "failed"
    assert "regular files" in result["error"].lower() or "link" in result["error"].lower()


def test_compose_bundle_hash_and_size_rejection(tmp_path: Path):
    _library, revision, data, headers = compose_bundle(tmp_path)
    value = compose_collector(tmp_path)
    bad_hash = dict(headers)
    bad_hash["x-bundle-sha256"] = "0" * 64
    assert value.execute_compose("compose.up", args(revision=revision), data, bad_hash)["status"] == "failed"

    bad_size = dict(headers)
    bad_size["content-length"] = str(len(data) + 1)
    assert value.execute_compose("compose.up", args(revision=revision), data, bad_size)["status"] == "failed"

    mismatch = value.execute_compose("compose.up", args(revision="b" * 64), data, headers)
    assert mismatch["status"] == "failed"
    assert "hash mismatch" in mismatch["error"].lower()


def test_compose_gate_and_project_validation(tmp_path: Path):
    _library, revision, data, headers = compose_bundle(tmp_path)
    disabled = compose_collector(tmp_path, allow_compose=False)
    denied = disabled.execute_compose("compose.up", args(revision=revision), data, headers)
    assert denied["status"] == "failed"
    assert "disabled" in denied["error"].lower()

    invalid = compose_collector(tmp_path)
    result = invalid.execute_compose("compose.up", args(revision=revision, project="Bad Project"), data, headers)
    assert result["status"] == "failed"
    assert "project" in result["error"].lower()

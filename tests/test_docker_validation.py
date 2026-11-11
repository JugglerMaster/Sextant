from __future__ import annotations

import pytest
from pydantic import ValidationError

from sextant_control.docker_rules import DOCKER_CLEANUP_SCOPES, normalize_docker_command_args
from sextant_control.models import (
    DockerActionIn,
    DockerCleanupIn,
    DockerContainerLogsIn,
    DockerContainerRenameIn,
    DockerContainerUpdateIn,
    DockerExecIn,
    DockerImageRemoveIn,
    DockerNetworkRemoveIn,
    DockerVolumeRemoveIn,
)

CID = "a" * 64
IID = "sha256:" + "b" * 64
NID = "c" * 64


def test_container_lifecycle_models_and_invalid_ids():
    action = DockerActionIn.model_validate({"action": "container.start", "container_id": CID})
    assert action.root.action == "container.start"
    assert action.root.container_id == CID
    with pytest.raises(ValidationError):
        DockerActionIn.model_validate({"action": "container.start", "container_id": "bad!"})
    with pytest.raises(ValidationError):
        DockerActionIn.model_validate({"action": "container.start", "container_id": CID, "extra": 1})


def test_container_rename_and_remove_models():
    renamed = DockerContainerRenameIn(action="container.rename", container_id=CID, new_name="web-2")
    assert renamed.new_name == "web-2"
    with pytest.raises(ValidationError):
        DockerContainerRenameIn(action="container.rename", container_id=CID, new_name="-bad")
    removed = DockerActionIn.model_validate(
        {"action": "container.remove", "container_id": CID, "force": True, "remove_volumes": True}
    )
    assert removed.root.force is True
    assert removed.root.remove_volumes is True


def test_container_update_validation():
    update = DockerContainerUpdateIn(
        action="container.update",
        container_id=CID,
        restart_policy="on-failure:3",
        memory="512m",
        cpus="1.5",
        pids_limit=256,
    )
    assert update.restart_policy == "on-failure:3"
    for payload in (
        {"action": "container.update", "container_id": CID},
        {"action": "container.update", "container_id": CID, "restart_policy": "sometimes"},
        {"action": "container.update", "container_id": CID, "memory": "lots"},
        {"action": "container.update", "container_id": CID, "cpus": "-1"},
        {"action": "container.update", "container_id": CID, "pids_limit": -1},
    ):
        with pytest.raises(ValidationError):
            DockerContainerUpdateIn(**payload)


def test_logs_and_inspect_models():
    logs = DockerContainerLogsIn(
        action="container.logs", container_id=CID, tail=1000, timestamps=True
    )
    assert logs.tail == 1000
    with pytest.raises(ValidationError):
        DockerContainerLogsIn(action="container.logs", container_id=CID, tail=250)
    inspect = DockerActionIn.model_validate({"action": "container.inspect", "container_id": CID})
    assert inspect.root.action == "container.inspect"


def test_exec_model_validation():
    exec_model = DockerExecIn(
        shell="bash",
        user="root",
        workdir="/srv/app",
        command="id && uname -a",
        timeout=60,
    )
    assert exec_model.shell == "bash"
    for payload in (
        {"shell": "zsh", "command": "id"},
        {"shell": "sh", "command": "id", "user": "bad user"},
        {"shell": "sh", "command": "id", "workdir": "relative"},
        {"shell": "sh", "command": ""},
        {"shell": "sh", "command": "id", "timeout": 4},
        {"shell": "sh", "command": "id", "timeout": 301},
        {"shell": "sh", "command": "id\x00"},
    ):
        with pytest.raises(ValidationError):
            DockerExecIn(**payload)


def test_resource_remove_models():
    assert DockerImageRemoveIn(action="image.remove", image_id=IID).image_id == IID
    assert DockerImageRemoveIn(action="image.remove", image_id="d" * 64).image_id == "d" * 64
    assert (
        DockerVolumeRemoveIn(action="volume.remove", volume_name="web_data").volume_name
        == "web_data"
    )
    assert DockerNetworkRemoveIn(action="network.remove", network_id=NID).network_id == NID
    with pytest.raises(ValidationError):
        DockerImageRemoveIn(action="image.remove", image_id="not-an-image")
    with pytest.raises(ValidationError):
        DockerVolumeRemoveIn(action="volume.remove", volume_name="../escape")
    with pytest.raises(ValidationError):
        DockerNetworkRemoveIn(action="network.remove", network_id="bridge")


def test_cleanup_model_scopes():
    for scope in DOCKER_CLEANUP_SCOPES:
        assert DockerCleanupIn(scope=scope).scope == scope
    with pytest.raises(ValidationError):
        DockerCleanupIn(scope="everything")
    with pytest.raises(ValidationError):
        DockerCleanupIn(scope="dangling_images", extra=True)


def test_normalize_command_args_round_trips():
    for payload in (
        {"action": "container.start", "container_id": CID},
        {"action": "container.rename", "container_id": CID, "new_name": "web-2"},
        {
            "action": "container.remove",
            "container_id": CID,
            "force": True,
            "remove_volumes": False,
        },
        {
            "action": "container.update",
            "container_id": CID,
            "restart_policy": "always",
            "memory": "1g",
            "cpus": "2",
            "pids_limit": 100,
        },
        {"action": "container.logs", "container_id": CID, "tail": 500, "timestamps": True},
        {"action": "container.inspect", "container_id": CID},
        {
            "action": "container.exec_one_shot",
            "container_id": CID,
            "shell": "ash",
            "user": "",
            "workdir": "/",
            "command": "echo ok",
            "timeout": 30,
        },
        {"action": "image.remove", "image_id": IID},
        {"action": "volume.remove", "volume_name": "web_data"},
        {"action": "network.remove", "network_id": NID},
        {"action": "cleanup", "scope": "dangling_images", "include_compose": False},
        {"action": "inventory.refresh"},
    ):
        assert normalize_docker_command_args(payload)["action"] == payload["action"]


def test_normalize_command_args_rejects_raw_or_malformed_values():
    for payload in (
        {"action": "container.start", "container_id": CID, "flags": "--privileged"},
        {"action": "container.start", "container_id": "abc"},
        {"action": "container.rename", "container_id": CID, "new_name": "../x"},
        {"action": "container.logs", "container_id": CID, "tail": 42},
        {"action": "container.exec_one_shot", "container_id": CID, "shell": "zsh", "command": "id"},
        {"action": "container.exec_one_shot", "container_id": CID, "shell": "sh", "command": "id", "workdir": "relative"},
        {"action": "container.exec_one_shot", "container_id": CID, "shell": "sh", "command": "id", "timeout": 3},
        {"action": "image.remove", "image_id": "repo:tag"},
        {"action": "cleanup", "scope": "all"},
        {"action": "container.nuke", "container_id": CID},
    ):
        with pytest.raises(ValueError):
            normalize_docker_command_args(payload)

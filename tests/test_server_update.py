from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sextant_control import selfupdate, updates
from sextant_control.config import Settings
from sextant_control.server import create_app
from tests.helpers import headers, login

SHA = "a" * 64


def _asset(version="9.9.9", **overrides):
    asset = {
        "name": f"sextant-{version}-py3-none-any.whl",
        "size": 128,
        "url": "https://example.test/sextant.whl",
        "sha256": SHA,
        "arch": "",
        "kind": "wheel",
        "version": version,
    }
    asset.update(overrides)
    return asset


def _status(version="9.9.9", **overrides):
    asset = overrides.pop("asset", _asset(version))
    data = {
        "enabled": True,
        "feed": "github:owner/repo",
        "feed_kind": "github",
        "feed_state": "ok",
        "current_version": "0.5.1",
        "latest_version": version,
        "update_available": True,
        "url": "https://example.test/releases",
        "notes": "Release notes",
        "published_at": "2026-11-12T00:00:00Z",
        "assets": [asset],
        "selected_asset": asset,
        "hash_available": bool(asset.get("sha256")),
        "apply_available": bool(asset.get("sha256") and asset.get("size") and asset.get("url")),
        "apply_disabled_reason": "",
        "checked_at": "2026-11-12T00:00:00Z",
        "error": "",
    }
    data.update(overrides)
    return data


def _settings(settings: Settings, tmp_path: Path, **overrides) -> Settings:
    values = {
        "update_feed": "github:owner/repo",
        "update_check_hours": 24,
        "update_release_root": str(tmp_path / "releases"),
        "update_current_link": str(tmp_path / "current"),
        "update_restart_trigger": str(tmp_path / "restart-request"),
        "update_download_max_mb": 16,
    }
    values.update(overrides)
    return replace(settings, **values)


def _audit_actions(app) -> list[tuple[str, str, str]]:
    with app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT action, target, detail FROM audit_log WHERE action LIKE 'update.%' ORDER BY id"
        ).fetchall()
    return [(row["action"], row["target"], row["detail"]) for row in rows]


def test_update_check_hours_default_and_bounds(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        static_dir=Path(__file__).resolve().parents[1] / "sextant_control" / "static",
        secret_key=b"x" * 32,
    )
    assert settings.update_check_hours == 24

    def from_env(value):
        return Settings.from_env(
            {
                "SEXTANT_DATA_DIR": str(tmp_path / "env-data"),
                "SEXTANT_STATIC_DIR": str(settings.static_dir),
                "SEXTANT_UPDATE_CHECK_HOURS": str(value),
            }
        ).update_check_hours

    assert from_env(1) == 1
    assert from_env(24) == 24
    assert from_env(168) == 168
    assert from_env(0) == 1
    assert from_env(999) == 168


def test_asset_sha_and_arch_selection():
    assets = [
        updates.normalize_asset(
            {
                "name": "sextant-9.9.9-cp312-cp312-manylinux_aarch64.whl",
                "url": "https://example.test/arm.whl",
                "digest": "sha256:" + "b" * 64,
                "size": 10,
                "arch": "aarch64",
            },
            version="9.9.9",
        ),
        updates.normalize_asset(
            {
                "name": "sextant-9.9.9-py3-none-any.whl",
                "url": "https://example.test/any.whl",
                "sha256": "c" * 64,
                "size": 20,
            },
            version="9.9.9",
        ),
    ]
    assert assets[0]["sha256"] == "b" * 64
    assert updates.select_server_asset(assets, "9.9.9", arch="aarch64")["name"].endswith(
        "aarch64.whl"
    )
    assert updates.select_server_asset(assets, "9.9.9", arch="x86_64")["name"].endswith(
        "py3-none-any.whl"
    )


def test_release_without_hash_is_shown_but_apply_disabled(monkeypatch):
    asset = _asset(sha256="")
    monkeypatch.setattr(
        updates,
        "_github_release",
        lambda repo: {
            "version": "9.9.9",
            "url": "https://example.test/releases",
            "notes": "notes",
            "published_at": "",
            "assets": [asset],
        },
    )
    result = updates.check_update("github:owner/repo", current_version="0.5.1")
    assert result["update_available"] is True
    assert result["hash_available"] is False
    assert result["apply_available"] is False
    assert "SHA-256" in result["apply_disabled_reason"]


def test_daily_cache_and_check_now(app, client, admin, settings, monkeypatch):
    calls = {"n": 0}

    def fake(feed, current_version=updates.__version__, **_kwargs):
        calls["n"] += 1
        return _status(checked_at=f"2026-11-12T0{calls['n']}:00:00Z")

    monkeypatch.setattr(updates, "check_update", fake)
    app.state.update_cache = {"at": 0.0, "data": None}
    app.state.settings = _settings(settings, settings.data_dir)

    first = client.get("/api/update/status", headers=headers(admin))
    second = client.get("/api/update/status", headers=headers(admin))
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["n"] == 1
    assert first.json()["check_interval_hours"] == 24
    assert first.json()["last_checked"]

    forced = client.post("/api/update/check", headers=headers(admin))
    assert forced.status_code == 200
    assert forced.json()["checked_now"] is True
    assert calls["n"] == 2

    app.state.update_cache["at"] = time.monotonic() - 25 * 3600
    client.get("/api/update/status", headers=headers(admin))
    assert calls["n"] == 3

    actions = [row[0] for row in _audit_actions(app)]
    assert "update.check" in actions
    assert "update.check_now" in actions


@contextmanager
def _artifact_server(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            status, extra_headers, body = route
            self.send_response(status)
            for key, value in extra_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_download_verifies_size_and_sha(tmp_path):
    data = b"wheel-bytes"
    sha = hashlib.sha256(data).hexdigest()
    with _artifact_server({"/wheel.whl": (200, {}, data)}) as base:
        destination = tmp_path / "wheel.whl"
        result = selfupdate.download_artifact(
            f"{base}/wheel.whl",
            destination,
            expected_size=len(data),
            expected_sha256=sha,
            max_bytes=1024,
        )
        assert result == {"size": len(data), "sha256": sha}
        assert destination.read_bytes() == data


def test_download_rejects_bad_size_and_hash(tmp_path):
    data = b"wheel-bytes"
    with _artifact_server({"/wheel.whl": (200, {}, data)}) as base:
        with pytest.raises(selfupdate.UpdateError, match="size"):
            selfupdate.download_artifact(
                f"{base}/wheel.whl",
                tmp_path / "a.whl",
                expected_size=len(data) + 1,
                expected_sha256=hashlib.sha256(data).hexdigest(),
                max_bytes=1024,
            )
        with pytest.raises(selfupdate.UpdateError, match="SHA-256"):
            selfupdate.download_artifact(
                f"{base}/wheel.whl",
                tmp_path / "b.whl",
                expected_size=len(data),
                expected_sha256="0" * 64,
                max_bytes=1024,
            )


def test_download_rejects_unexpected_redirects(tmp_path):
    data = b"ok"
    with _artifact_server(
        {
            "/redirect.whl": (302, {"Location": "file:///etc/passwd"}, b""),
            "/same-host.whl": (302, {"Location": "/wheel.whl"}, b""),
            "/wheel.whl": (200, {}, data),
        }
    ) as base:
        with pytest.raises(selfupdate.UpdateError, match="redirect"):
            selfupdate.download_artifact(
                f"{base}/redirect.whl",
                tmp_path / "a.whl",
                expected_size=len(data),
                expected_sha256=hashlib.sha256(data).hexdigest(),
                max_bytes=1024,
            )
        result = selfupdate.download_artifact(
            f"{base}/same-host.whl",
            tmp_path / "b.whl",
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).hexdigest(),
            max_bytes=1024,
        )
        assert result["size"] == len(data)

    with pytest.raises(selfupdate.UpdateError, match="scheme"):
        selfupdate.validate_download_url(
            "file:///etc/passwd", allowed_hosts={"example.test"}
        )
    with pytest.raises(selfupdate.UpdateError, match="credentials"):
        selfupdate.validate_download_url(
            "https://user:pass@example.test/wheel.whl", allowed_hosts={"example.test"}
        )
    with pytest.raises(selfupdate.UpdateError, match="unexpected host"):
        selfupdate.validate_download_url(
            "https://evil.test/wheel.whl", allowed_hosts={"example.test"}
        )


def _prepare_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    release_root = tmp_path / "releases"
    old = release_root / "0.5.1" / "venv"
    old.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(old.resolve(), target_is_directory=True)
    return release_root, current, tmp_path / "restart-request", tmp_path / "state.json"


def _apply_info(version="9.9.9", **overrides):
    info = _status(version)
    info.update(overrides)
    return info


def test_apply_stages_switches_and_rolls_back(tmp_path, monkeypatch):
    release_root, current, trigger, state_path = _prepare_roots(tmp_path)
    downloaded = {}

    def fake_download(url, destination, **kwargs):
        downloaded.update(kwargs)
        Path(destination).write_bytes(b"wheel")
        return {"size": len(b"wheel"), "sha256": SHA}

    def fake_stage(version, wheel, root):
        venv = Path(root) / version / "venv"
        venv.mkdir(parents=True, exist_ok=True)
        return venv

    monkeypatch.setattr(selfupdate, "download_artifact", fake_download)
    monkeypatch.setattr(selfupdate, "stage_venv", fake_stage)
    monkeypatch.setattr(selfupdate, "health_check", lambda venv, version: (True, ""))

    result = selfupdate.apply_update(
        _apply_info(),
        version="9.9.9",
        release_root=release_root,
        current_link=current,
        trigger_path=trigger,
        state_path=state_path,
        max_bytes=1024,
    )
    assert result["status"] == "switched"
    assert result["restart_requested"] is True
    assert current.resolve() == (release_root / "9.9.9" / "venv").resolve()
    assert downloaded["expected_sha256"] == SHA
    assert trigger.exists()
    assert json.loads(trigger.read_text())["action"] == "apply"
    state = json.loads(state_path.read_text())
    assert state["version"] == "9.9.9"
    assert state["previous_version"] == "0.5.1"
    assert Path(state["previous_target"]).name == "venv"

    rolled = selfupdate.rollback_update(
        release_root=release_root,
        current_link=current,
        trigger_path=trigger,
        state_path=state_path,
    )
    assert rolled["status"] == "rolled_back"
    assert current.resolve() == (release_root / "0.5.1" / "venv").resolve()
    assert json.loads(trigger.read_text())["action"] == "rollback"


def test_apply_health_failure_leaves_current_untouched(tmp_path, monkeypatch):
    release_root, current, trigger, state_path = _prepare_roots(tmp_path)
    old_target = current.resolve()

    monkeypatch.setattr(
        selfupdate,
        "download_artifact",
        lambda url, destination, **kwargs: {"size": 5, "sha256": SHA},
    )

    def fake_stage(version, wheel, root):
        venv = Path(root) / version / "venv"
        venv.mkdir(parents=True, exist_ok=True)
        return venv

    monkeypatch.setattr(selfupdate, "stage_venv", fake_stage)
    monkeypatch.setattr(
        selfupdate, "health_check", lambda venv, version: (False, "import failed")
    )
    with pytest.raises(selfupdate.UpdateError, match="health check"):
        selfupdate.apply_update(
            _apply_info(),
            version="9.9.9",
            release_root=release_root,
            current_link=current,
            trigger_path=trigger,
            state_path=state_path,
            max_bytes=1024,
        )
    assert current.resolve() == old_target
    assert not (release_root / "9.9.9").exists()
    assert not state_path.exists()


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"update_available": False}, "No newer release"),
        ({"latest_version": "8.8.8"}, "not the latest"),
        ({"selected_asset": _asset(sha256="")}, "SHA-256"),
        ({"selected_asset": _asset(size=0)}, "size"),
        ({"selected_asset": _asset(url="")}, "download URL"),
    ],
)
def test_apply_rejects_incomplete_or_wrong_target(tmp_path, overrides, message):
    release_root, current, trigger, state_path = _prepare_roots(tmp_path)
    info = _apply_info(**overrides)
    with pytest.raises(selfupdate.UpdateError, match=message):
        selfupdate.apply_update(
            info,
            version="9.9.9",
            release_root=release_root,
            current_link=current,
            trigger_path=trigger,
            state_path=state_path,
            max_bytes=1024,
        )


def test_apply_rejects_already_current_version(tmp_path):
    release_root, current, trigger, state_path = _prepare_roots(tmp_path)
    with pytest.raises(selfupdate.UpdateError, match="already installed"):
        selfupdate.apply_update(
            _apply_info(version="0.5.1", current_version="0.5.1"),
            version="0.5.1",
            release_root=release_root,
            current_link=current,
            trigger_path=trigger,
            state_path=state_path,
            max_bytes=1024,
            current_version="0.5.1",
        )


def test_update_endpoints_rbac_csrf_and_audit(settings, tmp_path, monkeypatch):
    config = _settings(settings, tmp_path)
    app = create_app(config)
    monkeypatch.setattr(updates, "check_update", lambda feed, **kwargs: _status())

    applied = []

    def fake_apply(info, **kwargs):
        applied.append((info["latest_version"], kwargs))
        return {
            "status": "switched",
            "version": info["latest_version"],
            "previous_version": "0.5.1",
            "sha256": SHA,
            "size": 128,
            "restart_required": True,
            "restarting": True,
            "restart_requested": True,
            "instructions": "Restarting.",
        }

    monkeypatch.setattr(selfupdate, "apply_update", fake_apply)
    with TestClient(app) as client:
        admin = login(client)

        missing_csrf = client.post("/api/update/apply", json={"version": "9.9.9"})
        assert missing_csrf.status_code == 403

        response = client.post(
            "/api/update/apply",
            json={"version": "9.9.9"},
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        assert response.json()["restarting"] is True
        assert applied and applied[0][0] == "9.9.9"

        wrong = client.post(
            "/api/update/apply",
            json={"version": "8.8.8"},
            headers=headers(admin),
        )
        assert wrong.status_code == 409

        client.post(
            "/api/users",
            json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
            headers=headers(admin),
        )
        viewer = login(client, "viewer1", "viewer password 123")
        assert (
            client.post(
                "/api/update/apply",
                json={"version": "9.9.9"},
                headers=headers(viewer),
            ).status_code
            == 403
        )
        assert (
            client.post("/api/update/check", headers=headers(viewer)).status_code == 403
        )
        assert (
            client.post("/api/update/rollback", headers=headers(viewer)).status_code
            == 403
        )

    actions = [row[0] for row in _audit_actions(app)]
    assert "update.apply" in actions


def test_apply_endpoint_disabled_without_hash(settings, tmp_path, monkeypatch):
    config = _settings(settings, tmp_path)
    app = create_app(config)
    status = _status(asset=_asset(sha256=""))
    status["hash_available"] = False
    status["apply_available"] = False
    status["apply_disabled_reason"] = "The release does not publish a SHA-256 digest for the server wheel."
    monkeypatch.setattr(updates, "check_update", lambda feed, **kwargs: status)
    with TestClient(app) as client:
        admin = login(client)
        response = client.post(
            "/api/update/apply",
            json={"version": "9.9.9"},
            headers=headers(admin),
        )
        assert response.status_code == 409
        assert "SHA-256" in response.json()["detail"]


def test_rollback_endpoint_and_audit(settings, tmp_path, monkeypatch):
    config = _settings(settings, tmp_path)
    app = create_app(config)
    monkeypatch.setattr(updates, "check_update", lambda feed, **kwargs: _status())
    monkeypatch.setattr(
        selfupdate,
        "rollback_update",
        lambda **kwargs: {
            "status": "rolled_back",
            "version": "0.5.1",
            "rolled_back_from": "9.9.9",
            "restart_required": True,
            "restarting": True,
            "restart_requested": True,
            "instructions": "Restarting.",
        },
    )
    with TestClient(app) as client:
        admin = login(client)
        response = client.post("/api/update/rollback", headers=headers(admin))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rolled_back"
    actions = [row[0] for row in _audit_actions(app)]
    assert "update.rollback" in actions


def test_feed_tokens_are_not_audited(settings, tmp_path, monkeypatch):
    config = _settings(
        settings,
        tmp_path,
        update_feed="https://example.test/feed?token=SUPERSECRET",
    )
    app = create_app(config)

    def fake(feed, **kwargs):
        return _status(
            feed="https://example.test/feed?token=SUPERSECRET",
            error="request failed for https://example.test/feed?token=SUPERSECRET",
        )

    monkeypatch.setattr(updates, "check_update", fake)
    with TestClient(app) as client:
        admin = login(client)
        assert client.post("/api/update/check", headers=headers(admin)).status_code == 200
    rows = _audit_actions(app)
    serialized = json.dumps(rows)
    assert "SUPERSECRET" not in serialized
    assert updates.sanitize_feed("https://example.test/feed?token=SUPERSECRET") == (
        "https://example.test/feed"
    )
    assert "SUPERSECRET" not in updates.sanitize_error(
        "GET https://example.test/feed?token=SUPERSECRET failed"
    )

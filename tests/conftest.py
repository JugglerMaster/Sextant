from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sextant_control.config import Settings
from sextant_control.db import Database
from sextant_control.discovery import DiscoveryManager
from sextant_control.domain_join import DomainSecretStore
from sextant_control.docker_console import ConsoleBroker
from sextant_control.docker_store import ComposeLibrary
from sextant_control.discovery_integrations import IntegrationSecretStore
from sextant_control.notifications import NotificationService
from sextant_control.security import LoginRateLimiter
from sextant_control.server import _DashboardState, create_app
from sextant_control.store import ArtifactStore
from tests.helpers import ADMIN_PASSWORD, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"


def _test_settings(data_dir: Path) -> Settings:
    return Settings(
        data_dir=data_dir,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        offline_after_seconds=90,
        enrollment_ttl_minutes=30,
    )


@pytest.fixture(scope="session")
def app(tmp_path_factory: pytest.TempPathFactory) -> "TestApp":
    """Build the FastAPI app (routes + middleware) once per session/worker.

    ``create_app`` is idempotent on an existing DB, so the app's initial DB
    doubles as the seed schema that per-test DBs are copied from. The seed path
    is captured before any per-test fixture swaps ``app.state.db``.
    """
    seed_dir = tmp_path_factory.mktemp("seed-")
    app = create_app(_test_settings(seed_dir))
    app.state._seed_db_path = app.state.db.path
    return app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return _test_settings(tmp_path)


@pytest.fixture()
def db(settings: Settings, app: "TestApp") -> Database:
    """Fresh per-test DB: copy the session seed schema, then point the compose
    root at this test's data dir."""
    db_path = settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Force a WAL checkpoint on the seed, then copy via backup API.
    src = sqlite3.connect(str(app.state._seed_db_path))
    src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dst = sqlite3.connect(str(db_path))
    src.backup(dst)
    dst.commit()
    src.close()
    dst.close()
    db = Database(db_path)
    db.set_docker_compose_setting(str(settings.docker_compose_root))
    return db


@pytest.fixture()
def _state(app: "TestApp", settings: Settings, db: Database) -> None:
    """Recreate all mutable per-test state on the session app.

    The session app's routes read ``request.app.state.<x>`` at call time, so
    swapping these attributes gives every test a clean, isolated instance while
    the expensive route/middleware registration happens once.
    """
    app.state.settings = settings
    app.state.base_settings = settings
    app.state.system_settings_values = {}
    app.state.db = db
    app.state.notifications = NotificationService(db, settings)
    app.state.limiter = LoginRateLimiter()

    store = ArtifactStore(
        settings.store_dir,
        max_blob_bytes=settings.store_max_blob_mb * 1024 * 1024,
        max_total_bytes=settings.store_max_total_gb * 1024**3,
    )
    store.ensure()
    store.cleanup_temp()
    app.state.store = store

    compose_root = settings.docker_compose_root
    compose_store = ComposeLibrary(
        compose_root,
        max_file_bytes=settings.docker_max_file_bytes,
        max_files=settings.docker_max_files,
        max_total_bytes=settings.docker_max_total_bytes,
        max_bundle_bytes=settings.docker_max_bundle_bytes,
    )
    compose_store.ensure()
    compose_store.cleanup_temp()
    app.state.compose_store = compose_store

    app.state.console_broker = ConsoleBroker(
        max_per_agent=settings.docker_console_max_sessions,
        max_input_bytes=settings.docker_console_max_input_bytes,
        max_output_bytes=settings.docker_console_max_output_bytes,
    )

    app.state.update_cache = {"at": 0.0, "data": None}
    dashboard_state = _DashboardState()
    app.state.dashboard_state = dashboard_state

    def notify_dashboard() -> None:
        dashboard_state.notify()

    app.state.discovery_manager = DiscoveryManager(
        db,
        settings.discovery,
        on_change=notify_dashboard,
    )
    app.state.integration_secrets = IntegrationSecretStore()
    app.state.domain_secrets = DomainSecretStore()


@pytest.fixture()
def client(app: "TestApp", _state: None) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin(client) -> dict:
    return login(client)


class TestApp:  # type: ignore[valid-type]
    """Placeholder type for the FastAPI app fixture (avoids importing FastAPI)."""

    state: object

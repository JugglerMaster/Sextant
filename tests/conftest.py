from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner_control.config import Settings
from runner_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "runner_control" / "static"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        offline_after_seconds=90,
        enrollment_ttl_minutes=30,
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin(client):
    return login(client)

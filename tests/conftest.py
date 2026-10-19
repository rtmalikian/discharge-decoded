import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AI_BACKEND", "stub")

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    return TestClient(app)

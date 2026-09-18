"""Test fixtures.

Every path the application reads from the environment is pointed at a
throwaway directory *before* app.py is imported, because app.py resolves
DB_PATH and IMAGE_ROOT once at import time. Nothing here touches the
repository's own metadata.db or mock_o_drive/.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="atlas-tests-"))
os.environ["ENTDATABASE_DB_PATH"] = str(_TMP / "metadata.db")
os.environ["ENTDATABASE_IMAGE_ROOT"] = str(_TMP / "images")
os.environ["ENTDATABASE_XLSX_PATH"] = str(REPO_ROOT / "sample_data" / "patient_log.xlsx")
os.environ["ENTDATABASE_ALLOW_DB_WRITES"] = "1"
os.environ.pop("ENTDATABASE_OPEN_REGISTRATION", None)
os.environ.pop("ENTDATABASE_DEMO_MODE", None)
os.environ.pop("ENTDATABASE_SESSION_TTL_HOURS", None)

import app as app_module  # noqa: E402  (must follow the environment setup above)


@pytest.fixture(scope="session")
def image_root() -> Path:
    app_module.IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return app_module.IMAGE_ROOT


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def db():
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_accounts():
    """Each test starts with no accounts and no sessions."""
    yield
    conn = sqlite3.connect(app_module.DB_PATH)
    for table in ("sessions", "favorites", "comments", "users"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # table not created yet — nothing to clear
    conn.commit()
    conn.close()

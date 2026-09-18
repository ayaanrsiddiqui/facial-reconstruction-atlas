"""§10.2 item 4, the slice the in-app editor needs.

Every account reaches every case, so without a role any of the twenty-odd
users could edit anybody's case record. This is the smallest thing that makes
an editing endpoint safe to expose: one flag, one dependency.
"""

from __future__ import annotations

import getpass
import sqlite3

import pytest
from fastapi import HTTPException

import app as app_module
import create_account


def _make(monkeypatch, username, *, admin):
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: "correct horse")
    assert create_account.create_account(username, is_admin=admin) == 0


def _request(client, username):
    client.post("/api/auth/login", json={"username": username, "password": "correct horse"})
    from starlette.requests import Request

    token = client.cookies["session_token"]
    return Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"session_token={token}".encode())],
            "method": "GET",
            "path": "/",
        }
    )


def test_a_new_account_is_not_an_administrator(monkeypatch, db):
    _make(monkeypatch, "resident", admin=False)

    assert db.execute("SELECT is_admin FROM users").fetchone()[0] == 0


def test_require_admin_refuses_an_ordinary_user(client, monkeypatch):
    _make(monkeypatch, "resident", admin=False)

    with pytest.raises(HTTPException) as caught:
        app_module.require_admin(_request(client, "resident"))

    assert caught.value.status_code == 403


def test_require_admin_allows_an_administrator(client, monkeypatch):
    _make(monkeypatch, "curator", admin=True)

    user = app_module.require_admin(_request(client, "curator"))

    assert user["username"] == "curator"
    assert user["is_admin"] == 1


def test_require_admin_refuses_an_anonymous_caller(client):
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})

    with pytest.raises(HTTPException) as caught:
        app_module.require_admin(request)
    assert caught.value.status_code == 401


def test_the_demo_account_is_never_an_administrator(client, monkeypatch):
    """Anonymous browsing on the public demo must not reach an editing endpoint."""
    monkeypatch.setenv("ENTDATABASE_DEMO_MODE", "1")
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})

    assert app_module.require_user(request) == app_module.DEMO_USER
    with pytest.raises(HTTPException) as caught:
        app_module.require_admin(request)
    assert caught.value.status_code == 403


def test_me_reports_the_flag(client, monkeypatch):
    _make(monkeypatch, "curator", admin=True)
    client.post("/api/auth/login", json={"username": "curator", "password": "correct horse"})

    assert client.get("/api/auth/me").json()["is_admin"] is True


def test_an_existing_database_gains_the_flag_without_granting_anything(tmp_path):
    """Upgrading must not quietly make existing accounts administrators."""
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO users (username, password_hash) VALUES ('old', 'x');
        """
    )
    conn.commit()

    app_module.ensure_auth_schema(conn)
    conn.commit()

    assert conn.execute("SELECT is_admin FROM users WHERE username = 'old'").fetchone()[0] == 0
    conn.close()


def test_listing_accounts_shows_the_role(monkeypatch, capsys):
    """Covered because the first version of --list selected the wrong columns."""
    _make(monkeypatch, "resident", admin=False)
    _make(monkeypatch, "curator", admin=True)
    capsys.readouterr()

    assert create_account.list_accounts() == 0

    out = capsys.readouterr().out
    assert "resident" in out and "reads only" in out
    assert "curator" in out and "administrator" in out


def test_promoting_an_unknown_account_is_refused(monkeypatch, capsys):
    assert create_account.set_admin("nobody", is_admin=True) == 2

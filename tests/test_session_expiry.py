"""§10.4 — sessions expire server-side.

The gap being covered: get_current_user() selected on the token alone. The
30-day cookie max_age was the only lifetime, and a cookie is client state — a
token kept from the cookie jar stayed valid indefinitely.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module


def _sign_in(client, monkeypatch, username="invited", password="hunter2"):
    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    monkeypatch.delenv("ENTDATABASE_OPEN_REGISTRATION")
    return client.cookies["session_token"]


def _age_session(db, token, *, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    db.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (stamp, token))
    db.commit()


def test_a_fresh_session_is_accepted(client, monkeypatch):
    _sign_in(client, monkeypatch)

    assert client.get("/api/auth/me").json()["username"] == "invited"
    assert client.get("/api/search").status_code == 200


def test_an_expired_session_cannot_read_case_data(client, db, monkeypatch):
    token = _sign_in(client, monkeypatch)
    _age_session(db, token, hours=1)

    assert client.get("/api/auth/me").json()["username"] is None
    assert client.get("/api/search").status_code == 401
    assert client.get("/api/cases/ENT-001").status_code == 401
    assert client.get("/api/image/ENT-001/01_preop.jpg").status_code == 401


def test_an_expired_session_row_is_deleted(client, db, monkeypatch):
    token = _sign_in(client, monkeypatch)
    _age_session(db, token, hours=1)

    client.get("/api/auth/me")

    assert db.execute(
        "SELECT COUNT(*) FROM sessions WHERE token = ?", (token,)
    ).fetchone()[0] == 0


def test_an_unreadable_expiry_is_treated_as_expired(client, db, monkeypatch):
    """Fail closed: a value the database cannot read as a date is not a licence."""
    token = _sign_in(client, monkeypatch)
    db.execute("UPDATE sessions SET expires_at = 'whenever' WHERE token = ?", (token,))
    db.commit()

    assert client.get("/api/search").status_code == 401


def test_expiry_is_taken_from_the_environment(client, db, monkeypatch):
    monkeypatch.setenv("ENTDATABASE_SESSION_TTL_HOURS", "2")
    token = _sign_in(client, monkeypatch)

    stored = db.execute(
        "SELECT expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()[0]
    expected = datetime.now(timezone.utc) + timedelta(hours=2)
    actual = datetime.strptime(stored, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    assert abs((actual - expected).total_seconds()) < 60


def test_cookie_lifetime_matches_the_server_side_ttl(client, monkeypatch):
    """Otherwise the browser keeps a cookie the server has already stopped honouring."""
    monkeypatch.setenv("ENTDATABASE_SESSION_TTL_HOURS", "2")
    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")

    response = client.post(
        "/api/auth/register", json={"username": "invited", "password": "hunter2"}
    )

    assert "Max-Age=7200" in response.headers["set-cookie"]


@pytest.mark.parametrize("value", ["0", "-3", "twelve", "12.5", "1h"])
def test_a_misconfigured_ttl_refuses_to_run(monkeypatch, value):
    monkeypatch.setenv("ENTDATABASE_SESSION_TTL_HOURS", value)

    with pytest.raises(ValueError):
        app_module.session_ttl_hours()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_ttl_defaults_when_unset_or_blank(monkeypatch, value):
    """Blank reads as unset — that is how a platform writes "no value set"."""
    if value is None:
        monkeypatch.delenv("ENTDATABASE_SESSION_TTL_HOURS", raising=False)
    else:
        monkeypatch.setenv("ENTDATABASE_SESSION_TTL_HOURS", value)

    assert app_module.session_ttl_hours() == app_module.DEFAULT_SESSION_TTL_HOURS


def test_sessions_predating_the_expiry_column_are_cleared(tmp_path):
    """Migration: a database written by the previous version must not keep
    serving sessions the expiry check cannot evaluate."""
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
        CREATE TABLE sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        INSERT INTO users (id, username, password_hash) VALUES (1, 'old', 'x');
        INSERT INTO sessions (token, user_id) VALUES ('old-token', 1);
        """
    )
    conn.commit()

    app_module.ensure_auth_schema(conn)
    conn.commit()

    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "expires_at" in columns
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1, (
        "the migration must not take accounts with it"
    )
    conn.close()

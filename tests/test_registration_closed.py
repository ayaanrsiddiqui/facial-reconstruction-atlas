"""§10.4 — registration is closed unless explicitly opened.

The gap being covered: /api/auth/register used to be guarded only by
require_writes(), which gates database writes and says nothing about who is
asking. With writes enabled — the normal internal configuration — anyone who
could reach the application could create an account and thereby reach every
case.
"""

from __future__ import annotations

import app as app_module


def test_register_is_404_by_default(client, db):
    response = client.post(
        "/api/auth/register", json={"username": "stranger", "password": "hunter2"}
    )

    assert response.status_code == 404
    assert "session_token" not in client.cookies
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_register_is_404_even_though_writes_are_enabled(client):
    """The distinction the old guard missed: writes on, identity still closed."""
    assert app_module.database_writes_allowed()

    assert client.post(
        "/api/auth/register", json={"username": "stranger", "password": "hunter2"}
    ).status_code == 404


def test_register_works_when_explicitly_opened(client, db, monkeypatch):
    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")

    response = client.post(
        "/api/auth/register", json={"username": "invited", "password": "hunter2"}
    )

    assert response.status_code == 200
    assert response.json() == {"username": "invited"}
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert client.get("/api/search").status_code == 200


def test_login_still_works_with_registration_closed(client, monkeypatch):
    """Accounts made out of band must still be able to sign in."""
    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")
    client.post("/api/auth/register", json={"username": "invited", "password": "hunter2"})
    client.post("/api/auth/logout")
    monkeypatch.delenv("ENTDATABASE_OPEN_REGISTRATION")

    response = client.post(
        "/api/auth/login", json={"username": "invited", "password": "hunter2"}
    )

    assert response.status_code == 200
    assert client.get("/api/search").status_code == 200


def test_me_reports_whether_registration_is_open(client, monkeypatch):
    """The sign-in screen needs this before anyone has a session."""
    anonymous = client.get("/api/auth/me")
    assert anonymous.status_code == 200
    assert anonymous.json() == {
        "username": None,
        "demo": False,
        "registration_open": False,
        "is_admin": False,
    }

    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")
    assert client.get("/api/auth/me").json()["registration_open"] is True


def test_anonymous_still_cannot_read_case_data(client):
    """Relaxing /api/auth/me to 200 must not relax anything else."""
    for path in ("/api/search", "/api/filters", "/api/anatomy", "/api/cases/ENT-001"):
        assert client.get(path).status_code == 401, path

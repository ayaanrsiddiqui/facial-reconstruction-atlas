"""§10.4 — the out-of-band path for the few accounts needed before SSO."""

from __future__ import annotations

import getpass

import create_account


def _answer_prompts(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda *_args, **_kwargs: next(replies))


def test_created_account_can_sign_in(client, monkeypatch, db):
    """End to end: the CLI's hash has to be one the login route accepts."""
    _answer_prompts(monkeypatch, "correct horse", "correct horse")

    assert create_account.create_account("dr.oyer") == 0
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

    response = client.post(
        "/api/auth/login", json={"username": "dr.oyer", "password": "correct horse"}
    )
    assert response.status_code == 200
    assert client.get("/api/search").status_code == 200


def test_mismatched_confirmation_creates_nothing(monkeypatch, db):
    _answer_prompts(monkeypatch, "correct horse", "correct hose")

    assert create_account.create_account("dr.oyer") == 2
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_rejects_a_password_that_fails_the_policy(monkeypatch, db):
    _answer_prompts(monkeypatch, "short", "short")

    assert create_account.create_account("dr.oyer") == 2
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_rejects_an_invalid_username(monkeypatch, db):
    assert create_account.create_account("no spaces allowed") == 2
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_refuses_to_create_a_duplicate(monkeypatch, db):
    _answer_prompts(monkeypatch, "correct horse", "correct horse", "other pass", "other pass")
    assert create_account.create_account("dr.oyer") == 0

    assert create_account.create_account("dr.oyer") == 2
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

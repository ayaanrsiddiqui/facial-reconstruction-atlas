"""The admin-only editing endpoints behind the in-app tool.

These exist so that corrections can be made by whoever maintains the atlas
after the current maintainer has gone, without a shell or a release.
"""

from __future__ import annotations

import getpass
import sqlite3

import pytest

import app as app_module
import create_account

FOLDER = "ENT-005"
FILES = ("01_preop.jpg", "02_defect.jpg", "IMG_9999.jpg")


@pytest.fixture
def case(image_root):
    folder = image_root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (folder / name).write_bytes(b"x")

    conn = sqlite3.connect(app_module.DB_PATH)
    conn.row_factory = sqlite3.Row
    app_module.load_image_overrides(conn)  # creates the table if it is new
    conn.execute("DELETE FROM image_overrides")
    app_module.recreate_schema(conn)
    app_module.seed_database(conn)
    conn.commit()
    conn.close()
    return FOLDER


def _sign_in(client, monkeypatch, *, admin):
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: "correct horse")
    name = "curator" if admin else "resident"
    create_account.create_account(name, is_admin=admin)
    client.post("/api/auth/login", json={"username": name, "password": "correct horse"})


def _names(response):
    return [image["filename"] for image in response.json()["images"]]


def test_an_ordinary_user_cannot_see_or_change_the_order(client, case, monkeypatch):
    _sign_in(client, monkeypatch, admin=False)

    assert client.get(f"/api/cases/{case}/images").status_code == 403
    assert client.post(
        f"/api/cases/{case}/images/{FILES[1]}/move", json={"direction": "up"}
    ).status_code == 403
    assert client.post(
        f"/api/cases/{case}/images/{FILES[1]}", json={"stage": "Anything"}
    ).status_code == 403


def test_an_anonymous_caller_gets_401(client, case):
    assert client.get(f"/api/cases/{case}/images").status_code == 401


def test_an_administrator_sees_every_photograph_including_unlabelled(
    client, case, monkeypatch
):
    _sign_in(client, monkeypatch, admin=True)

    response = client.get(f"/api/cases/{case}/images")

    assert response.status_code == 200
    assert _names(response) == ["01_preop.jpg", "02_defect.jpg", "IMG_9999.jpg"]
    assert [i["stage"] for i in response.json()["images"]] == [
        "Pre-op",
        "Defect",
        "Unlabelled",
    ]


def test_moving_up_and_back_down(client, case, monkeypatch):
    _sign_in(client, monkeypatch, admin=True)

    up = client.post(
        f"/api/cases/{case}/images/IMG_9999.jpg/move", json={"direction": "up"}
    )
    assert _names(up) == ["01_preop.jpg", "IMG_9999.jpg", "02_defect.jpg"]

    down = client.post(
        f"/api/cases/{case}/images/IMG_9999.jpg/move", json={"direction": "down"}
    )
    assert _names(down) == ["01_preop.jpg", "02_defect.jpg", "IMG_9999.jpg"]


def test_moving_the_second_photograph_to_the_front(client, case, monkeypatch):
    """There is no photograph for it to follow, which is its own case."""
    _sign_in(client, monkeypatch, admin=True)

    response = client.post(
        f"/api/cases/{case}/images/02_defect.jpg/move", json={"direction": "up"}
    )

    assert _names(response) == ["02_defect.jpg", "01_preop.jpg", "IMG_9999.jpg"]


def test_refuses_to_move_past_the_ends(client, case, monkeypatch):
    _sign_in(client, monkeypatch, admin=True)

    assert client.post(
        f"/api/cases/{case}/images/01_preop.jpg/move", json={"direction": "up"}
    ).status_code == 400
    assert client.post(
        f"/api/cases/{case}/images/IMG_9999.jpg/move", json={"direction": "down"}
    ).status_code == 400
    assert client.post(
        f"/api/cases/{case}/images/01_preop.jpg/move", json={"direction": "sideways"}
    ).status_code == 400


def test_relabelling(client, case, monkeypatch):
    _sign_in(client, monkeypatch, admin=True)

    response = client.post(
        f"/api/cases/{case}/images/IMG_9999.jpg", json={"stage": "Pedicle division"}
    )

    labels = {i["filename"]: i["stage"] for i in response.json()["images"]}
    assert labels["IMG_9999.jpg"] == "Pedicle division"
    assert labels["01_preop.jpg"] == "Pre-op", "other photographs are untouched"


def test_hiding_removes_it_from_the_case_but_not_from_the_editor(
    client, case, monkeypatch
):
    _sign_in(client, monkeypatch, admin=True)

    client.post(f"/api/cases/{case}/images/IMG_9999.jpg", json={"hidden": True})

    detail = client.get(f"/api/cases/{case}").json()
    assert "IMG_9999.jpg" not in [i["filename"] for i in detail["images"]]

    editor = client.get(f"/api/cases/{case}/images")
    hidden = {i["filename"]: i["hidden"] for i in editor.json()["images"]}
    assert hidden["IMG_9999.jpg"] is True, "still listed, so it can be unhidden"

    client.post(f"/api/cases/{case}/images/IMG_9999.jpg", json={"hidden": False})
    detail = client.get(f"/api/cases/{case}").json()
    assert "IMG_9999.jpg" in [i["filename"] for i in detail["images"]]


def test_an_edit_records_who_made_it(client, case, monkeypatch, db):
    """Who changed the case record matters once this outlives its author."""
    _sign_in(client, monkeypatch, admin=True)

    client.post(f"/api/cases/{case}/images/IMG_9999.jpg", json={"stage": "Revision"})

    row = db.execute(
        "SELECT updated_by, updated_at FROM image_overrides WHERE filename = ?",
        ("IMG_9999.jpg",),
    ).fetchone()
    assert row[0] == "curator"
    assert row[1]


def test_an_unknown_photograph_is_404(client, case, monkeypatch):
    _sign_in(client, monkeypatch, admin=True)

    assert client.post(
        f"/api/cases/{case}/images/not_here.jpg", json={"stage": "x"}
    ).status_code == 404


def test_path_segments_are_still_validated(client, case, monkeypatch):
    """§7.3 containment must hold on the new endpoints too."""
    _sign_in(client, monkeypatch, admin=True)

    for path in (
        f"/api/cases/{case}/images/..%2f..%2fsecret.jpg",
        "/api/cases/..%2f..%2fetc/images",
    ):
        assert client.get(path).status_code in (400, 404), path
        assert client.post(path, json={"stage": "x"}).status_code in (400, 404, 405)

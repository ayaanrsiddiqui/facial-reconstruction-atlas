"""§7.3 — the image proxy stays inside the image store.

No behaviour here is new. This pins the three containment checks in place so
that later work on the import pipeline (§10.2 item 1), which changes what
filenames reach this function, cannot quietly loosen them.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import app as app_module


@pytest.fixture
def case_image(image_root):
    folder = image_root / "ENT-001"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "01_preop.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdb not really a jpeg")
    return path


@pytest.fixture
def outside_file(image_root):
    secret = image_root.parent / "outside"
    secret.mkdir(parents=True, exist_ok=True)
    path = secret / "secret.jpg"
    path.write_bytes(b"not for serving")
    return path


def test_serves_a_file_inside_the_store(case_image):
    assert app_module.resolve_safe_image_path("ENT-001", "01_preop.jpg") == case_image


@pytest.mark.parametrize(
    "folder,filename",
    [
        ("..", "01_preop.jpg"),
        ("ENT-001", ".."),
        ("../outside", "secret.jpg"),
        ("ENT-001", "../../outside/secret.jpg"),
        ("ENT-001/../..", "secret.jpg"),
        ("ENT-001", "..\\..\\outside\\secret.jpg"),
        (" ENT-001", "01_preop.jpg"),
        ("", "01_preop.jpg"),
        ("ENT-001", ""),
    ],
)
def test_rejects_traversal_shaped_segments(folder, filename, outside_file):
    with pytest.raises(HTTPException) as caught:
        app_module.resolve_safe_image_path(folder, filename)
    assert caught.value.status_code == 400


@pytest.mark.parametrize("filename", ["notes.txt", "case.pdf", "script.js", "archive.zip", "noextension"])
def test_rejects_extensions_outside_the_allowlist(filename, image_root):
    (image_root / "ENT-001").mkdir(parents=True, exist_ok=True)
    (image_root / "ENT-001" / filename).write_bytes(b"x")

    with pytest.raises(HTTPException) as caught:
        app_module.resolve_safe_image_path("ENT-001", filename)
    assert caught.value.status_code == 400


def test_rejects_a_symlink_that_leaves_the_store(image_root, outside_file):
    link = image_root / "ENT-999"
    if not link.exists():
        link.symlink_to(outside_file.parent, target_is_directory=True)

    with pytest.raises(HTTPException) as caught:
        app_module.resolve_safe_image_path("ENT-999", "secret.jpg")
    assert caught.value.status_code == 400


def test_missing_file_is_a_404_not_a_500(image_root):
    (image_root / "ENT-001").mkdir(parents=True, exist_ok=True)

    with pytest.raises(HTTPException) as caught:
        app_module.resolve_safe_image_path("ENT-001", "99_nothing.jpg")
    assert caught.value.status_code == 404


def test_encoded_traversal_over_http(client, monkeypatch, outside_file):
    monkeypatch.setenv("ENTDATABASE_OPEN_REGISTRATION", "1")
    client.post("/api/auth/register", json={"username": "invited", "password": "hunter2"})
    monkeypatch.delenv("ENTDATABASE_OPEN_REGISTRATION")

    for path in (
        "/api/image/..%2f..%2foutside/secret.jpg",
        "/api/image/ENT-001/..%2f..%2foutside%2fsecret.jpg",
        "/api/image/%2e%2e/outside/secret.jpg",
    ):
        assert client.get(path).status_code in (400, 404), path

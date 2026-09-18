"""§10.2 item 1 — images come from the directory, not from the spreadsheet.

The departmental convention is `pt_<case>_img_<stage>[.<index>]` over six
stages, not every case has every stage, and some files carry a special case
such as `b2` in place of a stage. The rule that matters most here is the last
one: an unrecognised name is kept and shown unlabelled, never dropped.
"""

from __future__ import annotations

import pytest

import app as app_module
import image_enumeration as enum
from field_options import PATIENT_COLUMNS


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ENT-020", "20"),
        ("pt_20", "20"),
        ("pt_020", "20"),      # zero padding must not create a second case
        ("PT_20", "20"),
        ("ENT-023.1", "23.1"), # sub-case
        ("pt_23.1", "23.1"),
        ("pt_23_1", "23.1"),
        ("ENT-1", "1"),
        ("no-digits-here", None),
    ],
)
def test_case_key_reconciles_the_two_numbering_schemes(text, expected):
    assert enum.case_key(text) == expected


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("pt_20_img_1.jpg", (1, 0)),
        ("pt_20_img_6.jpg", (6, 0)),
        ("pt_20_img_3.1.jpg", (3, 1)),      # a stage photographed twice
        ("pt_20_img_5.2.jpg", (5, 2)),
        ("pt_20_img_5_2.jpg", (5, 2)),      # the other separator
        ("PT_20_IMG_4.JPG", (4, 0)),        # case does not matter
        ("pt_23.1_img_2.jpg", (2, 0)),      # sub-case folder numbering
        ("01_preop.jpg", (1, 0)),           # the placeholder convention
        ("06_healed.jpg", (6, 0)),
        ("pt_20_b2.jpg", (None, 0)),        # a special case, not a stage
        ("IMG_4821.jpg", (None, 0)),        # straight off the camera
        ("pt_20_img_9.jpg", (None, 0)),     # outside the six stages
        ("pt_20_img_0.jpg", (None, 0)),
        ("scan.jpg", (None, 0)),
    ],
)
def test_parse_stage(filename, expected):
    assert enum.parse_stage(filename) == expected


def _case(tmp_path, *filenames):
    folder = tmp_path / "pt_20"
    folder.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (folder / name).write_bytes(b"x")
    return folder


def test_orders_by_stage_then_sub_index(tmp_path):
    folder = _case(
        tmp_path,
        "pt_20_img_6.jpg",
        "pt_20_img_3.2.jpg",
        "pt_20_img_1.jpg",
        "pt_20_img_3.jpg",
        "pt_20_img_3.1.jpg",
    )

    assert enum.enumerate_case_images(folder) == [
        ("pt_20_img_1.jpg", "Pre-op", 1),
        ("pt_20_img_3.jpg", "Flap drawn", 2),
        ("pt_20_img_3.1.jpg", "Flap drawn", 3),
        ("pt_20_img_3.2.jpg", "Flap drawn", 4),
        ("pt_20_img_6.jpg", "Post-op", 5),
    ]


def test_an_unrecognised_name_is_kept_and_sorted_last(tmp_path):
    """The exceptions nobody has a complete list of must not disappear."""
    folder = _case(tmp_path, "pt_20_b2.jpg", "pt_20_img_1.jpg", "IMG_4821.jpg")

    rows = enum.enumerate_case_images(folder)

    assert rows == [
        ("pt_20_img_1.jpg", "Pre-op", 1),
        ("IMG_4821.jpg", enum.UNLABELLED, 2),
        ("pt_20_b2.jpg", enum.UNLABELLED, 3),
    ]


def test_missing_stages_are_absent_not_invented(tmp_path):
    folder = _case(tmp_path, "pt_20_img_1.jpg", "pt_20_img_6.jpg")

    assert [stage for _, stage, _ in enum.enumerate_case_images(folder)] == [
        "Pre-op",
        "Post-op",
    ]


def test_skips_files_the_app_cannot_serve(tmp_path):
    folder = _case(tmp_path, "pt_20_img_1.jpg", "notes.txt", "Thumbs.db", "scan.HEIC")

    assert [name for name, _, _ in enum.enumerate_case_images(folder)] == [
        "pt_20_img_1.jpg"
    ]


def test_does_not_flatten_a_subfolder(tmp_path):
    """A subfolder means somebody separated those deliberately."""
    folder = _case(tmp_path, "pt_20_img_1.jpg")
    (folder / "old").mkdir()
    (folder / "old" / "pt_20_img_2.jpg").write_bytes(b"x")

    assert len(enum.enumerate_case_images(folder)) == 1


def test_a_missing_folder_yields_nothing(tmp_path):
    assert enum.enumerate_case_images(tmp_path / "pt_999") == []


def test_folder_index_matches_across_naming_schemes(tmp_path):
    for name in ("pt_1", "pt_020", "pt_23.1", "notacase"):
        (tmp_path / name).mkdir()

    index = enum.index_case_folders(tmp_path)

    assert index["1"] == "pt_1"
    assert index["20"] == "pt_020"
    assert index["23.1"] == "pt_23.1"


def test_seeding_uses_the_share_for_folder_name_and_images(tmp_path, monkeypatch):
    """End to end: folder_name becomes the real directory, patient_id does not."""
    import sqlite3

    share = tmp_path / "share"
    for number in range(1, 13):
        folder = share / f"pt_{number}"
        folder.mkdir(parents=True)
        (folder / f"pt_{number}_img_1.jpg").write_bytes(b"x")
        (folder / f"pt_{number}_img_6.jpg").write_bytes(b"x")
    (share / "pt_4" / "pt_4_b2.jpg").write_bytes(b"x")

    monkeypatch.setattr(app_module, "IMAGE_ROOT", share)
    conn = sqlite3.connect(tmp_path / "seeded.db")
    conn.row_factory = sqlite3.Row
    app_module.recreate_schema(conn)
    app_module.seed_database(conn)
    conn.commit()

    assert PATIENT_COLUMNS[0] == "folder_name"
    row = conn.execute(
        "SELECT folder_name, patient_id FROM patients WHERE patient_id = 'ENT-004'"
    ).fetchone()
    assert row["folder_name"] == "pt_4"
    assert row["patient_id"] == "ENT-004"

    images = conn.execute(
        """
        SELECT filename, stage FROM images
        JOIN patients ON patients.id = images.patient_row_id
        WHERE patients.folder_name = 'pt_4' ORDER BY sort_order
        """
    ).fetchall()
    assert [(r["filename"], r["stage"]) for r in images] == [
        ("pt_4_img_1.jpg", "Pre-op"),
        ("pt_4_img_6.jpg", "Post-op"),
        ("pt_4_b2.jpg", enum.UNLABELLED),
    ]
    conn.close()

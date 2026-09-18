"""Ordering corrections recorded by hand, taken verbatim from the notes file.

These are not naming exceptions. `pt_34_img_3.jpg` parses perfectly well and
says "stage 3"; the department's note says it belongs between the pedicle
division photograph and stage 5. Nothing in the filename can express that, so
it is recorded against the case and reapplied on every import.
"""

from __future__ import annotations

import image_enumeration as enum

PED_DIV = "Pedicle division"


def _derived(*filenames):
    """Rows as enumeration would produce them, before any override."""
    return [
        (name, enum.STAGE_LABELS.get(index, enum.UNLABELLED), index)
        for index, name in enumerate(filenames, start=1)
    ]


def _order(rows):
    return [name for name, _, _ in rows]


def test_pt_34_chain():
    """'ped div goes bw 4 and 5. Stage 3 goes bw ped div and 5'"""
    rows = _derived(
        "pt_34_img_1.jpg",
        "pt_34_img_2.jpg",
        "pt_34_img_3.jpg",
        "pt_34_img_4.jpg",
        "pt_34_img_5.jpg",
        "pt_34_img_6.jpg",
        "pt_34_peddiv.jpg",
    )
    overrides = {
        "pt_34_peddiv.jpg": (PED_DIV, "pt_34_img_4.jpg"),
        "pt_34_img_3.jpg": (None, "pt_34_peddiv.jpg"),
    }

    result, unresolved = enum.apply_overrides(rows, overrides)

    assert _order(result) == [
        "pt_34_img_1.jpg",
        "pt_34_img_2.jpg",
        "pt_34_img_4.jpg",
        "pt_34_peddiv.jpg",
        "pt_34_img_3.jpg",
        "pt_34_img_5.jpg",
        "pt_34_img_6.jpg",
    ]
    assert unresolved == []
    assert result[3] == ("pt_34_peddiv.jpg", PED_DIV, 4)
    assert [order for _, _, order in result] == [1, 2, 3, 4, 5, 6, 7]


def test_pt_38_stage_2_between_4_and_5():
    rows = _derived(*[f"pt_38_img_{n}.jpg" for n in range(1, 7)])

    result, unresolved = enum.apply_overrides(
        rows, {"pt_38_img_2.jpg": (None, "pt_38_img_4.jpg")}
    )

    assert _order(result) == [
        "pt_38_img_1.jpg",
        "pt_38_img_3.jpg",
        "pt_38_img_4.jpg",
        "pt_38_img_2.jpg",
        "pt_38_img_5.jpg",
        "pt_38_img_6.jpg",
    ]
    assert unresolved == []


def test_pt_66_stage_3_between_4_and_5():
    rows = _derived(*[f"pt_66_img_{n}.jpg" for n in range(1, 7)])

    result, _ = enum.apply_overrides(
        rows, {"pt_66_img_3.jpg": (None, "pt_66_img_4.jpg")}
    )

    assert _order(result) == [
        "pt_66_img_1.jpg",
        "pt_66_img_2.jpg",
        "pt_66_img_4.jpg",
        "pt_66_img_3.jpg",
        "pt_66_img_5.jpg",
        "pt_66_img_6.jpg",
    ]


def test_a_moved_image_keeps_its_label_unless_the_override_changes_it():
    rows = _derived(*[f"pt_38_img_{n}.jpg" for n in range(1, 7)])

    result, _ = enum.apply_overrides(
        rows, {"pt_38_img_2.jpg": (None, "pt_38_img_4.jpg")}
    )

    moved = next(r for r in result if r[0] == "pt_38_img_2.jpg")
    assert moved[1] == "Defect"


def test_relabelling_without_moving():
    rows = _derived("pt_54_img_1.jpg", "pt_54_extra.jpg")

    result, _ = enum.apply_overrides(rows, {"pt_54_extra.jpg": (PED_DIV, None)})

    assert result == [
        ("pt_54_img_1.jpg", "Pre-op", 1),
        ("pt_54_extra.jpg", PED_DIV, 2),
    ]


def test_an_anchor_that_no_longer_exists_is_reported_not_dropped():
    """A photograph deleted from the share must not take another one with it."""
    rows = _derived("pt_54_img_1.jpg", "pt_54_img_2.jpg")

    result, unresolved = enum.apply_overrides(
        rows, {"pt_54_img_2.jpg": (PED_DIV, "pt_54_deleted.jpg")}
    )

    assert unresolved == ["pt_54_img_2.jpg"]
    assert _order(result) == ["pt_54_img_1.jpg", "pt_54_img_2.jpg"]
    assert result[1][1] == PED_DIV, "the label still applies even when the anchor does not"


def test_a_cycle_terminates_and_is_reported():
    rows = _derived("pt_1_img_1.jpg", "pt_1_img_2.jpg", "pt_1_img_3.jpg")

    result, unresolved = enum.apply_overrides(
        rows,
        {
            "pt_1_img_2.jpg": (None, "pt_1_img_3.jpg"),
            "pt_1_img_3.jpg": (None, "pt_1_img_2.jpg"),
        },
    )

    assert unresolved == ["pt_1_img_2.jpg", "pt_1_img_3.jpg"]
    assert len(result) == 3, "nothing is lost to a cycle"


def test_an_override_for_a_file_that_is_gone_is_simply_unused():
    rows = _derived("pt_1_img_1.jpg")

    result, unresolved = enum.apply_overrides(
        rows, {"pt_1_vanished.jpg": (PED_DIV, "pt_1_img_1.jpg")}
    )

    assert result == [("pt_1_img_1.jpg", "Pre-op", 1)]
    assert unresolved == []


def test_a_correction_survives_a_full_reimport(tmp_path, monkeypatch):
    """The point of the whole design.

    recreate_schema() drops patients and images and the importer rebuilds them
    from the spreadsheet and the share. A correction somebody made because they
    know the case must not go with them.
    """
    import sqlite3

    import app as app_module

    share = tmp_path / "share"
    for number in range(1, 13):
        folder = share / f"pt_{number}"
        folder.mkdir(parents=True)
        for stage in range(1, 7):
            (folder / f"pt_{number}_img_{stage}.jpg").write_bytes(b"x")
    (share / "pt_4" / "pt_4_peddiv.jpg").write_bytes(b"x")
    monkeypatch.setattr(app_module, "IMAGE_ROOT", share)

    conn = sqlite3.connect(tmp_path / "reimport.db")
    conn.row_factory = sqlite3.Row
    conn.execute(app_module.IMAGE_OVERRIDES_DDL)
    conn.executemany(
        """
        INSERT INTO image_overrides (folder_name, filename, stage, sort_after)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("pt_4", "pt_4_peddiv.jpg", PED_DIV, "pt_4_img_4.jpg"),
            ("pt_4", "pt_4_img_3.jpg", None, "pt_4_peddiv.jpg"),
        ],
    )
    conn.commit()

    def order():
        return [
            (row["filename"], row["stage"])
            for row in conn.execute(
                """
                SELECT filename, stage FROM images
                JOIN patients ON patients.id = images.patient_row_id
                WHERE folder_name = 'pt_4' ORDER BY sort_order
                """
            )
        ]

    expected = [
        ("pt_4_img_1.jpg", "Pre-op"),
        ("pt_4_img_2.jpg", "Defect"),
        ("pt_4_img_4.jpg", "Flap raised"),
        ("pt_4_peddiv.jpg", PED_DIV),
        ("pt_4_img_3.jpg", "Flap drawn"),
        ("pt_4_img_5.jpg", "Flap closed"),
        ("pt_4_img_6.jpg", "Post-op"),
    ]

    app_module.recreate_schema(conn)
    app_module.seed_database(conn)
    conn.commit()
    assert order() == expected

    # Whatever would trigger a re-import — a case added to the spreadsheet,
    # a schema change — runs exactly this.
    app_module.recreate_schema(conn)
    app_module.seed_database(conn)
    conn.commit()
    assert order() == expected, "the correction was lost on re-import"

    assert conn.execute("SELECT COUNT(*) FROM image_overrides").fetchone()[0] == 2
    conn.close()

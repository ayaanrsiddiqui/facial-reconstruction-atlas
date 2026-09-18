"""The survey tool's default output has to be safe to send in an email.

A real case folder may carry identifiers in its filenames. The report exists to
be pasted into a ticket, so a word that appears in only one folder — which is
what a patient's name looks like and what a convention token does not — is
withheld unless it is explicitly asked for. This is easy to reintroduce a leak
into, hence the test.
"""

from __future__ import annotations

import pytest

import inspect_image_share


def _share(tmp_path):
    for case in range(1, 6):
        for stage in range(1, 4):
            path = tmp_path / f"pt_{case}" / f"pt_{case}_img_{stage}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    (tmp_path / "pt_3" / "pt_3_hendricks_preop.jpg").write_bytes(b"x")
    return tmp_path


def test_safe_mode_withholds_a_name(tmp_path, capsys):
    inspect_image_share.survey(_share(tmp_path), show_rare_words=False)

    out = capsys.readouterr().out
    assert "hendricks" not in out.lower()
    assert "2 word(s) appear in exactly one case folder" in out, (
        "the count is reported even though the words are not"
    )
    assert "Withheld" in out


def test_full_mode_shows_it(tmp_path, capsys):
    inspect_image_share.survey(_share(tmp_path), show_rare_words=True)

    assert "hendricks" in capsys.readouterr().out.lower()


def test_reports_the_convention_and_the_exception(tmp_path, capsys):
    inspect_image_share.survey(_share(tmp_path), show_rare_words=False)

    out = capsys.readouterr().out
    assert "pt_#_img_#.jpg" in out, "the dominant shape is the convention"
    assert "pt_#_~_~.jpg" in out, "the exception is still visible as a shape"


def test_extension_is_never_masked(tmp_path, capsys):
    """Masking `.jpg` would hide whether the share holds files the app cannot serve."""
    (tmp_path / "pt_9").mkdir(parents=True)
    (tmp_path / "pt_9" / "pt_9_scan.HEIC").write_bytes(b"x")
    inspect_image_share.survey(_share(tmp_path), show_rare_words=False)

    out = capsys.readouterr().out
    assert ".HEIC" in out
    assert "not served by the app today" in out


def test_writes_nothing_to_the_share(tmp_path, capsys):
    share = _share(tmp_path)
    before = sorted(p.name for p in share.rglob("*"))

    inspect_image_share.survey(share, show_rare_words=False)
    capsys.readouterr()

    assert sorted(p.name for p in share.rglob("*")) == before


@pytest.mark.parametrize(
    "stem,expected",
    [
        ("pt_20_img_3", "3"),           # the convention
        ("pt_20_img_3.1", "3.1"),       # a stage photographed twice
        ("pt_20_img_5.2", "5.2"),
        ("pt_30_img_2_1", "2_1"),       # the other sub-index separator
        ("PT_12_IMG_2", "2"),           # case does not matter
        ("pt_20_b2", "b2"),             # a special case, not a stage
        ("pt_23.1_img_1", "1"),         # sub-case folder numbering
        ("IMG_4821", "4821"),           # camera default, no case prefix
        ("pt_7", ""),                   # nothing left to say about a stage
    ],
)
def test_stage_token_strips_only_the_boilerplate(stem, expected):
    assert inspect_image_share.stage_token(stem) == expected

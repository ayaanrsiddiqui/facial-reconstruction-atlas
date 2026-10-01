"""A publicly reachable deployment serves fabricated data, or it serves nothing.

The public repository contains only the twelve-case fabricated log, so this
holds today by construction. It is enforced because that is a property of how
the repository is arranged rather than of the code, and the cost of being wrong
once is patient data on a public URL.
"""

from __future__ import annotations

import pytest

import app as app_module

SAMPLE = app_module.SAMPLE_LOG


@pytest.fixture
def real_looking_log(tmp_path):
    """Stands in for the department's actual case log."""
    path = tmp_path / "patient_log.xlsx"
    path.write_bytes(SAMPLE.read_bytes() + b"\x00not the sample")
    return path


def test_inert_on_an_internal_deployment(monkeypatch, real_looking_log):
    """The internal deployment is the one that is *supposed* to hold real data."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("ENTDATABASE_PUBLIC_DEMO", raising=False)
    monkeypatch.setattr(app_module, "XLSX_PATH", real_looking_log)

    app_module.require_fabricated_data_only()  # does not raise


def test_passes_when_a_public_deployment_has_the_sample(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_module, "XLSX_PATH", SAMPLE)
    # The suite points the image root at a temp directory; a real public
    # deployment reads the placeholders generated into its own bundle.
    monkeypatch.setattr(app_module, "IMAGE_ROOT", app_module.BASE_DIR / "mock_o_drive")

    app_module.require_fabricated_data_only()


@pytest.mark.parametrize(
    "variable,value", [("VERCEL", "1"), ("ENTDATABASE_PUBLIC_DEMO", "1")]
)
def test_refuses_real_data_on_a_public_deployment(
    monkeypatch, real_looking_log, variable, value
):
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(app_module, "XLSX_PATH", real_looking_log)

    with pytest.raises(RuntimeError, match="fabricated"):
        app_module.require_fabricated_data_only()


def test_refuses_an_image_store_outside_the_deployment(monkeypatch, tmp_path):
    """The O drive share must never be reachable from the public instance."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_module, "XLSX_PATH", SAMPLE)
    monkeypatch.setattr(app_module, "IMAGE_ROOT", tmp_path / "o_drive")

    with pytest.raises(RuntimeError, match="placeholder images only"):
        app_module.require_fabricated_data_only()


def test_allows_the_bundled_image_directory(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_module, "XLSX_PATH", SAMPLE)
    monkeypatch.setattr(app_module, "IMAGE_ROOT", app_module.BASE_DIR / "mock_o_drive")

    app_module.require_fabricated_data_only()


def test_refuses_when_it_cannot_check(monkeypatch, tmp_path):
    """No sample file to compare against means no proof, which means no serving."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_module, "SAMPLE_LOG", tmp_path / "gone.xlsx")

    with pytest.raises(RuntimeError, match="missing"):
        app_module.require_fabricated_data_only()

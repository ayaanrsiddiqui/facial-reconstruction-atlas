"""Vercel build step: generate mock images and seed SQLite."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    os.environ["ENTDATABASE_ALLOW_DB_WRITES"] = "1"

    # Vercel builds from the public repo, where every real case log is
    # gitignored. Stage the fabricated sample data at the path
    # import_patient_log.py expects — the same "cp sample_data/patient_log.xlsx ."
    # step the README has a human run for local setup.
    if not os.getenv("ENTDATABASE_XLSX_PATH") and not any(Path(".").glob("*.xlsx")):
        shutil.copy("sample_data/patient_log.xlsx", "patient_log.xlsx")

    subprocess.check_call([sys.executable, "generate_placeholders.py"])
    from app import init_database

    init_database()
    print("Vercel build complete: mock_o_drive/ and metadata.db are ready.")


if __name__ == "__main__":
    main()

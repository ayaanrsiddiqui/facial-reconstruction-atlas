"""Vercel build step: generate mock images and seed SQLite."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    import os

    os.environ["ENTDATABASE_ALLOW_DB_WRITES"] = "1"
    subprocess.check_call([sys.executable, "generate_placeholders.py"])
    from app import init_database

    init_database()
    print("Vercel build complete: mock_o_drive/ and metadata.db are ready.")


if __name__ == "__main__":
    main()

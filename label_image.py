"""Record a stage or ordering correction for one photograph.

Enumeration reads the image store and derives what it can from the filename.
Some cases need a correction that no filename could carry — the department's
notes say things like "pt_34: ped div goes bw 4 and 5, stage 3 goes bw ped div
and 5", which is knowledge about the case, not about the naming.

Corrections are stored against (folder, filename) in a table the importer does
not rebuild, so re-running the import keeps them. A correction says the
photograph should follow another named photograph, rather than giving it a
number, because that is what the notes say and because a number stops meaning
"between 4 and 5" the moment another photograph is added to the case.

Usage:
    python label_image.py pt_34                                   # show current order
    python label_image.py pt_34 pt_34_peddiv.jpg \\
        --stage "Pedicle division" --after pt_34_img_4.jpg
    python label_image.py pt_34 pt_34_img_3.jpg --after pt_34_peddiv.jpg
    python label_image.py pt_34 pt_34_img_3.jpg --clear
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from app import (
    DB_PATH,
    IMAGE_OVERRIDES_DDL,
    IMAGE_ROOT,
    database_writes_allowed,
    load_image_overrides,
)
from image_enumeration import apply_overrides, case_key, enumerate_case_images, index_case_folders


def _connect() -> sqlite3.Connection:
    if not database_writes_allowed():
        raise SystemExit(
            "This deployment is configured read-only, so no correction can be "
            "saved. Point ENTDATABASE_DB_PATH at the writable database."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(IMAGE_OVERRIDES_DDL)
    return conn


def _resolve_folder(case: str) -> str:
    """Accept `pt_34`, `34` or `ENT-034` and return the folder on the store."""
    index = index_case_folders(IMAGE_ROOT)
    if case in index.values():
        return case
    key = case_key(case)
    if key is not None and key in index:
        return index[key]
    raise SystemExit(
        f"No folder for {case!r} under {IMAGE_ROOT}. "
        f"Known cases: {sorted(index.values())[:10]}"
    )


def _effective(conn: sqlite3.Connection, folder_name: str):
    rows = enumerate_case_images(IMAGE_ROOT / folder_name)
    overrides = load_image_overrides(conn).get(folder_name, {})
    return apply_overrides(rows, overrides)


def show(conn: sqlite3.Connection, folder_name: str) -> int:
    rows, unresolved = _effective(conn, folder_name)
    overrides = load_image_overrides(conn).get(folder_name, {})
    print(f"{folder_name} — {len(rows)} photograph(s) under {IMAGE_ROOT}")
    for filename, stage, order in rows:
        marker = " *" if filename in overrides else "  "
        print(f" {order:3}{marker} {filename:32} {stage}")
    if overrides:
        print("  (* corrected by hand)")
    if unresolved:
        print(f"  unresolved: {unresolved} — the photograph named to follow is gone")
    return 0


def refresh(conn: sqlite3.Connection, folder_name: str) -> None:
    """Rewrite this case's image rows so the correction shows immediately."""
    row = conn.execute(
        "SELECT id FROM patients WHERE folder_name = ?", (folder_name,)
    ).fetchone()
    if row is None:
        print(
            f"  {folder_name} is not in the database yet — the correction is saved and "
            "will apply on the next import.",
            file=sys.stderr,
        )
        return
    rows, _ = _effective(conn, folder_name)
    conn.execute("DELETE FROM images WHERE patient_row_id = ?", (row["id"],))
    conn.executemany(
        "INSERT INTO images (patient_row_id, filename, stage, sort_order) VALUES (?, ?, ?, ?)",
        [(row["id"], filename, stage, order) for filename, stage, order in rows],
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", help="case folder, e.g. pt_34 (or just 34)")
    parser.add_argument("filename", nargs="?", help="the photograph to correct")
    parser.add_argument("--stage", help='replacement label, e.g. "Pedicle division"')
    parser.add_argument("--after", help="filename this photograph should follow")
    parser.add_argument("--note", default="", help="where the correction came from")
    parser.add_argument("--clear", action="store_true", help="remove this correction")
    args = parser.parse_args()

    folder_name = _resolve_folder(args.case)
    conn = _connect()

    if args.filename is None:
        return show(conn, folder_name)

    present = {name for name, _, _ in enumerate_case_images(IMAGE_ROOT / folder_name)}
    if args.filename not in present:
        raise SystemExit(
            f"{args.filename!r} is not a servable photograph in {folder_name}. "
            f"Run `python label_image.py {folder_name}` to see what is."
        )

    if args.clear:
        conn.execute(
            "DELETE FROM image_overrides WHERE folder_name = ? AND filename = ?",
            (folder_name, args.filename),
        )
        conn.commit()
        print(f"Cleared the correction on {folder_name}/{args.filename}.")
    else:
        if args.stage is None and args.after is None:
            parser.error("give --stage, --after, or both (or --clear)")
        if args.after is not None and args.after not in present:
            raise SystemExit(
                f"{args.after!r} is not a photograph in {folder_name}, so nothing can "
                "be positioned after it."
            )
        if args.after == args.filename:
            raise SystemExit("A photograph cannot follow itself.")
        conn.execute(
            """
            INSERT INTO image_overrides (folder_name, filename, stage, sort_after, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (folder_name, filename) DO UPDATE SET
                stage = excluded.stage,
                sort_after = excluded.sort_after,
                note = excluded.note,
                updated_at = datetime('now')
            """,
            (folder_name, args.filename, args.stage, args.after, args.note),
        )
        conn.commit()
        print(f"Recorded against {folder_name}/{args.filename}.")

    refresh(conn, folder_name)
    print()
    return show(conn, folder_name)


if __name__ == "__main__":
    raise SystemExit(main())

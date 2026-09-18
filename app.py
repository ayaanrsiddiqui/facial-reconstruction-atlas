"""Facial Reconstruction Atlas — lightweight FastAPI backend."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from field_options import FILTER_OPTIONS, PATIENT_COLUMNS
from image_enumeration import (
    FRONT,
    UNLABELLED,
    apply_overrides,
    case_key,
    enumerate_case_images,
    index_case_folders,
)
from import_patient_log import SEED_PATIENTS, format_locations_display

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("ENTDATABASE_DB_PATH", BASE_DIR / "metadata.db"))
IMAGE_ROOT = Path(os.getenv("ENTDATABASE_IMAGE_ROOT", BASE_DIR / "mock_o_drive")).resolve()
BUNDLED_DB = BASE_DIR / "metadata.db"

# Flipped at runtime if DB_PATH turns out not to be writable despite
# database_writes_allowed() saying it should be — see init_database().
_writes_disabled_at_runtime = False


def database_writes_allowed() -> bool:
    """Vercel runtime is read-only; build.py opts in with ENTDATABASE_ALLOW_DB_WRITES=1."""
    if _writes_disabled_at_runtime:
        return False
    if os.getenv("ENTDATABASE_ALLOW_DB_WRITES") == "1":
        return True
    return os.getenv("VERCEL") != "1"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Face overview only — subunit drill-downs were removed because sublocation
# overlays could not be positioned accurately on the reference photo.
# Sublocations remain available via the filter dropdowns (Defect inventory).
ANATOMY_DIAGRAMS: dict[str, dict[str, Any]] = {
    "face": {
        "label": "Face",
        "parent": None,
        "regions": [
            {"id": "scalp", "label": "Scalp", "filter": {"region": "Scalp"}},
            {"id": "forehead", "label": "Forehead", "filter": {"region": "Forehead"}},
            {"id": "temple", "label": "Temple", "filter": {"region": "Temple"}},
            {
                "id": "periorbital",
                "label": "Periorbital",
                "filter": {"region": "Periorbital"},
                "detail_diagram": "periorbital",
            },
            {"id": "cheek", "label": "Cheek", "filter": {"region": "Cheek"}},
            {"id": "ear", "label": "Ear", "filter": {"region": "Ear"}},
            {
                "id": "nose",
                "label": "Nose",
                "filter": {"region": "Nose"},
                "detail_diagram": "nose",
            },
            {
                "id": "lip",
                "label": "Lip",
                "filter": {"region": "Lip"},
                "detail_diagram": "lip",
            },
            {"id": "chin", "label": "Chin", "filter": {"region": "Chin"}},
            {"id": "neck", "label": "Neck", "filter": {"region": "Neck"}},
        ],
    },
    "nose": {
        "label": "Nose",
        "parent": "face",
        "regions": [
            {
                "id": "nose_dorsum",
                "label": "Dorsum",
                "filter": {"region": "Nose", "sub_location": "Dorsum"},
            },
            {
                "id": "nose_sidewall",
                "label": "Sidewall",
                "filter": {"region": "Nose", "sub_location": "Sidewall"},
            },
            {
                "id": "nose_tip",
                "label": "Tip",
                "filter": {"region": "Nose", "sub_location": "Tip"},
            },
            {
                "id": "nose_ala",
                "label": "Ala",
                "filter": {"region": "Nose", "sub_location": "Ala"},
            },
            {
                "id": "nose_columella",
                "label": "Columella",
                "filter": {"region": "Nose", "sub_location": "Columella"},
            },
        ],
    },
    "periorbital": {
        "label": "Periorbital",
        "parent": "face",
        "regions": [
            {
                "id": "peri_brow",
                "label": "Brow",
                "filter": {"region": "Periorbital", "sub_location": "Brow"},
            },
            {
                "id": "peri_upper_lid",
                "label": "Upper Lid",
                "filter": {"region": "Periorbital", "sub_location": "Upper Lid"},
            },
            {
                "id": "peri_lower_lid",
                "label": "Lower Lid",
                "filter": {"region": "Periorbital", "sub_location": "Lower Lid"},
            },
            {
                "id": "peri_medial_canthus",
                "label": "Medial Canthus",
                "filter": {"region": "Periorbital", "sub_location": "Medial Canthus"},
            },
            {
                "id": "peri_lateral_canthus",
                "label": "Lateral Canthus",
                "filter": {"region": "Periorbital", "sub_location": "Lateral Canthus"},
            },
            {
                "id": "peri_glabella",
                "label": "Glabella",
                "filter": {"region": "Periorbital", "sub_location": "Glabella"},
            },
        ],
    },
    "lip": {
        "label": "Lip",
        "parent": "face",
        "regions": [
            {
                "id": "lip_upper",
                "label": "Upper",
                "filter": {"region": "Lip", "sub_location": "Upper"},
            },
            {
                "id": "lip_lower",
                "label": "Lower",
                "filter": {"region": "Lip", "sub_location": "Lower"},
            },
            {
                "id": "lip_vermillion",
                "label": "Vermillion",
                "filter": {"region": "Lip", "sub_location": "Vermillion"},
            },
            {
                "id": "lip_philtrum",
                "label": "Philtrum",
                "filter": {"region": "Lip", "sub_location": "Philtrum"},
            },
            {
                "id": "lip_commissure",
                "label": "Commissure",
                "filter": {"region": "Lip", "sub_location": "Commissure"},
            },
        ],
    },
}


def _open_db(*, read_only: bool = False) -> sqlite3.Connection:
    if read_only or not database_writes_allowed():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_connection() -> sqlite3.Connection:
    if not database_writes_allowed():
        if not DB_PATH.is_file():
            raise HTTPException(
                status_code=503,
                detail="Database not bundled for production deployment.",
            )
        return _open_db(read_only=True)

    if not DB_PATH.exists():
        init_database()
    else:
        with sqlite3.connect(DB_PATH) as conn:
            if not table_exists(conn, "patients"):
                init_database()

    return _open_db()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def patient_table_columns(conn: sqlite3.Connection) -> set[str]:
    if not table_exists(conn, "patients"):
        return set()
    rows = conn.execute("PRAGMA table_info(patients)").fetchall()
    return {row[1] for row in rows}


def schema_is_current(conn: sqlite3.Connection) -> bool:
    if patient_table_columns(conn) != set(PATIENT_COLUMNS):
        return False
    return table_exists(conn, "patient_locations")


def recreate_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS images")
    conn.execute("DROP TABLE IF EXISTS patient_locations")
    conn.execute("DROP TABLE IF EXISTS patients")
    conn.execute("DROP TABLE IF EXISTS cases")

    conn.execute(
        """
        CREATE TABLE patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_name TEXT NOT NULL UNIQUE,
            patient_id TEXT NOT NULL,
            location_raw TEXT NOT NULL DEFAULT '',
            defect_size TEXT NOT NULL,
            full_thickness TEXT NOT NULL,
            method_of_repair TEXT NOT NULL,
            general_flap TEXT NOT NULL,
            specific_flap_description TEXT NOT NULL,
            graft TEXT NOT NULL,
            graft_donor_site TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE patient_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_row_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            sub_location TEXT NOT NULL,
            FOREIGN KEY (patient_row_id) REFERENCES patients(id),
            UNIQUE (patient_row_id, region, sub_location)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_row_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            stage TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (patient_row_id) REFERENCES patients(id),
            UNIQUE (patient_row_id, filename)
        )
        """
    )


IMAGE_OVERRIDES_DDL = """
    CREATE TABLE IF NOT EXISTS image_overrides (
        folder_name TEXT NOT NULL,
        filename TEXT NOT NULL,
        stage TEXT,
        sort_after TEXT,
        hidden INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        updated_by TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (folder_name, filename)
    )
"""

# Columns added after the table first shipped. Added rather than rebuilt: the
# table holds decisions people made, which is the one thing here that cannot be
# regenerated.
IMAGE_OVERRIDE_ADDED_COLUMNS = {
    "hidden": "INTEGER NOT NULL DEFAULT 0",
    "updated_by": "TEXT NOT NULL DEFAULT ''",
}


def migrate_image_overrides(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(image_overrides)")}
    for column, definition in IMAGE_OVERRIDE_ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE image_overrides ADD COLUMN {column} {definition}")


def load_image_overrides(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, tuple[str | None, str | None, bool]]]:
    """Stage and ordering corrections people have recorded, by folder and filename.

    Deliberately not dropped by recreate_schema(): enumeration can be rebuilt
    from the image store at any time, but a correction somebody made because
    they know the case cannot be. Keeping the two apart is what lets the
    import re-run without losing them.
    """
    conn.execute(IMAGE_OVERRIDES_DDL)
    migrate_image_overrides(conn)
    overrides: dict[str, dict[str, tuple[str | None, str | None, bool]]] = {}
    for folder_name, filename, stage, sort_after, hidden in conn.execute(
        "SELECT folder_name, filename, stage, sort_after, hidden FROM image_overrides"
    ):
        overrides.setdefault(folder_name, {})[filename] = (
            stage,
            sort_after,
            bool(hidden),
        )
    return overrides


def _bootstrap_db_if_needed() -> None:
    """Seed a writable database location from the read-only bundled copy.

    On a serverless host the deployment bundle is read-only, so DB_PATH is pointed at
    somewhere writable (for example /tmp). The first request on a cold instance finds
    nothing there; rather than re-running the spreadsheet import, copy the database
    the build step already produced.
    """
    if DB_PATH == BUNDLED_DB or DB_PATH.exists():
        return
    if BUNDLED_DB.is_file():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUNDLED_DB, DB_PATH)


def init_database() -> None:
    global _writes_disabled_at_runtime
    if not database_writes_allowed():
        return

    try:
        _bootstrap_db_if_needed()
        IMAGE_ROOT.mkdir(parents=True, exist_ok=True)

        with _open_db() as conn:
            expected_count = len(SEED_PATIENTS)
            patient_count = (
                conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
                if schema_is_current(conn)
                else 0
            )

            if not schema_is_current(conn) or patient_count != expected_count:
                recreate_schema(conn)
                seed_database(conn)

            conn.commit()
    except (sqlite3.OperationalError, OSError) as exc:
        if isinstance(exc, sqlite3.OperationalError) and "readonly" not in str(exc).lower():
            raise
        _writes_disabled_at_runtime = True
        print(
            f"[entdatabase] {DB_PATH} is not writable — continuing in read-only mode. "
            "Set ENTDATABASE_DB_PATH to a writable location (for example /tmp/metadata.db) "
            "if this deployment needs to write.",
            file=sys.stderr,
        )


def seed_database(conn: sqlite3.Connection) -> None:
    """Load the case metadata, taking each case's images from the share.

    The spreadsheet says which cases exist; the image store says which
    photographs each one has. Where a case has no folder on the store at all,
    the spreadsheet's stage columns still supply a filename list, so a database
    can be seeded before the placeholder set has been generated.
    """
    columns = ", ".join(PATIENT_COLUMNS)
    placeholders = ", ".join("?" for _ in PATIENT_COLUMNS)
    folder_index = index_case_folders(IMAGE_ROOT)
    overrides_by_folder = load_image_overrides(conn)

    enumerated_cases = enumerated_images = unlabelled_images = 0
    cases_without_folder: list[str] = []
    empty_folders: list[str] = []
    unresolved_anchors: list[str] = []
    overrides_applied = 0

    for patient in SEED_PATIENTS:
        key = case_key(patient["patient_id"])
        folder_name = folder_index.get(key) if key is not None else None

        if folder_name is None:
            folder_name = patient["folder_name"]
            images = patient["images"]
            cases_without_folder.append(patient["patient_id"])
        else:
            images = enumerate_case_images(IMAGE_ROOT / folder_name)
            enumerated_cases += 1
            enumerated_images += len(images)
            unlabelled_images += sum(1 for _, stage, _ in images if stage == UNLABELLED)
            if not images:
                empty_folders.append(folder_name)

        # Always applied, even when empty, so that everything downstream sees
        # one row shape rather than two.
        case_overrides = overrides_by_folder.get(folder_name, {})
        images, unresolved = apply_overrides(images, case_overrides)
        overrides_applied += sum(1 for name, _, _, _ in images if name in case_overrides)
        unresolved_anchors.extend(f"{folder_name}/{name}" for name in unresolved)

        values = tuple(
            folder_name if column == "folder_name" else patient[column]
            for column in PATIENT_COLUMNS
        )
        cursor = conn.execute(
            f"INSERT INTO patients ({columns}) VALUES ({placeholders})",
            values,
        )
        patient_row_id = cursor.lastrowid
        conn.executemany(
            """
            INSERT INTO patient_locations (patient_row_id, region, sub_location)
            VALUES (?, ?, ?)
            """,
            [
                (patient_row_id, location["region"], location["sub_location"])
                for location in patient["locations"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO images (patient_row_id, filename, stage, sort_order, hidden)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (patient_row_id, filename, stage, sort_order, int(hidden))
                for filename, stage, sort_order, hidden in images
            ],
        )

    report_seeding(
        enumerated_cases,
        enumerated_images,
        unlabelled_images,
        cases_without_folder,
        empty_folders,
        overrides_applied,
        unresolved_anchors,
    )


def report_seeding(
    enumerated_cases: int,
    enumerated_images: int,
    unlabelled_images: int,
    cases_without_folder: list[str],
    empty_folders: list[str],
    overrides_applied: int = 0,
    unresolved_anchors: list[str] | None = None,
) -> None:
    """Say what the image store actually yielded.

    Run against the real share this is the only account of what the naming
    rules did and did not recognise, so it is printed rather than counted
    silently.
    """
    print(
        f"[entdatabase] seeded {len(SEED_PATIENTS)} case(s); "
        f"{enumerated_cases} matched a folder under {IMAGE_ROOT} "
        f"and yielded {enumerated_images} image(s).",
        file=sys.stderr,
    )
    if unlabelled_images:
        print(
            f"[entdatabase] {unlabelled_images} image(s) matched no known naming rule "
            f"and are recorded as '{UNLABELLED}'. They are shown in the interface, "
            "sorted after the labelled images.",
            file=sys.stderr,
        )
    if empty_folders:
        print(
            f"[entdatabase] {len(empty_folders)} case folder(s) held no servable image: "
            f"{empty_folders[:10]}. Those cases will not appear in search results.",
            file=sys.stderr,
        )
    if cases_without_folder:
        print(
            f"[entdatabase] {len(cases_without_folder)} case(s) had no folder on the "
            f"image store: {cases_without_folder[:10]}. Their filenames came from the "
            "spreadsheet's stage columns instead, which is correct only for the "
            "generated placeholder set.",
            file=sys.stderr,
        )
    if overrides_applied:
        print(
            f"[entdatabase] applied {overrides_applied} recorded stage/order "
            "correction(s).",
            file=sys.stderr,
        )
    if unresolved_anchors:
        print(
            f"[entdatabase] {len(unresolved_anchors)} correction(s) name a photograph to "
            f"follow that no longer exists, or form a loop: {unresolved_anchors[:10]}. "
            "Those images kept their derived position; their label, if any, still "
            "applied.",
            file=sys.stderr,
        )


SESSION_COOKIE = "session_token"
PBKDF2_ITERATIONS = 200_000

SESSION_TTL_ENV = "ENTDATABASE_SESSION_TTL_HOURS"
DEFAULT_SESSION_TTL_HOURS = 12

# A session row is unusable once it has passed its expiry — or if expires_at
# cannot be read as a datetime at all, which julianday() reports as NULL. An
# unreadable expiry counts as expired so the check cannot fail open.
EXPIRED_SESSION_CLAUSE = (
    "julianday(expires_at) IS NULL OR expires_at <= datetime('now')"
)


def session_ttl_hours() -> int:
    """How long a newly issued session stays valid, in hours.

    A mistyped TTL is a security control that silently did not apply, so an
    unparseable or non-positive value raises instead of falling back to the
    default. on_startup() reads it once so that surfaces at boot, not at the
    first login attempt.
    """
    raw = os.getenv(SESSION_TTL_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SESSION_TTL_HOURS
    try:
        hours = int(raw)
    except ValueError:
        raise ValueError(
            f"{SESSION_TTL_ENV} must be a whole number of hours, got {raw!r}."
        ) from None
    if hours < 1:
        raise ValueError(f"{SESSION_TTL_ENV} must be at least 1 hour, got {hours}.")
    return hours


def session_expires_at() -> str:
    """Absolute expiry for a session issued now, in SQLite's datetime() format.

    Absolute rather than sliding: a sliding window would mean a database write
    on every authenticated read, which the read-only deployment mode cannot do.
    """
    expiry = datetime.now(timezone.utc) + timedelta(hours=session_ttl_hours())
    return expiry.strftime("%Y-%m-%d %H:%M:%S")


SESSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
"""


def ensure_auth_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    migrate_users_to_roles(conn)
    conn.execute(SESSIONS_DDL)
    migrate_sessions_to_expiring(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            patient_row_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, patient_row_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (patient_row_id) REFERENCES patients(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_row_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (patient_row_id) REFERENCES patients(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def migrate_users_to_roles(conn: sqlite3.Connection) -> None:
    """Add the administrator flag to a users table created before it existed.

    Added rather than rebuilt, unlike the sessions migration: a session is
    disposable and an account is not. Existing accounts default to 0, so an
    upgrade grants nobody anything — the first administrator is named
    deliberately with create_account.py.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_admin" in columns:
        return
    conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    print(
        "[entdatabase] users table gained an is_admin flag; every existing account "
        "defaults to non-administrator. Promote one with "
        "`python create_account.py --promote <username>`.",
        file=sys.stderr,
    )


def migrate_sessions_to_expiring(conn: sqlite3.Connection) -> None:
    """Add server-side expiry to a sessions table created before it had one.

    Rows predating the column were issued with no server-side lifetime, so
    they are discarded rather than backfilled from created_at: the cost is one
    extra sign-in, and it leaves nothing in the table the expiry check would
    have to guess about.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "expires_at" in columns:
        return
    conn.execute("DROP TABLE sessions")
    conn.execute(SESSIONS_DDL)
    print(
        "[entdatabase] sessions table upgraded to server-side expiry; "
        "existing sessions were cleared and users will need to sign in again.",
        file=sys.stderr,
    )


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    """Delete session rows the expiry check has already rejected.

    Skipped on a read-only deployment, where the rows simply stay: expiry is
    enforced by the comparison in get_current_user(), not by the row's absence.
    """
    if not database_writes_allowed():
        return
    conn.execute(f"DELETE FROM sessions WHERE {EXPIRED_SESSION_CLAUSE}")
    conn.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"{salt}:{digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest_hex = stored_hash.split(":", 1)
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(expected.hex(), digest_hex)


def validate_username(username: str) -> str:
    username = username.strip()
    if not (3 <= len(username) <= 30):
        raise HTTPException(status_code=400, detail="Username must be 3-30 characters.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        raise HTTPException(
            status_code=400,
            detail="Username may only contain letters, numbers, underscores, hyphens, and periods.",
        )
    return username


def validate_password(password: str) -> str:
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    return password


def require_writes() -> None:
    if not database_writes_allowed():
        raise HTTPException(
            status_code=503, detail="Accounts are not available in this deployment."
        )


def open_registration_enabled() -> bool:
    """Self-service account creation is opt-in via ENTDATABASE_OPEN_REGISTRATION=1.

    Off by default. require_writes() gates database writes, not identity, so
    with only that check anyone who could reach the application with writes
    enabled could create themselves an account. The handful of accounts needed
    before SSO lands are created on the host with create_account.py.
    """
    return os.getenv("ENTDATABASE_OPEN_REGISTRATION") == "1"


def require_open_registration() -> None:
    """Hide the registration endpoint entirely unless it is switched on."""
    if not open_registration_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def get_current_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with get_db_connection() as conn:
        if not table_exists(conn, "sessions") or not table_exists(conn, "users"):
            return None
        row = conn.execute(
            f"""
            SELECT users.id, users.username, users.is_admin,
                   ({EXPIRED_SESSION_CLAUSE}) AS expired
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        if row["expired"]:
            purge_expired_sessions(conn)
            return None
    return row


def demo_mode_enabled() -> bool:
    """Public demonstration instance: browsing without an account.

    Set only on the public demo deployment, which runs on fabricated sample data
    and a read-only database. Never set on an internal deployment — it disables
    the login requirement on every read endpoint.
    """
    return os.getenv("ENTDATABASE_DEMO_MODE") == "1"


# The shared demo account browses; it never administers anything.
DEMO_USER: dict[str, Any] = {"id": 0, "username": "demo", "is_admin": 0}


def require_user(request: Request) -> sqlite3.Row | dict[str, Any]:
    # A real session always wins. The demo user is only a fallback for anonymous
    # visitors on the public demo — otherwise a signed-in user's favorites and
    # comments would be written against the shared demo account.
    user = get_current_user(request)
    if user is not None:
        return user
    if demo_mode_enabled():
        return DEMO_USER
    raise HTTPException(status_code=401, detail="Login required.")


def require_admin(request: Request) -> sqlite3.Row | dict[str, Any]:
    """An administrator edits the case record; an ordinary user only reads it.

    Separate from require_user() rather than folded into it, because the
    distinction is the point: every account can reach every case, so without
    this any of the twenty-odd users could reorder anybody's photographs.

    A 403 rather than a 404: unlike the development routes, these endpoints are
    a permanent part of the application and the interface already knows they
    exist. Hiding them would make a permissions problem look like a bug.
    """
    user = require_user(request)
    if not user["is_admin"]:
        raise HTTPException(
            status_code=403, detail="This action needs an administrator account."
        )
    return user


def dev_tools_enabled() -> bool:
    """Development-only routes are opt-in via ENTDATABASE_DEV_TOOLS=1."""
    return os.getenv("ENTDATABASE_DEV_TOOLS") == "1"


def require_dev_tools(request: Request) -> sqlite3.Row:
    """Hide dev routes entirely unless enabled, and require a login when they are."""
    if not dev_tools_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return require_user(request)


def get_patient_row_id(conn: sqlite3.Connection, folder_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM patients WHERE folder_name = ?", (folder_name,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return row["id"]


def enrich_cases_with_social(
    conn: sqlite3.Connection, cases: list[dict[str, Any]], user_id: int | None
) -> None:
    if not cases:
        return
    ids = [case["id"] for case in cases]
    placeholders = ",".join("?" for _ in ids)

    comment_counts: dict[int, int] = {}
    if table_exists(conn, "comments"):
        rows = conn.execute(
            f"""
            SELECT patient_row_id, COUNT(*) AS n
            FROM comments
            WHERE patient_row_id IN ({placeholders})
            GROUP BY patient_row_id
            """,
            ids,
        ).fetchall()
        comment_counts = {row["patient_row_id"]: row["n"] for row in rows}

    favorited_ids: set[int] = set()
    if user_id is not None and table_exists(conn, "favorites"):
        rows = conn.execute(
            f"""
            SELECT patient_row_id
            FROM favorites
            WHERE user_id = ? AND patient_row_id IN ({placeholders})
            """,
            [user_id, *ids],
        ).fetchall()
        favorited_ids = {row["patient_row_id"] for row in rows}

    for case in cases:
        case["comment_count"] = comment_counts.get(case["id"], 0)
        case["favorited"] = case["id"] in favorited_ids


class AuthRequest(BaseModel):
    username: str
    password: str


class CommentRequest(BaseModel):
    body: str


class ImageEditRequest(BaseModel):
    stage: str | None = None
    hidden: bool | None = None


class ImageMoveRequest(BaseModel):
    direction: str


class FaceRegionShape(BaseModel):
    region: str
    label: str
    points: list[list[float]]
    labelPos: list[float]


class FaceRegionsPayload(BaseModel):
    diagram: str = "face"
    shapes: list[FaceRegionShape]


DIAGRAM_MARKERS: dict[str, tuple[str, str]] = {
    "face": ("<!-- FACE_REGIONS_START -->", "<!-- FACE_REGIONS_END -->"),
    "nose": ("<!-- NOSE_REGIONS_START -->", "<!-- NOSE_REGIONS_END -->"),
    "periorbital": (
        "<!-- PERIORBITAL_REGIONS_START -->",
        "<!-- PERIORBITAL_REGIONS_END -->",
    ),
    "lip": ("<!-- LIP_REGIONS_START -->", "<!-- LIP_REGIONS_END -->"),
}


def render_face_regions_markup(shapes: list[FaceRegionShape]) -> str:
    blocks: list[str] = []
    for shape in shapes:
        if len(shape.points) < 3:
            continue
        first_x, first_y = shape.points[0]
        d_parts = [f"M{first_x:g} {first_y:g}"]
        for x, y in shape.points[1:]:
            d_parts.append(f"L{x:g} {y:g}")
        d_parts.append("Z")
        d = " ".join(d_parts)

        region = html.escape(shape.region, quote=True)
        label = html.escape(shape.label)
        label_x, label_y = shape.labelPos

        blocks.append(
            "                <path\n"
            '                  class="anatomy-region"\n'
            f'                  data-region-id="{region}"\n'
            f'                  d="{d}"\n'
            "                />\n"
            f'                <text x="{label_x:g}" y="{label_y:g}" class="diagram-label">{label}</text>'
        )
    return "\n\n".join(blocks)


SHAPE_MARKUP_RE = re.compile(
    r'<path\s+class="anatomy-region"\s+data-region-id="([^"]+)"\s+d="([^"]+)"\s*/>'
    r'\s*<text x="(-?[0-9.]+)" y="(-?[0-9.]+)" class="diagram-label">([^<]*)</text>',
)
PATH_COMMAND_RE = re.compile(r"[ML](-?[0-9.]+) (-?[0-9.]+)")


def parse_face_regions_markup(block: str) -> list[dict[str, Any]]:
    shapes = []
    for region, d, label_x, label_y, label in SHAPE_MARKUP_RE.findall(block):
        points = [[float(x), float(y)] for x, y in PATH_COMMAND_RE.findall(d)]
        shapes.append(
            {
                "region": html.unescape(region),
                "label": html.unescape(label),
                "points": points,
                "labelPos": [float(label_x), float(label_y)],
            }
        )
    return shapes


def read_current_face_regions() -> dict[str, list[dict[str, Any]]]:
    index_path = BASE_DIR / "index.html"
    current_html = index_path.read_text()
    result: dict[str, list[dict[str, Any]]] = {}
    for diagram, (start_marker, end_marker) in DIAGRAM_MARKERS.items():
        start_idx = current_html.find(start_marker)
        end_idx = current_html.find(end_marker)
        if start_idx == -1 or end_idx == -1:
            result[diagram] = []
            continue
        block = current_html[start_idx + len(start_marker) : end_idx]
        result[diagram] = parse_face_regions_markup(block)
    return result


def validate_path_segment(value: str, label: str) -> str:
    if not value or value.strip() != value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    if "/" in value or "\\" in value or ".." in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    return value


def resolve_safe_image_path(folder_name: str, filename: str) -> Path:
    folder_name = validate_path_segment(folder_name, "folder name")
    filename = validate_path_segment(filename, "filename")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    candidate = (IMAGE_ROOT / folder_name / filename).resolve()

    if IMAGE_ROOT not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid image path.")

    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")

    return candidate


def image_to_dict(row: sqlite3.Row, folder_name: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "stage": row["stage"],
        "sort_order": row["sort_order"],
        "url": f"/api/image/{folder_name}/{row['filename']}",
    }


def fetch_patient_locations(
    conn: sqlite3.Connection, patient_row_id: int
) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT region, sub_location
        FROM patient_locations
        WHERE patient_row_id = ?
        ORDER BY id
        """,
        (patient_row_id,),
    ).fetchall()
    return [{"region": row["region"], "sub_location": row["sub_location"]} for row in rows]


def patient_row_to_dict(
    row: sqlite3.Row,
    images: list[sqlite3.Row],
    locations: list[dict[str, str]],
) -> dict[str, Any]:
    folder_name = row["folder_name"]
    primary = locations[0] if locations else {"region": "Unknown", "sub_location": "Unspecified"}
    return {
        "id": row["id"],
        "folder_name": folder_name,
        "patient_id": row["patient_id"],
        "location_raw": row["location_raw"],
        "locations": locations,
        "location_display": format_locations_display(locations),
        "region": primary["region"],
        "sub_location": primary["sub_location"],
        "defect_size": row["defect_size"],
        "full_thickness": row["full_thickness"],
        "method_of_repair": row["method_of_repair"],
        "general_flap": row["general_flap"],
        "specific_flap_description": row["specific_flap_description"],
        "graft": row["graft"],
        "graft_donor_site": row["graft_donor_site"],
        "notes": row["notes"],
        "images": [image_to_dict(image, folder_name) for image in images],
    }


def fetch_patients(
    conn: sqlite3.Connection,
    where_clause: str = "",
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    query = f"""
        SELECT id, folder_name, patient_id, location_raw, defect_size,
               full_thickness, method_of_repair, general_flap,
               specific_flap_description, graft, graft_donor_site, notes
        FROM patients
        {where_clause}
        ORDER BY patient_id
    """
    patients = conn.execute(query, params).fetchall()
    results: list[dict[str, Any]] = []

    for patient in patients:
        images = conn.execute(
            """
            SELECT id, filename, stage, sort_order
            FROM images
            WHERE patient_row_id = ? AND hidden = 0
            ORDER BY sort_order, filename
            """,
            (patient["id"],),
        ).fetchall()
        locations = fetch_patient_locations(conn, patient["id"])
        results.append(patient_row_to_dict(patient, images, locations))

    return results


def build_search_query(
    q: str | None,
    region: str | None,
    sub_location: str | None,
    full_thickness: str | None,
    method_of_repair: str | None,
    general_flap: str | None,
    specific_flap_description: str | None,
    graft: str | None,
    graft_donor_site: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if q:
        like = f"%{q.strip()}%"
        search_fields = [
            "patient_id",
            "folder_name",
            "location_raw",
            "defect_size",
            "full_thickness",
            "method_of_repair",
            "general_flap",
            "specific_flap_description",
            "graft",
            "graft_donor_site",
            "notes",
        ]
        field_clauses = " OR ".join(f"patients.{field} LIKE ?" for field in search_fields)
        location_clause = """
            EXISTS (
                SELECT 1
                FROM patient_locations pl
                WHERE pl.patient_row_id = patients.id
                  AND (pl.region LIKE ? OR pl.sub_location LIKE ?)
            )
        """
        clauses.append(f"(({field_clauses}) OR {location_clause})")
        params.extend([like] * len(search_fields))
        params.extend([like, like])

    if region and sub_location:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM patient_locations pl
                WHERE pl.patient_row_id = patients.id
                  AND pl.region = ?
                  AND pl.sub_location = ?
            )
            """
        )
        params.extend([region, sub_location])
    elif region:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM patient_locations pl
                WHERE pl.patient_row_id = patients.id
                  AND pl.region = ?
            )
            """
        )
        params.append(region)
    elif sub_location:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM patient_locations pl
                WHERE pl.patient_row_id = patients.id
                  AND pl.sub_location = ?
            )
            """
        )
        params.append(sub_location)

    filter_map = {
        "full_thickness": full_thickness,
        "method_of_repair": method_of_repair,
        "general_flap": general_flap,
        "specific_flap_description": specific_flap_description,
        "graft": graft,
        "graft_donor_site": graft_donor_site,
    }

    for column, value in filter_map.items():
        if value:
            clauses.append(f"patients.{column} = ?")
            params.append(value)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_clause, params


def pick_display_image(images: list[dict[str, Any]]) -> dict[str, Any] | None:
    for preferred_stage in ("Healed", "Post-op"):
        for image in images:
            if image["stage"] == preferred_stage:
                return image
    return images[0] if images else None


def patient_to_case_card(
    patient: dict[str, Any], *, include_images: bool = False
) -> dict[str, Any] | None:
    images = patient["images"]
    display_image = pick_display_image(images)
    if display_image is None:
        return None

    card = {
        **{key: patient[key] for key in patient if key != "images"},
        "image_filename": display_image["filename"],
        "image_url": display_image["url"],
    }
    if include_images:
        card["images"] = images
    return card


app = FastAPI(title="Facial Reconstruction Atlas", version="0.4.0")

DEFAULT_ALLOWED_ORIGINS = "http://127.0.0.1:8001,http://localhost:8001"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ENTDATABASE_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
def on_startup() -> None:
    session_ttl_hours()  # fail at boot on a misconfigured TTL, not at first login
    init_database()
    if database_writes_allowed():
        with sqlite3.connect(DB_PATH) as conn:
            ensure_auth_schema(conn)
            conn.commit()


@app.get("/api/anatomy")
def get_anatomy(_user: sqlite3.Row = Depends(require_user)) -> dict[str, Any]:
    return ANATOMY_DIAGRAMS


@app.get("/api/filters")
def get_filters(_user: sqlite3.Row = Depends(require_user)) -> dict[str, Any]:
    with get_db_connection() as conn:
        def distinct_locations(column: str) -> list[str]:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM patient_locations ORDER BY {column}"
            ).fetchall()
            return [row[0] for row in rows]

        def distinct_patients(column: str) -> list[str]:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM patients ORDER BY {column}"
            ).fetchall()
            return [row[0] for row in rows]

        return {
            **FILTER_OPTIONS,
            "regions_in_db": distinct_locations("region"),
            "sub_locations_in_db": distinct_locations("sub_location"),
            "full_thicknesses_in_db": distinct_patients("full_thickness"),
            "methods_of_repair_in_db": distinct_patients("method_of_repair"),
            "general_flaps_in_db": distinct_patients("general_flap"),
            "specific_flap_descriptions_in_db": distinct_patients("specific_flap_description"),
            "grafts_in_db": distinct_patients("graft"),
            "graft_donor_sites_in_db": distinct_patients("graft_donor_site"),
        }


@app.get("/api/search")
def search_cases(
    request: Request,
    q: str | None = Query(None, description="Free-text search across all fields."),
    region: str | None = Query(None),
    sub_location: str | None = Query(None),
    full_thickness: str | None = Query(None),
    method_of_repair: str | None = Query(None),
    general_flap: str | None = Query(None),
    specific_flap_description: str | None = Query(None),
    graft: str | None = Query(None),
    graft_donor_site: str | None = Query(None),
    _user: sqlite3.Row = Depends(require_user),
) -> dict[str, Any]:
    where_clause, params = build_search_query(
        q,
        region,
        sub_location,
        full_thickness,
        method_of_repair,
        general_flap,
        specific_flap_description,
        graft,
        graft_donor_site,
    )

    with get_db_connection() as conn:
        patients = fetch_patients(conn, where_clause, tuple(params))

        cases: list[dict[str, Any]] = []
        for patient in patients:
            case = patient_to_case_card(patient)
            if case is not None:
                cases.append(case)

        user = get_current_user(request)
        enrich_cases_with_social(conn, cases, user["id"] if user is not None else None)

    return {"count": len(cases), "cases": cases}


@app.post("/api/auth/register")
def register(payload: AuthRequest, response: Response) -> dict[str, Any]:
    require_open_registration()
    require_writes()
    username = validate_username(payload.username)
    password = validate_password(payload.password)

    with get_db_connection() as conn:
        ensure_auth_schema(conn)
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="That username is already taken.")

        password_hash = hash_password(password)
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        user_id = cursor.lastrowid
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, session_expires_at()),
        )
        conn.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=session_ttl_hours() * 3600,
        path="/",
    )
    return {"username": username}


@app.post("/api/auth/login")
def login(payload: AuthRequest, response: Response) -> dict[str, Any]:
    require_writes()
    username = payload.username.strip()

    with get_db_connection() as conn:
        ensure_auth_schema(conn)
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, row["id"], session_expires_at()),
        )
        conn.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=session_ttl_hours() * 3600,
        path="/",
    )
    return {"username": row["username"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if token and database_writes_allowed():
        with get_db_connection() as conn:
            if table_exists(conn, "sessions"):
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def get_me(request: Request) -> dict[str, Any]:
    """Session state for the frontend.

    Answers with 200 and a null username when there is no session, rather than
    401: the sign-in screen has to know whether registration is open before
    anyone has a session, otherwise it offers a Register button that 404s.
    """
    user = get_current_user(request)
    return {
        "username": user["username"] if user is not None else None,
        "demo": user is None and demo_mode_enabled(),
        "registration_open": open_registration_enabled(),
        "is_admin": bool(user["is_admin"]) if user is not None else False,
    }


@app.get("/api/cases/{folder_name}")
def get_case_detail(
    folder_name: str,
    request: Request,
    _user: sqlite3.Row = Depends(require_user),
) -> dict[str, Any]:
    with get_db_connection() as conn:
        patients = fetch_patients(conn, "WHERE folder_name = ?", (folder_name,))
        if not patients:
            raise HTTPException(status_code=404, detail="Case not found.")

        case = patient_to_case_card(patients[0], include_images=True)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found.")

        user = get_current_user(request)
        enrich_cases_with_social(conn, [case], user["id"] if user is not None else None)

    return case


@app.post("/api/cases/{folder_name}/favorite")
def toggle_favorite(folder_name: str, request: Request) -> dict[str, Any]:
    require_writes()
    user = require_user(request)

    with get_db_connection() as conn:
        ensure_auth_schema(conn)
        patient_row_id = get_patient_row_id(conn, folder_name)
        existing = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND patient_row_id = ?",
            (user["id"], patient_row_id),
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO favorites (user_id, patient_row_id) VALUES (?, ?)",
                (user["id"], patient_row_id),
            )
            favorited = True
        else:
            conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND patient_row_id = ?",
                (user["id"], patient_row_id),
            )
            favorited = False
        conn.commit()

    return {"favorited": favorited}


@app.get("/api/favorites")
def list_favorites(request: Request) -> dict[str, Any]:
    user = require_user(request)

    with get_db_connection() as conn:
        ensure_auth_schema(conn)
        rows = conn.execute(
            """
            SELECT patients.folder_name
            FROM favorites
            JOIN patients ON patients.id = favorites.patient_row_id
            WHERE favorites.user_id = ?
            """,
            (user["id"],),
        ).fetchall()
        folder_names = [row["folder_name"] for row in rows]

        if not folder_names:
            return {"count": 0, "cases": []}

        placeholders = ",".join("?" for _ in folder_names)
        patients = fetch_patients(
            conn, f"WHERE folder_name IN ({placeholders})", tuple(folder_names)
        )
        cases = [
            case
            for case in (patient_to_case_card(patient) for patient in patients)
            if case is not None
        ]
        enrich_cases_with_social(conn, cases, user["id"])

    return {"count": len(cases), "cases": cases}


@app.get("/api/cases/{folder_name}/comments")
def list_comments(
    folder_name: str, _user: sqlite3.Row = Depends(require_user)
) -> dict[str, Any]:
    with get_db_connection() as conn:
        if not table_exists(conn, "comments"):
            return {"count": 0, "comments": []}
        patient_row_id = get_patient_row_id(conn, folder_name)
        rows = conn.execute(
            """
            SELECT comments.id, comments.body, comments.created_at, users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.patient_row_id = ?
            ORDER BY comments.created_at ASC, comments.id ASC
            """,
            (patient_row_id,),
        ).fetchall()

    comments = [
        {
            "id": row["id"],
            "username": row["username"],
            "body": row["body"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {"count": len(comments), "comments": comments}


@app.post("/api/cases/{folder_name}/comments")
def add_comment(folder_name: str, payload: CommentRequest, request: Request) -> dict[str, Any]:
    require_writes()
    # Favorites are open to anonymous visitors on the public demo, but comments are
    # free text on a publicly reachable page — those need a real account.
    if demo_mode_enabled() and get_current_user(request) is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to leave a comment. Accounts on this demo are temporary.",
        )
    user = require_user(request)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
    if len(body) > 2000:
        raise HTTPException(status_code=400, detail="Comment is too long.")

    with get_db_connection() as conn:
        ensure_auth_schema(conn)
        patient_row_id = get_patient_row_id(conn, folder_name)
        cursor = conn.execute(
            "INSERT INTO comments (patient_row_id, user_id, body) VALUES (?, ?, ?)",
            (patient_row_id, user["id"], body),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, body, created_at FROM comments WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    return {
        "id": row["id"],
        "username": user["username"],
        "body": row["body"],
        "created_at": row["created_at"],
    }


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request) -> dict[str, Any]:
    require_writes()
    user = require_user(request)

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM comments WHERE id = ?", (comment_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Comment not found.")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="You can only delete your own comments.")
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()

    return {"ok": True}


def case_image_rows(conn: sqlite3.Connection, folder_name: str) -> list[dict[str, Any]]:
    """Every photograph on a case, hidden ones included — the administrator's view."""
    patient_row_id = get_patient_row_id(conn, folder_name)
    rows = conn.execute(
        """
        SELECT filename, stage, sort_order, hidden
        FROM images
        WHERE patient_row_id = ?
        ORDER BY sort_order, filename
        """,
        (patient_row_id,),
    ).fetchall()
    return [
        {
            "filename": row["filename"],
            "stage": row["stage"],
            "sort_order": row["sort_order"],
            "hidden": bool(row["hidden"]),
            "url": f"/api/image/{folder_name}/{row['filename']}",
        }
        for row in rows
    ]


def rewrite_case_images(conn: sqlite3.Connection, folder_name: str) -> None:
    """Recompute one case's photographs from the store and the recorded decisions.

    Called after an edit so the change is visible immediately rather than at
    the next import. The image store stays the source of truth for which files
    exist; only their labelling and order come from the override table.
    """
    folder = IMAGE_ROOT / folder_name
    if not folder.is_dir():
        raise HTTPException(
            status_code=409,
            detail="This case has no folder on the image store, so its photographs "
            "cannot be edited.",
        )
    overrides = load_image_overrides(conn).get(folder_name, {})
    images, _ = apply_overrides(enumerate_case_images(folder), overrides)
    patient_row_id = get_patient_row_id(conn, folder_name)
    conn.execute("DELETE FROM images WHERE patient_row_id = ?", (patient_row_id,))
    conn.executemany(
        """
        INSERT INTO images (patient_row_id, filename, stage, sort_order, hidden)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (patient_row_id, filename, stage, sort_order, int(hidden))
            for filename, stage, sort_order, hidden in images
        ],
    )
    conn.commit()


def save_override(
    conn: sqlite3.Connection,
    folder_name: str,
    filename: str,
    *,
    stage: str | None,
    sort_after: str | None,
    hidden: bool,
    username: str,
) -> None:
    conn.execute(
        """
        INSERT INTO image_overrides
            (folder_name, filename, stage, sort_after, hidden, updated_by)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (folder_name, filename) DO UPDATE SET
            stage = excluded.stage,
            sort_after = excluded.sort_after,
            hidden = excluded.hidden,
            updated_by = excluded.updated_by,
            updated_at = datetime('now')
        """,
        (folder_name, filename, stage, sort_after, int(hidden), username),
    )


def current_override(
    conn: sqlite3.Connection, folder_name: str, filename: str
) -> tuple[str | None, str | None, bool]:
    row = conn.execute(
        "SELECT stage, sort_after, hidden FROM image_overrides "
        "WHERE folder_name = ? AND filename = ?",
        (folder_name, filename),
    ).fetchone()
    if row is None:
        return None, None, False
    return row["stage"], row["sort_after"], bool(row["hidden"])


def checked_case_image(
    conn: sqlite3.Connection, folder_name: str, filename: str
) -> list[dict[str, Any]]:
    """Validate the two path segments and confirm the photograph is on this case."""
    validate_path_segment(folder_name, "folder name")
    validate_path_segment(filename, "filename")
    images = case_image_rows(conn, folder_name)
    if filename not in {image["filename"] for image in images}:
        raise HTTPException(status_code=404, detail="No such photograph on this case.")
    return images


@app.get("/api/cases/{folder_name}/images")
def list_case_images(
    folder_name: str, _user: sqlite3.Row = Depends(require_admin)
) -> dict[str, Any]:
    with get_db_connection() as conn:
        validate_path_segment(folder_name, "folder name")
        return {"images": case_image_rows(conn, folder_name)}


# POST rather than PATCH: PATCH would have to be added to the CORS method
# allowlist, and widening that to gain a verb is a poor trade.
@app.post("/api/cases/{folder_name}/images/{filename}")
def edit_case_image(
    folder_name: str,
    filename: str,
    payload: ImageEditRequest,
    user: sqlite3.Row = Depends(require_admin),
) -> dict[str, Any]:
    require_writes()
    with get_db_connection() as conn:
        checked_case_image(conn, folder_name, filename)
        stage, sort_after, hidden = current_override(conn, folder_name, filename)

        if payload.stage is not None:
            stage = payload.stage.strip() or None
            if stage is not None and len(stage) > 60:
                raise HTTPException(status_code=400, detail="That label is too long.")
        if payload.hidden is not None:
            hidden = payload.hidden

        save_override(
            conn,
            folder_name,
            filename,
            stage=stage,
            sort_after=sort_after,
            hidden=hidden,
            username=user["username"],
        )
        rewrite_case_images(conn, folder_name)
        return {"images": case_image_rows(conn, folder_name)}


@app.post("/api/cases/{folder_name}/images/{filename}/move")
def move_case_image(
    folder_name: str,
    filename: str,
    payload: ImageMoveRequest,
    user: sqlite3.Row = Depends(require_admin),
) -> dict[str, Any]:
    require_writes()
    if payload.direction not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="Direction must be up or down.")

    with get_db_connection() as conn:
        images = checked_case_image(conn, folder_name, filename)
        order = [image["filename"] for image in images]
        index = order.index(filename)

        if payload.direction == "up":
            if index == 0:
                raise HTTPException(status_code=400, detail="Already first.")
            # Moving past the first photograph leaves nothing to follow.
            sort_after = FRONT if index == 1 else order[index - 2]
        else:
            if index == len(order) - 1:
                raise HTTPException(status_code=400, detail="Already last.")
            sort_after = order[index + 1]

        stage, _, hidden = current_override(conn, folder_name, filename)
        save_override(
            conn,
            folder_name,
            filename,
            stage=stage,
            sort_after=sort_after,
            hidden=hidden,
            username=user["username"],
        )
        rewrite_case_images(conn, folder_name)
        return {"images": case_image_rows(conn, folder_name)}


@app.get("/api/image/{folder_name}/{filename}")
def get_image(
    folder_name: str, filename: str, _user: sqlite3.Row = Depends(require_user)
) -> FileResponse:
    return FileResponse(resolve_safe_image_path(folder_name, filename))


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/region-editor")
def serve_region_editor(_user: sqlite3.Row = Depends(require_dev_tools)) -> FileResponse:
    return FileResponse(BASE_DIR / "region-editor.html")


@app.get("/api/dev/face-regions")
def get_face_regions(
    _user: sqlite3.Row = Depends(require_dev_tools),
) -> dict[str, list[dict[str, Any]]]:
    return read_current_face_regions()


@app.post("/api/dev/face-regions")
def save_face_regions(
    payload: FaceRegionsPayload, _user: sqlite3.Row = Depends(require_dev_tools)
) -> dict[str, Any]:
    require_writes()

    markers = DIAGRAM_MARKERS.get(payload.diagram)
    if markers is None:
        raise HTTPException(status_code=400, detail=f"Unknown diagram '{payload.diagram}'.")
    start_marker, end_marker = markers

    index_path = BASE_DIR / "index.html"
    current_html = index_path.read_text()
    start_idx = current_html.find(start_marker)
    end_idx = current_html.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise HTTPException(
            status_code=500, detail="Region markers not found in index.html."
        )

    new_markup = render_face_regions_markup(payload.shapes)
    start_idx += len(start_marker)
    updated_html = (
        current_html[:start_idx]
        + "\n"
        + new_markup
        + "\n                "
        + current_html[end_idx:]
    )
    index_path.write_text(updated_html)

    return {"ok": True, "shapeCount": len(payload.shapes)}


@app.get("/script.js")
def serve_script() -> FileResponse:
    return FileResponse(BASE_DIR / "script.js", media_type="application/javascript")


@app.get("/styles.css")
def serve_styles() -> FileResponse:
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/static/face-reference.jpg")
def serve_face_reference() -> FileResponse:
    return FileResponse(
        BASE_DIR / "static" / "face-reference.jpg",
        media_type="image/jpeg",
    )

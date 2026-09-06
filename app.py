"""Facial Reconstruction Atlas — lightweight FastAPI backend."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from field_options import FILTER_OPTIONS, PATIENT_COLUMNS
from import_patient_log import SEED_PATIENTS, format_locations_display

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("ENTDATABASE_DB_PATH", BASE_DIR / "metadata.db"))
IMAGE_ROOT = Path(os.getenv("ENTDATABASE_IMAGE_ROOT", BASE_DIR / "mock_o_drive")).resolve()
def database_writes_allowed() -> bool:
    """Vercel runtime is read-only; build.py opts in with ENTDATABASE_ALLOW_DB_WRITES=1."""
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
            FOREIGN KEY (patient_row_id) REFERENCES patients(id),
            UNIQUE (patient_row_id, filename)
        )
        """
    )


def init_database() -> None:
    if not database_writes_allowed():
        return

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


def seed_database(conn: sqlite3.Connection) -> None:
    columns = ", ".join(PATIENT_COLUMNS)
    placeholders = ", ".join("?" for _ in PATIENT_COLUMNS)

    for patient in SEED_PATIENTS:
        values = tuple(patient[column] for column in PATIENT_COLUMNS)
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
            "INSERT INTO images (patient_row_id, filename, stage, sort_order) VALUES (?, ?, ?, ?)",
            [
                (patient_row_id, filename, stage, sort_order)
                for filename, stage, sort_order in patient["images"]
            ],
        )


SESSION_COOKIE = "session_token"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
PBKDF2_ITERATIONS = 200_000


def ensure_auth_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
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


def get_current_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with get_db_connection() as conn:
        if not table_exists(conn, "sessions") or not table_exists(conn, "users"):
            return None
        row = conn.execute(
            """
            SELECT users.id, users.username
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
    return row


def demo_mode_enabled() -> bool:
    """Public demonstration instance: browsing without an account.

    Set only on the public demo deployment, which runs on fabricated sample data
    and a read-only database. Never set on an internal deployment — it disables
    the login requirement on every read endpoint.
    """
    return os.getenv("ENTDATABASE_DEMO_MODE") == "1"


DEMO_USER: dict[str, Any] = {"id": 0, "username": "demo"}


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
            WHERE patient_row_id = ?
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
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id)
        )
        conn.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
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
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"])
        )
        conn.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
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
    user = get_current_user(request)
    if user is None:
        if demo_mode_enabled():
            return {"username": None, "demo": True}
        raise HTTPException(status_code=401, detail="Not logged in.")
    return {"username": user["username"], "demo": False}


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

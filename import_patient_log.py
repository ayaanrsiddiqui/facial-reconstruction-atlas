"""Load case seed data from a de-identified Excel case log."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_XLSX_NAME = "patient_log.xlsx"


def _warn(message: str) -> None:
    print(f"[import_patient_log] {message}", file=sys.stderr)


def _resolve_xlsx_path() -> Path:
    """Find the source spreadsheet.

    Checked in order: ENTDATABASE_XLSX_PATH env var, the historical default
    filename, then (if that's gone — e.g. a new dated export replaced it) the
    single .xlsx file sitting in the project folder. This lets a new
    spreadsheet just be dropped in under its own name without renaming it.
    """
    override = os.getenv("ENTDATABASE_XLSX_PATH")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            raise FileNotFoundError(
                f"ENTDATABASE_XLSX_PATH is set to {path}, but that file doesn't exist."
            )
        return path

    default_path = BASE_DIR / DEFAULT_XLSX_NAME
    if default_path.exists():
        return default_path

    candidates = sorted(
        p for p in BASE_DIR.glob("*.xlsx") if not p.name.startswith("~$")
    )
    if len(candidates) == 1:
        _warn(f"Using {candidates[0].name} (default spreadsheet not found).")
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            "No source spreadsheet found. Put the patient log .xlsx in the project "
            "folder, or set ENTDATABASE_XLSX_PATH to its location."
        )
    raise FileNotFoundError(
        "Found multiple .xlsx files in the project folder and none is named "
        f"{DEFAULT_XLSX_NAME!r}, so it's ambiguous which to use. Set "
        f"ENTDATABASE_XLSX_PATH to the one you want. Found: "
        f"{[p.name for p in candidates]}"
    )


XLSX_PATH = _resolve_xlsx_path()

# Column headers in the finalized sheet (matched by name, not index).
COL_PT = "Pt #"
COL_LOCATION = "location"
COL_FULL_THICKNESS = "Full thickness (nose, ear, eyelid, lip)"
COL_SIZE = "Size of defect (cm x cm)"
COL_METHOD = (
    "method of repair (Local flap, graft, Regional flap [forehead, melolabial], "
    "second intention, free flap) "
)
COL_GENERAL_FLAP = "General flap"
COL_SPECIFIC_FLAP = "Specific flap description "
COL_GRAFT = "Graft "
COL_DONOR = "Graft donor site "
COL_NOTES_AYAAN = "Notes for Ayaan"
COL_PEARLS = "Supplemental information (surgical pearls)"
COL_PREOP = "pre-op (considering same as defect)"
COL_DEFECT = "Defect"
COL_DRAWN = "Flap drawn"
COL_RAISED = "Flap raised"
COL_CLOSED = "Flap closed"
COL_POSTOP = "post op"
COL_EXCEL_COMPLETE = "Excel entry complete "

REGION_MAP = {
    "forehead": "Forehead",
    "nose": "Nose",
    "cheek": "Cheek",
    "ear": "Ear",
    "lips": "Lip",
    "lip": "Lip",
    "temple": "Temple",
    "scalp": "Scalp",
    "periorbital": "Periorbital",
    "eyelid": "Periorbital",
    "chin": "Chin",
    "neck": "Neck",
}

REGION_FIND_RE = re.compile(
    r"(?i)\b(forehead|nose|cheek|ear|lips|lip|temple|scalp|periorbital|eyelid|chin|neck)\b"
)

# Canonical labels from the Defect inventory sheet.
SUBLOCATION_MAP = {
    "central": "Central",
    "lateral": "Lateral",
    "dorsum": "Dorsum",
    "sidewall": "Sidewall",
    "tip": "Tip",
    "ala": "Ala",
    "columella": "Columella",
    "medial": "Medial",
    "middle": "Middle",
    "helix": "Helix",
    "central ear": "Central Ear",
    "upper": "Upper",
    "lower": "Lower",
    "commissure": "Commissure",
    "philtrum": "Philtrum",
    "vermillion": "Vermillion",
    "vertex": "Vertex",
    "parietal": "Parietal",
    "occipital": "Occipital",
    "frontal": "Frontal",
    "brow": "Brow",
    "glabella": "Glabella",
    "medial canthus": "Medial Canthus",
    "lateral canthus": "Lateral Canthus",
    "lower lid": "Lower Lid",
    "upper lid": "Upper Lid",
    "nasal sill": "Other",
    "septum": "Other",
    "other": "Other",
    "other - nasal sill, septum": "Other",
}

# Regions with no inventory sublocations stay blank (Temple, Chin, Neck).
DEFAULT_SUBLOCATION = {
    "Forehead": "Central",
    "Nose": "Dorsum",
    "Cheek": "Medial",
    "Ear": "Helix",
    "Lip": "Lower",
    "Temple": "",
    "Scalp": "Vertex",
    "Periorbital": "Lower Lid",
    "Chin": "",
    "Neck": "",
}

CANONICAL_METHODS = {"Local flap", "Graft", "Regional flap", "Second intention", "Free flap"}

STAGE_MARKERS: list[tuple[str, str, str]] = [
    (COL_PREOP, "01_preop.jpg", "Pre-op"),
    (COL_DEFECT, "02_defect.jpg", "Defect"),
    (COL_DRAWN, "03_marking.jpg", "Flap drawn"),
    (COL_RAISED, "04_intraop.jpg", "Flap raised"),
    (COL_CLOSED, "05_intraop_closed.jpg", "Flap closed"),
    (COL_POSTOP, "06_healed.jpg", "Post-op"),
]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_full_thickness(value: str) -> str:
    lower = value.lower()
    if not lower or lower.startswith("n/a"):
        return "N/A"
    if lower.startswith("yes"):
        return "Yes"
    if lower.startswith("no"):
        return "No"
    return "N/A"


def _normalize_method(value: str) -> str:
    lower = value.lower().strip()
    if not lower:
        return "Unknown"
    if lower.startswith("local flap") or "local flap" in lower[:40]:
        return "Local flap"
    if lower.startswith("graft") and "flap" not in lower.split(",")[0]:
        return "Graft"
    if lower.startswith("regional flap"):
        return "Regional flap"
    if "second intention" in lower:
        return "Second intention"
    if "free flap" in lower:
        return "Free flap"
    if "forehead flap" in lower or "melolabial" in lower:
        return "Regional flap"
    if "ftsg" in lower or "stsg" in lower or "composite" in lower or "cartilage graft" in lower:
        if "flap" not in lower.split(",")[0].lower():
            return "Graft"
    if "flap" in lower:
        return "Local flap"
    if "graft" in lower:
        return "Graft"
    return value.split("(")[0].strip() or "Unknown"


def _normalize_general_flap(value: str) -> str:
    cleaned = _clean(value) or "n/a"
    replacements = {
        "interpolated flap (forehead melolabial)": "interpolated flap",
        "transposition (bilobe rhombic)": "transposition",
    }
    return replacements.get(cleaned, cleaned)


def _normalize_defect_size(value: str) -> str:
    value = _clean(value)
    if not value or value.lower() == "not recorded":
        return "not recorded"
    if "diameter" in value.lower() or "cm" in value.lower():
        return value
    if re.search(r"\d", value):
        return f"{value} cm"
    return value


def _normalize_graft(value: str) -> str:
    value = _clean(value)
    return "n/a" if not value or value.lower() == "n/a" else value


def _normalize_donor_site(value: str) -> str:
    value = _clean(value)
    return "n/a" if not value or value.lower() == "n/a" else value


def _split_outside_parens(text: str, delimiter: str = ",") -> list[str]:
    """Split text on delimiter characters that are not inside parentheses."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == delimiter and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _region_matches_outside_parens(text: str) -> list[re.Match[str]]:
    """Find region keywords that appear outside parentheses."""
    matches: list[re.Match[str]] = []
    depth = 0
    for match in REGION_FIND_RE.finditer(text):
        # Compute paren depth at match start.
        depth = 0
        for ch in text[: match.start()]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
        if depth == 0:
            matches.append(match)
    return matches


def _split_location_sites(raw: str) -> list[str]:
    """Split a free-text location into per-region site strings."""
    comma_parts = _split_outside_parens(raw, ",")
    sites: list[str] = []
    for part in comma_parts:
        matches = _region_matches_outside_parens(part)
        if len(matches) <= 1:
            sites.append(part)
            continue
        starts = [match.start() for match in matches]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(part)
            piece = part[start:end].strip(" ,")
            if piece:
                sites.append(piece)
    return sites


def _resolve_sublocation(region: str, value: str) -> str:
    key = value.strip().lower()
    if not key:
        return DEFAULT_SUBLOCATION.get(region, "Unspecified")

    if key in SUBLOCATION_MAP:
        label = SUBLOCATION_MAP[key]
    else:
        label = None
        for mapped_key, mapped_label in sorted(
            SUBLOCATION_MAP.items(), key=lambda item: -len(item[0])
        ):
            if mapped_key in key:
                label = mapped_label
                break
        if label is None:
            label = value.strip().title()

    # Region-aware corrections for short Excel shorthand.
    if region == "Periorbital":
        if label in {"Lower", "Lower Lid"} or key in {"lower", "lower lid"}:
            return "Lower Lid"
        if label in {"Upper", "Upper Lid"} or key in {"upper", "upper lid"}:
            return "Upper Lid"
        if label in {"Lateral", "Lateral Canthus"} or "canthus" in key:
            if "medial" in key:
                return "Medial Canthus"
            return "Lateral Canthus"
    if region == "Ear":
        if label in {"Central", "Central Ear"} or key in {"central", "central ear"}:
            return "Central Ear"
    if region == "Nose" and label in {"Nasal Sill", "Septum"}:
        return "Other"

    return label


def parse_locations(location: str) -> tuple[str, list[dict[str, str]]]:
    """Return the raw location string and all region/sub-location pairs."""
    # Keep primary case text; drop alternate case notes after " / case 2:"
    raw = re.split(r"\s*/\s*case\s*\d+", _clean(location), flags=re.IGNORECASE)[0].strip()
    raw = raw.split("/")[0].strip()
    if not raw:
        return "", [{"region": "Unknown", "sub_location": "Unspecified"}]

    sites = _split_location_sites(raw)
    parsed: list[dict[str, str]] = []

    for site in sites:
        site_lower = site.lower()
        region = "Unknown"
        for keyword, label in sorted(REGION_MAP.items(), key=lambda item: -len(item[0])):
            if re.search(rf"\b{re.escape(keyword)}\b", site_lower):
                region = label
                break

        paren_chunks = re.findall(r"\(([^)]+)\)", site)
        if paren_chunks:
            for chunk in paren_chunks:
                # Keep "other - …" as one subunit; otherwise allow comma-separated pieces.
                if chunk.strip().lower().startswith("other"):
                    pieces = [chunk]
                else:
                    pieces = _split_outside_parens(chunk, ",")
                    if len(pieces) == 1:
                        pieces = [p.strip() for p in re.split(r"[/]", chunk) if p.strip()]
                for piece in pieces:
                    piece = piece.strip()
                    if not piece:
                        continue
                    parsed.append(
                        {
                            "region": region,
                            "sub_location": _resolve_sublocation(region, piece),
                        }
                    )
        else:
            parsed.append(
                {
                    "region": region,
                    "sub_location": DEFAULT_SUBLOCATION.get(region, "Unspecified"),
                }
            )

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for entry in parsed:
        key = (entry["region"], entry["sub_location"])
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    return raw, unique or [{"region": "Unknown", "sub_location": "Unspecified"}]


def format_locations_display(locations: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for loc in locations:
        region = loc["region"]
        sub = (loc.get("sub_location") or "").strip()
        if sub:
            parts.append(f"{region} ({sub})")
        else:
            parts.append(region)
    return "; ".join(parts)


def primary_location(locations: list[dict[str, str]]) -> dict[str, str]:
    return locations[0] if locations else {"region": "Unknown", "sub_location": "Unspecified"}


def _stage_marked(value: Any) -> bool:
    return _clean(value).lower() in {"x", "y"}


def _images_from_row(row: dict[str, Any]) -> list[tuple[str, str, int]]:
    images: list[tuple[str, str, int]] = []
    order = 1
    for column, filename, stage in STAGE_MARKERS:
        if _stage_marked(row.get(column)):
            images.append((filename, stage, order))
            order += 1

    if not images:
        images = [
            ("01_preop.jpg", "Pre-op", 1),
            ("06_healed.jpg", "Post-op", 2),
        ]
    return images


def _format_patient_id(pt_number: Any) -> str:
    text = _clean(pt_number)
    # Excel/openpyxl sometimes hands back a whole number as a float ("23" -> 23.0
    # -> "23.0") if the column's number format ever changes; treat that as "23",
    # not sub-case "23.0", so the folder name still matches the Pt # people expect.
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    if re.fullmatch(r"\d+", text):
        return f"ENT-{int(text):03d}"
    if re.fullmatch(r"\d+\.\d+", text):
        whole, frac = text.split(".", 1)
        return f"ENT-{int(whole):03d}.{frac}"
    return f"ENT-{text}"


def _build_notes(row: dict[str, Any], raw_method: str, method_category: str) -> str:
    pearls = _clean(row.get(COL_PEARLS))
    notes_parts: list[str] = []
    if pearls:
        notes_parts.append(pearls)

    # Preserve detailed method text when it adds info beyond the category label.
    method_detail = raw_method.strip()
    if (
        method_detail
        and method_detail.lower() != method_category.lower()
        and len(method_detail) > len(method_category) + 3
        and method_detail.lower() not in pearls.lower()
    ):
        notes_parts.append(f"Method detail: {method_detail}")

    incomplete = _clean(row.get(COL_EXCEL_COMPLETE))
    if not incomplete or incomplete.lower() in {"n", "no"}:
        ayaan_note = _clean(row.get(COL_NOTES_AYAAN))
        if ayaan_note:
            notes_parts.append(f"Entry note: {ayaan_note}")
        else:
            notes_parts.append("Excel entry incomplete")

    return " | ".join(notes_parts)


def row_to_patient(row: dict[str, Any]) -> dict[str, Any] | None:
    pt_number = row.get(COL_PT)
    if pt_number is None or _clean(pt_number) == "":
        return None

    patient_id = _format_patient_id(pt_number)
    location_raw, locations = parse_locations(_clean(row.get(COL_LOCATION)))
    primary = primary_location(locations)
    raw_method = _clean(row.get(COL_METHOD))
    method_category = _normalize_method(raw_method)
    if method_category not in CANONICAL_METHODS:
        _warn(
            f"{patient_id}: method of repair text {raw_method!r} didn't match a known "
            f"category — using {method_category!r} as-is, which won't show up under "
            "the Method of Repair filter until field_options.py is updated."
        )

    return {
        "folder_name": patient_id,
        "patient_id": patient_id,
        "location_raw": location_raw,
        "locations": locations,
        "region": primary["region"],
        "sub_location": primary["sub_location"],
        "defect_size": _normalize_defect_size(_clean(row.get(COL_SIZE))),
        "full_thickness": _normalize_full_thickness(_clean(row.get(COL_FULL_THICKNESS))),
        "method_of_repair": method_category,
        "general_flap": _normalize_general_flap(row.get(COL_GENERAL_FLAP)),
        "specific_flap_description": _clean(row.get(COL_SPECIFIC_FLAP)) or "n/a",
        "graft": _normalize_graft(_clean(row.get(COL_GRAFT))),
        "graft_donor_site": _normalize_donor_site(_clean(row.get(COL_DONOR))),
        "notes": _build_notes(row, raw_method, method_category),
        "images": _images_from_row(row),
    }


# Every column the importer actually reads. Kept as one list so header matching
# and the "which columns are required" check both stay in sync with row_to_patient.
KNOWN_COLUMNS = [
    COL_PT,
    COL_LOCATION,
    COL_FULL_THICKNESS,
    COL_SIZE,
    COL_METHOD,
    COL_GENERAL_FLAP,
    COL_SPECIFIC_FLAP,
    COL_GRAFT,
    COL_DONOR,
    COL_NOTES_AYAAN,
    COL_PEARLS,
    COL_PREOP,
    COL_DEFECT,
    COL_DRAWN,
    COL_RAISED,
    COL_CLOSED,
    COL_POSTOP,
    COL_EXCEL_COMPLETE,
]

REQUIRED_COLUMNS = [
    COL_PT,
    COL_LOCATION,
    COL_FULL_THICKNESS,
    COL_SIZE,
    COL_METHOD,
    COL_GENERAL_FLAP,
    COL_SPECIFIC_FLAP,
    COL_GRAFT,
    COL_DONOR,
    COL_PEARLS,
]


def _normalize_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _header_map(header_row: list[Any]) -> dict[str, int]:
    """Map each known column constant to its index.

    Matching is whitespace/case-insensitive so a header that picks up or loses
    a stray trailing space, or changes capitalization, between spreadsheet
    exports still resolves — only a genuine rename or removal should ever
    trip the "missing expected columns" check below.
    """
    by_normalized: dict[str, int] = {}
    for index, value in enumerate(header_row):
        if value is None:
            continue
        norm = _normalize_header(value)
        if norm and norm not in by_normalized:
            by_normalized[norm] = index

    mapping: dict[str, int] = {}
    for expected in KNOWN_COLUMNS:
        norm = _normalize_header(expected)
        if norm in by_normalized:
            mapping[expected] = by_normalized[norm]
    return mapping


def load_patients_from_xlsx(xlsx_path: Path | None = None) -> list[dict[str, Any]]:
    path = xlsx_path or XLSX_PATH
    workbook = load_workbook(path, data_only=True, read_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    header = list(next(rows_iter))
    columns = _header_map(header)

    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ValueError(f"Spreadsheet missing expected columns: {missing}")

    patients: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for values in rows_iter:
        if values is None or all(v is None or str(v).strip() == "" for v in values):
            continue
        row = {name: values[index] if index < len(values) else None for name, index in columns.items()}
        patient = row_to_patient(row)
        if patient is None:
            continue
        if patient["patient_id"] in seen_ids:
            _warn(
                f"Duplicate Pt # for {patient['patient_id']} — keeping the first "
                "occurrence and dropping this row."
            )
            continue
        if patient["region"] == "Unknown":
            _warn(
                f"{patient['patient_id']}: couldn't recognize a region in the "
                f"location text {patient['location_raw']!r} — filed under Unknown."
            )
        seen_ids.add(patient["patient_id"])
        patients.append(patient)

    patients.sort(key=lambda item: item["patient_id"])
    return patients


SEED_PATIENTS: list[dict[str, Any]] = load_patients_from_xlsx()

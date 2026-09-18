"""Pre-flight check for a patient log spreadsheet before wiring it into the app.

Run this against any candidate .xlsx — the current one or a brand-new export —
to see what the importer (import_patient_log.py) would do with it: how many
cases it finds, which rows it can't fully make sense of, and which values
won't be filterable in the UI until field_options.py is updated. Nothing on
disk is changed; this only reads the spreadsheet and reports.

Usage:
    python validate_spreadsheet.py                       # checks the file the app would currently load
    python validate_spreadsheet.py path/to/new_export.xlsx
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

from app import IMAGE_ROOT
from field_options import FILTER_OPTIONS
from image_enumeration import UNLABELLED, case_key, enumerate_case_images, index_case_folders
from import_patient_log import (
    CANONICAL_METHODS,
    COL_PT,
    STAGE_MARKERS,
    _clean,
    _header_map,
    _stage_marked,
    load_patients_from_xlsx,
)
from openpyxl import load_workbook


def _line(char: str = "-", width: int = 70) -> None:
    print(char * width)


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else None

    print("Facial Reconstruction Atlas — spreadsheet pre-flight check")
    _line("=")

    try:
        patients = load_patients_from_xlsx(target)
    except FileNotFoundError as exc:
        print(f"FAIL — couldn't find a spreadsheet to check.\n  {exc}")
        return 1
    except ValueError as exc:
        print(f"FAIL — this spreadsheet can't be imported as-is.\n  {exc}")
        print(
            "\nThe importer expects these exact columns (whitespace/case may drift, "
            "the wording may not): see KNOWN_COLUMNS in import_patient_log.py."
        )
        return 1

    resolved_path = target or _current_default_path()
    print(f"Checked file: {resolved_path}")
    print(f"Cases imported: {len(patients)}")

    # Raw row count for comparison against what actually made it through.
    workbook = load_workbook(resolved_path, data_only=True, read_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    header = list(next(rows_iter))
    columns = _header_map(header)
    pt_index = columns.get(COL_PT)
    raw_row_count = 0
    if pt_index is not None:
        for values in rows_iter:
            if values is None:
                continue
            if pt_index < len(values) and _clean(values[pt_index]):
                raw_row_count += 1
    print(f"Non-empty Pt # rows in the sheet: {raw_row_count}")
    if raw_row_count != len(patients):
        print(
            f"  -> {raw_row_count - len(patients)} row(s) were dropped (duplicate Pt #, "
            "or blank required fields). Warnings above (run with 2>&1 to see them "
            "interleaved) name which ones."
        )

    issues = 0

    _line()
    print("Region / location")
    unknown = [p for p in patients if p["region"] == "Unknown"]
    if unknown:
        issues += len(unknown)
        print(f"  {len(unknown)} case(s) filed under 'Unknown' region — the location text")
        print("  didn't contain a recognized region keyword. These are still imported and")
        print("  searchable by keyword, but won't appear when clicking a region or filtering")
        print("  by Location:")
        for p in unknown[:15]:
            print(f"    {p['patient_id']}: {p['location_raw']!r}")
        if len(unknown) > 15:
            print(f"    ... and {len(unknown) - 15} more")
    else:
        print("  OK — every case resolved to a known region.")

    _line()
    print("Sub-locations not in the curated filter list (field_options.py)")
    known_subs = FILTER_OPTIONS["sub_locations"]
    unmapped_subs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for p in patients:
        for loc in p["locations"]:
            region, sub = loc["region"], loc["sub_location"]
            allowed = known_subs.get(region, [])
            if sub and sub not in allowed and sub != "Unspecified":
                unmapped_subs[(region, sub)].append(p["patient_id"])
    if unmapped_subs:
        issues += len(unmapped_subs)
        print(
            f"  {len(unmapped_subs)} region/sub-location combination(s) will be stored and shown"
        )
        print("  on the case, but can't be selected from the Sub-location dropdown until added")
        print("  to FILTER_OPTIONS['sub_locations'] in field_options.py:")
        for (region, sub), ids in sorted(unmapped_subs.items()):
            sample = ", ".join(ids[:3]) + ("…" if len(ids) > 3 else "")
            print(f"    {region} -> {sub!r}  (e.g. {sample})")
    else:
        print("  OK — every sub-location matches the curated list.")

    _line()
    print("Method of repair")
    bad_methods = Counter(p["method_of_repair"] for p in patients if p["method_of_repair"] not in CANONICAL_METHODS)
    if bad_methods:
        issues += len(bad_methods)
        print("  These method-of-repair values didn't normalize to one of the five")
        print("  canonical categories, so they won't show up under the Method of Repair filter:")
        for value, count in bad_methods.most_common():
            print(f"    {value!r} — {count} case(s)")
    else:
        print("  OK — every case normalized to a canonical method.")

    _line()
    print("Flap / graft vocabulary (informational — free-form fields)")
    for field, options_key, label in [
        ("general_flap", "general_flaps", "General flap"),
        ("graft", "grafts", "Graft"),
        ("graft_donor_site", "graft_donor_sites", "Graft donor site"),
    ]:
        allowed = {v.lower() for v in FILTER_OPTIONS[options_key]}
        unknown_values = Counter(
            p[field] for p in patients if p[field] and p[field].lower() not in allowed
        )
        if unknown_values:
            issues += len(unknown_values)
            print(f"  {label}: {len(unknown_values)} value(s) not in field_options.py, won't be")
            print("  selectable via that dropdown (shown on the case card/detail regardless):")
            for value, count in unknown_values.most_common(10):
                print(f"    {value!r} — {count} case(s)")
        else:
            print(f"  {label}: OK.")

    _line()
    print("Duplicate patient IDs")
    ids = [p["patient_id"] for p in patients]
    dupes = [pid for pid, count in Counter(ids).items() if count > 1]
    if dupes:
        issues += len(dupes)
        print(f"  {len(dupes)} id(s) appear more than once after import (shouldn't happen): {dupes}")
    else:
        print("  OK — no duplicates in the imported set (duplicate rows in the sheet were")
        print("  already dropped and reported above, if any).")

    _line()
    print("Stage-marker columns (which columns say a photo exists for that step)")
    stray_values: Counter[str] = Counter()
    workbook2 = load_workbook(resolved_path, data_only=True, read_only=True)
    sheet2 = workbook2.active
    rows2 = sheet2.iter_rows(values_only=True)
    header2 = list(next(rows2))
    columns2 = _header_map(header2)
    stage_cols = [(col, idx) for col, _, _ in STAGE_MARKERS if (idx := columns2.get(col)) is not None]
    pt_idx2 = columns2.get(COL_PT)
    for values in rows2:
        if values is None:
            continue
        if pt_idx2 is not None and (pt_idx2 >= len(values) or not _clean(values[pt_idx2])):
            continue  # blank/separator row — the real importer skips these too
        for col_name, idx in stage_cols:
            if idx >= len(values):
                continue
            raw = _clean(values[idx])
            if raw and not _stage_marked(raw):
                stray_values[raw] += 1
    if stray_values:
        issues += len(stray_values)
        print("  These stage columns had a value that isn't blank, 'x', or 'y' — that row's")
        print("  photo for that stage will be silently skipped:")
        for value, count in stray_values.most_common(10):
            print(f"    {value!r} — {count} cell(s)")
    else:
        print("  OK — every marked cell uses 'x'/'y' as expected.")

    _line()
    print("Image store")
    if IMAGE_ROOT.is_dir():
        print(f"  Root: {IMAGE_ROOT}")
        folder_index = index_case_folders(IMAGE_ROOT)
        case_keys = {case_key(p["patient_id"]): p["patient_id"] for p in patients}

        missing = sorted(pid for key, pid in case_keys.items() if key not in folder_index)
        orphaned = sorted(name for key, name in folder_index.items() if key not in case_keys)
        if missing:
            issues += len(missing)
            print(f"  {len(missing)} case(s) have no folder on the store (expected for")
            print("  brand-new cases — regenerate placeholders, or add the photographs,")
            print("  before going live). Their filenames would come from the spreadsheet's")
            print("  stage columns instead, which is only correct for the placeholder set:")
            print(f"    {missing[:15]}{'...' if len(missing) > 15 else ''}")
        else:
            print("  OK — every case has a matching folder.")
        if orphaned:
            print(f"  {len(orphaned)} folder(s) match no case in this spreadsheet (fine if")
            print("  intentional, otherwise check for a renumbered Pt #):")
            print(f"    {orphaned[:15]}{'...' if len(orphaned) > 15 else ''}")

        total = 0
        unlabelled: list[str] = []
        empty: list[str] = []
        for key, pid in sorted(case_keys.items(), key=lambda kv: kv[1]):
            folder_name = folder_index.get(key)
            if folder_name is None:
                continue
            rows = enumerate_case_images(IMAGE_ROOT / folder_name)
            total += len(rows)
            if not rows:
                empty.append(folder_name)
            unlabelled.extend(
                f"{folder_name}/{filename}"
                for filename, stage, _ in rows
                if stage == UNLABELLED
            )
        print(f"  {total} photograph(s) across {len(folder_index)} folder(s).")
        if empty:
            issues += len(empty)
            print(f"  {len(empty)} folder(s) hold no servable image, so those cases will not")
            print(f"  appear in search results at all: {empty[:10]}")
        if unlabelled:
            issues += len(unlabelled)
            print(f"  {len(unlabelled)} photograph(s) match no known naming rule. They are")
            print(f"  imported and shown, labelled '{UNLABELLED}', sorted after the rest:")
            for name in unlabelled[:15]:
                print(f"    {name}")
            if len(unlabelled) > 15:
                print(f"    ... and {len(unlabelled) - 15} more")
        else:
            print("  OK — every photograph resolved to one of the six stages.")
    else:
        print(f"  {IMAGE_ROOT} not found — skipping (nothing to cross-check yet).")

    _line("=")
    if issues == 0:
        print("PASS — no issues found. Safe to import.")
    else:
        print(f"REVIEWED — {issues} item(s) flagged above. None of these block the import (only")
        print("a missing required column does that), but review them before treating the data")
        print("as fully clean. Most are fixed by adding a new value to field_options.py, or by")
        print("tightening the wording in the spreadsheet to match the existing convention.")
    return 0


def _current_default_path() -> Path:
    import import_patient_log

    return import_patient_log.XLSX_PATH


if __name__ == "__main__":
    raise SystemExit(main())

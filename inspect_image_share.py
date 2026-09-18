"""Read-only survey of a real case-image share, to establish its file naming.

The importer currently derives image filenames from the spreadsheet's stage
columns. Replacing that with real directory enumeration needs the actual naming
convention, and the actual exceptions to it — which nobody can recall reliably
from memory and which should not be guessed at in code.

Run this against the real share. It opens nothing, writes nothing, and copies
nothing: it lists directories, groups filenames by shape, and reports what it
found. Nothing about image content is read.

Filenames from a real share may themselves carry identifiers, so the default
output is written to be safe to paste into a ticket or an email:

  * digits are masked, so `pt_20_img_3.jpg` is reported as `pt_#_img_#.jpg`
  * a word appearing in only one case folder is withheld and counted, on the
    grounds that a convention token appears everywhere and a patient's name
    appears once. `--show-rare-words` reveals them, for when you have looked
    and they are innocuous.

Usage:
    python inspect_image_share.py /path/to/share
    python inspect_image_share.py                    # uses ENTDATABASE_IMAGE_ROOT
    python inspect_image_share.py /path/to/share --show-rare-words
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
DIGIT_RUN = re.compile(r"\d+")
WORD = re.compile(r"[A-Za-z]+")

# `pt_20_img_3`, `pt_20_img_3.1`, `pt_20_b2` — strip the case prefix and the
# `img` marker, and whatever is left is what the file says about its stage.
CASE_PREFIX = re.compile(r"(?i)^pt[._-]*\d+(?:[._-]\d+)?[._-]*")
IMG_MARKER = re.compile(r"(?i)^img[._-]*")
# Six reconstruction stages, optionally with a sub-index where a stage was
# photographed more than once.
STAGE_TOKEN = re.compile(r"^(\d+)(?:([._-])(\d+))?$")
STAGE_COUNT = 6


def stage_token(stem: str) -> str:
    """What a filename says about its stage, with the boilerplate removed."""
    token = CASE_PREFIX.sub("", stem, count=1)
    token = IMG_MARKER.sub("", token, count=1)
    return token.strip("._- ")


def _line(char: str = "-", width: int = 74) -> None:
    print(char * width)


def mask(name: str) -> str:
    """`pt_20_img_3.jpg` -> `pt_#_img_#.jpg` — shape without the numbers."""
    return DIGIT_RUN.sub("#", name)


def words(name: str) -> list[str]:
    return [w.lower() for w in WORD.findall(name)]


def survey(root: Path, show_rare_words: bool) -> int:
    print("Facial Reconstruction Atlas — image share survey (read-only)")
    _line("=")
    print(f"Share root: {root}")

    if not root.is_dir():
        print(f"FAIL — {root} is not a directory.")
        return 1

    folders = sorted(p for p in root.iterdir() if p.is_dir())
    loose = sorted(p for p in root.iterdir() if p.is_file() and not p.name.startswith("."))
    print(f"Case folders: {len(folders)}")
    if loose:
        print(f"Loose files at the root (not in any case folder): {len(loose)}")
        for path in loose[:10]:
            print(f"    {mask(path.name)}")

    if not folders:
        print("\nNothing to survey.")
        return 1

    per_folder: dict[str, list[Path]] = {
        folder.name: sorted(
            p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")
        )
        for folder in folders
    }

    # A word used across many folders is part of the convention; one used in a
    # single folder might be somebody's name. Folder names are shown in full
    # only when every word in them is conventional.
    word_folders: dict[str, set[str]] = defaultdict(set)
    for folder_name, files in per_folder.items():
        for path in files:
            for word in words(path.stem):
                word_folders[word].add(folder_name)
    for folder in folders:
        for word in words(folder.name):
            word_folders[word].add(folder.name)
    common_words = {w for w, seen in word_folders.items() if len(seen) > 1}

    def label(folder_name: str) -> str:
        """The folder's own name, unless part of it looks individual to it."""
        if all(word in common_words for word in words(folder_name)):
            return folder_name
        return mask(folder_name)

    def shape(name: str) -> str:
        """The masked shape of a name, with anything folder-specific removed.

        Digits always go. A word used in only one folder also goes unless
        --show-rare-words is set, because at that point it could be a name
        rather than part of the convention.
        """
        masked = mask(name)
        if show_rare_words:
            return masked
        # The extension and single letters stay: a file type identifies nobody,
        # and a lone letter is a disambiguator (img_3b) that the parser needs.
        stem, dot, suffix = masked.rpartition(".")
        if not dot:
            stem, suffix = masked, ""
        hidden = WORD.sub(
            lambda m: m.group()
            if len(m.group()) == 1 or m.group().lower() in common_words
            else "~",
            stem,
        )
        return f"{hidden}.{suffix}" if dot else hidden

    def sample(names: list[str], limit: int = 8) -> str:
        shown = [label(n) for n in names[:limit]]
        suffix = f" (+{len(names) - limit} more)" if len(names) > limit else ""
        return f"{shown}{suffix}"

    print(
        "Output mode: "
        + ("full — every word shown, check before sharing"
           if show_rare_words
           else "safe — words used in only one folder are replaced with ~")
    )

    _line()
    print("Folder naming")
    folder_shapes = Counter(shape(p.name) for p in folders)
    for folder_shape, count in folder_shapes.most_common():
        print(f"  {folder_shape!r} — {count} folder(s)")
    if len(folder_shapes) > 1:
        print("  -> more than one folder shape; the importer will need to handle each.")

    numbers = [m.group() for p in folders if (m := DIGIT_RUN.search(p.name))]
    padded = [n for n in numbers if len(n) > 1 and n.startswith("0")]
    print(f"  Zero-padded numbers: {len(padded)} of {len(numbers)}"
          f"{' (e.g. ' + padded[0] + ')' if padded else ''}")
    decimals = [p.name for p in folders if re.search(r"\d+[._-]\d+$", p.name)]
    if decimals:
        print(f"  Possible sub-case folders ({len(decimals)}): {sample(decimals, 5)}")

    _line()
    print("Files per folder")
    counts = [len(v) for v in per_folder.values()]
    print(f"  total files: {sum(counts)}")
    print(f"  min {min(counts)} / median {statistics.median(counts):g} / max {max(counts)}")
    empty = [name for name, files in per_folder.items() if not files]
    if empty:
        print(f"  {len(empty)} empty folder(s): {sample(empty)}")
    nested = [name for name, files in per_folder.items()
              if any(f.parent.name != name for f in files)]
    if nested:
        print(f"  {len(nested)} folder(s) contain subfolders — enumeration must decide")
        print(f"    whether to recurse: {sample(nested)}")

    _line()
    print("Extensions")
    extensions = Counter(p.suffix.lower() for files in per_folder.values() for p in files)
    for suffix, count in extensions.most_common():
        flag = "" if suffix in IMAGE_EXTENSIONS else "   <-- not served by the app today"
        print(f"  {suffix or '(none)'!r:12} {count:5}{flag}")

    _line()
    print("Filename shapes (digits masked), most common first")
    shapes: Counter[str] = Counter()
    for files in per_folder.values():
        for path in files:
            shapes[shape(path.name)] += 1
    total_files = sum(shapes.values())
    for name_shape, count in shapes.most_common(25):
        share = 100 * count / total_files
        print(f"  {count:5}  ({share:5.1f}%)  {name_shape}")
    if len(shapes) > 25:
        tail = sum(count for _, count in shapes.most_common()[25:])
        print(f"  ... {len(shapes) - 25} further shape(s), {tail} file(s)")
    print(f"  {len(shapes)} distinct shape(s) across {total_files} files.")
    print("  The top shape is the convention; everything below it is an exception the")
    print("  importer has to cope with.")

    _line()
    print("Stage tokens")
    plain: Counter[int] = Counter()
    subindexed: Counter[str] = Counter()
    separators: Counter[str] = Counter()
    out_of_range: Counter[int] = Counter()
    special: Counter[str] = Counter()
    stages_present: dict[str, set[int]] = defaultdict(set)

    for folder_name, files in per_folder.items():
        for path in files:
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            token = stage_token(path.stem)
            match = STAGE_TOKEN.match(token)
            if match is None:
                special[shape(token) or "(empty)"] += 1
                continue
            number = int(match.group(1))
            separator, sub_index = match.group(2), match.group(3)
            if not 1 <= number <= STAGE_COUNT:
                out_of_range[number] += 1
                continue
            stages_present[folder_name].add(number)
            if sub_index is None:
                plain[number] += 1
            else:
                subindexed[f"{number}{separator}{sub_index}"] += 1
                separators[separator] += 1

    recognised = sum(plain.values()) + sum(subindexed.values())
    total_images = sum(
        1 for files in per_folder.values() for p in files
        if p.suffix.lower() in IMAGE_EXTENSIONS
    )
    print(f"  Images carrying a stage in 1-{STAGE_COUNT}: {recognised} of {total_images}")
    print(f"    plain stage number:     {sum(plain.values())}  {dict(sorted(plain.items()))}")
    print(f"    stage with a sub-index: {sum(subindexed.values())}")
    if subindexed:
        for token, count in subindexed.most_common(12):
            print(f"      {token!r} - {count}")
        print(f"    sub-index separator: {dict(separators)}"
              f"{'   <-- more than one separator in use' if len(separators) > 1 else ''}")
    if out_of_range:
        print(f"    number outside 1-{STAGE_COUNT}: {sum(out_of_range.values())} "
              f"{dict(sorted(out_of_range.items()))}")
        print("      -> a sequence rather than a stage, or a seventh category.")

    print(f"\n  Images with no recognisable stage: {sum(special.values())}")
    if special:
        print("  These are the special cases. Each needs a rule, or a human label:")
        for token, count in special.most_common(20):
            print(f"      {token!r} - {count} file(s)")
        if len(special) > 20:
            print(f"      ... and {len(special) - 20} further distinct token(s)")

    print("\n  Stage coverage per case")
    complete = [f for f, seen in stages_present.items() if len(seen) == STAGE_COUNT]
    print(f"    cases with all {STAGE_COUNT} stages: {len(complete)} of {len(per_folder)}")
    missing_counts = Counter(
        STAGE_COUNT - len(stages_present.get(name, set())) for name in per_folder
    )
    print(f"    stages missing per case: {dict(sorted(missing_counts.items()))}")
    never_seen = [
        n for n in range(1, STAGE_COUNT + 1)
        if not any(n in seen for seen in stages_present.values())
    ]
    if never_seen:
        print(f"    stage number(s) never seen anywhere: {never_seen}")

    _line()
    print("Words used in filenames")
    common = {w: f for w, f in word_folders.items() if len(f) > 1}
    rare = {w: f for w, f in word_folders.items() if len(f) == 1}
    print(f"  {len(common)} word(s) appear in more than one case folder — convention:")
    for word, seen in sorted(common.items(), key=lambda kv: -len(kv[1]))[:30]:
        print(f"    {word!r} — {len(seen)} folder(s)")
    print(f"\n  {len(rare)} word(s) appear in exactly one case folder.")
    if rare:
        print("  A convention word appears everywhere; a name or an MRN appears once.")
        print("  Check these before treating filenames as safe to store and put in URLs.")
        if show_rare_words:
            for word, seen in sorted(rare.items()):
                print(f"    {word!r} in {label(next(iter(seen)))}")
        else:
            print("  Withheld, and replaced with ~ in the shapes above. Once you have")
            print("  looked at them, rerun with --show-rare-words for the full report.")

    _line()
    print("Cross-reference against the spreadsheet")
    try:
        from import_patient_log import SEED_PATIENTS
    except Exception as exc:  # noqa: BLE001 — the spreadsheet is optional here
        print(f"  Skipped: no spreadsheet available to compare against ({type(exc).__name__}).")
        print("  Set ENTDATABASE_XLSX_PATH to include this check.")
    else:
        sheet_numbers = {
            m.group(): p["patient_id"]
            for p in SEED_PATIENTS
            if (m := DIGIT_RUN.search(p["patient_id"]))
        }
        folder_numbers = {
            m.group().lstrip("0") or "0": p.name
            for p in folders
            if (m := DIGIT_RUN.search(p.name))
        }
        sheet_keys = {k.lstrip("0") or "0" for k in sheet_numbers}
        print(f"  Cases in the spreadsheet: {len(sheet_keys)}")
        print(f"  Folders on the share:     {len(folder_numbers)}")
        no_folder = sorted(sheet_keys - folder_numbers.keys(), key=lambda s: int(s) if s.isdigit() else 0)
        no_case = sorted(folder_numbers.keys() - sheet_keys, key=lambda s: int(s) if s.isdigit() else 0)
        print(f"  Cases with no folder: {len(no_folder)} {no_folder[:12]}"
              f"{' ...' if len(no_folder) > 12 else ''}")
        print(f"  Folders with no case: {len(no_case)} {no_case[:12]}"
              f"{' ...' if len(no_case) > 12 else ''}")

    _line("=")
    print("Paste this whole report back. It is what the enumeration and the stage")
    print("derivation get designed against.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "root",
        nargs="?",
        default=os.getenv("ENTDATABASE_IMAGE_ROOT"),
        help="the share to survey; defaults to ENTDATABASE_IMAGE_ROOT",
    )
    parser.add_argument(
        "--show-rare-words",
        action="store_true",
        help="print words that appear in only one folder instead of withholding them",
    )
    args = parser.parse_args()
    if not args.root:
        parser.error("no share given and ENTDATABASE_IMAGE_ROOT is not set")
    return survey(Path(args.root).expanduser(), args.show_rare_words)


if __name__ == "__main__":
    raise SystemExit(main())

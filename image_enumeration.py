"""Enumerate the photographs actually present in a case folder.

The importer derived image filenames from the spreadsheet's stage columns,
producing a fixed six-file set that happened to match the generated
placeholders. Real case folders hold a variable number of photographs under the
department's own naming, so what is on disk is the only reliable answer to
which images a case has.

Two conventions are recognised, each by name rather than by pattern-sniffing:

  departmental   pt_<case>_img_<stage>[.<index>].jpg
                 Stage is 1-6. The optional index distinguishes a stage
                 photographed more than once (3.1, 3.2). Not every case has
                 every stage, and the numbering is not expected to be
                 contiguous.

  placeholder    01_preop.jpg ... 06_healed.jpg
                 What generate_placeholders.py writes. Recognised so the
                 fabricated twelve-case set and the public demo keep working.

Anything matching neither is **kept, not dropped**. It is recorded with no
stage and sorted after the recognised images, so an unfamiliar name surfaces in
the interface as an unlabelled photograph that somebody can look at, rather
than vanishing silently. That is deliberate: the department's folders contain
special cases nobody has a complete list of, and a photograph the application
declines to show is worse than one it shows without a label.
"""

from __future__ import annotations

import re
from pathlib import Path

from import_patient_log import STAGE_MARKERS

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Stage number -> label, and the placeholder filenames, both taken from the one
# existing definition of the six stages rather than restated here.
STAGE_LABELS: dict[int, str] = {
    number: stage for number, (_, _, stage) in enumerate(STAGE_MARKERS, start=1)
}
PLACEHOLDER_STAGES: dict[str, int] = {
    filename: number for number, (_, filename, _) in enumerate(STAGE_MARKERS, start=1)
}
UNLABELLED = "Unlabelled"

# `pt_20_img_3`, `pt_20_img_3.1`, `PT_7_IMG_5_2`. The sub-index separator is a
# period in everything seen so far; an underscore is accepted too because the
# two are indistinguishable in intent and rejecting one would silently drop the
# photograph.
DEPARTMENTAL = re.compile(
    r"""(?ix)
    ^pt [._-]* (?P<case>\d+ (?:[._-]\d+)?)   # pt_20, pt_23.1
    [._-]+ img [._-]*                        # _img_
    (?P<stage>\d+)                           # 3
    (?: [._-] (?P<index>\d+) )?              # .1
    $""",
)
CASE_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def case_key(text: str) -> str | None:
    """Reduce a case reference to something two naming schemes can be matched on.

    `ENT-020`, `pt_20` and `pt_020` all key to `20`; `ENT-023.1` and `pt_23.1`
    both key to `23.1`. Returns None when there is no number to key on.
    """
    match = CASE_NUMBER.search(text.replace("_", "."))
    if match is None:
        return None
    whole, _, fraction = match.group(1).partition(".")
    trimmed = str(int(whole))
    return f"{trimmed}.{fraction}" if fraction else trimmed


def index_case_folders(image_root: Path) -> dict[str, str]:
    """Map each case key to the folder holding that case's photographs."""
    if not image_root.is_dir():
        return {}
    index: dict[str, str] = {}
    for folder in sorted(p for p in image_root.iterdir() if p.is_dir()):
        key = case_key(folder.name)
        if key is not None:
            index.setdefault(key, folder.name)
    return index


def parse_stage(filename: str) -> tuple[int | None, int]:
    """Return the stage number a filename claims, and its index within that stage.

    `(None, 0)` means neither convention recognised it.
    """
    stem = Path(filename).stem

    match = DEPARTMENTAL.match(stem)
    if match is not None:
        stage = int(match.group("stage"))
        if stage in STAGE_LABELS:
            return stage, int(match.group("index") or 0)
        # A number outside 1-6 is not a stage this application knows about.
        return None, 0

    placeholder = PLACEHOLDER_STAGES.get(filename.lower())
    if placeholder is not None:
        return placeholder, 0

    return None, 0


def enumerate_case_images(folder: Path) -> list[tuple[str, str, int]]:
    """List a case folder as (filename, stage label, sort order) rows.

    Only the top level is read. Photographs filed in a subfolder are left out
    rather than flattened into the case, because a subfolder means somebody
    separated them deliberately and guessing at why would be worse than a
    reported gap.
    """
    if not folder.is_dir():
        return []

    recognised: list[tuple[int, int, str]] = []
    unrecognised: list[str] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            continue
        stage, index = parse_stage(path.name)
        if stage is None:
            unrecognised.append(path.name)
        else:
            recognised.append((stage, index, path.name))

    rows: list[tuple[str, str, int]] = []
    for sort_order, (stage, _, filename) in enumerate(sorted(recognised), start=1):
        rows.append((filename, STAGE_LABELS[stage], sort_order))
    for offset, filename in enumerate(sorted(unrecognised), start=len(rows) + 1):
        rows.append((filename, UNLABELLED, offset))
    return rows


FRONT = ""
"""An anchor meaning "first in the case", as distinct from None, which means
"wherever enumeration put it". Moving the second photograph up needs a way to
say this."""


def apply_overrides(
    rows: list[tuple[str, str, int]],
    overrides: dict[str, tuple[str | None, str | None, bool]],
) -> tuple[list[tuple[str, str, int, bool]], list[str]]:
    """Re-label, re-position and hide enumerated images from recorded decisions.

    An override carries a replacement stage, a filename this image should
    follow, whether it is hidden, or any combination. Position is stored as
    "goes after this file" rather than as a number because that is what the
    department's notes say — "ped div goes bw 4 and 5" — and because a number
    would silently stop meaning "between 4 and 5" as soon as another
    photograph is added to the case.

    Anchors chain. `pt_34` reads "ped div goes bw 4 and 5, stage 3 goes bw ped
    div and 5": the pedicle-division photograph anchors to stage 4, and stage 3
    then anchors to the pedicle-division photograph. Each pass places whatever
    it can, so the chain resolves in order.

    Hidden images keep their place in the ordering rather than being removed
    from it, so unhiding one puts it back where it was.

    Returns rows as (filename, stage, sort order, hidden), and the filenames
    whose anchor could not be resolved — a deleted anchor, or a cycle. Those
    keep their derived position rather than being dropped.
    """
    def override(filename: str) -> tuple[str | None, str | None, bool]:
        return overrides.get(filename, (None, None, False))

    labelled = {
        filename: (override(filename)[0] or stage) for filename, stage, _ in rows
    }
    concealed = {filename: override(filename)[2] for filename, _, _ in rows}
    anchors = {
        filename: override(filename)[1]
        for filename, _, _ in rows
        if override(filename)[1] is not None
    }

    order = [filename for filename, _, _ in rows if filename not in anchors]
    pending = dict(anchors)

    # Front-anchored images first, so that a chain can then hang off them.
    # Reversed, so that sorting is stable once they are all inserted at 0.
    for filename in sorted((f for f, a in pending.items() if a == FRONT), reverse=True):
        order.insert(0, filename)
        del pending[filename]

    while pending:
        placeable = {
            filename: anchor for filename, anchor in pending.items() if anchor in order
        }
        if not placeable:
            break
        for filename in sorted(placeable):  # deterministic regardless of dict order
            order.insert(order.index(placeable[filename]) + 1, filename)
            del pending[filename]

    unresolved = sorted(pending)
    order.extend(unresolved)

    return (
        [
            (filename, labelled[filename], index, concealed[filename])
            for index, filename in enumerate(order, start=1)
        ],
        unresolved,
    )

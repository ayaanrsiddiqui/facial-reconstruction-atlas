"""Generate labeled staged placeholder images in patient subfolders."""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app import MOCK_O_DRIVE_PATH
from import_patient_log import SEED_PATIENTS, format_locations_display, primary_location

WIDTH, HEIGHT = 640, 480

REGION_COLORS = {
    "Nose": ("#e8edf4", "#2c4a6b"),
    "Forehead": ("#dce8f2", "#1f5f8b"),
    "Cheek": ("#e6f0ea", "#2f5d4a"),
    "Ear": ("#f0e8f2", "#5a3d6b"),
    "Temple": ("#edf0f6", "#3d4f6b"),
    "Lip": ("#f5ece8", "#6b4a3d"),
    "Scalp": ("#ece8f0", "#4a3d6b"),
    "Periorbital": ("#e8f0f5", "#3d5a6b"),
    "Chin": ("#f2ebe4", "#6b5a3d"),
    "Neck": ("#e8ebe8", "#4a5a4a"),
}

# Relative positions on the face oval for a simple region marker (cx, cy, rx, ry).
# Face oval is centered at (0, 0) with rx=1, ry=1.
REGION_MARKERS = {
    "Forehead": (0.0, -0.62, 0.42, 0.18),
    "Temple": (0.72, -0.28, 0.18, 0.16),
    "Cheek": (0.55, 0.18, 0.28, 0.22),
    "Nose": (0.0, 0.05, 0.18, 0.28),
    "Ear": (1.15, 0.0, 0.18, 0.28),
    "Lip": (0.0, 0.55, 0.28, 0.12),
    "Chin": (0.0, 0.78, 0.22, 0.14),
    "Neck": (0.0, 1.05, 0.28, 0.12),
    "Scalp": (0.0, -1.05, 0.45, 0.16),
    "Periorbital": (-0.32, -0.18, 0.22, 0.14),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def draw_face_sketch(
    base: Image.Image,
    center: tuple[int, int],
    face_rx: int,
    face_ry: int,
    ink: str,
    accent: str,
    region: str,
) -> Image.Image:
    """Draw a rough oval face with basic features and a region highlight."""
    image = base.convert("RGBA")
    draw = ImageDraw.Draw(image)
    cx, cy = center
    stroke = max(2, face_rx // 40)

    draw.ellipse(
        (cx - face_rx, cy - face_ry, cx + face_rx, cy + face_ry),
        outline=ink,
        width=stroke + 1,
    )

    ear_rx = int(face_rx * 0.18)
    ear_ry = int(face_ry * 0.28)
    for side in (-1, 1):
        ex = cx + side * int(face_rx * 0.98)
        draw.ellipse(
            (ex - ear_rx, cy - ear_ry, ex + ear_rx, cy + ear_ry),
            outline=ink,
            width=stroke,
        )

    eye_y = cy - int(face_ry * 0.18)
    eye_dx = int(face_rx * 0.32)
    eye_r = max(3, face_rx // 18)
    for side in (-1, 1):
        ex = cx + side * eye_dx
        draw.ellipse((ex - eye_r, eye_y - eye_r, ex + eye_r, eye_y + eye_r), fill=ink)
        brow_y = eye_y - eye_r * 3
        draw.line(
            (ex - eye_r * 2, brow_y, ex + eye_r * 2, brow_y - 1),
            fill=ink,
            width=stroke,
        )

    nose_top = cy - int(face_ry * 0.05)
    nose_bottom = cy + int(face_ry * 0.22)
    nose_w = int(face_rx * 0.12)
    draw.line((cx, nose_top, cx - nose_w, nose_bottom), fill=ink, width=stroke)
    draw.line((cx, nose_top, cx + nose_w, nose_bottom), fill=ink, width=stroke)
    draw.line((cx - nose_w, nose_bottom, cx + nose_w, nose_bottom), fill=ink, width=stroke)

    mouth_y = cy + int(face_ry * 0.48)
    mouth_w = int(face_rx * 0.28)
    draw.arc(
        (cx - mouth_w, mouth_y - 8, cx + mouth_w, mouth_y + 14),
        start=20,
        end=160,
        fill=ink,
        width=stroke + 1,
    )

    marker = REGION_MARKERS.get(region, (0.0, 0.0, 0.2, 0.2))
    mx, my, mrx, mry = marker
    mark_box = (
        cx + int(mx * face_rx) - int(mrx * face_rx),
        cy + int(my * face_ry) - int(mry * face_ry),
        cx + int(mx * face_rx) + int(mrx * face_rx),
        cy + int(my * face_ry) + int(mry * face_ry),
    )
    highlight = Image.new("RGBA", image.size, (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    accent_rgb = _hex_to_rgb(accent)
    hdraw.ellipse(mark_box, fill=(*accent_rgb, 90), outline=(*accent_rgb, 220), width=stroke + 1)
    return Image.alpha_composite(image, highlight)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    wrapped: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        tb = draw.textbbox((0, 0), trial, font=font)
        if tb[2] - tb[0] <= max_width or not current:
            current = trial
        else:
            wrapped.append(current)
            current = word
    if current:
        wrapped.append(current)
    return wrapped or [text]


def create_stage_image(patient: dict, filename: str, stage: str, output_path: Path) -> None:
    primary = primary_location(patient.get("locations", []))
    region = primary["region"]
    sub_location = primary["sub_location"]
    bg_color, accent = REGION_COLORS.get(region, ("#edf2f7", "#1f5f8b"))
    image = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 56), fill=accent)
    draw.text((24, 16), "Facial Reconstruction Atlas — Placeholder", font=load_font(20), fill="#ffffff")

    title_font = load_font(26, bold=True)
    body_font = load_font(18)
    small_font = load_font(15)
    ink = "#243447"

    face_cx, face_cy = 200, 250
    face_rx, face_ry = 110, 140
    image = draw_face_sketch(
        image,
        (face_cx, face_cy),
        face_rx,
        face_ry,
        ink,
        accent,
        region,
    ).convert("RGB")
    draw = ImageDraw.Draw(image)

    caption = f"~ {region} site marked"
    bbox = draw.textbbox((0, 0), caption, font=small_font)
    draw.text(
        (face_cx - (bbox[2] - bbox[0]) // 2, face_cy + face_ry + 12),
        caption,
        font=small_font,
        fill="#6b7c8f",
    )

    location_line = format_locations_display(patient.get("locations", []))
    if len(location_line) > 72:
        location_line = location_line[:69] + "..."

    lines = [
        patient["patient_id"],
        location_line or f"{region} ({sub_location})",
        stage,
        f"{patient['method_of_repair']} — {patient['specific_flap_description']}",
        f"Full thickness: {patient['full_thickness']} | Graft: {patient['graft']}",
    ]

    text_x = 360
    y = 110
    for index, line in enumerate(lines):
        font = title_font if index == 0 else body_font if index < 3 else small_font
        for part in _wrap_text(draw, line, font, WIDTH - text_x - 24):
            draw.text((text_x, y), part, font=font, fill=accent if index == 0 else ink)
            tb = draw.textbbox((0, 0), part, font=font)
            y += (tb[3] - tb[1]) + 6
        y += 10 if index < 2 else 6

    bbox = draw.textbbox((0, 0), filename, font=small_font)
    draw.text(
        ((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 36),
        filename,
        font=small_font,
        fill="#6b7c8f",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG", quality=90)


def main() -> None:
    if MOCK_O_DRIVE_PATH.exists():
        shutil.rmtree(MOCK_O_DRIVE_PATH)
    MOCK_O_DRIVE_PATH.mkdir(parents=True, exist_ok=True)

    for patient in SEED_PATIENTS:
        folder = MOCK_O_DRIVE_PATH / patient["folder_name"]
        folder.mkdir(parents=True, exist_ok=True)
        for filename, stage, _ in patient["images"]:
            create_stage_image(patient, filename, stage, folder / filename)
            print(f"Wrote {patient['folder_name']}/{filename}")


if __name__ == "__main__":
    main()

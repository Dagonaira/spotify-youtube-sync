"""Regenerates the app icon (assets/icon.ico, app/static/favicon.png, and the
PWA icon set in app/static/icons/) from the brand mark used in the app
header - an indigo rounded square with two overlapping circles. Re-run this
if the brand colors ever change.

Usage (from the project root): python assets/generate_icon.py
Requires Pillow (not a runtime dependency of the app itself - `pip install pillow`).
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

SCALE = 4
SIZE = 1024 * SCALE

INDIGO = (0x6C, 0x5C, 0xE0, 255)
INDIGO_SOFT = (0xB4, 0xA7, 0xF5, 255)
LIME_SOFT = (0x7E, 0xEA, 0xC0)


def _draw_mark(canvas_size: int, maskable: bool = False) -> Image.Image:
    """maskable=True fills the entire canvas edge-to-edge with the indigo
    background (no transparency, no rounded corners) and keeps the circles
    within Android's ~80% "safe zone", since a maskable icon gets its own
    shape (circle/squircle/rounded square) applied by the OS and would
    otherwise clip a transparent margin or the rounded-square outline oddly.
    """
    base = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    if maskable:
        draw.rectangle([0, 0, canvas_size, canvas_size], fill=INDIGO)
        inner = canvas_size * 0.8
    else:
        margin = canvas_size * 0.05
        radius = int(canvas_size * 0.24)
        draw.rounded_rectangle([margin, margin, canvas_size - margin, canvas_size - margin], radius=radius, fill=INDIGO)
        inner = canvas_size

    offset = (canvas_size - inner) / 2
    cy = canvas_size // 2
    r = int(inner * 0.225)
    cx1 = int(offset + inner * 0.395)
    cx2 = int(offset + inner * 0.605)
    draw.ellipse([cx1 - r, cy - r, cx1 + r, cy + r], fill=INDIGO_SOFT)

    lime_layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw2 = ImageDraw.Draw(lime_layer)
    draw2.ellipse([cx2 - r, cy - r, cx2 + r, cy + r], fill=(*LIME_SOFT, int(255 * 0.9)))
    return Image.alpha_composite(base, lime_layer)


def main():
    base = _draw_mark(SIZE).resize((1024, 1024), Image.LANCZOS)

    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ROOT / "assets" / "icon.ico", format="ICO", sizes=icon_sizes)
    base.resize((256, 256), Image.LANCZOS).save(ROOT / "app" / "static" / "favicon.png")

    # PWA icon set (manifest.json). "any" icons fill the whole canvas like
    # the desktop favicon; the "maskable" one keeps the mark within Android's
    # ~80% safe zone so a circular/squircle mask doesn't clip it.
    icons_dir = ROOT / "app" / "static" / "icons"
    icons_dir.mkdir(exist_ok=True)
    for size in (192, 512):
        base.resize((size, size), Image.LANCZOS).save(icons_dir / f"icon-{size}.png")
    maskable = _draw_mark(SIZE, maskable=True).resize((1024, 1024), Image.LANCZOS)
    for size in (192, 512):
        maskable.resize((size, size), Image.LANCZOS).save(icons_dir / f"icon-{size}-maskable.png")

    print("wrote assets/icon.ico, app/static/favicon.png, and app/static/icons/*")


if __name__ == "__main__":
    main()

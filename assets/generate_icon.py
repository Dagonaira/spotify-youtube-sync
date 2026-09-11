"""Regenerates the app icon (assets/icon.ico, app/static/favicon.png) from
the brand mark used in the app header - an indigo rounded square with two
overlapping circles. Re-run this if the brand colors ever change.

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


def main():
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    margin = int(SIZE * 0.05)
    radius = int(SIZE * 0.24)
    draw.rounded_rectangle([margin, margin, SIZE - margin, SIZE - margin], radius=radius, fill=INDIGO)

    cy = SIZE // 2
    r = int(SIZE * 0.225)
    cx1 = int(SIZE * 0.395)
    cx2 = int(SIZE * 0.605)
    draw.ellipse([cx1 - r, cy - r, cx1 + r, cy + r], fill=INDIGO_SOFT)

    lime_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw2 = ImageDraw.Draw(lime_layer)
    draw2.ellipse([cx2 - r, cy - r, cx2 + r, cy + r], fill=(*LIME_SOFT, int(255 * 0.9)))
    base = Image.alpha_composite(base, lime_layer)

    base = base.resize((1024, 1024), Image.LANCZOS)

    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ROOT / "assets" / "icon.ico", format="ICO", sizes=icon_sizes)
    base.resize((256, 256), Image.LANCZOS).save(ROOT / "app" / "static" / "favicon.png")
    print("wrote assets/icon.ico and app/static/favicon.png")


if __name__ == "__main__":
    main()

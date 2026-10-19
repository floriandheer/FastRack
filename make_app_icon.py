#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate assets/Fastrack_AppIcon.png - the macOS Dock / app icon.

The brand logo (Logo_FlorianDheer_LogoWhite.png) is white on transparent, so
it vanishes on a light Dock. This centres it on a dark rounded square that
matches the app's theme, using the same 824px-in-1024 grid Apple's icon
template uses so it sits at the same visual size as other Dock icons.

The generated PNG is committed: fastrack_hub.py loads it at runtime (Dock
icon) and make_shortcut.py converts it to the .icns inside Fastrack.app.
Re-run this only if the logo or theme colour changes.
"""

import os

from PIL import Image, ImageDraw

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(SCRIPT_DIR, "assets", "Logo_FlorianDheer_LogoWhite.png")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "assets", "Fastrack_AppIcon.png")

CANVAS = 1024
TILE = 824                      # Apple icon grid: 824px tile on a 1024 canvas
CORNER_RADIUS = 185
TILE_COLOR = (13, 17, 23, 255)  # shared_colors.BG_DARKEST
LOGO_WIDTH = 480                # logo width inside the tile


def build_icon() -> Image.Image:
    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # Supersample the tile mask so the rounded corners are anti-aliased.
    scale = 4
    mask = Image.new("L", (TILE * scale, TILE * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, TILE * scale - 1, TILE * scale - 1),
        radius=CORNER_RADIUS * scale,
        fill=255,
    )
    mask = mask.resize((TILE, TILE), Image.LANCZOS)
    offset = (CANVAS - TILE) // 2
    icon.paste(Image.new("RGBA", (TILE, TILE), TILE_COLOR), (offset, offset), mask)

    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_height = round(logo.height * LOGO_WIDTH / logo.width)
    logo = logo.resize((LOGO_WIDTH, logo_height), Image.LANCZOS)
    icon.alpha_composite(
        logo, ((CANVAS - LOGO_WIDTH) // 2, (CANVAS - logo_height) // 2)
    )
    return icon


if __name__ == "__main__":
    build_icon().save(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")

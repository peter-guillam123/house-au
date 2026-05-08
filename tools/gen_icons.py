# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=10"]
# ///
"""Render House AU favicon.ico, apple-touch-icon.png, og.png from
PIL primitives so we don't need cairo/imagemagick on the system."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path("/Users/cjmoran/Desktop/Claude projects/HouseAU")

# --- palette ---
PAPER  = (246, 243, 236, 255)   # #f6f3ec
INK    = (20, 17, 13, 255)
GREEN  = (0, 132, 61, 255)      # Pantone 348C
GOLD   = (255, 205, 0, 255)     # Pantone 116C
INK_BLUE = (1, 33, 105, 255)
RED    = (200, 16, 46, 255)
WHITE  = (255, 255, 255, 255)


def draw_columns(draw, size, scale, with_dot=True):
    """The columns/portcullis glyph, scaled to size×size canvas.
    Coords below are in the original 64-unit space."""
    s = size / 64.0
    def r(x0, y0, x1, y1, fill):
        draw.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], fill=fill)
    # left column
    r(14, 14, 22, 50, PAPER)
    # right column
    r(42, 14, 50, 50, PAPER)
    # lintel
    r(22, 29, 42, 35, PAPER)
    # cap bars
    r(11, 14, 25, 17, PAPER)
    r(39, 14, 53, 17, PAPER)
    # base bars
    r(11, 47, 25, 50, PAPER)
    r(39, 47, 53, 50, PAPER)
    if with_dot:
        # gold dot lower-right
        cx, cy, rad = 52 * s, 52 * s, 5 * s
        draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=GOLD)


def make_favicon_layer(px, with_dot=True):
    img = Image.new("RGBA", (px, px), GREEN)
    # round corners
    mask = Image.new("L", (px, px), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, px - 1, px - 1], radius=int(0.18 * px), fill=255)
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    draw = ImageDraw.Draw(out)
    draw_columns(draw, px, 1.0, with_dot=with_dot and px >= 32)
    return out


# --- 1) favicon.ico — multi-size ---
# PIL needs the source image to be at least as large as the biggest
# requested embed; otherwise it silently keeps just the source size.
sizes = [16, 32, 48, 64]
biggest = make_favicon_layer(64)
biggest.save(ROOT / "favicon.ico", format="ICO", sizes=[(s, s) for s in sizes])
print("wrote favicon.ico")

# --- 2) apple-touch-icon.png at 180 ---
apple = make_favicon_layer(180)
apple.save(ROOT / "apple-touch-icon.png")
print("wrote apple-touch-icon.png")

# --- 3) og.png at 1200×630 ---
W, H = 1200, 630
og = Image.new("RGBA", (W, H), PAPER)
draw = ImageDraw.Draw(og)

# A subtle horizontal hairline near the top, like an editorial layout
draw.rectangle([80, 80, W - 80, 82], fill=(217, 209, 189, 255))  # --rule

# A bold green band on the left, one-eighth of the canvas — frames the layout
draw.rectangle([0, 0, 12, H], fill=GREEN)

# Wordmark: column glyph + "House" big, then small AU flag + " · AU"
# Glyph: 80×80 in green-tinted paper
glyph_size = 96
gx, gy = 96, 240
glyph = Image.new("RGBA", (glyph_size, glyph_size), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glyph)
# Same column motif, but in green on paper background
def r2(x0, y0, x1, y1):
    s = glyph_size / 64.0
    gdraw.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], fill=GREEN)
r2(14, 14, 22, 50); r2(42, 14, 50, 50); r2(22, 29, 42, 35)
r2(11, 14, 25, 17); r2(39, 14, 53, 17)
r2(11, 47, 25, 50); r2(39, 47, 53, 50)
og.paste(glyph, (gx, gy), glyph)

# Try to load Source Serif / system serif. Fall back gracefully.
def load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()

SERIF_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Iowan Old Style.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
]
SANS_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]
title_font = load_font(SERIF_CANDIDATES, 132)
sub_font   = load_font(SANS_CANDIDATES, 30)
caps_font  = load_font(SANS_CANDIDATES, 22)

# "House" wordmark
title_x = gx + glyph_size + 36
title_y = 220
draw.text((title_x, title_y), "House", font=title_font, fill=INK)

# Small AU flag inline next to wordmark
def draw_au_flag(img, x, y, w):
    h = w // 2
    fl = Image.new("RGBA", (w, h), INK_BLUE)
    fd = ImageDraw.Draw(fl)
    # Union Jack canton (upper-left half-width × half-height)
    cw, ch = w // 2, h // 2
    # white diagonals
    fd.line([(0, 0), (cw, ch)], fill=WHITE, width=max(2, w // 30))
    fd.line([(cw, 0), (0, ch)], fill=WHITE, width=max(2, w // 30))
    # red diagonals
    fd.line([(0, 0), (cw, ch)], fill=RED, width=max(1, w // 60))
    fd.line([(cw, 0), (0, ch)], fill=RED, width=max(1, w // 60))
    # white cross
    fd.rectangle([cw // 2 - w // 28, 0, cw // 2 + w // 28, ch], fill=WHITE)
    fd.rectangle([0, ch // 2 - w // 28, cw, ch // 2 + w // 28], fill=WHITE)
    # red cross
    fd.rectangle([cw // 2 - w // 50, 0, cw // 2 + w // 50, ch], fill=RED)
    fd.rectangle([0, ch // 2 - w // 50, cw, ch // 2 + w // 50], fill=RED)
    # Federation star (lower-left)
    fd.ellipse([cw // 2 - w // 28, ch + w // 14, cw // 2 + w // 28, ch + w // 14 + w // 14], fill=WHITE)
    # Southern Cross (right half)
    for (sx, sy, r) in [(0.65, 0.20, 0.014), (0.82, 0.35, 0.018),
                        (0.78, 0.62, 0.014), (0.90, 0.75, 0.014),
                        (0.74, 0.85, 0.010)]:
        cx, cy = sx * w, sy * h
        rad = r * w
        fd.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=WHITE)
    img.paste(fl, (x, y))

flag_w = 96
flag_x = title_x + draw.textlength("House", font=title_font) + 28
flag_y = title_y + 70
draw_au_flag(og, int(flag_x), int(flag_y), flag_w)

# "Australia" caption beneath flag
draw.text((title_x, title_y + 150), "Federal parliament search · House AU",
          font=sub_font, fill=(106, 98, 83, 255))  # ink-muted

# Bottom-right caps stamp
stamp = "INTERNAL NEWSROOM TOOL"
stamp_x = W - 80 - draw.textlength(stamp, font=caps_font)
draw.text((stamp_x, H - 80 - 22), stamp, font=caps_font, fill=GREEN)

# Soft gold underline accent for the wordmark area (Pantone 116C echo)
gold_y = title_y + 120
draw.rectangle([title_x, gold_y, title_x + 96, gold_y + 6], fill=GOLD)

og.convert("RGB").save(ROOT / "og.png", optimize=True)
print("wrote og.png")

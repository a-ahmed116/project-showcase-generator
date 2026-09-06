#!/usr/bin/env python3
"""
Portfolio carousel builder
==========================

Turns project screenshots into a LinkedIn-ready PDF carousel: each screenshot
is placed in a realistic phone frame, auto-sized and centred, under a title and
subtitle. A final slide can show the tech stack using official brand icons.

Usage
-----
    python build_carousel.py config.json
    python build_carousel.py config.json --out my-carousel.pdf

Config format: see example_config.json. Minimal example:

{
  "output": "carousel.pdf",
  "rtl": true,
  "slides": [
    {
      "type": "screens",
      "title": "Home",
      "subtitle": "What the user sees first.",
      "screenshots": ["shots/home.png"]
    },
    {
      "type": "stack",
      "title": "Built with",
      "items": ["flutter", "supabase", "firebase"]
    }
  ]
}

Every slide may override "bg", "title_color", "text_color", and "rtl".

Available stack icons: see ICONS_DIR (flutter, react, laravel, nestjs, mysql,
supabase, firebase, wordpress, python, redis). Any other value is treated as a
path to your own image file.
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import PIL.JpegImagePlugin  # noqa: F401  # PDF multipage encode needs JPEG writer registered
import PIL.PdfImagePlugin  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(HERE, "icons")
FONTS_DIR = os.path.join(HERE, "fonts")
ICON_CACHE_DIR = os.path.join(HERE, "icons", ".cache")

# When run as `python build_carousel.py`, expose this module as build_carousel
# so sibling slides.py can import helpers without a second copy.
sys.modules["build_carousel"] = sys.modules[__name__]

# Font used for the synthetic status-bar clock; set by Theme at build time.
_STATUS_FONT = [None]

# Extra vertical space between title and subtitle when text sits above
# centred frames (row / stagger / tilt / overlap / grid / bleed).
TITLE_SUBTITLE_GAP = 48

# ---------------------------------------------------------------- defaults

DEFAULTS = {
    "width": 1080,
    "height": 1350,
    "bg": "#F7F5F0",
    "title_color": "#11692B",
    "text_color": "#14221A",
    "muted_color": "#6E7266",
    "card_color": "#FFFFFF",
    "rtl": True,
    "margin": 80,
    "top_margin": 72,
    "bottom_margin": 40,
    "title_size": 46,
    "subtitle_size": 32,
    "gap": 30,             # gap between phone frames
    "frame_height": 1000,  # STATIC device height; frames only shrink if a row
                           # would otherwise overflow the slide
    "font_bold": None,
    "font_regular": None,
    "font_latin_bold": None,
    "font_latin_regular": None,
    "status_font": None,
    "accent_color": None,      # falls back to title_color
    "layout": "row",           # row|split|overlap|grid|stagger|tilt|bleed
    "rhythm": False,           # auto-vary layouts across screens slides

}

ICON_LABELS = {
    "flutter": "Flutter",
    "react": "React.js",
    "laravel": "Laravel",
    "nestjs": "Nest.js",
    "mysql": "MySQL",
    "supabase": "Supabase",
    "firebase": "Firebase",
    "wordpress": "WordPress",
    "python": "Python",
    "redis": "Redis",
}

ICON_URLS = {
    "flutter": "https://github.com/flutter.png?size=512",
    "react": "https://github.com/reactjs.png?size=512",
    "laravel": "https://raw.githubusercontent.com/laravel/art/master/laravel-logo.png",
    "nestjs": "https://github.com/nestjs.png?size=512",
    "supabase": "https://github.com/supabase.png?size=512",
    "firebase": "https://github.com/firebase.png?size=512",
    "wordpress": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        "9/98/WordPress_blue_logo.svg/960px-WordPress_blue_logo.svg.png"
    ),
    "python": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        "c/c3/Python-logo-notext.svg/1280px-Python-logo-notext.svg.png"
    ),
    "redis": "https://rastalion.dev/wp-content/uploads/2019/09/redis.png",
}


# ------------------------------------------------------------------ utils

def hex2rgb(value):
    if isinstance(value, (tuple, list)):
        return tuple(value)[:3]
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def find_font(explicit, *candidates):
    """Resolve a font path: explicit config value, bundled font, or system."""
    if explicit and os.path.exists(explicit):
        return explicit
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "No usable font found. Put a .ttf in %s or set font_bold/font_regular "
        "in the config." % FONTS_DIR)


def contains_arabic(text):
    for ch in text or "":
        o = ord(ch)
        if (0x0600 <= o <= 0x06FF
                or 0x0750 <= o <= 0x077F
                or 0x08A0 <= o <= 0x08FF
                or 0xFB50 <= o <= 0xFDFF
                or 0xFE70 <= o <= 0xFEFF):
            return True
    return False


# Arabic-primary fonts (e.g. Noto Kufi) often lack Latin punctuation; map the
# common ones so subtitles don't end with tofu boxes.
_AR_PUNCT = str.maketrans({
    ".": "\u06D4",  # Arabic full stop
    ",": "\u060C",  # Arabic comma
    ";": "\u061B",  # Arabic semicolon
    "?": "\u061F",  # Arabic question mark
})


def normalize_text(text):
    """When drawing Arabic, swap Latin punctuation Arabic fonts usually miss."""
    if not text or not contains_arabic(text):
        return text
    return text.translate(_AR_PUNCT)


class Theme:
    """Resolved styling for one slide."""

    def __init__(self, cfg, slide=None):
        slide = slide or {}
        def pick(key):
            return slide.get(key, cfg.get(key, DEFAULTS[key]))

        self.W = int(pick("width"))
        self.H = int(pick("height"))
        self.bg = hex2rgb(pick("bg"))
        self.title_color = hex2rgb(pick("title_color"))
        self.text_color = hex2rgb(pick("text_color"))
        self.muted_color = hex2rgb(pick("muted_color"))
        self.card_color = hex2rgb(pick("card_color"))
        self.rtl = bool(pick("rtl"))
        self.margin = int(pick("margin"))
        self.top_margin = int(pick("top_margin"))
        self.bottom_margin = int(pick("bottom_margin"))
        self.title_size = int(pick("title_size"))
        self.subtitle_size = int(pick("subtitle_size"))
        accent = pick("accent_color")
        self.accent_color = hex2rgb(accent) if accent else hex2rgb(pick("title_color"))
        self.layout = str(pick("layout"))
        self.gap = int(pick("gap"))
        self.frame_height = int(pick("frame_height"))

        self.font_bold = find_font(
            pick("font_bold"),
            os.path.join(FONTS_DIR, "NotoKufiArabic-Bold.ttf"),
            "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
        self.font_regular = find_font(
            pick("font_regular"),
            os.path.join(FONTS_DIR, "NotoKufiArabic-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoKufiArabic-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        self.font_latin_bold = find_font(
            pick("font_latin_bold"),
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            self.font_bold,
        )
        self.font_latin_regular = find_font(
            pick("font_latin_regular"),
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            self.font_regular,
        )
        _STATUS_FONT[0] = find_font(
            pick("status_font"),
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            self.font_latin_bold,
        )

    @property
    def content_width(self):
        return self.W - 2 * self.margin

    def font(self, path, size):
        return ImageFont.truetype(path, size)

    def bold(self, size, text=""):
        path = self.font_bold if (not text or contains_arabic(text)) else self.font_latin_bold
        return self.font(path, size)

    def regular(self, size, text=""):
        path = self.font_regular if (not text or contains_arabic(text)) else self.font_latin_regular
        return self.font(path, size)

    @property
    def direction(self):
        return "rtl" if self.rtl else "ltr"


# ------------------------------------------------------------------- text

def text_width(draw, text, fnt, direction):
    text = normalize_text(text)
    try:
        return draw.textlength(text, font=fnt, direction=direction)
    except Exception:
        return draw.textlength(text, font=fnt)


def wrap(draw, text, fnt, max_w, direction):
    text = normalize_text(text)
    words, lines, cur = text.split(), [], ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if text_width(draw, candidate, fnt, direction) <= max_w or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_centered(draw, text, fnt, cx, y, fill, direction):
    text = normalize_text(text)
    w = text_width(draw, text, fnt, direction)
    try:
        draw.text((cx - w / 2, y), text, font=fnt, fill=fill, direction=direction)
    except Exception:
        draw.text((cx - w / 2, y), text, font=fnt, fill=fill)
    return w


def draw_centered_block(draw, text, fnt, cx, y, max_w, fill, direction, leading=1.4):
    text = normalize_text(text)
    lh = int(fnt.size * leading)
    for line in wrap(draw, text, fnt, max_w, direction):
        draw_centered(draw, line, fnt, cx, y, fill, direction)
        y += lh
    return y


def draw_aligned(draw, text, fnt, left, right, y, fill, direction, align="start"):
    """Draw one line inside a box, honouring text direction.
    align: 'start' (right edge in RTL, left in LTR), 'center', 'end'."""
    text = normalize_text(text)
    w = text_width(draw, text, fnt, direction)
    if align == "center":
        x = (left + right) / 2 - w / 2
    elif (align == "start") == (direction == "rtl"):
        x = right - w
    else:
        x = left
    try:
        draw.text((x, y), text, font=fnt, fill=fill, direction=direction)
    except Exception:
        draw.text((x, y), text, font=fnt, fill=fill)
    return w


def draw_aligned_block(draw, text, fnt, left, right, y, fill, direction,
                       align="start", leading=1.45):
    text = normalize_text(text)
    lh = int(fnt.size * leading)
    for line in wrap(draw, text, fnt, right - left, direction):
        draw_aligned(draw, line, fnt, left, right, y, fill, direction, align)
        y += lh
    return y


def rotate_frame(frame, degrees):
    return frame.rotate(degrees, resample=Image.BICUBIC, expand=True)


def block_height(draw, text, fnt, max_w, direction, leading=1.4):
    if not text:
        return 0
    text = normalize_text(text)
    return len(wrap(draw, text, fnt, max_w, direction)) * int(fnt.size * leading)


# ---------------------------------------------------------------- imaging

def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _safe_composite(canvas, img, x, y):
    """alpha_composite that tolerates images falling partly off the canvas,
    which is what the bleed/offset layouts rely on."""
    x, y = int(x), int(y)
    cw, ch = canvas.size
    if x >= cw or y >= ch or x + img.width <= 0 or y + img.height <= 0:
        return
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(img.width, cw - x), min(img.height, ch - y)
    if ex <= sx or ey <= sy:
        return
    canvas.alpha_composite(img.crop((sx, sy, ex, ey)), (x + sx, y + sy))


def paste_with_shadow(canvas, img, x, y, radius=56, blur=26, offset=14, alpha=65):
    pad = blur * 2
    shadow = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad, pad + img.width, pad + img.height], radius=radius, fill=(0, 0, 0, alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    _safe_composite(canvas, shadow, x - pad, y - pad + offset)
    _safe_composite(canvas, img, x, y)


def _uniform_top_band(a, h, w):
    """Height of a solid-colour band at the very top (a notch/letterbox strip
    some emulators bake into the capture), or 0.

    The band must be a genuinely different colour from the page background --
    otherwise a screenshot that simply starts with blank background above its
    header would look like a notch.
    """
    rows = a[:max(2, int(h * 0.10))]
    first = rows[0]
    flat = np.abs(rows - rows.mean(axis=1, keepdims=True)).sum(axis=2).max(axis=1) < 30
    same = np.abs(rows - first.mean(axis=0)).sum(axis=2).mean(axis=1) < 24
    ok = flat & same
    k = 0
    while k < ok.size and ok[k]:
        k += 1
    if k < 2 or k >= rows.shape[0]:
        return 0

    band_rgb = first.mean(axis=0)
    page_rgb = np.median(a.reshape(-1, 3), axis=0)
    if np.abs(band_rgb - page_rgb).sum() < 90:      # same as the page -> not a notch
        return 0

    below = a[k:k + max(4, int(h * 0.01))]
    if np.abs(below.mean(axis=(0, 1)) - band_rgb).sum() < 40:
        return 0
    return k


def detect_status_bar(img):
    """Return the height, in the image's own pixels, of the OS status bar at
    the top of a screenshot -- or 0 if there isn't one.

    Handles both real-device and emulator captures:
      * a normal glyph bar (clock and/or signal, wifi, battery)
      * emulator bars with no SIM icons, a clock only, or extra height
      * a solid notch/letterbox band baked into the top of the image

    An app's own header is rejected because a status bar has to start flush
    against the very top, stay short, be sparse, and be followed by a clear
    gap before the app's content starts.

    Returns 0 for component-level captures that never had a status bar.
    """
    rgb = img.convert("RGB")
    a = np.asarray(rgb, dtype=np.int16)
    h, w = a.shape[:2]

    scan = a[:max(4, int(h * 0.09))]
    med = np.median(scan.reshape(scan.shape[0], -1, 3), axis=1)
    ink = np.abs(scan - med[:, None, :]).sum(axis=2) > 60
    per_row = ink.sum(axis=1)
    rows = np.flatnonzero(per_row > 2)

    if rows.size:
        first = int(rows[0])
        if first <= h * 0.018:                 # must hug the very top
            last, gap, tol = first, 0, max(4, int(h * 0.004))
            for y in range(first, per_row.size):
                if per_row[y] > 2:
                    last, gap = y, 0
                else:
                    gap += 1
                    if gap > tol:
                        break

            band = last + first + 1
            ok = (h * 0.008 <= band <= h * 0.06
                  and band <= w * 0.14                       # short next to its width
                  and ink[:band].sum() <= band * w * 0.28)   # sparse: glyphs, not a title
            if ok:
                tail = per_row[last + 1:last + 1 + max(4, int(band * 0.35))]
                if not (tail.size and (tail > 2).mean() > 0.34):
                    return int(band)

    solid = _uniform_top_band(a, h, w)
    return int(solid) if h * 0.008 <= solid <= h * 0.09 else 0


def draw_status_bar(canvas, x, y, w, h, fg):
    """Draw a clean synthetic status bar: clock, signal, wifi, battery."""
    d = ImageDraw.Draw(canvas)
    pad = int(w * 0.075)
    cy = y + h / 2

    # --- clock (left)
    size = max(9, int(h * 0.44))
    try:
        f = ImageFont.truetype(_STATUS_FONT[0], size)
    except Exception:
        f = ImageFont.load_default()
    d.text((x + pad, cy), "9:41", font=f, fill=fg, anchor="lm")

    # --- battery (far right)
    bh = max(6, int(h * 0.34))
    bw = int(bh * 2.05)
    bx1 = x + w - pad
    bx0 = bx1 - bw
    by0, by1 = cy - bh / 2, cy + bh / 2
    r = max(2, int(bh * 0.28))
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=r, outline=fg,
                        width=max(1, int(bh * 0.11)))
    inset = max(1, int(bh * 0.22))
    d.rounded_rectangle([bx0 + inset, by0 + inset, bx0 + inset + (bw - 2 * inset) * 0.72,
                         by1 - inset], radius=max(1, r - inset), fill=fg)
    tip_h = bh * 0.34
    d.rounded_rectangle([bx1 + inset * 0.6, cy - tip_h / 2,
                         bx1 + inset * 0.6 + max(1.5, bh * 0.09), cy + tip_h / 2],
                        radius=max(1, int(bh * 0.06)), fill=fg)

    # --- wifi (arcs + dot)
    wx = bx0 - int(w * 0.045)
    wr = max(5, int(h * 0.30))
    base = cy + wr * 0.62
    for i, frac in enumerate((1.0, 0.62)):
        rr = wr * frac
        d.arc([wx - rr, base - rr, wx + rr, base + rr], start=215, end=325,
              fill=fg, width=max(1, int(h * 0.055)))
    dot = max(1.5, h * 0.05)
    d.ellipse([wx - dot, base - dot, wx + dot, base + dot], fill=fg)

    # --- signal bars
    sx = wx - int(w * 0.055)
    barw = max(2, int(h * 0.075))
    step = barw + max(1, int(h * 0.045))
    for i in range(4):
        bh_i = h * (0.15 + 0.075 * i)
        x0 = sx - (3 - i) * step
        d.rounded_rectangle([x0, cy + h * 0.17 - bh_i, x0 + barw, cy + h * 0.17],
                            radius=max(1, barw // 2), fill=fg)


def prepare_screen(path, crop=None, status_bar=None):
    """Load a screenshot and remove its OS status bar.

    status_bar: None  -> auto-detect and strip (default)
                False -> leave the capture untouched
                int   -> strip exactly this many pixels off the top
    """
    src = Image.open(path).convert("RGB")
    if crop:
        top, bottom = crop
        src = src.crop((0, top, src.width, min(bottom, src.height)))
    if status_bar is False:
        return src
    strip = status_bar if isinstance(status_bar, int) else detect_status_bar(src)
    if strip:
        src = src.crop((0, min(strip, src.height - 1), src.width, src.height))
    return src


def deck_aspect(cfg):
    """The device screen aspect (h/w) used for EVERY frame in the deck.

    Taken as the tallest screenshot present, so nothing ever has to be cropped
    to fit; shorter screens are padded with their own background colour, which
    reads as empty screen space. This is what makes all frames come out the
    same size instead of each one following its own screenshot's proportions.
    """
    override = cfg.get("device_aspect")
    if override:
        return float(override)
    tallest = 0.0
    for slide in cfg.get("slides", []):
        if slide.get("type", "screens") != "screens":
            continue
        for spec in normalize_screenshots(slide):
            img = prepare_screen(spec["path"], spec.get("crop"), spec.get("status_bar"))
            tallest = max(tallest, img.height / img.width)
    return tallest or 2.1667          # 9:19.5 fallback


def phone_frame(path, shot_w, aspect, crop=None, status_bar=None):
    """Render one screenshot inside a realistic phone body.

    The screenshot's own OS status bar (if any) is detected and removed, then a
    clean synthetic status bar is drawn at a height proportional to the frame,
    with the dynamic island centred in it.

    The screen area is always `shot_w` x `shot_w * aspect`, regardless of the
    screenshot's own proportions -- shorter captures are padded at the bottom
    with their background colour. Every frame in a deck is therefore identical
    in size.
    """
    src = prepare_screen(path, crop, status_bar)

    shot_h = int(src.height * shot_w / src.width)
    src = src.resize((shot_w, max(1, shot_h)), Image.LANCZOS)

    bar_h = int(shot_w * 0.115)
    target_h = int(shot_w * aspect)
    bg = src.getpixel((3, 3))

    screen_img = Image.new("RGB", (shot_w, target_h + bar_h), bg)
    screen_img.paste(src, (0, bar_h))

    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fg = (20, 20, 22) if lum > 140 else (245, 245, 247)
    draw_status_bar(screen_img, 0, 0, shot_w, bar_h, fg)

    screen = rounded(screen_img, int(shot_w * 0.085))
    shot_h = target_h + bar_h

    bez_side = max(6, int(shot_w * 0.030))
    bez_v = max(10, int(shot_w * 0.055))
    outer_w = shot_w + bez_side * 2
    outer_h = shot_h + bez_v * 2
    btn = max(6, int(shot_w * 0.022))

    frame = Image.new("RGBA", (outer_w + btn * 2, outer_h), (0, 0, 0, 0))
    body = Image.new("RGBA", (outer_w, outer_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    r = int(outer_w * 0.12)
    bd.rounded_rectangle([0, 0, outer_w - 1, outer_h - 1], radius=r, fill=(22, 23, 25, 255))
    bd.rounded_rectangle([3, 3, outer_w - 4, outer_h - 4], radius=max(r - 3, 0),
                         outline=(70, 72, 76, 255), width=2)
    body.alpha_composite(screen, (bez_side, bez_v))

    # dynamic island, vertically centred inside the status-bar band
    isl_w = int(shot_w * 0.27)
    isl_h = max(14, int(bar_h * 0.62))
    isl_y = bez_v + (bar_h - isl_h) // 2
    bd.rounded_rectangle(
        [(outer_w - isl_w) // 2, isl_y, (outer_w + isl_w) // 2, isl_y + isl_h],
        radius=isl_h // 2, fill=(10, 10, 11, 255))

    frame.alpha_composite(body, (btn, 0))
    fd = ImageDraw.Draw(frame)
    bw = max(4, int(shot_w * 0.021))
    unit = outer_h
    for y0, y1 in [(0.14, 0.19), (0.21, 0.28), (0.30, 0.37)]:
        fd.rounded_rectangle([0, unit * y0, bw, unit * y1], radius=bw // 2,
                             fill=(22, 23, 25, 255))
    fd.rounded_rectangle([frame.width - bw, unit * 0.215, frame.width, unit * 0.32],
                         radius=bw // 2, fill=(22, 23, 25, 255))
    return frame


def text_bottom_for(slide, cfg):
    """Where the title/subtitle block ends on a slide (needed to know how much
    vertical room the frames have)."""
    t = Theme(cfg, slide)
    probe = ImageDraw.Draw(Image.new("RGB", (t.W, t.H)))
    y = t.top_margin
    if slide.get("title"):
        y += int(t.title_size * 1.35)
        if slide.get("subtitle"):
            y += TITLE_SUBTITLE_GAP
    if slide.get("subtitle"):
        y += block_height(probe, slide["subtitle"],
                          t.regular(t.subtitle_size, slide["subtitle"]),
                          t.content_width, t.direction, leading=1.38)
    return y + 24


OVERLAP_STEP = 0.62      # each overlapped frame advances this fraction of its width

# Layouts whose geometry is deliberately different in scale, so they are sized
# on their own rather than dragging the deck-wide uniform size down.
FREE_SCALE_LAYOUTS = {"split", "bleed"}

# Composition rhythm. Portfolio carousels read better when consecutive slides
# alternate composition instead of repeating one centred shot: a strong opener,
# then alternating sides, an occasional tilt or bleed for pace, groups for
# related screens. Chosen by screenshot count so the shape always fits.
RHYTHM = {
    1: ["row", "split", "tilt", "split", "bleed", "row"],
    2: ["row", "stagger", "split", "row"],
    3: ["overlap", "row", "stagger"],
}


def assign_rhythm(cfg):
    """Fill in a varied `layout` for screens slides that don't specify one.
    Alternates the side of `split` slides so the eye zig-zags down the deck."""
    if not cfg.get("rhythm"):
        return cfg
    cfg = dict(cfg)
    slides, seen, flip = [], {}, {}
    for slide in cfg.get("slides", []):
        if slide.get("type", "screens") == "screens" and "layout" not in slide:
            n = min(3, max(1, len(normalize_screenshots(slide))))
            seq = RHYTHM[n]
            i = seen.get(n, 0)
            seen[n] = i + 1
            slide = dict(slide)
            slide["layout"] = seq[i % len(seq)]
            if slide["layout"] == "split" and "frame_side" not in slide:
                k = flip.get("split", 0)
                flip["split"] = k + 1
                slide["frame_side"] = "right" if k % 2 == 0 else "left"
        slides.append(slide)
    cfg["slides"] = slides
    return cfg


def split_columns(t):
    """(text column, frame column) widths for the split layout."""
    gap = int(t.content_width * 0.06)
    frame_w = int(t.content_width * 0.50)
    text_w = t.content_width - frame_w - gap
    return text_w, frame_w, gap


def layout_limits(slide, cfg, n):
    """Available width-per-frame and vertical budget for a screens slide,
    given its layout."""
    t = Theme(cfg, slide)
    layout = str(slide.get("layout", t.layout))
    avail_h = t.H - text_bottom_for(slide, cfg) - t.bottom_margin

    if layout == "grid":
        rows = 2 if n > 1 else 1
        cols = -(-n // rows)
        w_each = (t.content_width - (cols - 1) * t.gap) / max(1, cols)
        h_each = (avail_h - (rows - 1) * t.gap) / rows
    elif layout == "overlap":
        span = 1 + (n - 1) * OVERLAP_STEP
        w_each = t.content_width / span
        h_each = avail_h * 0.92
    elif layout == "stagger":
        w_each = (t.content_width - (n - 1) * t.gap) / max(1, n)
        h_each = avail_h - int(t.H * 0.05)      # room for the offset
    elif layout == "tilt":
        w_each = t.content_width / max(1, n)
        h_each = avail_h * 0.88                  # rotation needs headroom
    elif layout == "split":
        _, frame_w, gap = split_columns(t)
        # multiple frames overlap inside the column instead of each shrinking
        span = 1 + (n - 1) * OVERLAP_STEP
        w_each = frame_w / span
        h_each = t.H - t.top_margin - t.bottom_margin
    elif layout == "bleed":
        w_each = t.content_width * 0.72 / max(1, n)
        h_each = (t.H - text_bottom_for(slide, cfg)) * 1.40   # allowed to run off
    else:                                        # row
        w_each = (t.content_width - (n - 1) * t.gap) / max(1, n)
        h_each = avail_h
    return w_each, h_each


def deck_frame_width(cfg, aspect):
    """One screen width shared by EVERY frame in the deck, so all devices come
    out exactly the same size. It's the largest width that still satisfies the
    tightest slide. Slides marked "uniform": false are excluded and sized on
    their own."""
    per_w = aspect + 0.115 + 2 * 0.055        # outer height / screen width
    frame_factor = 1.11                        # outer width  / screen width

    limit = float("inf")
    for slide in cfg.get("slides", []):
        if slide.get("type", "screens") != "screens":
            continue
        if slide.get("uniform") is False:
            continue
        if str(slide.get("layout", Theme(cfg, slide).layout)) in FREE_SCALE_LAYOUTS:
            continue
        specs = normalize_screenshots(slide)
        if not specs:
            continue
        t = Theme(cfg, slide)
        w_each, h_each = layout_limits(slide, cfg, len(specs))
        limit = min(limit,
                    w_each / frame_factor,
                    h_each / per_w,
                    t.frame_height / per_w)
    return int(max(80, limit)) if limit != float("inf") else 0


def slide_frame_width(slide, cfg, aspect):
    """Width for a slide that opted out of the deck-wide uniform size."""
    per_w = aspect + 0.115 + 2 * 0.055
    specs = normalize_screenshots(slide)
    t = Theme(cfg, slide)
    layout = str(slide.get("layout", t.layout))
    w_each, h_each = layout_limits(slide, cfg, len(specs))
    caps = [w_each / 1.11, h_each / per_w]
    if layout != "bleed":          # bleed is meant to outgrow the static height
        caps.append(t.frame_height / per_w)
    return int(max(80, min(caps)))


# ------------------------------------------------------------------ slides

def normalize_screenshots(slide):
    out = []
    for item in slide.get("screenshots", []):
        if isinstance(item, str):
            out.append({"path": item})
        else:
            spec = dict(item)
            if "crop" in spec and spec["crop"]:
                spec["crop"] = tuple(spec["crop"])
            out.append(spec)
    return out


def _download_icon(key, url):
    """Fetch a remote brand icon into the local cache. Return path or None."""
    os.makedirs(ICON_CACHE_DIR, exist_ok=True)
    dest = os.path.join(ICON_CACHE_DIR, key + ".png")
    if os.path.exists(dest):
        try:
            Image.open(dest).verify()
            return dest
        except Exception:
            try:
                os.remove(dest)
            except OSError:
                pass
    req = urllib.request.Request(
        url, headers={"User-Agent": "project-showcase-generator/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.save(dest, format="PNG")
        return dest
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def resolve_icon(name):
    """Accept a known key, or a path to a custom image.

    Known keys try ICON_URLS first (cached under icons/.cache/). On download
    or decode failure, fall back to icons/<key>.png. Custom paths still work.
    """
    key = str(name).strip().lower()
    label = ICON_LABELS.get(key, name)

    url = ICON_URLS.get(key)
    if url:
        cached = _download_icon(key, url)
        if cached:
            return cached, label

    builtin = os.path.join(ICONS_DIR, key + ".png")
    if os.path.exists(builtin):
        return builtin, label
    if os.path.exists(name):
        custom_label = os.path.splitext(os.path.basename(name))[0]
        return name, custom_label
    raise FileNotFoundError(
        "Unknown stack icon %r. Built-ins: %s. Or pass a path to an image."
        % (name, ", ".join(sorted(ICON_LABELS))))


from slides import (
    slide_browser,
    slide_cover,
    slide_features,
    slide_image,
    slide_metrics,
    slide_quote,
    slide_screens,
    slide_stack,
    slide_text,
)


BUILDERS = {
    "screens": slide_screens,
    "stack": slide_stack,
    "text": slide_text,
    "cover": slide_cover,
    "browser": slide_browser,
    "features": slide_features,
    "metrics": slide_metrics,
    "quote": slide_quote,
    "image": slide_image,
}


# -------------------------------------------------------------------- main

def build(cfg, out_path=None):
    slides = cfg.get("slides", [])
    if not slides:
        raise ValueError("Config has no slides.")

    cfg = assign_rhythm(cfg)
    slides = cfg["slides"] if cfg.get("rhythm") else slides
    # One device size for the whole deck: every frame comes out identical.
    aspect = deck_aspect(cfg)
    shot_w = deck_frame_width(cfg, aspect)

    pages = []
    for i, slide in enumerate(slides, 1):
        kind = slide.get("type", "screens")
        if kind not in BUILDERS:
            raise ValueError("Slide %d: unknown type %r (use %s)"
                             % (i, kind, "/".join(BUILDERS)))
        if kind == "screens":
            pages.append(slide_screens(slide, cfg, shot_w, aspect))
        else:
            pages.append(BUILDERS[kind](slide, cfg))

    out = out_path or cfg.get("output", "carousel.pdf")
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150.0)

    if cfg.get("save_pngs"):
        stem = os.path.splitext(out)[0]
        for i, p in enumerate(pages, 1):
            p.save("%s-%02d.png" % (stem, i))
    return out, len(pages)


def inspect_screenshots(paths):
    """Report what the status-bar detector sees, so you can sanity-check a new
    project's screenshots (device vs emulator) before building a whole deck."""
    print("%-46s %8s %8s  %s" % ("screenshot", "size", "strip", "verdict"))
    print("-" * 82)
    for p in paths:
        try:
            img = Image.open(p)
        except Exception as exc:
            print("%-46s %8s %8s  ERROR: %s" % (os.path.basename(p), "-", "-", exc))
            continue
        n = detect_status_bar(img)
        pct = 100.0 * n / img.height if img.height else 0
        if n == 0:
            verdict = "no status bar found (a clean one will be added)"
        elif pct > 7:
            verdict = "SUSPICIOUS - that is a lot; check it"
        else:
            verdict = "will strip %.1f%% off the top" % pct
        print("%-46s %8s %8d  %s"
              % (os.path.basename(p)[:46], "%dx%d" % img.size, n, verdict))
    print("\nIf a row looks wrong, set \"status_bar\": false on that screenshot "
          "to keep it as-is,\nor \"status_bar\": <pixels> to strip an exact amount.")


def main():
    ap = argparse.ArgumentParser(description="Build a portfolio carousel PDF.")
    ap.add_argument("config", nargs="?", help="path to JSON config")
    ap.add_argument("--out", help="override output path")
    ap.add_argument("--pngs", action="store_true", help="also write per-slide PNGs")
    ap.add_argument("--inspect", nargs="+", metavar="IMG",
                    help="report status-bar detection for these screenshots and exit")
    args = ap.parse_args()

    if args.inspect:
        inspect_screenshots(args.inspect)
        return 0
    if not args.config:
        ap.error("a config is required (or use --inspect)")

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if args.pngs:
        cfg["save_pngs"] = True

    base = os.path.dirname(os.path.abspath(args.config))
    cwd = os.getcwd()
    os.chdir(base)
    try:
        out, n = build(cfg, args.out and os.path.join(cwd, args.out))
    finally:
        os.chdir(cwd)
    print("wrote %s (%d slides)" % (out, n))


if __name__ == "__main__":
    sys.exit(main())

"""Animation helpers for the video showcase builder.

Keeps the per-frame work cheap: everything that is invariant across a
slide's frames (the phone bezel/buttons, the rounded-corner mask, the
frozen status bar) is built once by `build_phone_chrome` /
`freeze_status_bar`, and `render_scroll_frame` only pastes the moving
piece -- a crop of the full-height screenshot -- into it.
"""

import numpy as np
from PIL import Image, ImageDraw

from build_carousel import draw_status_bar

# ---------------------------------------------------------------- easing

def linear(p):
    return p


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def ease_in_out_cubic(p):
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def clamp(p, lo=0.0, hi=1.0):
    return max(lo, min(hi, p))


# ----------------------------------------------------------- phone chrome

def _rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def build_phone_chrome(shot_w, aspect):
    """Precompute the parts of a phone frame that never change across a
    slide's frames: the dark bezel body, side buttons, and the rounded
    screen-corner mask. Geometry mirrors build_carousel.phone_frame() so
    video and PDF output read as the same device."""
    bar_h = int(shot_w * 0.115)
    viewport_h = int(shot_w * aspect)
    screen_size = (shot_w, bar_h + viewport_h)

    bez_side = max(6, int(shot_w * 0.030))
    bez_v = max(10, int(shot_w * 0.055))
    outer_w = shot_w + bez_side * 2
    outer_h = screen_size[1] + bez_v * 2
    btn = max(6, int(shot_w * 0.022))

    body = Image.new("RGBA", (outer_w, outer_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    r = int(outer_w * 0.12)
    bd.rounded_rectangle([0, 0, outer_w - 1, outer_h - 1], radius=r, fill=(22, 23, 25, 255))
    bd.rounded_rectangle([3, 3, outer_w - 4, outer_h - 4], radius=max(r - 3, 0),
                         outline=(70, 72, 76, 255), width=2)

    shell = Image.new("RGBA", (outer_w + btn * 2, outer_h), (0, 0, 0, 0))
    shell.alpha_composite(body, (btn, 0))
    fd = ImageDraw.Draw(shell)
    bw = max(4, int(shot_w * 0.021))
    unit = outer_h
    for y0, y1 in [(0.14, 0.19), (0.21, 0.28), (0.30, 0.37)]:
        fd.rounded_rectangle([0, unit * y0, bw, unit * y1], radius=bw // 2,
                             fill=(22, 23, 25, 255))
    fd.rounded_rectangle([shell.width - bw, unit * 0.215, shell.width, unit * 0.32],
                         radius=bw // 2, fill=(22, 23, 25, 255))

    isl_w = int(shot_w * 0.27)
    isl_h = max(14, int(bar_h * 0.62))
    screen_pos = (btn + bez_side, bez_v)
    isl_x0 = screen_pos[0] + (shot_w - isl_w) // 2
    isl_y0 = screen_pos[1] + (bar_h - isl_h) // 2

    return {
        "shell": shell,
        "bar_h": bar_h,
        "viewport_h": viewport_h,
        "screen_size": screen_size,
        "screen_pos": screen_pos,
        "island_box": (isl_x0, isl_y0, isl_x0 + isl_w, isl_y0 + isl_h),
        "mask": _rounded_mask(screen_size, int(shot_w * 0.085)),
        "size": shell.size,
    }


def freeze_status_bar(chrome, bg):
    """Render the synthetic status bar once (icons don't change while the
    content scrolls underneath) as a standalone transparent-background
    layer to be re-composited every frame."""
    w, bar_h = chrome["screen_size"][0], chrome["bar_h"]
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fg = (20, 20, 22) if lum > 140 else (245, 245, 247)
    layer = Image.new("RGBA", (w, bar_h), (0, 0, 0, 0))
    draw_status_bar(layer, 0, 0, w, bar_h, fg)
    return layer


def render_scroll_frame(chrome, content_full, bg, status_layer, offset_y):
    """Composite one animation frame: a window of `content_full` (cropped
    at `offset_y`, or top-aligned/padded if it's shorter than the
    viewport) inside the precomputed chrome."""
    w, h = chrome["screen_size"]
    bar_h, viewport_h = chrome["bar_h"], chrome["viewport_h"]

    canvas = Image.new("RGBA", (w, h), tuple(bg) + (255,))
    if content_full.height <= viewport_h:
        canvas.paste(content_full, (0, bar_h))
    else:
        offset_y = int(round(clamp(offset_y, 0, content_full.height - viewport_h)))
        crop = content_full.crop((0, offset_y, w, offset_y + viewport_h))
        canvas.paste(crop, (0, bar_h))
    canvas.alpha_composite(status_layer, (0, 0))
    canvas.putalpha(chrome["mask"])

    out = chrome["shell"].copy()
    out.alpha_composite(canvas, chrome["screen_pos"])
    d = ImageDraw.Draw(out)
    x0, y0, x1, y1 = chrome["island_box"]
    d.rounded_rectangle(chrome["island_box"], radius=(y1 - y0) // 2, fill=(10, 10, 11, 255))
    return out


def scroll_offset_at(t, hold, scroll_time, scroll_range):
    """Offset in source pixels at time `t` (seconds) within a slide's
    hold/scroll/hold timeline."""
    if scroll_range <= 0:
        return 0
    if t < hold:
        return 0
    if t < hold + scroll_time:
        return ease_in_out_cubic(clamp((t - hold) / scroll_time)) * scroll_range
    return scroll_range


def scroll_timing(avail_seconds, hold_frac=0.18, min_hold=0.25, min_scroll=0.6):
    """Split the time available for the hold/scroll/hold phase of a slide."""
    hold = max(min_hold, avail_seconds * hold_frac)
    scroll_time = max(min_scroll, avail_seconds - 2 * hold)
    return hold, scroll_time


def make_shadow(w, h, radius=56, blur=26, alpha=65):
    """A blurred rounded-rect shadow sprite for a `w`x`h` element. Returns
    (shadow_image, pad, offset) -- composite at (x - pad, y - pad + offset)."""
    offset = 14
    pad = blur * 2
    shadow = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad, pad + w, pad + h], radius=radius, fill=(0, 0, 0, alpha))
    from PIL import ImageFilter
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    return shadow, pad, offset


def rotate_in(image, center, progress, from_deg=-9.0, ease=ease_out_cubic):
    """Rotate `image` from `from_deg` to 0 and fade it in, recentring the
    rotated bounding box on `center` (cx, cy) each step. `progress` is
    0..1 (0 = start of entrance, 1 = fully settled)."""
    p = ease(clamp(progress))
    angle = from_deg * (1 - p)
    alpha = clamp(progress / 0.6)          # fade finishes a bit before settle

    rotated = image if abs(angle) < 0.05 else image.rotate(
        angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1.0:
        arr = np.array(rotated)
        arr[..., 3] = (arr[..., 3].astype(np.float32) * alpha).astype(np.uint8)
        rotated = Image.fromarray(arr, "RGBA")

    cx, cy = center
    pos = (int(cx - rotated.width / 2), int(cy - rotated.height / 2))
    return rotated, pos

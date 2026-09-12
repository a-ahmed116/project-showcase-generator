#!/usr/bin/env python3
"""
Video showcase builder
=======================

Turns the same project config used by build_carousel.py into an animated
MP4: phone frames rotate in, tall (full-page) screenshots auto-scroll
inside the frame like a real screen recording, and slides crossfade into
each other.

Usage
-----
    python build_showcase.py config.json
    python build_showcase.py config.json --out demo.mp4 --fps 30
    python build_showcase.py config.json --filmstrip strip.png   # debug: dump sampled frames

Every "screens" layout (row/split/grid/overlap/stagger/tilt/bleed) gets
the rotate-in + scroll treatment, settling into whatever static
arrangement build_carousel.slide_screens() would draw for that layout
(including overlap/tilt's permanent per-frame tilt). Every other slide
type renders once via its existing PDF slide builder, with a slow Ken
Burns zoom, and holds for its duration -- crossfading in and out either
way. See README.md for the config additions (`video` block, per-slide
`duration`/`entrance`/`scroll`/`scroll_speed`/`scroll_hold`/`zoom`).
"""

import argparse
import json
import os

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw

from build_carousel import (
    BUILDERS,
    FREE_SCALE_LAYOUTS,
    OVERLAP_STEP,
    TITLE_SUBTITLE_GAP,
    Theme,
    assign_rhythm,
    block_height,
    deck_frame_width,
    draw_aligned_block,
    draw_centered,
    draw_centered_block,
    normalize_screenshots,
    prepare_screen,
    slide_frame_width,
    split_columns,
)
from motion import (
    build_phone_chrome,
    clamp,
    ease_out_cubic,
    freeze_status_bar,
    make_shadow,
    natural_scroll_duration,
    plan_scroll_timing,
    render_scroll_frame,
    rotate_in,
    rotated_bbox,
    scroll_offset_at,
)

DEVICE_ASPECT_FALLBACK = 2.1667   # same fallback as build_carousel.deck_aspect

VIDEO_DEFAULTS = {
    "fps": 30,
    "transition": "crossfade",
    "transition_duration": 0.5,
    "entrance_duration": 0.6,
    "output": "showcase.mp4",
}


# ------------------------------------------------------------- geometry

def _shot_width(slide, cfg, aspect, deck_shot_w):
    t = Theme(cfg, slide)
    layout = str(slide.get("layout", t.layout))
    if slide.get("uniform") is False or layout in FREE_SCALE_LAYOUTS:
        return slide_frame_width(slide, cfg, aspect)
    return deck_shot_w


def _text_layer(canvas_size, cfg, slide, title_pos="above"):
    """Transparent RGBA layer with title/subtitle drawn on it (no bg), so
    it can be faded in independently of the background/frames. Returns
    (layer, text_bottom_y) for "above" placement."""
    t = Theme(cfg, slide)
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = canvas_size[0] // 2
    title, subtitle = slide.get("title", ""), slide.get("subtitle", "")

    y = t.top_margin
    if title:
        draw_centered(d, title, t.bold(t.title_size, title), cx, y, t.title_color, t.direction)
        y += int(t.title_size * 1.35)
        if subtitle:
            y += TITLE_SUBTITLE_GAP
    if subtitle:
        y = draw_centered_block(d, subtitle, t.regular(t.subtitle_size, subtitle), cx, y,
                                t.content_width, t.text_color, t.direction, leading=1.38)
    return layer, y + 24


def _split_text_layer(canvas_size, cfg, slide, text_l, text_r, fh):
    t = Theme(cfg, slide)
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    title, subtitle = slide.get("title", ""), slide.get("subtitle", "")
    tf, sf = t.bold(t.title_size, title), t.regular(t.subtitle_size, subtitle)
    th = block_height(d, title, tf, text_r - text_l, t.direction, leading=1.25)
    sh = block_height(d, subtitle, sf, text_r - text_l, t.direction, leading=1.45)
    ty = max(t.top_margin, (t.H - (th + (28 if subtitle else 0) + sh)) // 2)
    align = "center" if slide.get("text_align") == "center" else "start"
    if title:
        ty = draw_aligned_block(d, title, tf, text_l, text_r, ty, t.title_color,
                                t.direction, align, leading=1.25)
        ty += 28
    if subtitle:
        draw_aligned_block(d, subtitle, sf, text_l, text_r, ty, t.text_color,
                           t.direction, align, leading=1.45)
    return layer


# --------------------------------------------------------- scene: screens

def _screens_layout(slide, cfg, canvas_size, chrome):
    """(center_x, center_y, resting_angle) for each chrome, plus the static
    text layer -- mirrors slide_screens()'s own per-layout math exactly.
    A frame's rotated bounding box has the same centre as the unrotated
    one (PIL's rotate(expand=True) rotates about the centre), which is
    what lets overlap/tilt's permanently-tilted frames reuse the same
    "place by centre" compositing as every other layout."""
    t = Theme(cfg, slide)
    layout = str(slide.get("layout", t.layout))
    fw, fh = chrome["size"]
    n = len(normalize_screenshots(slide))
    cx = canvas_size[0] // 2

    if layout == "split":
        text_w, frame_w, gap = split_columns(t)
        frame_right = str(slide.get("frame_side", "right")) == "right"
        if frame_right:
            text_l, text_r = t.margin, t.margin + text_w
            fx0 = t.margin + text_w + gap
        else:
            fx0 = t.margin
            text_l, text_r = t.margin + frame_w + gap, t.W - t.margin
        text_layer = _split_text_layer(canvas_size, cfg, slide, text_l, text_r, fh)

        step = int(fw * OVERLAP_STEP)
        total_w = fw + step * (n - 1)
        x = fx0 + max(0, (frame_w - total_w) // 2)
        top = max(t.top_margin, (t.H - fh) // 2)
        positions = [(x + i * step + fw / 2, top + fh / 2, 0.0) for i in range(n)]
        return text_layer, positions

    text_layer, text_bottom = _text_layer(canvas_size, cfg, slide)
    avail_h = t.H - text_bottom - t.bottom_margin

    if layout == "bleed":
        top = text_bottom + int(t.H * 0.015)
        shift = int(float(slide.get("offset_x", 0)) * fw)
        total_w = n * fw + (n - 1) * t.gap
        x = cx - total_w // 2 + shift
        positions = []
        for _ in range(n):
            positions.append((x + fw / 2, top + fh / 2, 0.0))
            x += fw + t.gap
        return text_layer, positions

    if layout == "grid" and n > 1:
        rows = 2
        cols = -(-n // rows)
        block_h = rows * fh + (rows - 1) * t.gap
        top0 = text_bottom + max(0, (avail_h - block_h) // 2)
        positions = []
        for i in range(n):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            row_w = in_row * fw + (in_row - 1) * t.gap
            x = cx - row_w // 2 + c * (fw + t.gap)
            y = top0 + r * (fh + t.gap)
            positions.append((x + fw / 2, y + fh / 2, 0.0))
        return text_layer, positions

    if layout == "overlap" and n > 1:
        tilt = float(slide.get("tilt", 4))
        angles = [-((i - (n - 1) / 2) * tilt) for i in range(n)]
        tall = max(rotated_bbox(fw, fh, a)[1] for a in angles)
        top0 = text_bottom + max(0, (avail_h - tall) // 2)
        step = int(fw * OVERLAP_STEP)
        total_w = fw + step * (n - 1)
        x0 = cx - total_w // 2
        positions = [(x0 + i * step + fw / 2, top0 + tall / 2, angles[i]) for i in range(n)]
        return text_layer, positions

    if layout == "tilt":
        ang = float(slide.get("tilt", 6))
        angles = [-ang if i % 2 == 0 else ang for i in range(n)]
        widths = [rotated_bbox(fw, fh, a)[0] for a in angles]
        tall = max((rotated_bbox(fw, fh, a)[1] for a in angles), default=fh)
        top0 = text_bottom + max(0, (avail_h - tall) // 2)
        total_w = sum(widths) + t.gap * (n - 1)
        x = cx - total_w // 2
        positions = []
        for i in range(n):
            positions.append((x + widths[i] / 2, top0 + tall / 2, angles[i]))
            x += widths[i] + t.gap
        return text_layer, positions

    if layout == "stagger" and n > 1:
        drop = int(slide.get("stagger", t.H * 0.045))
        total_w = n * fw + (n - 1) * t.gap
        top0 = text_bottom + max(0, (avail_h - fh - drop) // 2)
        x = cx - total_w // 2
        positions = []
        for i in range(n):
            y = top0 + (drop if i % 2 else 0)
            positions.append((x + fw / 2, y + fh / 2, 0.0))
            x += fw + t.gap
        return text_layer, positions

    # row (default, and the fallback slide_screens() itself uses when
    # grid/overlap/stagger are requested with only one screenshot)
    total_w = n * fw + (n - 1) * t.gap
    top = text_bottom + max(0, (avail_h - fh) // 2)
    x0 = cx - total_w // 2
    positions = [(x0 + i * (fw + t.gap) + fw / 2, top + fh / 2, 0.0) for i in range(n)]
    return text_layer, positions


ENTRANCE_OFFSETS = {"rotate-in": 9.0, "fade-in": 0.0}


def _prep_contents(specs, chrome, shot_w, allow_scroll):
    contents = []
    for s in specs:
        src = prepare_screen(s["path"], s.get("crop"), s.get("status_bar"))
        h = int(src.height * shot_w / src.width)
        content_full = src.resize((shot_w, max(1, h)), Image.LANCZOS)
        bg = content_full.getpixel((3, 3))
        scroll_range = max(0, content_full.height - chrome["viewport_h"]) if allow_scroll else 0
        contents.append({
            "content": content_full,
            "bg": bg,
            "status": freeze_status_bar(chrome, bg),
            "range": scroll_range,
        })
    return contents


def render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size, fps,
                         entrance_dur, skip_entrance=False):
    specs = normalize_screenshots(slide)
    if not specs:
        return None  # caller falls back to a static render

    entrance_mode = str(slide.get("entrance", "rotate-in"))
    allow_scroll = slide.get("scroll", "auto") not in (False, "none", "off")
    entrance_offset = ENTRANCE_OFFSETS.get(entrance_mode, 9.0)
    scroll_speed = float(slide.get("scroll_speed", 550.0))
    scroll_hold = float(slide.get("scroll_hold", 0.4))

    shot_w = _shot_width(slide, cfg, aspect, deck_shot_w)
    chrome = build_phone_chrome(shot_w, aspect)
    fw, fh = chrome["size"]
    contents = _prep_contents(specs, chrome, shot_w, allow_scroll)
    text_layer, positions = _screens_layout(slide, cfg, canvas_size, chrome)

    max_range = max((c["range"] for c in contents), default=0)
    duration = float(slide.get("duration") or (
        entrance_dur + 2 * scroll_hold + natural_scroll_duration(max_range, scroll_speed)
        if max_range > 0 else entrance_dur + 1.6))
    duration = clamp(duration, entrance_dur + 0.8, 10.0)
    avail = max(0.4, duration - entrance_dur)
    hold, scroll_time = plan_scroll_timing(max_range, avail, scroll_speed, scroll_hold)

    t = Theme(cfg, slide)
    bg_layer = Image.new("RGBA", canvas_size, t.bg + (255,))
    # Rotated bounding boxes (and their shadows) are constant per slide even
    # though the scrolling content inside them isn't, so both are
    # precomputed once here rather than every frame.
    rot_dims = [rotated_bbox(fw, fh, a) if abs(a) > 0.05 else (fw, fh)
               for _, _, a in positions]
    shadows = [make_shadow(round(rw), round(rh)) for rw, rh in rot_dims]

    n_frames = max(1, round(duration * fps))
    frames = []
    for f in range(n_frames):
        tsec = f / fps
        canvas = bg_layer.copy()

        text_alpha = 1.0 if skip_entrance else clamp(tsec / max(entrance_dur, 1e-6))
        if text_alpha > 0:
            layer = text_layer if text_alpha >= 1 else Image.fromarray(
                _scaled_alpha(np.array(text_layer), text_alpha), "RGBA")
            canvas.alpha_composite(layer)

        scroll_t = tsec if skip_entrance else tsec - entrance_dur
        for i, (ccx, ccy, rest_angle) in enumerate(positions):
            content = contents[i]
            if skip_entrance or entrance_mode == "none":
                entrance_p = 1.0
            else:
                stagger = i * 0.06
                entrance_p = clamp((tsec - stagger) / max(entrance_dur - stagger, 1e-6))

            if entrance_p >= 1.0:
                offset_y = scroll_offset_at(scroll_t, hold, scroll_time, content["range"])
                chrome_img = render_scroll_frame(chrome, content["content"], content["bg"],
                                                 content["status"], offset_y)
                rw, rh = rot_dims[i]
                shadow, pad, off = shadows[i]
                canvas.alpha_composite(shadow, (int(ccx - rw / 2 - pad),
                                                int(ccy - rh / 2 - pad + off)))
                if abs(rest_angle) > 0.05:
                    rotated, pos = rotate_in(chrome_img, (ccx, ccy), 1.0,
                                             from_deg=rest_angle, to_deg=rest_angle)
                    canvas.alpha_composite(rotated, pos)
                else:
                    canvas.alpha_composite(chrome_img, (int(ccx - fw / 2), int(ccy - fh / 2)))
            elif entrance_p > 0:
                chrome_img = render_scroll_frame(chrome, content["content"], content["bg"],
                                                 content["status"], 0)
                start_angle = (rest_angle - entrance_offset if rest_angle >= 0
                              else rest_angle + entrance_offset)
                rotated, pos = rotate_in(chrome_img, (ccx, ccy), entrance_p,
                                         from_deg=start_angle, to_deg=rest_angle)
                canvas.alpha_composite(rotated, pos)
        frames.append(canvas.convert("RGB"))
    return frames


def _scaled_alpha(arr, factor):
    arr = arr.copy()
    arr[..., 3] = (arr[..., 3].astype(np.float32) * factor).astype(np.uint8)
    return arr


# ---------------------------------------------------- scene: static fallback

def _ken_burns_frame(image, canvas_size, zoom):
    """Slow zoom-in: render `image` at `zoom`x, crop back to canvas_size
    around the centre. zoom <= 1 returns image unchanged."""
    if zoom <= 1.0001:
        return image
    w, h = canvas_size
    zw, zh = max(w, round(w * zoom)), max(h, round(h * zoom))
    resized = image.resize((zw, zh), Image.LANCZOS)
    x0, y0 = (zw - w) // 2, (zh - h) // 2
    return resized.crop((x0, y0, x0 + w, y0 + h))


def render_static_scene(slide, cfg, canvas_size, fps, entrance_dur, skip_entrance=False):
    kind = slide.get("type", "screens")
    image = BUILDERS[kind](slide, cfg).convert("RGB")
    if image.size != canvas_size:
        image = image.resize(canvas_size, Image.LANCZOS)

    duration = clamp(float(slide.get("duration") or 3.0), 1.0, 10.0)
    n_frames = max(1, round(duration * fps))
    zoom_amount = float(slide.get("zoom", 0.06))
    skip_fade = skip_entrance or str(slide.get("entrance", "fade-in")) == "none"

    t = Theme(cfg, slide)
    bg = Image.new("RGB", canvas_size, t.bg)
    frames = []
    for f in range(n_frames):
        tsec = f / fps
        zoom = 1.0 + zoom_amount * ease_out_cubic(clamp(tsec / duration))
        frame_img = _ken_burns_frame(image, canvas_size, zoom) if zoom_amount > 0 else image
        if skip_fade:
            frames.append(frame_img)
            continue
        alpha = clamp(tsec / max(entrance_dur, 1e-6), 0, 1)
        frames.append(frame_img if alpha >= 1 else Image.blend(bg, frame_img, ease_out_cubic(alpha)))
    return frames


# -------------------------------------------------------------- assembly

def build_video(cfg, out_path=None):
    vcfg = {**VIDEO_DEFAULTS, **cfg.get("video", {})}
    fps = int(vcfg["fps"])
    entrance_dur = float(vcfg["entrance_duration"])
    transition_frames = max(1, round(float(vcfg["transition_duration"]) * fps))
    aspect = float(cfg.get("device_aspect", DEVICE_ASPECT_FALLBACK))

    cfg = assign_rhythm(cfg)          # same "rhythm": true handling as the PDF builder
    slides = cfg.get("slides", [])
    if not slides:
        raise ValueError("Config has no slides.")

    t0 = Theme(cfg)
    canvas_size = (t0.W, t0.H)
    deck_shot_w = deck_frame_width(cfg, aspect)

    per_slide_frames = []
    for i, slide in enumerate(slides):
        # A slide crossfading in from a predecessor doesn't need its own
        # entrance animation too -- that just washes out the dissolve (see
        # README/commit notes). Only the deck's very first slide, or any
        # slide when transitions are off, plays its full entrance.
        skip_entrance = i > 0 and vcfg["transition"] != "cut"
        kind = slide.get("type", "screens")
        frames = None
        if kind == "screens":
            frames = render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size,
                                          fps, entrance_dur, skip_entrance)
        if frames is None:
            frames = render_static_scene(slide, cfg, canvas_size, fps, entrance_dur,
                                         skip_entrance)
        per_slide_frames.append(frames)

    all_frames = []
    for i, frames in enumerate(per_slide_frames):
        if i == 0 or vcfg["transition"] == "cut":
            all_frames.extend(frames)
            continue
        prev_last = all_frames[-1]
        k = min(transition_frames, len(frames))
        for j in range(k):
            alpha = (j + 1) / k
            all_frames.append(Image.blend(prev_last, frames[j], alpha))
        all_frames.extend(frames[k:])

    out = out_path or vcfg["output"]
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # macro_block_size=1 keeps the exact configured resolution (imageio's
    # ffmpeg writer otherwise rounds to a multiple of 16).
    writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=8,
                                pixelformat="yuv420p", macro_block_size=1)
    try:
        for frame in all_frames:
            writer.append_data(np.array(frame))
    finally:
        writer.close()
    return out, len(all_frames)


def dump_filmstrip(cfg, out_path, n=12, slide_index=None):
    """Debug aid: render one representative slide across n sampled frames
    and tile them into a single contact-sheet PNG."""
    vcfg = {**VIDEO_DEFAULTS, **cfg.get("video", {})}
    fps = int(vcfg["fps"])
    entrance_dur = float(vcfg["entrance_duration"])
    aspect = float(cfg.get("device_aspect", DEVICE_ASPECT_FALLBACK))
    cfg = assign_rhythm(cfg)
    t0 = Theme(cfg)
    canvas_size = (t0.W, t0.H)
    deck_shot_w = deck_frame_width(cfg, aspect)

    slides = cfg["slides"]
    if slide_index is not None:
        slide = slides[slide_index]
    else:
        slide = next((s for s in slides if s.get("type", "screens") == "screens"), slides[0])
    frames = render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size, fps,
                                  entrance_dur) or render_static_scene(
        slide, cfg, canvas_size, fps, entrance_dur)

    idx = np.linspace(0, len(frames) - 1, n).astype(int)
    thumbs = [frames[i].resize((canvas_size[0] // 4, canvas_size[1] // 4)) for i in idx]
    cols = 4
    rows = -(-len(thumbs) // cols)
    tw, th = thumbs[0].size
    sheet = Image.new("RGB", (tw * cols, th * rows), (30, 30, 30))
    for i, im in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet.paste(im, (c * tw, r * th))
    sheet.save(out_path)
    return out_path, len(frames)


def main():
    ap = argparse.ArgumentParser(description="Build an animated showcase video.")
    ap.add_argument("config", help="path to JSON config (same format as build_carousel.py)")
    ap.add_argument("--out", help="override output path")
    ap.add_argument("--fps", type=int, help="override frames per second")
    ap.add_argument("--filmstrip", metavar="PNG",
                    help="debug: render sampled frames of one scene into a contact sheet, no video encode")
    ap.add_argument("--filmstrip-slide", type=int, metavar="N",
                    help="debug: which slide index to use with --filmstrip (default: first screens slide)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if args.fps:
        cfg.setdefault("video", {})["fps"] = args.fps

    base = os.path.dirname(os.path.abspath(args.config))
    cwd = os.getcwd()
    os.chdir(base)
    try:
        if args.filmstrip:
            out, n = dump_filmstrip(cfg, os.path.join(cwd, args.filmstrip),
                                    slide_index=args.filmstrip_slide)
            print("wrote %s (sampled from %d frames)" % (out, n))
        else:
            out, n = build_video(cfg, args.out and os.path.join(cwd, args.out))
            print("wrote %s (%d frames)" % (out, n))
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    main()

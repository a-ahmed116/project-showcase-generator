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

Only "screens" slides with a "row" or "split" layout get the full
rotate-in + scroll treatment; every other slide type/layout renders once
via its existing PDF slide builder and holds for its duration, still
crossfading in and out. See README.md for the config additions
(`video` block, per-slide `duration`/`entrance`/`scroll`).
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
    render_scroll_frame,
    rotate_in,
    scroll_offset_at,
    scroll_timing,
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
    """Positions (top-left of each chrome) plus the static text layer, for
    row or split layout -- mirrors slide_screens()'s own math exactly."""
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
        positions = [(x + i * step, top) for i in range(n)]
        return text_layer, positions

    # row (default)
    text_layer, text_bottom = _text_layer(canvas_size, cfg, slide)
    avail_h = t.H - text_bottom - t.bottom_margin
    total_w = n * fw + (n - 1) * t.gap
    top = text_bottom + max(0, (avail_h - fh) // 2)
    x0 = cx - total_w // 2
    positions = [(x0 + i * (fw + t.gap), top) for i in range(n)]
    return text_layer, positions


def _prep_contents(specs, chrome, shot_w):
    contents = []
    for s in specs:
        src = prepare_screen(s["path"], s.get("crop"), s.get("status_bar"))
        h = int(src.height * shot_w / src.width)
        content_full = src.resize((shot_w, max(1, h)), Image.LANCZOS)
        bg = content_full.getpixel((3, 3))
        contents.append({
            "content": content_full,
            "bg": bg,
            "status": freeze_status_bar(chrome, bg),
            "range": max(0, content_full.height - chrome["viewport_h"]),
        })
    return contents


def render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size, fps,
                         entrance_dur, default_duration):
    specs = normalize_screenshots(slide)
    layout = str(slide.get("layout", Theme(cfg, slide).layout))
    if not specs or layout not in ("row", "split"):
        return None  # caller falls back to a static render

    shot_w = _shot_width(slide, cfg, aspect, deck_shot_w)
    chrome = build_phone_chrome(shot_w, aspect)
    fw, fh = chrome["size"]
    contents = _prep_contents(specs, chrome, shot_w)
    text_layer, positions = _screens_layout(slide, cfg, canvas_size, chrome)

    max_range = max((c["range"] for c in contents), default=0)
    duration = float(slide.get("duration") or (
        entrance_dur + 2 * 0.4 + max(0.8, max_range / 550.0) if max_range > 0
        else entrance_dur + 1.6))
    duration = clamp(duration, entrance_dur + 0.8, 10.0)
    avail = max(0.4, duration - entrance_dur)
    hold, scroll_time = scroll_timing(avail)

    t = Theme(cfg, slide)
    bg_layer = Image.new("RGBA", canvas_size, t.bg + (255,))
    shadows = [make_shadow(fw, fh) for _ in positions]

    n_frames = max(1, round(duration * fps))
    frames = []
    for f in range(n_frames):
        tsec = f / fps
        canvas = bg_layer.copy()

        text_alpha = clamp(tsec / max(entrance_dur, 1e-6))
        if text_alpha > 0:
            layer = text_layer if text_alpha >= 1 else Image.fromarray(
                _scaled_alpha(np.array(text_layer), text_alpha), "RGBA")
            canvas.alpha_composite(layer)

        for i, (x, y) in enumerate(positions):
            stagger = i * 0.06
            entrance_p = clamp((tsec - stagger) / max(entrance_dur - stagger, 1e-6))
            content = contents[i]

            if entrance_p >= 1.0:
                shadow, pad, off = shadows[i]
                canvas.alpha_composite(shadow, (x - pad, y - pad + off))
                offset_y = scroll_offset_at(tsec - entrance_dur, hold, scroll_time, content["range"])
                chrome_img = render_scroll_frame(chrome, content["content"], content["bg"],
                                                 content["status"], offset_y)
                canvas.alpha_composite(chrome_img, (x, y))
            elif entrance_p > 0:
                chrome_img = render_scroll_frame(chrome, content["content"], content["bg"],
                                                 content["status"], 0)
                rotated, pos = rotate_in(chrome_img, (x + fw / 2, y + fh / 2), entrance_p)
                canvas.alpha_composite(rotated, pos)
        frames.append(canvas.convert("RGB"))
    return frames


def _scaled_alpha(arr, factor):
    arr = arr.copy()
    arr[..., 3] = (arr[..., 3].astype(np.float32) * factor).astype(np.uint8)
    return arr


# ---------------------------------------------------- scene: static fallback

def render_static_scene(slide, cfg, canvas_size, fps, entrance_dur):
    kind = slide.get("type", "screens")
    image = BUILDERS[kind](slide, cfg).convert("RGB")
    if image.size != canvas_size:
        image = image.resize(canvas_size, Image.LANCZOS)

    t = Theme(cfg, slide)
    bg = Image.new("RGB", canvas_size, t.bg)
    duration = clamp(float(slide.get("duration") or 3.0), 1.0, 10.0)
    n_frames = max(1, round(duration * fps))
    frames = []
    for f in range(n_frames):
        tsec = f / fps
        alpha = clamp(tsec / max(entrance_dur, 1e-6), 0, 1)
        if alpha >= 1:
            frames.append(image)
        else:
            frames.append(Image.blend(bg, image, ease_out_cubic(alpha)))
    return frames


# -------------------------------------------------------------- assembly

def build_video(cfg, out_path=None):
    vcfg = {**VIDEO_DEFAULTS, **cfg.get("video", {})}
    fps = int(vcfg["fps"])
    entrance_dur = float(vcfg["entrance_duration"])
    transition_frames = max(1, round(float(vcfg["transition_duration"]) * fps))
    aspect = float(cfg.get("device_aspect", DEVICE_ASPECT_FALLBACK))

    slides = cfg.get("slides", [])
    if not slides:
        raise ValueError("Config has no slides.")

    t0 = Theme(cfg)
    canvas_size = (t0.W, t0.H)
    deck_shot_w = deck_frame_width(cfg, aspect)

    per_slide_frames = []
    for slide in slides:
        kind = slide.get("type", "screens")
        frames = None
        if kind == "screens":
            frames = render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size,
                                          fps, entrance_dur, 3.0)
        if frames is None:
            frames = render_static_scene(slide, cfg, canvas_size, fps, entrance_dur)
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


def dump_filmstrip(cfg, out_path, n=12):
    """Debug aid: render one representative slide across n sampled frames
    and tile them into a single contact-sheet PNG."""
    vcfg = {**VIDEO_DEFAULTS, **cfg.get("video", {})}
    fps = int(vcfg["fps"])
    entrance_dur = float(vcfg["entrance_duration"])
    aspect = float(cfg.get("device_aspect", DEVICE_ASPECT_FALLBACK))
    t0 = Theme(cfg)
    canvas_size = (t0.W, t0.H)
    deck_shot_w = deck_frame_width(cfg, aspect)

    slide = next((s for s in cfg.get("slides", [])
                 if s.get("type", "screens") == "screens"
                 and str(s.get("layout", "row")) in ("row", "split")), cfg["slides"][0])
    frames = render_screens_scene(slide, cfg, aspect, deck_shot_w, canvas_size, fps,
                                  entrance_dur, 3.0) or render_static_scene(
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
            out, n = dump_filmstrip(cfg, os.path.join(cwd, args.filmstrip))
            print("wrote %s (sampled from %d frames)" % (out, n))
        else:
            out, n = build_video(cfg, args.out and os.path.join(cwd, args.out))
            print("wrote %s (%d frames)" % (out, n))
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    main()

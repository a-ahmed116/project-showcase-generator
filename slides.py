"""Slide builders for the portfolio carousel."""

from PIL import Image, ImageDraw, ImageFont

from build_carousel import (
    FREE_SCALE_LAYOUTS,
    OVERLAP_STEP,
    TITLE_SUBTITLE_GAP,
    Theme,
    _STATUS_FONT,
    block_height,
    deck_aspect,
    deck_frame_width,
    draw_aligned,
    draw_aligned_block,
    draw_centered,
    draw_centered_block,
    normalize_screenshots,
    paste_with_shadow,
    phone_frame,
    resolve_icon,
    rotate_frame,
    rounded,
    slide_frame_width,
    split_columns,
    wrap,
)

def slide_screens(slide, cfg, shot_w=None, aspect=None):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2
    layout = str(slide.get("layout", t.layout))

    specs = normalize_screenshots(slide)
    if not specs:
        return canvas.convert("RGB")

    if aspect is None:
        aspect = deck_aspect(cfg)
    if slide.get("uniform") is False or layout in FREE_SCALE_LAYOUTS:
        shot_w = slide_frame_width(slide, cfg, aspect)
    elif shot_w is None:
        shot_w = deck_frame_width(cfg, aspect)

    frames = [phone_frame(s["path"], shot_w, aspect, s.get("crop"),
                          s.get("status_bar")) for s in specs]
    fw, fh = frames[0].size
    title = slide.get("title", "")
    subtitle = slide.get("subtitle", "")

    # ---------------------------------------------------------------- split
    # Text in one column, devices in the other. The strongest layout for
    # variety: alternating the side down a deck makes it read as designed
    # rather than as a template.
    if layout == "split":
        text_w, frame_w, gap = split_columns(t)
        frame_right = str(slide.get("frame_side", "right")) == "right"
        if frame_right:
            text_l, text_r = t.margin, t.margin + text_w
            fx0 = t.margin + text_w + gap
        else:
            fx0 = t.margin
            text_l, text_r = t.margin + frame_w + gap, t.W - t.margin

        tf, sf = t.bold(t.title_size, title), t.regular(t.subtitle_size, subtitle)
        th = block_height(d, title, tf, text_r - text_l, t.direction, leading=1.25)
        sh = block_height(d, subtitle, sf, text_r - text_l, t.direction, leading=1.45)
        ty = max(t.top_margin, (t.H - (th + (28 if subtitle else 0) + sh)) // 2)
        align = "center" if slide.get("text_align") == "center" else "start"
        if title:
            ty = draw_aligned_block(d, title, tf, text_l, text_r, ty,
                                    t.title_color, t.direction, align, leading=1.25)
            ty += 28
        if subtitle:
            draw_aligned_block(d, subtitle, sf, text_l, text_r, ty,
                               t.text_color, t.direction, align, leading=1.45)

        step = int(fw * OVERLAP_STEP)
        total_w = fw + step * (len(frames) - 1)
        x = fx0 + max(0, (frame_w - total_w) // 2)
        top = max(t.top_margin, (t.H - fh) // 2)
        for i in sorted(range(len(frames)),
                        key=lambda i: -abs(i - (len(frames) - 1) / 2)):
            paste_with_shadow(canvas, frames[i], x + i * step, top)
        return canvas.convert("RGB")

    # ------------------------------------------------- text above the frames
    y = t.top_margin
    if title:
        draw_centered(d, title, t.bold(t.title_size, title), cx, y, t.title_color, t.direction)
        y += int(t.title_size * 1.35)
        if subtitle:
            y += TITLE_SUBTITLE_GAP
    if subtitle:
        y = draw_centered_block(d, subtitle, t.regular(t.subtitle_size, subtitle), cx, y,
                                t.content_width, t.text_color, t.direction, leading=1.38)
    text_bottom = y + 24
    avail_h = t.H - text_bottom - t.bottom_margin

    if layout == "bleed":
        # One oversized device anchored just under the text so it runs off the
        # bottom edge -- a change of pace that reads as intentional.
        top = text_bottom + int(t.H * 0.015)
        shift = int(float(slide.get("offset_x", 0)) * fw)
        total_w = len(frames) * fw + (len(frames) - 1) * t.gap
        x = cx - total_w // 2 + shift
        for f in frames:
            paste_with_shadow(canvas, f, x, top)
            x += fw + t.gap

    elif layout == "grid" and len(frames) > 1:
        rows = 2
        cols = -(-len(frames) // rows)
        block_h = rows * fh + (rows - 1) * t.gap
        top0 = text_bottom + max(0, (avail_h - block_h) // 2)
        for i, f in enumerate(frames):
            r, c = divmod(i, cols)
            in_row = min(cols, len(frames) - r * cols)
            row_w = in_row * fw + (in_row - 1) * t.gap
            x = cx - row_w // 2 + c * (fw + t.gap)
            paste_with_shadow(canvas, f, x, top0 + r * (fh + t.gap))

    elif layout == "overlap" and len(frames) > 1:
        tilt = float(slide.get("tilt", 4))
        step = int(fw * OVERLAP_STEP)
        total_w = fw + step * (len(frames) - 1)
        x0 = cx - total_w // 2
        rendered = [rotate_frame(f, -((i - (len(frames) - 1) / 2) * tilt)) if tilt else f
                    for i, f in enumerate(frames)]
        tall = max(r.height for r in rendered)
        top0 = text_bottom + max(0, (avail_h - tall) // 2)
        order = sorted(range(len(rendered)),
                       key=lambda i: -abs(i - (len(rendered) - 1) / 2))
        for i in order:
            r = rendered[i]
            paste_with_shadow(canvas, r, x0 + i * step - (r.width - fw) // 2,
                              top0 + (tall - r.height) // 2)

    elif layout == "tilt":
        ang = float(slide.get("tilt", 6))
        rendered = [rotate_frame(f, -ang if i % 2 == 0 else ang)
                    for i, f in enumerate(frames)]
        tall = max(r.height for r in rendered)
        total_w = sum(r.width for r in rendered) + t.gap * (len(rendered) - 1)
        x = cx - total_w // 2
        top0 = text_bottom + max(0, (avail_h - tall) // 2)
        for r in rendered:
            paste_with_shadow(canvas, r, x, top0 + (tall - r.height) // 2)
            x += r.width + t.gap

    elif layout == "stagger" and len(frames) > 1:
        drop = int(slide.get("stagger", t.H * 0.045))
        total_w = len(frames) * fw + (len(frames) - 1) * t.gap
        top0 = text_bottom + max(0, (avail_h - fh - drop) // 2)
        x = cx - total_w // 2
        for i, f in enumerate(frames):
            paste_with_shadow(canvas, f, x, top0 + (drop if i % 2 else 0))
            x += fw + t.gap

    else:                                    # row
        total_w = len(frames) * fw + (len(frames) - 1) * t.gap
        top = text_bottom + max(0, (avail_h - fh) // 2)
        x = cx - total_w // 2
        for f in frames:
            paste_with_shadow(canvas, f, x, top)
            x += fw + t.gap

    return canvas.convert("RGB")


def slide_stack(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2

    items = []
    for entry in slide.get("items", []):
        if isinstance(entry, dict):
            path, label = resolve_icon(entry.get("icon", entry.get("name")))
            label = entry.get("label", label)
        else:
            path, label = resolve_icon(entry)
        items.append((label, path))
    if not items:
        return canvas.convert("RGB")

    title = slide.get("title", "")
    tf = t.bold(slide.get("stack_title_size", 46), title)
    label_size = int(slide.get("stack_label_size", 32))

    # lay out in rows of at most `per_row`
    per_row = int(slide.get("per_row", 3 if len(items) <= 6 else 4))
    rows = [items[i:i + per_row] for i in range(0, len(items), per_row)]

    card = int(slide.get("card_size", 240 if per_row <= 3 else 190))
    gap = int(slide.get("card_gap", 64 if per_row <= 3 else 44))

    # Auto-fit: never let a row overflow the content width. Shrink the gap
    # first, then the cards themselves.
    widest = max(len(r) for r in rows)
    if widest > 1:
        gap = max(20, min(gap, (t.content_width - widest * 90) // (widest - 1)))
    max_card = (t.content_width - (widest - 1) * gap) // widest
    card = max(90, min(card, int(max_card)))

    label_gap = 30
    row_h = card + label_gap + int(label_size * 1.2)
    grid_h = len(rows) * row_h + (len(rows) - 1) * 40

    title_h = int(tf.size * 1.6) + 46 if title else 0
    block_top = max(t.top_margin, (t.H - (title_h + grid_h)) // 2)

    y = block_top
    if title:
        draw_centered(d, title, tf, cx, y, t.text_color, t.direction)
        line_y = y + int(tf.size * 2.1)
        d.line([cx - 60, line_y, cx + 60, line_y], fill=t.title_color, width=6)
        y = line_y + 46

    for row in rows:
        total_w = len(row) * card + (len(row) - 1) * gap
        x = cx - total_w // 2
        for label, path in row:
            plate = Image.new("RGBA", (card, card), (0, 0, 0, 0))
            pd = ImageDraw.Draw(plate)
            pd.rounded_rectangle([0, 0, card - 1, card - 1], radius=int(card * 0.17),
                                 fill=t.card_color + (255,))
            pd.rounded_rectangle([1, 1, card - 2, card - 2], radius=int(card * 0.17) - 1,
                                 outline=(232, 230, 224, 255), width=2)
            icon = Image.open(path).convert("RGBA")
            box = int(card * 0.56)
            scale = box / max(icon.size)
            icon = icon.resize((max(1, int(icon.width * scale)),
                                max(1, int(icon.height * scale))), Image.LANCZOS)
            plate.alpha_composite(icon, ((card - icon.width) // 2,
                                         (card - icon.height) // 2))
            paste_with_shadow(canvas, plate, x, y, radius=int(card * 0.17),
                              blur=22, offset=10, alpha=32)
            draw_centered(d, label, t.regular(label_size, label), x + card // 2,
                          y + card + label_gap, t.text_color, t.direction)
            x += card + gap
        y += row_h + 40
    return canvas.convert("RGB")


def slide_text(slide, cfg):
    """Simple centred title + body slide (intro / outro / section break)."""
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2

    title = slide.get("title", "")
    body = slide.get("subtitle", slide.get("body", ""))
    tf = t.bold(slide.get("title_size_large", 64), title)
    bf = t.regular(t.subtitle_size, body)

    th = int(tf.size * 1.35) if title else 0
    bh = block_height(d, body, bf, t.content_width, t.direction, leading=1.5)
    y = max(t.top_margin, (t.H - (th + (40 if title and body else 0) + bh)) // 2)

    if title:
        draw_centered(d, title, tf, cx, y, t.text_color, t.direction)
        y += th + (40 if body else 0)
    if body:
        draw_centered_block(d, body, bf, cx, y, t.content_width,
                            t.muted_color, t.direction, leading=1.5)
    return canvas.convert("RGB")


def browser_frame(path, width, chrome_url=None, dark=False):
    """Put a desktop/web screenshot in a browser chrome window."""
    src = Image.open(path).convert("RGB")
    inner_w = width
    inner_h = int(src.height * inner_w / src.width)
    src = src.resize((inner_w, max(1, inner_h)), Image.LANCZOS)

    bar_h = max(28, int(width * 0.062))
    win = Image.new("RGB", (inner_w, inner_h + bar_h),
                    (38, 40, 44) if dark else (238, 236, 231))
    d = ImageDraw.Draw(win)
    r = max(5, int(bar_h * 0.17))
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cxx = int(bar_h * 0.62) + i * int(bar_h * 0.52)
        d.ellipse([cxx - r, bar_h / 2 - r, cxx + r, bar_h / 2 + r], fill=col)
    pill_l = int(bar_h * 0.62) + 3 * int(bar_h * 0.52) + int(bar_h * 0.5)
    pill_r = inner_w - int(bar_h * 0.5)
    if pill_r > pill_l:
        d.rounded_rectangle([pill_l, bar_h * 0.24, pill_r, bar_h * 0.76],
                            radius=int(bar_h * 0.26),
                            fill=(58, 61, 66) if dark else (250, 249, 246))
        if chrome_url:
            try:
                f = ImageFont.truetype(_STATUS_FONT[0], max(9, int(bar_h * 0.30)))
                d.text((pill_l + bar_h * 0.34, bar_h / 2), chrome_url, font=f,
                       fill=(200, 202, 206) if dark else (120, 122, 126), anchor="lm")
            except Exception:
                pass
    win.paste(src, (0, bar_h))
    return rounded(win, max(8, int(width * 0.018)))


def slide_browser(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2

    y = t.top_margin
    if slide.get("title"):
        draw_centered(d, slide["title"], t.bold(t.title_size, slide["title"]), cx, y,
                      t.title_color, t.direction)
        y += int(t.title_size * 1.35)
    if slide.get("subtitle"):
        y = draw_centered_block(d, slide["subtitle"], t.regular(t.subtitle_size, slide["subtitle"]),
                                cx, y, t.content_width, t.text_color,
                                t.direction, leading=1.38)

    shots = slide.get("screenshots", [])
    if not shots:
        return canvas.convert("RGB")
    if isinstance(shots, str):
        shots = [shots]
    path = shots[0] if isinstance(shots[0], str) else shots[0]["path"]

    text_bottom = y + 28
    avail_h = t.H - text_bottom - t.bottom_margin
    width = t.content_width
    win = browser_frame(path, width, slide.get("url"), slide.get("dark_chrome", False))
    if win.height > avail_h:                       # scale to fit vertically
        k = avail_h / win.height
        win = win.resize((max(1, int(win.width * k)), max(1, int(win.height * k))),
                         Image.LANCZOS)
    top = text_bottom + max(0, (avail_h - win.height) // 2)
    paste_with_shadow(canvas, win, cx - win.width // 2, top,
                      radius=max(8, int(width * 0.018)), blur=24, offset=12, alpha=55)
    return canvas.convert("RGB")


def slide_cover(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    left, right = t.margin, t.W - t.margin
    align = slide.get("align", "start")
    cx = t.W // 2

    eyebrow = slide.get("eyebrow", "")
    title = slide.get("title", "")
    subtitle = slide.get("subtitle", "")
    logo = slide.get("logo")

    tf = t.bold(slide.get("cover_title_size", 74), title)
    sf = t.regular(slide.get("cover_subtitle_size", 36), subtitle)
    ef = t.bold(slide.get("eyebrow_size", 28), eyebrow)

    logo_h = int(slide.get("logo_size", 132)) if logo else 0
    th = block_height(d, title, tf, right - left, t.direction, leading=1.22)
    sh = block_height(d, subtitle, sf, right - left, t.direction, leading=1.45)
    eh = int(ef.size * 1.9) if eyebrow else 0
    total = (logo_h + 46 if logo else 0) + eh + th + (30 + sh if subtitle else 0) + 60
    y = max(t.top_margin, (t.H - total) // 2)

    if logo:
        img = Image.open(logo).convert("RGBA")
        k = logo_h / max(img.size)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))),
                         Image.LANCZOS)
        img = rounded(img, int(img.width * 0.22)) if slide.get("logo_rounded", True) else img
        lx = {"center": cx - img.width // 2,
              "start": (right - img.width) if t.rtl else left,
              "end": left if t.rtl else (right - img.width)}[align]
        canvas.alpha_composite(img, (lx, y))
        y += logo_h + 46

    if eyebrow:
        draw_aligned(d, eyebrow, ef, left, right, y, t.accent_color, t.direction, align)
        y += eh
    if title:
        y = draw_aligned_block(d, title, tf, left, right, y, t.text_color,
                               t.direction, align, leading=1.22)
    y += 26
    bar_l = {"center": cx - 60, "start": (right - 120) if t.rtl else left,
             "end": left if t.rtl else (right - 120)}[align]
    d.rounded_rectangle([bar_l, y, bar_l + 120, y + 7], radius=3, fill=t.accent_color)
    y += 7 + 32
    if subtitle:
        draw_aligned_block(d, subtitle, sf, left, right, y, t.muted_color,
                           t.direction, align, leading=1.45)
    return canvas.convert("RGB")


def slide_features(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    left, right = t.margin, t.W - t.margin
    cx = t.W // 2

    items = slide.get("items", [])
    item_size = int(slide.get("item_size", 36))
    icon_px = int(slide.get("item_icon_size", 62))

    rows = []
    for it in items:
        if isinstance(it, dict):
            rows.append((it.get("text", ""), it.get("icon")))
        else:
            rows.append((str(it), None))

    inset = icon_px + 28 if any(i for _, i in rows) else 34
    body_h = 0
    for text, _ in rows:
        bf = t.regular(item_size, text)
        line_h = int(bf.size * 1.4)
        n = len(wrap(d, text, bf, right - left - inset, t.direction))
        body_h += max(n * line_h, icon_px) + int(slide.get("item_gap", 34))

    th = int(t.title_size * 1.5) if slide.get("title") else 0
    sh = block_height(d, slide.get("subtitle", ""), t.regular(t.subtitle_size, slide.get("subtitle", "")),
                      right - left, t.direction, leading=1.4)
    y = max(t.top_margin, (t.H - (th + sh + 34 + body_h)) // 2)

    if slide.get("title"):
        draw_centered(d, slide["title"], t.bold(t.title_size, slide["title"]), cx, y, t.title_color, t.direction)
        y += th
    if slide.get("subtitle"):
        y = draw_centered_block(d, slide["subtitle"], t.regular(t.subtitle_size, slide["subtitle"]),
                                cx, y, right - left, t.text_color, t.direction)
    y += 34

    for text, icon in rows:
        bf = t.regular(item_size, text)
        line_h = int(bf.size * 1.4)
        lines = wrap(d, text, bf, right - left - inset, t.direction)
        h = max(len(lines) * line_h, icon_px)
        if icon:
            path, _ = resolve_icon(icon)
            im = Image.open(path).convert("RGBA")
            k = icon_px / max(im.size)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                           Image.LANCZOS)
            ix = right - im.width if t.rtl else left
            canvas.alpha_composite(im, (ix, y + (h - im.height) // 2))
        else:
            dot = 9
            dx = right - dot * 2 if t.rtl else left
            d.ellipse([dx, y + line_h // 2 - dot, dx + dot * 2, y + line_h // 2 + dot],
                      fill=t.accent_color)
        tl, tr = (left, right - inset) if t.rtl else (left + inset, right)
        yy = y + max(0, (h - len(lines) * line_h) // 2)
        for ln in lines:
            draw_aligned(d, ln, bf, tl, tr, yy, t.text_color, t.direction, "start")
            yy += line_h
        y += h + int(slide.get("item_gap", 34))
    return canvas.convert("RGB")


def slide_metrics(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2

    items = slide.get("items", [])
    if not items:
        return canvas.convert("RGB")
    per_row = int(slide.get("per_row", 2 if len(items) <= 4 else 3))
    rows = [items[i:i + per_row] for i in range(0, len(items), per_row)]

    value_size = int(slide.get("value_size", 82))
    label_size_m = int(slide.get("label_size", 30))
    tf = t.bold(t.title_size, slide.get("title", ""))

    sample_v = t.bold(value_size, "0")
    sample_l = t.regular(label_size_m, "A")
    cell_h = int(sample_v.size * 1.25) + int(sample_l.size * 1.9)
    grid_h = len(rows) * cell_h + (len(rows) - 1) * 56
    th = int(tf.size * 1.9) if slide.get("title") else 0
    y = max(t.top_margin, (t.H - (th + grid_h)) // 2)

    if slide.get("title"):
        draw_centered(d, slide["title"], t.bold(t.title_size, slide["title"]), cx, y, t.title_color, t.direction)
        y += th

    for row in rows:
        cw = t.content_width // len(row)
        for i, it in enumerate(row):
            value = str(it.get("value", "")) if isinstance(it, dict) else str(it)
            label = it.get("label", "") if isinstance(it, dict) else ""
            ccx = t.margin + cw * i + cw // 2
            vf = t.bold(value_size, value)
            draw_centered(d, value, vf, ccx, y, t.accent_color, t.direction)
            if label:
                lf = t.regular(label_size_m, label)
                draw_centered_block(d, label, lf, ccx, y + int(vf.size * 1.25),
                                    cw - 24, t.muted_color, t.direction, leading=1.3)
        y += cell_h + 56
    return canvas.convert("RGB")


def slide_quote(slide, cfg):
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2
    left, right = t.margin, t.W - t.margin

    text = slide.get("text", slide.get("quote", ""))
    author = slide.get("author", "")
    qf = t.bold(slide.get("quote_size", 50), text)
    af = t.regular(slide.get("author_size", 30), author)

    mark_f = ImageFont.truetype(_STATUS_FONT[0], slide.get("mark_size", 130))
    qh = block_height(d, text, qf, right - left, t.direction, leading=1.42)
    ah = int(af.size * 1.8) if author else 0
    total = 120 + qh + (40 + ah if author else 0)
    y = max(t.top_margin, (t.H - total) // 2)

    mark = "\u201D" if t.rtl else "\u201C"
    mw = d.textlength(mark, font=mark_f)
    d.text((cx - mw / 2, y - 40), mark, font=mark_f, fill=t.accent_color)
    y += 110
    y = draw_centered_block(d, text, qf, cx, y, right - left, t.text_color,
                            t.direction, leading=1.42)
    if author:
        y += 40
        draw_centered(d, author, af, cx, y, t.muted_color, t.direction)
    return canvas.convert("RGB")


def slide_image(slide, cfg):
    """A plain image with no device frame -- logos, diagrams, wide shots."""
    t = Theme(cfg, slide)
    canvas = Image.new("RGBA", (t.W, t.H), t.bg + (255,))
    d = ImageDraw.Draw(canvas)
    cx = t.W // 2

    y = t.top_margin
    if slide.get("title"):
        draw_centered(d, slide["title"], t.bold(t.title_size, slide["title"]), cx, y,
                      t.title_color, t.direction)
        y += int(t.title_size * 1.35)
    if slide.get("subtitle"):
        y = draw_centered_block(d, slide["subtitle"], t.regular(t.subtitle_size, slide["subtitle"]),
                                cx, y, t.content_width, t.text_color,
                                t.direction, leading=1.38)

    path = slide.get("image") or slide.get("path")
    if not path:
        return canvas.convert("RGB")
    text_bottom = y + 28
    avail_h = t.H - text_bottom - t.bottom_margin
    img = Image.open(path).convert("RGBA")
    k = min(t.content_width / img.width, avail_h / img.height)
    img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))),
                     Image.LANCZOS)
    radius = int(slide.get("radius", 0))
    if radius:
        img = rounded(img, radius)
    top = text_bottom + max(0, (avail_h - img.height) // 2)
    if slide.get("shadow", True):
        paste_with_shadow(canvas, img, cx - img.width // 2, top,
                          radius=max(radius, 2), blur=22, offset=10, alpha=45)
    else:
        canvas.alpha_composite(img, (cx - img.width // 2, top))
    return canvas.convert("RGB")



# Project Showcase Generator

Turns app/project screenshots into a polished, LinkedIn-ready PDF carousel, or an animated MP4 showcase video. Each screenshot is placed into a realistic phone frame, auto-sized and centered under a title and subtitle. Supports RTL (Arabic) and LTR layouts, multiple slide types, and a closing "tech stack" slide built from official brand icons.

## Requirements

- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`PIL`)
- [NumPy](https://pypi.org/project/numpy/)
- [imageio](https://pypi.org/project/imageio/) + [imageio-ffmpeg](https://pypi.org/project/imageio-ffmpeg/) (video builder only; bundles its own ffmpeg binary, no system install needed)

```bash
pip install -r requirements.txt
```

## Usage

### PDF carousel

```bash
python build_carousel.py config.json
python build_carousel.py config.json --out my-carousel.pdf
python build_carousel.py config.json --pngs        # also write per-slide PNGs
python build_carousel.py --inspect shots/*.png      # check status-bar detection, no build
```

### Video showcase

```bash
python build_showcase.py config.json
python build_showcase.py config.json --out demo.mp4 --fps 30
python build_showcase.py config.json --filmstrip strip.png   # debug: sampled-frame contact sheet, no encode
```

Uses the same config as the PDF carousel, plus an optional `video` block and a few per-slide keys:

```json
{
  "video": { "fps": 30, "transition": "crossfade", "transition_duration": 0.5 },
  "slides": [
    { "type": "screens", "screenshots": ["shots/detail.png"], "duration": 4.0 }
  ]
}
```

Every `screens` layout (`row`/`split`/`grid`/`overlap`/`stagger`/`tilt`/`bleed`) gets the full treatment: each phone frame animates in and settles into the same static arrangement `build_carousel.py` would draw for that layout -- including `overlap`/`tilt`'s permanent per-frame tilt (which always settles via rotation, regardless of the chosen entrance style below). If a screenshot is taller than the device viewport it then auto-scrolls top to bottom like a real screen recording (holds if it already fits; set `"scroll": false` to force a static hold instead). Scroll pacing is tunable per slide: `"scroll_speed"` (px/sec for the longest screenshot in the slide, default 550) and `"scroll_hold"` (seconds paused at top/bottom, default 0.4) -- both are soft targets that get scaled to fit an explicit `"duration"` if one is set.

Every other slide type (or a `screens` slide with `"rhythm": true` picking a layout for it) renders once via its existing PDF slide builder, with a slow Ken Burns zoom-in (`"zoom"`, default 0.06 = 6% over the slide; set to `0` to disable). Everything crossfades between slides.

**Entrance motion templates**: `"entrance"` picks how a slide's content(s) animate in -- `rotate-in` (default), `flip` (card-flip squash), `slide-in` (rises into place), `zoom-in` (scales up), or `none`. Leave it unset and each slide gets a *randomly* assigned template, freshly shuffled every run (cycling through the shuffled pool so a deck longer than 4 slides still avoids immediate repeats, though the sequence does repeat every `len(pool)` slides) -- set `"video": {"motion_seed": 42}` for a reproducible mix instead, or `"motion_templates": ["rotate-in", "zoom-in"]` to restrict the pool. A slide arriving via crossfade still plays its template, just without the extra alpha fade-in (fading on top of the crossfade's own blend is what would wash the transition out to flat background); only the deck's first slide, or any slide when `"transition": "cut"`, also fades. Output canvas is the deck's `width`/`height` (default 1080x1350, a 4:5 ratio suited to LinkedIn's in-feed video player).

Paths inside the config (screenshots, icon files) are resolved relative to the config file's own directory, not the current working directory.

## Config format

A config is a JSON file with top-level defaults and a list of slides. See [`examples/example_mock_config.json`](examples/example_mock_config.json) for a full real-world example (Arabic/RTL car-financing app).

```json
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
```

Top-level keys (all optional except `slides`): `output`, `rtl`, `bg`, `title_color`, `text_color`, `save_pngs`, `rhythm` (auto-vary layouts across `screens` slides), plus sizing/spacing defaults (`width`, `height`, `margin`, `title_size`, `subtitle_size`, `frame_height`, etc. — see `DEFAULTS` in `build_carousel.py`). Any slide may override `bg`, `title_color`, `text_color`, and `rtl`.

### Slide types

| `type`     | Purpose                                              |
|------------|-------------------------------------------------------|
| `screens`  | One or more phone-framed screenshots with title/subtitle |
| `stack`    | Tech-stack slide using brand icons                    |
| `text`     | Plain text slide                                      |
| `cover`    | Opening/cover slide                                   |
| `browser`  | Screenshot inside a browser-window frame              |
| `features` | Bullet/feature list slide                             |
| `metrics`  | Stat/metric callouts                                  |
| `quote`    | Pull-quote slide                                      |
| `image`    | Freeform image slide                                  |

### Layouts (`screens` slides)

`layout` controls how multiple screenshots on one slide are arranged: `row` (default) | `split` | `overlap` | `grid` | `stagger` | `tilt` | `bleed`.

### Screenshot entries

A screenshot can be a plain path string, or an object for extra control:

```json
{ "path": "mock_shots/review.png", "crop": [1140, 2340] }
```

Use `--inspect` to check how much of each screenshot's status bar will be auto-stripped; override per-screenshot with `"status_bar": false` (keep as-is) or `"status_bar": <pixels>` (strip an exact amount).

### Stack icons

Built-in icon keys (fetched once from their source and cached under `icons/.cache/`): `flutter`, `react`, `laravel`, `nestjs`, `mysql`, `supabase`, `firebase`, `wordpress`, `python`, `redis`. Any other value in `items` is treated as a path to your own image file. An item may also be an object with a custom `label`:

```json
{ "icon": "firebase", "label": "Firebase Messaging" }
```

## Fonts

Arabic text renders with the bundled Noto Kufi Arabic fonts in [`fonts/`](fonts). Override with `font_bold` / `font_regular` / `font_latin_bold` / `font_latin_regular` in the config if needed.

## Project layout

```
build_carousel.py    CLI entry point, config parsing, layout engine, PDF output
build_showcase.py    CLI entry point for the animated MP4 video builder
slides.py            Slide-type rendering helpers (imported by build_carousel.py)
motion.py            Easing, phone-chrome/scroll compositing (imported by build_showcase.py)
fonts/               Bundled Noto Kufi Arabic fonts
icons/               Cached tech-stack brand icons (.cache/)
examples/            Mock configs + their sample screenshots
```

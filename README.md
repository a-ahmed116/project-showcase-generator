# Project Showcase Generator

Turns app/project screenshots into a polished, LinkedIn-ready PDF carousel. Each screenshot is placed into a realistic phone frame, auto-sized and centered under a title and subtitle. Supports RTL (Arabic) and LTR layouts, multiple slide types, and a closing "tech stack" slide built from official brand icons.

## Requirements

- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`PIL`)
- [NumPy](https://pypi.org/project/numpy/)

```bash
pip install -r requirements.txt
```

## Usage

```bash
python build_carousel.py config.json
python build_carousel.py config.json --out my-carousel.pdf
python build_carousel.py config.json --pngs        # also write per-slide PNGs
python build_carousel.py --inspect shots/*.png      # check status-bar detection, no build
```

Paths inside the config (screenshots, icon files) are resolved relative to the config file's own directory, not the current working directory.

## Config format

A config is a JSON file with top-level defaults and a list of slides. See [`example_mock_config.json`](example_mock_config.json) for a full real-world example (Arabic/RTL car-financing app).

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
slides.py            Slide-type rendering helpers (imported by build_carousel.py)
fonts/               Bundled Noto Kufi Arabic fonts
icons/               Cached tech-stack brand icons (.cache/)
mock_shots/          Sample screenshots used by example_mock_config.json
example_mock_config.json   Full example config
```

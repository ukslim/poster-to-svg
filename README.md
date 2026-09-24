# poster-to-svg

A Claude Code skill, and the Python tools behind it, for rebuilding
AI-generated bitmap posters as real SVGs: the artwork cut out of the raster,
the type reset in a real typeface that the browser shapes and kerns.

It was built for the poster-prompt examples on
[john.hartnup.uk](https://john.hartnup.uk/poster-prompts/). `SKILL.md` is the
working guide: read it first.

## What it expects

The tools work on a Jekyll site, not on this repo. The site must have:

- `_data/events.yaml` — the known copy for each event (`fete`, `gig`)
- `_includes/poster_prompts.html` — the prompt template, for the labels it adds
- `assets/poster-examples/{style}-{event}-v2.webp` — the posters

Output goes to the site: `assets/poster-svg/*.svg`, with the solution each was
built from in `assets/poster-svg/solutions/`.

The site is `$POSTER_SITE` if set, otherwise the nearest directory above the
current directory that contains `_data/events.yaml`.

## Setup

```bash
pip install -r requirements.txt
python3 tools/catalogue.py build   # describe all ~6,500 Google Fonts styles; ~5 min, no fonts kept
```

Faces are fetched from the Google Fonts CSS API as small subsets when they
are shortlisted or chosen, and cached in `fonts/cache/`.

Rendering and verification use headless Chrome, at the macOS default path;
set `$CHROME` to point elsewhere. Artwork is compressed with ImageMagick
(`magick`).

## Installing as a skill

Link the repo into a project's skills directory:

```bash
ln -s ../../../poster-to-svg path/to/site/.claude/skills/poster-to-svg
```

## Tests

```bash
python3 tools/test_measure.py && python3 tools/test_svgkit.py && python3 tools/test_bands.py
python3 tools/regress.py        # re-measure every solved poster (run from the site)
python3 tools/roundtrip.py --suite   # 12 synthetic posters with known answers, graded against tests/roundtrip_baseline.json
python3 tools/bench_fonts.py    # identify known faces from degraded specimens
```

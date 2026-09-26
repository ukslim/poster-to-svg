---
name: poster-to-svg
description: Rebuild one of the site's generated bitmap posters (assets/poster-examples/*.webp) as a real SVG -- artwork cut out of the raster, type reset in a real font the browser shapes and kerns. Use to convert, SVG-ise or redraw a poster, to fix a flawed conversion, to record that a poster cannot be converted, or to change the conversion tools themselves.
---

# Rebuilding a generated poster as an SVG

The posters were drawn by an image model. The job: **cut the artwork out of
the raster and reset the type in a real typeface.** Scripts measure; you look
and decide. **Let the font win**: generated lettering drifts, so a rebuild
that differs from the original's widths and spacing is the point, never a
defect. Nothing here is ever judged by pixel agreement with the original.

```bash
S=.claude/skills/poster-to-svg/tools        # run everything from inside the site
```

Budget: about 10 tool calls and 3 images per poster. Far past that, the
measurement is wrong: fix it or record the poster as flawed.

## Converting a poster

1. **Look once, and judge each line.** A line of copy that is lettering rather
   than type -- hand-drawn, painted, brushed, 3D or chrome, multicoloured letter
   by letter, collaged, warped to a shape, or sitting on a photograph or
   painting -- stays as the original's pixels:
   `--art "headliner@X0,Y0,X1,Y1@3D chrome lettering"` (read the box off
   `overlay.py --grid`; keep it clear of the lines around it; end with what
   makes it unresettable, in a few words, for the SVG's metadata). Its words
   still go in the SVG's `<desc>`.
   Everything else is reset. Only when **no** line can be reset, take the
   escape hatch:
   ```bash
   python3 $S/cannot_convert.py assets/poster-svg/{style}-{event}-v2.svg \
     --style "Punk Fanzine" --event gig --reason "What you saw, and what measured it."
   ```
   Done when: every line is either to be reset or named in an `--art` box, or
   the escape hatch is written.

2. **Check the assignment.** `python3 $S/overlay.py {style}-{event}` numbers
   every band the solver found and labels it with its copy line (`#N?` = no
   copy). Fix *bands* with knobs (a line missing, merged, or made of artwork:
   `--min-glyphs`, `--exclude`, `--solid-radius`); fix *which copy is where* by
   saying so: `--assign venue=#7+#8`, `--assign footer0=none`.
   Done when: every copy line the poster prints sits on its own band.

3. **Brief the character** of each face group, from that same look, in Google
   Fonts' vocabulary (`python3 $S/character.py vocab`):
   `--character headliner="Sans 90, Loud 80, Vintage 60, Playful 5"`. Name the
   2-4 things that decide it: a broad class (Sans, Serif, Slab, Script) unless
   the sub-class is plain; a theme where the face has one (Woodtype, Art Deco,
   Pixel, Stencil); the expressive words that separate it from look-alikes.
   Done when: the display face and the text face each have a brief.

4. **Convert.**
   ```bash
   python3 $S/convert.py {style} {event} --character ... --assign ...
   ```
   Knobs persist in the solution, so a re-run repeats them (`--fresh` drops
   them); a knob on the command line replaces only the stored entries for the
   lines it names. Before the first publish, the last run's knobs (from its
   work directory, --resolve-only included) are carried the same way. `NOT BUILT` means the copy is on the wrong lines: back to step 2.
   Done when: it prints `look at: .../look.png`.

5. **Look at `look.png`**: the assignment, the rebuild beside it, and a
   contact sheet per face group (the original line above its top candidates).
   Decide:
   - **faces**: `--face display=#2` (stored by name), any catalogue face
     `--face body="PT Sans Narrow:700"`, or one line on its own
     `--face date="Fira Sans Condensed:600"`
   - **case**: where the lettering fools the test, `--case support=mixed`
   - **artwork**: `mask` (default) for anything with texture; for flat
     geometry describe it, `--artwork "shapes:circle 974 753 442 #E72F1A"`
     (see Artwork below)
   Done when: each face was chosen by eye against its contact sheet.

6. **Review the type, and fix it.** This step is the point of the workflow,
   not a check on it: the tools measure, but only a look catches a face of
   the wrong genre, a ghost letter, or a headline off centre.
   `python3 $S/review.py {style}-{event}` shows the original and the rebuild
   whole, then each face group as a full-width strip, original over rebuild,
   captioned with its face, whether anyone chose it, and the next candidates.
   `python3 $S/audit.py {style}-{event}` lists measured leads (edge lost, size
   or weight off, case, ghosts, damaged artwork). For each face group ask:
   - **Genre.** The same class as the original -- sans, serif, slab, script --
     and the same kind within it: geometric, grotesque or humanist; round or
     square bowls (look at C, O, G); stroke contrast; serif size. A slab where
     the original is sans, or the reverse, is wrong however well it scored.
   - **Signature letters.** Where the lettering's character lives in letters
     no face has -- a flared V, a spurred C, a custom ampersand -- the line is
     lettering: `--art` it rather than lose what makes it.
   - **Hand-made lettering.** Hand-lettered, painted, engraved or decorated
     titles are lettering: `--art`.
   - **Tone.** The face belongs to the poster's period and mood: no playful or
     novelty face on a sober poster, no rounded techno face on a period one.
   - **Layout.** A centred block stays centred; sizes keep the original's
     hierarchy; the rows of a block never touch; no ghost of the original ink.
   Fix with `--face` (alternatives: `sheet.py` on the solution, or any
   catalogue face), `--art`, `--character`, and rebuild. Choose **every**
   group's face with `--face GROUP="Family sub"`, even to confirm the
   ranking's first: `meta.py --reviewed` refuses a poster with a face nobody
   chose. When it reads right:
   `python3 $S/meta.py assets/poster-svg/{style}-{event}-v2.svg --reviewed`.
   After three attempts keep the best and mark it instead:
   ```bash
   python3 $S/flag_flawed.py assets/poster-svg/{style}-{event}-v2.svg --verdict fixable \
     --flaw "What is still wrong, precisely." --outlook "The fix or feature that would cure it."
   ```
   Done when: every face was chosen against this checklist and the poster is
   marked reviewed, or it is flagged.

7. **Report**: the faces and the evidence for them, the artwork treatment,
   and every place you declined to copy the original, with its measurement.

## Rebuilding many posters

After a tool change, `convert.py` re-run over stored solutions rebuilds
without anyone looking: every build clears its `reviewed` date, and
`manifest.py --unreviewed` is the queue. A batch is not finished until each
poster whose render changed has been through step 6 -- looked at against the
original and fixed with knobs -- not merely flagged. Never write an outlook
that says the full workflow would fix it: run the workflow.

## Reference

### Knobs (`convert.py`)

| knob | use |
|---|---|
| `--exclude X0,Y0,X1,Y1` | a drawing whose fine detail reads as small type (engravings, maps, chips). Keeps it artwork; never cut through a line of type |
| `--min-glyphs N` | stray marks becoming lines (default 3) |
| `--solid-radius N` | heavy display type eaten as artwork (raise) / artwork in the bands (lower) |
| `--rule-length 0` | rule stripping eating glyphs |
| `--contrast N` | noisy bands on photographic art (raise) |
| `--wrap-cap-ratio R` | a wrapped headline split across too few bands (raise) |
| `--assign KEY=#N[+#M]`, `KEY=none` | put copy on bands as read off the overlay; lifts the weak-alignment gate. Replaces the stored pin for that line only; other stored pins stay |
| `--character KEY=BRIEF` | the face's character, as a gate on the ranking |
| `--face GROUP\|KEY=#N\|"Family:weight"` | choose a face; a copy key gives that line its own face |
| `--case KEY=upper\|mixed` | case where the test is fooled |
| `--art KEYS@X0,Y0,X1,Y1@WHY` | lettering that cannot be reset stays the original's pixels; the box is artwork (an obstacle, never blanked). WHY, a few words on what makes it unresettable, goes into the SVG's metadata. Pair with `--exclude` for detailed artwork elsewhere |
| `--tilted KEYS@X0,Y0,X1,Y1@RISE` | copy set on a slant (a banner, a rotated panel): name the lines, a box round them, and the slope by eye in degrees rising to the right (`-8` falls); the exact angle is measured near it. Without RISE a bar or rays in the box can win. Warped or perspective type is still a skip |
| `--align KEY=left\|centre\|right` | the edge a line is ranged on, where block detection got it wrong: a centred headline built off centre |
| `--track KEY=fit\|EM` | letter-spacing the measurement missed: `fit` spans the original's width (one value for a wrapped line), or an amount in em |
| `--fill KEY=#RRGGBB[@WORDS]` | a line's colour, where the measured ink is wrong (glitch fringes, a glow, texture); with `@WORDS`, only those words: a headline whose last word is in the accent colour |
| `--shadow KEY=DX,DY,#RGB` | a hard drop shadow behind a line (extruded or offset-printed type), in px |
| `--fit KEY` | set a line no wider than the original's, where it must stay inside a frame and the face has no narrower cut (a narrower cut of the same face is better: try `sheet.py --try "Family 700 wdth75"` first) |
| `--knockout X0,Y0,X1,Y1` | a paper panel knocked out of a shape (reported as `possible_knockout`; right about 1 in 3, so look) |
| `--artwork auto\|mask\|crop:..\|shapes:..\|none` | artwork treatment |
| `--force`, `--fresh`, `--resolve-only` | build despite weak alignment; ignore stored knobs; solve only |

### Artwork

`mask` blanks the type's own ink and repaints it from the artwork around it,
continuing edges between flat colours; it works wherever the ground under the
type is flat (`ground under the type` in the report: 5 or less). Over a
photograph or painting nothing can repaint what was behind the letters: that
is a skip. `shapes:` redraws flat geometry as vector and is often the best
answer: `circle CX CY R #RGB`, `ellipse CX CY RX RY #RGB`, `rect X Y W H #RGB`,
`poly #RGB X1,Y1 X2,Y2 ...`, `;`-separated, back to front; `--artwork auto`
prints the measured shapes to write them from. Aim for an SVG no larger than
the source `.webp`.

### Reading the face ranking

`width` is measured width over the face's natural width (~1.00 fits); `feat`
is distance on stroke weight, contrast, slant and letter proportions; `char`
is mismatch with your brief (free under 0.3); `shape` is chamfer distance
between real letterforms (under ~1.0 the same form). `shape glyphs: NONE`
means the ranking ran on width and features alone: look harder.
`[id-only]` marks a system face (Helvetica, Futura, Didot): it identifies what
the generator imitated and is never shipped.

### Tools

| tool | does |
|---|---|
| `convert.py` | solve, build, check, publish one poster; writes `look.png` |
| `overlay.py` | the band assignment drawn on the poster (`--grid` for coordinates; `--work` for the last convert.py run with all its knobs; `--vs-solution`, `--vs-baseline`) |
| `sheet.py` | contact sheet: the original line beside the shortlist |
| `review.py` | the type review: each face group as a full-width strip, original over rebuild |
| `character.py` | `vocab`; `blocks` (every line cut out); `rank BRIEF` |
| `audit.py` | a finished SVG checked line by line against the original's intent |
| `check_svg.py` | font-substitution check (run by convert) |
| `manifest.py` | status of all 200 (`--todo`, `--flawed`); `--json` carries each SVG's metadata record; `--site-data` writes the site's `_data/poster_svg.json` for its index page (run after converting) |
| `meta.py` | the metadata record every SVG carries (faults, text kept as bitmap and why, unset lines, faces, version): print, `--refresh`, or `--stale` to list SVGs this version did not build |
| `cannot_convert.py`, `flag_flawed.py` | the escape hatch; the flawed mark |
| `measure.py` | band measurement as text, no rendering |
| `catalogue.py` | the Google Fonts catalogue: `build` (once, ~5 min), `show FAMILY` |
| `pin.py` | pin judged-right assignments into the solutions |

## Changing the tools

A heuristic is code only where calling on you would be wasteful; where one
look settles it, add a knob and an image instead. Every change is graded
before it is kept:

```bash
python3 $S/roundtrip.py --suite     # synthetic posters with known answers: the main gate
python3 $S/roundtrip.py --suite --tilt   # the gig seeds with a slanted panel
python3 $S/roundtrip.py --suite --art    # the gig seeds with an unresettable headline
python3 $S/regress.py               # the 61 solved posters: what changed, judged score, pins
python3 $S/test_measure.py && python3 $S/test_bands.py && python3 $S/test_svgkit.py
```

The round trip is the oracle: every score must hold or rise against
`tests/roundtrip_baseline.json` (`--save-baseline` once a change is accepted).
The stored conversions are not ground truth -- some were made with in-progress
or ad-hoc code -- so a real poster that changes is looked at (`overlay.py
--vs-baseline`) and judged, and `tests/judged.json` records which assignments
are right. Why the existing rules are as they are: `docs/NOTES.md`.

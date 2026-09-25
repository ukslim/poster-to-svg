# poster-to-svg: working notes and history

The reasons behind the rules, gathered while the tool was built poster by
poster. SKILL.md is the procedure; read this when a rule seems wrong or a
heuristic misfires and you need to know what it was protecting against.

Some of it describes the tool as it was (the local font corpus, the old
report columns, one case decision per line); the code and SKILL.md are
authoritative where they disagree.

---

# Turning a generated poster into an SVG

The posters in `assets/poster-examples/` were drawn by an image model. The job
is to rebuild one as an SVG: **cut the artwork out of the raster, and reset the
type in a real typeface.** There are 200 of them (100 styles × gig and fête),
so the work is done by a solver; you supervise it.

## Where things live

The tool is its own repo (github.com/ukslim/poster-to-svg), linked into the
blog as `.claude/skills/poster-to-svg`. Everything it reads and writes belongs
to the site, found as `$POSTER_SITE` or the nearest directory above the cwd
with `_data/events.yaml`:

- `assets/poster-examples/*.webp` — the originals
- `assets/poster-svg/*.svg` — the deliverables, committed in the blog
- `assets/poster-svg/solutions/*.json` — how each was built (kept out of the
  published site by `_config.yml`)
- `_data/events.yaml`, `_includes/poster_prompts.html` — the copy, as the
  prompt set it

The font cache (`fonts/`) belongs to the tool and is gitignored there.

## The point

The output is not meant to be a pixel copy. Generated type is subtly wrong —
inconsistent tracking, stretched headlines, letterforms that drift between
sizes. If the SVG matched the bitmap exactly there would be no reason to make
it. **Let the font win** wherever the two disagree.

Pixel diff is a *registration* check — is the artwork in the right place, are
the baselines right — never a score to minimise. Never pin words or glyphs
individually to hit a number. Never distort a face to fill a measured width.

## Do this

```bash
S=.claude/skills/poster-to-svg/tools
python3 $S/convert.py swiss_international gig          # solve, build, verify
python3 $S/manifest.py --todo                          # what is left
```

`convert.py` measures the poster, assigns the known copy from
`_data/events.yaml`, ranks every face in the index and builds the SVG in
`/tmp/p2svg-{style}-{event}/`. Only if the alignment is sound and `check_svg`
passes does it publish:

- `assets/poster-svg/{style}-{event}-v2.svg` — the deliverable
- `assets/poster-svg/solutions/{style}-{event}-v2.json` — so it can be rebuilt without re-deriving

A re-run starts from the knobs stored in that solution (`--exclude`, `--face`,
`--min-glyphs`...); the command line overrides them and `--fresh` ignores them.
A weak alignment is not built at all unless `--force`.

To see the assignment rather than read it: `python3 $S/overlay.py
{style}-{event}` draws every band labelled with its copy key (`--vs-solution`
puts the stored assignment beside the current one). After building,
`python3 $S/audit.py {style}-{event}` reports, per line, where the render
departs from the original's intent -- alignment edge, size, weight, case,
ghosts of the original, damaged artwork. It never scores pixel agreement: the
font is meant to differ.

Read what it prints. It ends with either `no ambiguities; safe to build` or a
`NEEDS A DECISION` list. **Those decisions are your job; the rest is not.**

**`weak alignment` means do not ship**, and `convert.py` refuses to build it. It
has been right every time: where the aligner reports a high mean cost, the
built SVG has copy on the wrong lines, text colliding, or lines missing. Check
the assignment with `overlay.py` and fix it, or leave the poster outstanding. A poster with no SVG is better than a
poster with a wrong one -- `manifest.py` will keep showing it as work to do.

**`ok` is not proof.** The aligner has reported `ok` with a list item set on a
flower petal and the support act on the second line of the headline. Before
building, look at `overlay.py {style}-{event}`: every band is numbered, boxed
and labelled with the copy line it was given.

**Case** is decided per band: capitals where the line's glyphs sit at the
capital line. Where generated lettering defeats that -- letters fused with
hatching, a footer whose x-height letters are drawn oddly -- say it:
`--case support=mixed`, `--case footer0=upper`.

**Where the aligner is wrong, say where the lines are** rather than tuning
knobs until it agrees. Reading the overlay is one look; `--assign` records it:

```bash
python3 $S/convert.py contemporary_flat_illustration fete \
  --assign attraction0=#6 --assign attraction1=#7 --assign footer0=none
```

`key=#N` puts a copy line on band N, `key=#N+#M` on a line wrapped over two,
`key=none` leaves it out (not on the poster, or its band is missing or merged
-- the original pixels then stay in the artwork). A band given to a key is
taken from whatever the aligner gave it. Assignments are stored as points, so
they survive re-measurement, and a hand assignment lifts the weak-alignment
gate. Use knobs (`--min-glyphs 3`, `--exclude`) when the *bands* are wrong --
a line missing, merged or made of artwork; use `--assign` when the bands are
right and the copy is on the wrong ones.

Budget: about ≤10 tool calls and ≤3 images per poster. If one is taking forty,
something is wrong with the measurement — fix that, don't grind.

## Before converting: can it be converted at all?

**Look at the poster once.** The solver can detect structural impossibility but
not that lettering is *hand-drawn*: a psychedelic or woodcut poster with tidy
separable bands passes every structural test and would be silently, wrongly
reset. One image view is cheap insurance.

Use the escape hatch when the lettering is hand-drawn, painted, torn, woodcut,
sprayed or cut-and-pasted; when the type is textured into the artwork so it
cannot be separated; or when there is no font-like text. A poster **whose style
is its lettering** — grunge, punk fanzine, psychedelic, calligraphic — is
normally a no. Converting it produces a clean, wrong poster.

**The ground under the type decides it.** Type is removed by painting the
background back over it, so it can only be removed if the background can be
repainted. `convert.py` reports this first, as `ground under the type`:

- **≤ 5 — flat.** Masking is clean. Typical flat posters measure 0.0–2.5.
- **> 5 — busy. Look, and tell the two cases apart.** Over a *photograph or
  illustration* nothing can reconstruct what is behind the letters, so it is a
  skip. Over a *distressed or grainy flat ground* it is recoverable: the fill
  is taken from the surrounding pixels, so tone and texture carry across.
  `soviet_propaganda` measured 6.5 and `constructivist` 8.1; both were skips,
  the first because its headline also runs across a banner in perspective.

Another tell that the ground is hopeless: the builder reporting that it swept
hundreds of "undetected glyphs" out of the artwork. That means the texture
itself is full of letter-sized specks, and nothing downstream can be trusted.

```bash
python3 $S/cannot_convert.py assets/poster-svg/punk_fanzine-gig-v2.svg \
  --style "Punk Fanzine" --event gig \
  --reason "The headline is a ransom-note paste-up: every letter is a separate
            scrap at its own angle. Measured, 13 rows share no baseline and 12
            are single isolated glyphs."
```

Cite what you saw *and* what the solver measured. A skip is a legitimate
outcome, not a failure — record it and move on.

**Giving up means writing the escape hatch.** Never leave a poster you have
looked at and abandoned as "outstanding": `manifest.py --todo` means *not yet
tried*, and a poster left there gets picked again. Where a tool change could
rescue it, say so in the reason ("Tool limitation: ...") so the skipped list
doubles as a record of what is recoverable.

**The ground number can read flat over a painting.** big_eyes sets its type on
the smooth dark lower part of the picture and measured 0.46, well under 5 -- but
the type is still painted into an illustration, which is a skip. The number
catches texture, not subject; your one look at the poster decides.

## Choosing the face

The candidates come from the whole Google Fonts catalogue (~6,500 styles,
`catalogue.py`), ranked on three kinds of evidence:

- **metrics** -- each line's natural width in the face at the measured cap
  height, and how consistently one face explains every line in the group
- **features** -- stroke weight and contrast, slant, the squareness of O and
  the width of every letter, measured on the poster's own glyphs and on the
  face the same way (`typefeatures.py`)
- **character** -- what the face is *like*, which no measurement sees: a
  playbill wood type and a sans with the same proportions measure alike and
  are nothing alike. You say it, in Google Fonts' own vocabulary, from the one
  look you take at the poster:

```bash
python3 $S/character.py vocab          # the words: classes, themes, expressive
python3 $S/character.py blocks {style} {event}   # every line cut out, labelled
python3 $S/convert.py mid_century_modern_graphic gig \
  --character headliner="Sans 90, Loud 80, Vintage 60, Playful 5" \
  --character date="Sans 90, Business 60, Playful 10"
```

A brief is keyed by a copy line or a face group and applies to that line's
whole group. Name the 2-4 things that decide it. Prefer a broad class (`Sans`,
`Serif`, `Slab`, `Script`) unless the sub-class is plain; a theme (`Woodtype`,
`Art Deco`, `Pixel`, `Stencil`...) when the face has one; and the expressive
words that separate it from its look-alikes (`Playful 5` is what keeps a comic
face out). Character is a gate, not a referee: a roughly right family costs
nothing, and shape decides between near neighbours; a family of the wrong kind
is pushed far down however well it measures.

```
[display]  headliner
  confidence low (margin 0.0)   shape glyphs: RCAOVHDTEF
  brief: Sans 90, Loud 80, Vintage 60, Playful 5
  measured: weight 0.186  contrast 0.945  slant 0.0  square 0.828
    Anton        400  w400  width 1.007 spread 0.000 feat 1.023 char 0.144 shape 1.0075  => 1.525
```

| column | meaning |
|---|---|
| `width` | measured width / the face's natural width at the measured cap. ~1.00 is the face fitting unaided |
| `spread` | how consistently one face explains *every* line in the group. Low is good |
| `feat` | feature distance: weight, contrast, slant, letter proportions. Low is good |
| `char` | mismatch with your brief, 0-1; free under 0.3 |
| `shape` | chamfer distance between real letterforms, per glyph. Under ~1.0 is the same letterform, over ~2.5 a different one |
| `shape glyphs` | which glyphs were compared. `NONE` means the ranking ran on width and features only: say so and look |
| `measured` | the poster's own features for the group, to check a candidate against |

`[id-only]` marks a system font (Helvetica, Futura, Didot...): useful for
*identifying* what the generator imitated, never embedded; the builder skips
to the best open face.

Confidence is honestly low most of the time: faces that fit a line to a few
percent are common. **Look at the contact sheet** -- the original line beside
the top six, set in the same words at the same size -- and pick:

```bash
python3 $S/sheet.py /tmp/p2svg-{style}-{event}/solution.json   # -> faces.png
python3 $S/convert.py {style} {event} --face display=#3
python3 $S/convert.py {style} {event} --face body="PT Sans Narrow:700"   # any catalogue face
python3 $S/convert.py {style} {event} --face date="PT Sans Narrow:700"   # one line, its own face
```

### Adjudicating a low-confidence face

Render the original's glyphs beside the candidates at matched height and look.
Do not trust your impression of the *style* over the measurement: on
`bauhaus_modernist` the headline read as geometric and the chamfer said
neo-grotesque — the chamfer was right. Equally, do not trust a ranking built on
one or two glyphs.

```bash
python3 $S/convert.py bauhaus_modernist gig --face display="Roboto Condensed"
```

`--face group=Name` or `--face group=#2` to take the second candidate.

**Two ranking failures recur; recognise them on sight.**

- `shape glyphs: none usable` with `high` confidence, or a nonsense `stem` in
  `measure.py` (0.01-0.03 on heavy type): the ranking ran on width and stem
  alone, and it picks Bangers or Luckiest Guy (comic faces). A stem under 0.05
  is now ignored rather than trusted -- trusting it chose weight-100 hairlines
  for six bold headlines -- so the comic-face case is the one left. Look further
  down the shortlist for the upright face that fits: League Gothic, Anton,
  Antonio. On contemporary_flat_illustration's gig League Gothic scored
  `shape 0.82` (the same letterform) at position 10.
- `shape` values of ~26 or ~50 for every candidate: shape scoring has failed
  outright, usually on light or coloured type, and the order is noise. It
  offered Pirata One (blackletter) and Monoton for plain sans caps. Override,
  and if nothing on the shortlist fits, the poster is flawed -- say so.

**Check an override in absolute terms before you ship it.** Weight is the
easiest thing to misjudge: a very condensed face reads as heavy because its
counters are narrow, and a heavy wide face reads as correct next to it. On
bauhaus_modernist's fête the measured title was 560px wide at cap 169; the
override I picked by eye, Anton, sets it at 951px — 70% too wide — and the
collision rule then shrank the whole title to fit, so it came out both too
wide and too short. One line settles it:

```python
Face(c['path'], index=c['index']).width('BRINDLEWICK', 169 / c['cap_ratio'])
```

against the band's measured `width`. If nothing in the shortlist lands near
1.0, the corpus has no face at those proportions — say so rather than forcing
the nearest one.

## Artwork

`--artwork` is the one thing the solver cannot decide.

| mode | when |
|---|---|
| `mask` | **the default and usually right.** Blanks the type's own ink and embeds the rest. Works whether artwork sits beside, behind or around the type |
| `auto` | flat geometry — emits `rect`/`ellipse`, and falls back to `mask` unless the shapes it draws match the real artwork (IoU ≥ 0.92) |
| `crop:x0,y0,x1,y1` | artwork confined to a band, and you want only that region |
| `shapes:...` | **describe the artwork yourself — often the best answer.** Inline: `--artwork "shapes:circle 974 753 442 #E72F1A"`, or a path to an SVG fragment |
| `none` | no artwork |

A fully vector poster is the better result where the artwork genuinely is flat
geometry — `de_stijl` is 41KB with no raster at all. But `auto` refuses rather
than guesses: a half-disc has the same bounding box and fill ratio as an
ellipse and would be drawn wrong, so it falls back.

**Describing the artwork is the cheapest useful thing you can do.** When `auto`
gives up it prints the colour, box and fill ratio of every shape it found.
Reading those against one look at the poster is usually enough:

```
   measured shapes -- describe them with --artwork "shapes:..."
     #E72F1A  box 532,311 492x885  fill 0.809  (solid)
```

A box 885 tall whose left edge is at 532 is a circle of radius 442 centred at
(974, 753), running off the right edge — so `circle 974 753 442 #E72F1A`. That
one line replaced masking the type out, inpainting behind it and embedding a
20KB raster, and removed a ghost of the original headline along with it:
`swiss_international` fête went to 27KB with no raster at all.

The vocabulary is `circle CX CY R #RGB`, `ellipse CX CY RX RY #RGB`,
`rect X Y W H #RGB`, `poly #RGB X1,Y1 X2,Y2 ...`, separated by `;`. Shapes are
drawn in the order given, so put the background ones first. Reach for it
whenever the artwork is a handful of flat shapes; `mask` is for artwork with
real texture or detail.

Aim for an SVG no larger than the source `.webp`.

## Telling the solver where the artwork is

`--exclude X0,Y0,X1,Y1` (repeatable) says "do not look for type in this box".
It does **not** say the box is empty: what is inside stays in the artwork, so
it still keeps type from running into it, and `mask` leaves it untouched.

Use it for a drawing whose fine detail behaves like small glyphs -- an
engraving, a map, a grid of colour chips, a crowd of marks. Those fragments
join neighbouring bands, drag a line's left edge across the page and defeat the
span checks, so copy lands on an illustration label instead of on the headline.
No measurement separates that from small type reliably; a glance at the poster
does. On the botanical plate it took the alignment from `weak (0.49)`, with the
beneficiary set on a leaf label, to `ok`.

Two cautions, both learned the hard way:

- **Do not cut through a line of type.** If several bands report the same right
  edge, and it is your box's edge, you are clipping them -- the measured widths
  are then too small and every one of those lines is set too narrow.
- **Keep the box off artwork the type needs to stop at.** Excluding a bar that a
  headline butts against is fine for finding the type, but check the result:
  the shape is still an obstacle, so the headline should still stop.

## Panels knocked out of a shape

Some posters hold their title in a rectangle knocked out of a shape — a Bauhaus
fête sets it in a cream box cut from a red circle. The box has no colour of its
own, so nothing that looks for ink finds it, and **masking cannot recover it**:
the type sits against the box's edge, blanking the type eats the edge, and a
fill taken from the surroundings comes back as a red smear or, once tidied, a
soft organic curve where a hard vertical line should be.

If you see that, name the rectangle and the builder repaints it exactly:

```bash
--knockout 546,236,624,505
```

The solver reports candidates as `possible_knockout`, from the one measurable
tell: the shape behind stops being the shape it is. A circle's outline moves
every row, so a run of 270 rows at a constant x can only be something covering
it. **Treat the report as a hint and look before using it.** Across all 200
posters this test fires on 9 and is right about one time in three — posters are
full of shapes with genuinely straight sides, and the sun on a wave, a doorway,
a colour wheel and a chair all came back as candidates. That is why it is a
knob and not an automatic step.

## When the measurement is wrong

Every poster is allowed to be its own special case. Adjust a knob rather than
editing the tools:

| symptom | knob |
|---|---|
| a line or headline missing entirely | `--rule-length 0` (rule stripping is eating glyphs), or `--solid-radius 0` |
| heavy display type detected as artwork | raise `--solid-radius` |
| artwork surviving into the text bands | lower `--solid-radius` |
| stray marks becoming "lines" | raise `--min-glyphs` |
| noisy bands on photographic or distressed art | raise `--contrast` (default 40) |
| a wrapped headline split across too few bands | raise `--wrap-cap-ratio` (default 1.3); an accented cap like `Fête` measures shorter than its neighbours |
| copy on the wrong lines, or lines missing | the aligner is lost — do not ship; see `weak alignment` above |
| a line of copy missing from the render | compare every line against the original — dropping copy is never acceptable. If it is a fête line that moved position, `copy_orderings` should catch it; if not, the aligner needs the ordering adding there |
| ghost letters left in the artwork | usually automatic; the builder detaches rules, finds letter-shaped leftovers inside the text block and blanks them |

`measure.py` alone is the cheapest way to see what the solver sees — it prints
bands, caps, baselines and the copy assignment as text, no rendering:

```bash
python3 $S/measure.py assets/poster-examples/de_stijl-gig-v2.webp --event gig
```

Check the assignment before anything else. If `headliner` has the wrong number
of bands, or a line is `UNASSIGNED` that shouldn't be, fix that first — every
later number depends on it.

## What the builder guarantees

You do not need to re-derive these; they are enforced in code:

- one logical line = **one string in one `<text>`**, so the browser shapes and
  kerns it. A wrapped headline becomes one `<text>` per visual line
- size from the measured cap height, baseline from the measured baseline
- x from the measured ink edge with the first glyph's sidebearing backed out,
  then a shared origin where the lines agree on one
- **no word-spacing or scaling, and letter-spacing only where the design
  has it.** A line counts as tracked when its letter gaps are wide (>= 0.24 of
  cap) *and* even (CV <= 0.4); generator noise is wide but uneven, ordinary
  setting is narrow. Measured on the posters: tracked 0.26-0.84, plain
  0.04-0.19. The builder then sets whatever this face needs to span the
  line, rounded to 0.05em, and a list shares its members' median. The aligner
  and ranker take the tracking out of the width first, so a tracked heading
  no longer looks like longer copy
- an `<?xml version="1.0" encoding="UTF-8"?>` declaration, and every character
  above ASCII written as a numeric reference (`&#163;` for `£`). An SVG is XML,
  so a reader without a declaration is *required* to assume UTF-8 — but macOS
  QuickLook reads Latin-1 and shows the two bytes of a `£` as `Â£`. Chrome is
  fine either way, so this only appears in Finder previews. Both belt and
  braces are cheap; `check_svg.py` enforces them
- fonts subset to the glyphs used, `kern` retained, embedded base64. Two
  groups that resolved to the same file share one face and the union of their
  text; anything else gets a family name of its own, and the class pins the
  weight. Two different subsets must never share a family name — the browser
  matches both classes to one of them and substitutes a system font for every
  glyph the other needed, which comes out as a word half in one face and half
  in another ("noon" with heavy `n`s and light `o`s). The builder re-reads each
  embedded subset and warns if a character is missing
- **a copy line wrapped over several bands is one entity**, set at one size:
  measuring each band alone turns a pixel of noise into a visibly odd line.
  Bands whose caps already disagree by more than 12% are left alone, so a
  designer who really did step the sizes gets what they drew. If clearing
  artwork forces one band more than 15% below its siblings, the builder keeps
  them separate and says so rather than taking the whole headline down — that
  gap means the face runs wider than the original, so try a narrower one
- a line the generator squeezed to fit its block is set at the size that fits,
  not reproduced as a squash
- **type never runs into artwork.** This outranks setting at the face's natural
  width: the original was composed round its shapes, so a line that collides
  with one is wrong however faithful its letterforms. A line with a shape to
  its right is reduced until it clears, using the shapes the solver measured --
  **unless the original line already ran past that shape**, in which case the
  overlap is the design (a Bauhaus title set across a circle) and holding the
  reset line clear of it shrinks the headline by a quarter for nothing.
  Where you described the artwork with `shapes:`, the type is fitted against
  the real geometry row by row rather than a bounding box — beside the top of
  a circle the true edge can be hundreds of pixels right of its box, and
  blocking on the box shrinks lines the original composed comfortably past the
  curve. This is a further reason to spend a moment describing the artwork
- the page edge is a plain 8px trim. The builder does **not** try to infer the
  design's own right margin: the left margin is wrong because a headline may
  be set much closer to the trim than the body, and `page - max(right)` is
  wrong because type stopping short of the right side usually means artwork is
  there. Both were tried; both shrank headlines by a third on posters that
  were already right
- the copy is tried in a few plausible orders, because posters move a line
  about — a fête commonly sets the beneficiary down among the footer rather
  than under the venue, and without this it is dropped for being out of place
- **columns are read a column at a time.** A row is split at a gutter (a gap
  over 1.5× its height; word spaces measure under 0.8), and where bands sit
  side by side the left column is read to its foot before the right. A band
  that crosses the gutter -- a centred label, a full-width footer -- ends the
  zone. So a beneficiary beside the attractions, or a list set in two halves,
  aligns without help. `test_measure.py` covers the cases
- **a word in an accent colour continues its line.** VILLAGE in blue then FÊTE
  in red, a word space apart, are one copy line over two bands, each set in
  its own colour. A gap under one cap height is a word space, not a gutter
- **list markers stay in the artwork.** A bullet or dash well clear of the
  first letter is left out of the line, so it is neither blanked nor lost
- **light type knocked out of a coloured panel is found and blanked**, judged
  by the share of dark ground around it and by the ground actually under it.
  White type on teal ghosted through before this, because white matched paper
- the ghost sweep never touches an `--exclude` box, so an illustration's own
  labels survive

## Verify

```bash
python3 tools/check_svg.py            # or a single path
```

Resolves every `<text>` the way a browser will — family and weight from the
class and the inherited `text {}` rule, then CSS weight matching — and checks
the face it lands on carries every character. Exits non-zero on a failure.
Cheap, deterministic, and it catches the substitution defect above, which is
invisible in a diff and easy to miss by eye.

`convert.py` then runs `tools/sense_check.py`, which reads the finished SVG
back as text and says what looks odd — two halves of one phrase at different
sizes, one list item smaller than its siblings, a line running off the page.
**Everything it raises is suspicious, not certainly wrong**, so it prints the
measurement that would explain each one away and leaves the judgement to you:
a headline whose bands really do measure different caps was probably drawn
that way, whereas bands that measure the same and are *set* differently mean
the fitting did it, and that is worth opening the poster for. Silence is the
normal outcome and costs nothing to read.

Run `audit.py` on the poster. Every finding is a lead with the numbers
behind it, not a verdict: a size or weight step may be the font winning, a
type-like band left in the artwork may be the artwork's own lettering. A
ghost (the original's ink still under a reset line) or damaged artwork is
almost always real.

Then load it in real Chrome and confirm:

```js
await document.fonts.ready;
({ loaded: document.fonts.check('100px TheFamily'),
   lines: [...document.querySelectorAll('text')].map(t => t.textContent),
   spacing: [...new Set([...document.querySelectorAll('text')].map(t =>
       getComputedStyle(t).letterSpacing + '/' + getComputedStyle(t).wordSpacing))] })
```

Every font loads; each line is one whole string; spacing is `normal/0px`;
a kerned pair measures narrower than its glyphs summed. Close the tab and kill
any server.

## Final pass: examine it and fix it yourself

The numbers above cannot see most defects. Every build ends with a look at the
render beside the original, at a size where small type is legible -- the
headline, the footer, and anywhere type met artwork. Look for: ghosts of the
original type left in the artwork, fragments of artwork showing through
letters, copy on the wrong line or in the wrong colour, a wrong face or weight,
lines running into rules or shapes, doubled punctuation, lost artwork.

Fix what you find -- a knob, an `--exclude`, a `--face`, a `--knockout`, or a
fix to the tools when the fault is general -- and look again. **Three attempts,
then stop.** If it is still wrong, keep the best version and mark it:

```bash
python3 $S/flag_flawed.py assets/poster-svg/{style}-{event}-v2.svg \
  --verdict fixable \
  --flaw "One sentence: what is still wrong, specific enough to start a fix from." \
  --outlook "One sentence: what bug fix or feature would cure it -- or why nothing would."
```

That puts a small alert icon in the top-left corner; hovering it (pure CSS)
opens "I know this conversion is flawed" with the two sentences. `--verdict`
is `fixable` (a bug fix or feature would cure it) or `dead-end` (the poster
itself defeats a reset, e.g. type and artwork sharing the same pixels). Name
the fix in the outlook: that sentence is what turns the flawed list into a
work queue. Every poster ends in one of three states, all read by
`manifest.py` from the files themselves:

| state | meaning |
|---|---|
| no SVG, or the escape hatch | abandoned early: not convertible, or not worth the attempt |
| **flawed** (marked) | tried, examined, still broken after three attempts; `manifest.py --flawed` lists each with its verdict and outlook, as the queue for fixes |
| done | examined and right |

A rebuild writes a fresh SVG, so fixing a flawed poster clears its mark; if
the fix falls short, mark it again. `flag_flawed.py --clear` removes a mark by
hand, and `--refresh` redraws every mark from its stored text after a change
to the mark's design.

### The metadata record

Every SVG carries a `<metadata id="p2svg-meta">` block: a JSON record of its
status, faults (the flawed mark's sentences), the lettering kept as bitmap and
why, copy lines left unset, tilted lines, the faces that ship and what each
sets, the artwork treatment, bytes of embedded bitmap, and the tool commit and
date of the build. `manifest.py --json` copies it into each row, and
`manifest.py --site-data` writes the part the site's index page shows to
`_data/poster_svg.json`. It is derived from the SVG and its solution, never typed
in -- except `why`, the reason given with `--art` -- and every tool that
changes an SVG rewrites it (`convert.py`, `flag_flawed.py`,
`cannot_convert.py`). After changing what it records, rewrite them all with
`meta.py assets/poster-svg/*.svg --refresh`, which keeps each build stamp.

## Report

The face(s) and the evidence that chose them; artwork treatment and size;
registration numbers; and **explicitly, every place you declined to reproduce
the original, with the measurement that justified it**. Or, for a skip, what
about the poster made it impossible.

---

## Tools

| | |
|---|---|
| `convert.py` | solve + build + verify one poster. Start here |
| `measure.py` | measurement only, as text. Cheapest way to diagnose |
| `solve_type.py` | face ranking and distortion policy; writes solution.json |
| `build_svg.py` | solution.json → SVG |
| `sense_check.py` | reads a finished SVG back and flags what looks odd |
| `check_svg.py` | resolves every `<text>` the way a browser will; catches font substitution |
| `manifest.py` | status across all 200 |
| `cannot_convert.py` | the escape hatch |
| `flag_flawed.py` | mark a conversion known to be flawed: alert icon, hover for flaw and outlook (`--clear`, `--refresh`) |
| `meta.py` | the metadata record in each SVG: print it, or `--refresh` it |
| `catalogue.py` | describe every Google Fonts style (metrics, features, tags); `build`, `show` |
| `fontfetch.py` | fetch a style as a subset of the characters needed, cached |
| `character.py` | the character vocabulary, cut-out line blocks, brief scoring |
| `sheet.py` | contact sheet: the original line beside the shortlist |
| `typefeatures.py`, `glyphs.py`, `linemask.py` | feature measurement, glyph segmentation, two-colour line masks |
| `bench_fonts.py` | font-identification benchmark on degraded known faces |
| `overlay.py` | draw the band assignment on the poster (`--vs-solution` for stored vs current) |
| `audit.py` | per-line check of a finished SVG against the original's intent |
| `regress.py` | re-measure every solved poster; report changed assignments and the judged score |
| `test_svgkit.py` | unit tests for the metric machinery |
| `analyse.py`, `components.py`, `shapescore.py`, `svgkit.py` | libraries |

Set up on a fresh clone (the cache and index are gitignored):

```bash
python3 $S/catalogue.py build
```

**PIL cannot kern.** Without libraqm its layout ignores GPOS entirely, so a
PIL-rendered *line* is never kerned and must not be compared against the
original as though it were. Shape scoring therefore compares single glyphs,
where no pair exists; where true kerned positions are needed they come from
Chrome via `svgkit.probe_pens`, the same shaper that renders the SVG.
`test_svgkit.py` asserts this so it cannot quietly change.

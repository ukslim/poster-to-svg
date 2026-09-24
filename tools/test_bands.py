#!/usr/bin/env python3
"""Polarity tests for line measurement.

    python3 test_bands.py

The same line of type, rendered dark on light, light on dark and colour on
colour, must measure the same: same glyph runs, stroke weight within 5%. And
the two mirror-image cases -- white type knocked out of a navy panel, and
ultra-black type whose letters fill their own box -- must not be confused.
Synthetic images only; negligible cost.
"""
import glob, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bands import resegment  # noqa: E402
from components import label  # noqa: E402
from typefeatures import weight  # noqa: E402

fails = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'   {detail}' if detail else ''))
    if not ok:
        fails.append(name)


def font(pattern, size):
    hits = sorted(glob.glob(os.path.join(HERE, '..', 'fonts', pattern)))
    if not hits:
        hits = ['/System/Library/Fonts/Helvetica.ttc']
    return ImageFont.truetype(hits[0], size)


def page(text, fg, bg, paper=(250, 246, 238), size=40, face='Inter-700.ttf',
         panel=True, h=200, base=120):
    """A 900xh page: `text` in `fg` on a panel of `bg` sitting on `paper`.
    Softened slightly, as a generated bitmap is."""
    im = Image.new('RGB', (900, h), paper)
    d = ImageDraw.Draw(im)
    if panel:
        d.rectangle([20, 30, 880, h - 30], fill=bg)
    d.text((60, base), text, font=font(face, size), fill=fg, anchor='ls')
    return np.array(im.filter(ImageFilter.GaussianBlur(0.6))).astype(int)


def detected_runs(a, fg, rows=(40, 160), cols=(30, 870)):
    """What a detection mask would hand over: components near the ink colour,
    within the text's rows (a real detection mask is local; it does not see
    the paper round the panel as ink)."""
    near = np.abs(a - np.array(fg)).max(2) < 90
    near[:rows[0]] = False
    near[rows[1]:] = False
    near[:, :cols[0]] = False
    near[:, cols[1]:] = False
    return [(c['box'][0], c['box'][2], c['box'][1], c['box'][3])
            for c in label(near) if c['area'] > 6]


def hexc(c):
    return '#%02X%02X%02X' % tuple(c)


TEXT = 'Grand tombola and BBQ'
cases = {
    'dark on light': ((20, 20, 20), (250, 246, 238)),
    'light on dark': ((240, 240, 240), (8, 36, 61)),
    'colour on colour': ((250, 200, 40), (20, 110, 110)),
}
measured = {}
for name, (fg, bg) in cases.items():
    a = page(TEXT, fg, bg)
    runs = detected_runs(a, fg)
    seg = resegment(a, runs, hexc(fg))
    ok = isinstance(seg, tuple)
    check(f'{name}: re-segments', ok, '' if ok else repr(seg))
    if ok:
        new, mask, box, ground, ink = seg
        cap = max(r[3] for r in new) - min(r[2] for r in new) + 1
        measured[name] = (len(new), weight(mask, cap))

if len(measured) == len(cases):
    counts = {n: v[0] for n, v in measured.items()}
    check('same glyph runs in every polarity', len(set(counts.values())) == 1, str(counts))
    ws = [v[1] for v in measured.values()]
    spread = (max(ws) - min(ws)) / max(ws)
    check('stroke weight within 5% in every polarity', spread <= 0.05,
          ', '.join(f'{n} {v[1]:.3f}' for n, v in measured.items()))

# White type on a navy panel, as a dark mask sees it: the navy BETWEEN the
# letters is the "ink". That band must be recognised as a ground.
a = page(TEXT, (240, 240, 240), (8, 36, 61))
near = np.abs(a - np.array((8, 36, 61))).max(2) < 90
near[:, :58] = False                        # only between the letters,
near[:, 525:] = False                       # as local contrast finds it
navy = detected_runs(np.where(near[..., None], a, 255), (8, 36, 61), rows=(88, 122))
seg = resegment(a, navy, hexc((8, 36, 61)))
check('navy gaps between white letters are a ground', seg == 'ground', repr(seg)[:60])

# Ultra-black condensed capitals fill most of their own box, but paper
# surrounds them: they are letters, not a ground.
a = page('CARVER', (0, 0, 0), (0, 0, 0), size=150, face='Anton-*.ttf', panel=False,
         h=300, base=220)
seg = resegment(a, detected_runs(a, (0, 0, 0), rows=(0, 300)), '#000000')
check('ultra-black headline is not a ground', isinstance(seg, tuple), repr(seg)[:60])

print('\nall passed' if not fails else f'\n{len(fails)} FAILED: {", ".join(fails)}')
sys.exit(1 if fails else 0)

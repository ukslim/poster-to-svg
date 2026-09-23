#!/usr/bin/env python3
"""Unit tests for the parts of svgkit that are easy to get silently wrong.

    python3 test_svgkit.py

Pure arithmetic and tiny rasters — no posters, negligible cost.
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import (Face, render_line, render_glyph, pil_font,  # noqa: E402
                    has_shaping, kern_pairs)

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts')
KERNED = os.path.join(FONTS, 'texgyreheros-bold.otf')

fails = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'   {detail}' if detail else ''))
    if not ok:
        fails.append(name)


def ink_box(arr):
    ys, xs = np.where(arr > 128)
    if not len(xs):
        return 0, 0
    return xs.max() - xs.min() + 1, ys.max() - ys.min() + 1


face = Face(KERNED)
f = pil_font(face, 200)

# --- what the font has, vs what PIL will honour -----------------------------
check('font declares kern pairs', kern_pairs(face) > 100,
      f'{kern_pairs(face)} PairPos coverage glyphs')

shaping = has_shaping()
print(f'      (PIL shaping available: {shaping} -- '
      f'{"lines are kerned" if shaping else "lines are NOT kerned; shape score uses single glyphs"})')

# This is the trap, asserted so it can never surprise us again: without raqm,
# PIL's own metrics ignore GPOS. If this ever starts failing, raqm arrived and
# line-level comparison became trustworthy.
pair, apart = f.getlength('AV'), f.getlength('A') + f.getlength('V')
check('PIL metrics match the documented shaping capability',
      (abs(pair - apart) > 0.5) == shaping,
      f'AV {pair:.1f} vs A+V {apart:.1f}, raqm={shaping}')

# --- render_line ------------------------------------------------------------
line = render_line(face, 'HAMBURG', 200, 1200, 400, 300, track=0)
w0, h0 = ink_box(line)
check('render_line produces ink', w0 > 100 and h0 > 50, f'{w0}x{h0}px')

tracked = render_line(face, 'HAMBURG', 200, 1200, 400, 300, track=10)
w1, _ = ink_box(tracked)
check('tracking widens by ~6 gaps', abs((w1 - w0) - 60) < 12,
      f'delta {w1 - w0}px, expected ~60')

# --- render_glyph: the unit of shape comparison -----------------------------
g = render_glyph(face, 'R', 200)
gw, gh = ink_box(g)
check('render_glyph crops tight to one glyph', g is not None and 60 < gw < 200,
      f'{gw}x{gh}px')
check('render_glyph scales with size',
      abs(ink_box(render_glyph(face, 'R', 400))[1] - 2 * gh) <= 3)
check('render_glyph returns None for a blank', render_glyph(face, ' ', 200) is None)

# --- Face metrics -----------------------------------------------------------
check('cap_ratio sane', 0.6 < face.cap_ratio() < 0.8, f'{face.cap_ratio():.3f}')
check('size_for_cap round-trips',
      abs(face.cap_ratio() * face.size_for_cap(100) - 100) < 0.01)
check('missing() finds absent glyphs', face.missing('ABC中') == ['中'])
check('ink width scales linearly',
      abs(face.width('HELLO', 200) - 2 * face.width('HELLO', 100)) < 0.01)
check('CFF outlines give real bounds', face.bounds('H')[3] > 0,
      f"H yMax {face.bounds('H')[3]}")

print()
print(f'{len(fails)} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)

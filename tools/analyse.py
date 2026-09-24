#!/usr/bin/env python3
"""Measure a bitmap poster: background, ink colours, text lines, type metrics.

    python3 analyse.py poster.png                 # overview
    python3 analyse.py poster.png --band Y0 Y1    # one band, glyph by glyph

Everything it prints is a measurement of the ORIGINAL. Nothing here decides
anything; it gives you the numbers the rest of the conversion is built on.
"""
import argparse, collections, sys
import numpy as np
from PIL import Image, ImageFilter


def load(path):
    return np.array(Image.open(path).convert('RGB')).astype(int)


def modal_colour(a, step=17):
    c = collections.Counter(map(tuple, a.reshape(-1, 3)[::step]))
    return c.most_common(1)[0][0]


def masks(a, paper):
    """Common ink masks. Tune the thresholds per poster if the report looks wrong."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx, mn = a.max(2), a.min(2)
    return {
        'dark':   (mx < 130) & ((mx - mn) < 45),
        'blue':   (b - r > 40) & (b > 80),
        'red':    (r - g > 55) & (r - b > 55) & (r > 100),
        'yellow': (r - b > 55) & (g - b > 45) & (r > 130),
        'green':  (g - np.maximum(r, b) > 25) & (g > 80),
        'ink':    np.abs(a - np.array(paper)).max(2) > 28,
    }


def core_colour(a, m, shrink=7):
    """Median colour of the mask's interior, so antialiased edges don't skew it."""
    e = np.array(Image.fromarray((m * 255).astype('uint8'))
                 .filter(ImageFilter.MinFilter(shrink))) > 0
    if e.sum() < 20:
        e = m
    return tuple(np.median(a[e], 0).astype(int))


def row_bands(mask, min_px=1):
    rows = mask.sum(1)
    out, prev, s = [], False, 0
    for y in range(mask.shape[0]):
        on = rows[y] >= min_px
        if on and not prev:
            s = y
        if not on and prev:
            out.append((s, y - 1))
        prev = on
    if prev:
        out.append((s, mask.shape[0] - 1))
    return out


def glyph_runs(mask, y0, y1, x0, x1, min_w=2):
    """Column runs of ink in a band -> (x0, x1, top, bottom) per run."""
    m = mask[y0:y1 + 1, x0:x1 + 1]
    cols, spans, prev, s = m.any(0), [], False, 0
    for i in range(m.shape[1]):
        if cols[i] and not prev:
            s = i
        if not cols[i] and prev:
            if i - s >= min_w:
                spans.append((s, i - 1))
        prev = cols[i]
    if prev and m.shape[1] - s >= min_w:
        spans.append((s, m.shape[1] - 1))
    out = []
    for s, e in spans:
        yy = np.where(m[:, s:e + 1].any(1))[0]
        out.append((x0 + s, x0 + e, y0 + yy.min(), y0 + yy.max()))
    return out


def line_metrics(runs):
    """Baseline, cap/ascender top and x-height top, robust to a stray pixel.

    baseline    = most common run bottom (most letters sit on it)
    capTop      = highest top among runs big enough to be real glyphs
    xTop        = most common top (lowercase, in mixed-case text)

    capTop used to mean "the lowest top shared by >= 2 runs", which kept a
    speck of noise from setting the cap but read the x-height as the cap on any
    line whose tall letters are all different heights: "Ferret racing" has only
    F, t and the i-dot above the x-height, no two of them level, so nothing was
    shared and the cap came out a third short. Size the guard by the run
    instead -- a speck, a comma or a stray mark is *small* -- so a top only
    counts when its run has a plausible glyph's vertical extent.
    """
    if not runs:
        return None
    bots = collections.Counter(r[3] for r in runs)
    tops = collections.Counter(r[2] for r in runs)
    base = bots.most_common(1)[0][0]
    ext = [r[3] - r[2] + 1 for r in runs]
    real = [r[2] for r, e in zip(runs, ext) if e >= 0.4 * max(ext)]
    cap_top = min(real) if real else min(tops)
    # What share of the glyphs sit at x-height rather than full height? In
    # ALL-CAPS setting essentially none do, in mixed case a good half. This is
    # the reliable form of the test: comparing the modal top with the cap top
    # fails on a short word like "Brindlewick", where the ascenders and caps
    # outnumber the x-height letters and the mode lands on the cap line.
    # Specks and punctuation are excluded -- they are small and sit low.
    real = [r for r, e in zip(runs, ext) if e >= 0.4 * max(ext)]
    # Judged against the capital LINE -- a low percentile of the tops -- not
    # the single highest: the accent over FETE stands above the capitals, and
    # taking it for the cap made every capital of VILLAGE FETE look lower case
    # (0.92 "lower" on screenprint_pop's all-caps title).
    #
    # Only a LONE spike is discounted: a percentile would put the "capital
    # line" at small-cap height in a small-caps face with three capitals in
    # the line, and call it all capitals.
    high = sorted(r[2] for r in real)
    h = base + 1 - cap_top
    line = high[1] if len(high) >= 3 and high[1] - high[0] > 0.10 * h else cap_top
    lower = sum(1 for r in real if r[2] - line > 0.12 * (base + 1 - line))
    return dict(lower_frac=round(lower / len(real), 3) if real else 0.0,
                base=base, capTop=cap_top, xTop=tops.most_common(1)[0][0],
                inkTop=min(tops), inkBot=max(bots),
                cap=base + 1 - cap_top, xh=base + 1 - tops.most_common(1)[0][0],
                left=min(r[0] for r in runs), right=max(r[1] for r in runs),
                nruns=len(runs))


def tracking(runs, cap):
    """Excess letter-spacing on a line, in px per gap; 0.0 if it is not tracked.

    Deliberate tracking and a generator's sloppiness look different. Tracked
    capitals have wide gaps that are EVEN: measured on the posters, 0.26-0.84
    of cap height with a spread (CV) under 0.36. Ordinary setting has gaps of
    0.04-0.19; where junk makes them look wider they are wildly uneven (CV 0.7
    and up). Word spaces are left out by taking gaps near the median, and the
    face's own sidebearings -- about 0.09 of cap -- are not tracking.
    """
    if len(runs) < 6 or not cap:
        return 0.0
    r = sorted(runs)
    g = np.array([b[0] - a[1] - 1 for a, b in zip(r, r[1:])], float)
    med = float(np.median(g))
    g = g[g <= max(1.6 * med, med + 0.15 * cap)]
    if len(g) < 5 or g.mean() <= 0:
        return 0.0
    ratio, cv = float(np.median(g)) / cap, float(g.std() / g.mean())
    if ratio < 0.24 or cv > 0.4:
        return 0.0
    return round((ratio - 0.09) * cap, 2)



def report(path, band=None):
    a = load(path)
    h, w, _ = a.shape
    paper = modal_colour(a)
    M = masks(a, paper)
    print(f'{path}  {w}x{h}')
    print(f'paper  #{paper[0]:02X}{paper[1]:02X}{paper[2]:02X}')
    for name, m in M.items():
        if name == 'ink' or m.sum() < 400:
            continue
        ys = np.where(m.any(1))[0]
        print(f'{name:7} px={m.sum():8}  rows {ys.min()}..{ys.max()}  '
              f'core #{"".join(f"{v:02X}" for v in core_colour(a, m))}')

    if band:
        y0, y1 = band
        print(f'\n--- glyph runs in y={y0}..{y1} ---')
        for name in ('dark', 'blue', 'red', 'yellow', 'green'):
            runs = glyph_runs(M[name], y0, y1, 0, w - 1)
            if len(runs) < 2:
                continue
            lm = line_metrics(runs)
            print(f'[{name}] {lm}')
            for r in runs:
                print(f'    x={r[0]:4}..{r[1]:4} w={r[1]-r[0]+1:3}  '
                      f'y={r[2]:4}..{r[3]:4} h={r[3]-r[2]+1:3}')
        return

    print('\n--- ink bands (any non-paper pixel) ---')
    for s, e in row_bands(M['ink'], min_px=6):
        cols = np.where(M['ink'][s:e + 1].any(0))[0]
        dark_px = M['dark'][s:e + 1].sum()
        print(f'y={s:5}..{e:5} h={e-s+1:4}  x={cols.min():4}..{cols.max():4}  '
              f'ink={M["ink"][s:e+1].sum():7}  dark={dark_px:7}')
    print('\nA band that is tall, wide and mostly dark is usually a text line.')
    print('Re-run with --band Y0 Y1 on each text band to get its type metrics.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('image')
    p.add_argument('--band', nargs=2, type=int, default=None)
    args = p.parse_args()
    report(args.image, args.band)

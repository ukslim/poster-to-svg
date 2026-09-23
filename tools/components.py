#!/usr/bin/env python3
"""Connected components, and grouping them into lines of type.

Row-banding (split the page wherever a row has no ink at all) is too fragile
for posters: one vertical rule, border or full-bleed background makes every row
inky and the whole poster collapses into a single "band" that then looks far
too tall to be text. Components don't care how the page is arranged.
"""
import numpy as np
from scipy import ndimage

EIGHT = np.ones((3, 3), bool)


def label(mask):
    """-> list of components, each {box:(x0,y0,x1,y1), area, n_runs}."""
    lab, n = ndimage.label(mask, structure=EIGHT)
    if not n:
        return []
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        ys, xs = sl
        sub = lab[sl] == i
        out.append({'box': (int(xs.start), int(ys.start),
                            int(xs.stop) - 1, int(ys.stop) - 1),
                    'area': int(sub.sum()),
                    'n_runs': int(sub.shape[0])})
    return out


def is_artwork(c, page_w, page_h):
    """True if this component is a shape rather than a glyph."""
    x0, y0, x1, y1 = c['box']
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    density = c['area'] / max(1, bw * bh)
    if bh > 0.30 * page_h or bw > 0.75 * page_w:
        return True, 'spans the page'
    if bw * bh > 0.015 * page_w * page_h and density > 0.80:
        return True, f'large and solid ({density:.0%})'
    if bh < 4 or bw < 2:
        return True, 'hairline'
    return False, ''


def group_lines(comps, overlap=0.40, gutter=1.5):
    """Cluster glyph components into lines by vertical overlap.

    Overlap rather than shared baseline, so ascenders and descenders stay with
    their line: a 'p' overlaps its neighbours heavily even though its bottom
    sits below the baseline.

    A row is then split wherever the gap between neighbours exceeds `gutter`
    times the row's height. Two columns of type set on shared baselines are
    one row by overlap alone, and would come out as one band holding the ends
    of two different lines. Measured on three column posters, word spaces run
    to 0.8 of the row height and column gutters start at 1.9.
    """
    comps = sorted(comps, key=lambda c: (c['box'][1] + c['box'][3]) / 2)
    lines = []
    for c in comps:
        _, y0, _, y1 = c['box']
        h = y1 - y0 + 1
        placed = False
        for ln in lines:
            ly0 = min(x['box'][1] for x in ln)
            ly1 = max(x['box'][3] for x in ln)
            lo, hi = max(y0, ly0), min(y1, ly1)
            if hi >= lo and (hi - lo + 1) >= overlap * min(h, ly1 - ly0 + 1):
                ln.append(c)
                placed = True
                break
        if not placed:
            lines.append([c])
    out = []
    for ln in lines:
        ln.sort(key=lambda c: c['box'][0])
        h = max(c['box'][3] for c in ln) - min(c['box'][1] for c in ln) + 1
        # On a tracked line the word spaces widen with the letters, so a fixed
        # multiple of the height cuts it into words. There, and only there, a
        # gutter must also be wide against the row's OWN letter gaps. Only for
        # rows whose gaps pass the tracking test: scattered artwork marks have
        # wide gaps too, and widening the limit for them glued illustration
        # onto three posters' type.
        from measure import tracking
        limit = gutter * h
        runs = [(c['box'][0], c['box'][2], c['box'][1], c['box'][3]) for c in ln]
        if tracking(runs, h):
            gaps = sorted(b['box'][0] - a['box'][2] - 1 for a, b in zip(ln, ln[1:]))
            limit = max(limit, 4.0 * gaps[len(gaps) // 2])
        pieces, cur = [], [ln[0]]
        for c in ln[1:]:
            if c['box'][0] - max(p['box'][2] for p in cur) - 1 > limit:
                pieces.append(cur)
                cur = []
            cur.append(c)
        pieces.append(cur)
        if len(pieces) == 1:
            out.append(ln)
            continue
        # Each piece is grouped again on its own. A tall line in one column
        # overlaps two short lines in the next, which is what glued all three
        # into one row; once the gutter separates them, the short lines no
        # longer overlap anything but each other.
        for piece in pieces:
            out.extend(group_lines(piece, overlap, gutter))
    return out

"""Cut a measured line of type into (character, glyph crop) pairs.

Shape scoring compares the original's letterforms with a candidate face's, so
it needs to know which ink is which letter. The old rule -- use a line only
when it has exactly one ink run per character -- threw away nearly every line:
specks in the counters of an A, the dot of an i, a colon's two dots, an H that
came out in two pieces, two letters kerned into contact. On mid_century's gig
not one line qualified, shape scoring ran on nothing, and the ranking fell
back to width and stem alone -- which is how Bangers kept winning.

So align the runs to the characters instead, as a small dynamic programme:

    1 run  -> 1 char     the normal case; the only one that yields a crop
    2-3 runs -> 1 char   an i and its dot, a colon, a glyph split in pieces
    1 run  -> 2 chars    letters that touch; no crop, but the line still counts

Each step is costed by how its ink width fits the characters' expected widths,
taken from a reference face and scaled to the line. The reference need not be
the right face: it only has to know that an m is wider than an i.
"""
import os, sys
import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from linemask import line_mask  # noqa: E402

_REF = None


def _reference():
    """Relative ink widths of characters, from a plain sans on this machine."""
    global _REF
    if _REF is None:
        from svgkit import Face
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, '..', 'fonts', 'Inter-400.ttf'),
                  os.path.join(here, '..', 'fonts', 'Roboto-400.ttf'),
                  '/System/Library/Fonts/Helvetica.ttc'):
            if os.path.exists(p):
                _REF = Face(p, index=0 if p.endswith('.ttc') else None)
                break
    return _REF


def expected_widths(chars):
    """Ink width of each character in the reference face, in em."""
    f = _reference()
    out = []
    for c in chars:
        if f is not None and f.has(c):
            x0, _, x1, _ = f.bounds(c)
            out.append(max(0.05, (x1 - x0) / f.upem))
        else:
            out.append(0.5)
    return out


def align(runs, chars):
    """-> list of (run indices, char indices) steps, or None if hopeless.

    `runs` are (x0, x1, y0, y1) sorted left to right; `chars` has no spaces.
    """
    n, m = len(runs), len(chars)
    if not n or not m or n > 3 * m or m > 2 * n:
        return None
    exp = np.array(expected_widths(chars))
    widths = np.array([r[1] - r[0] + 1 for r in runs], float)
    # Scale: the line's ink over the characters' expected ink, ignoring the
    # gaps, which a scale on widths alone cannot see.
    scale = widths.sum() / exp.sum()
    INF = float('inf')
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    # (runs taken, chars taken, fixed penalty) -- merging and splitting are
    # allowed, but a 1:1 reading is preferred when the widths agree.
    moves = ((1, 1, 0.0), (2, 1, 0.15), (3, 1, 0.3), (1, 2, 0.2))
    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == INF:
                continue
            for dr, dc, pen in moves:
                if i + dr > n or j + dc > m:
                    continue
                rs = runs[i:i + dr]
                w = max(r[1] for r in rs) - min(r[0] for r in rs) + 1
                want = exp[j:j + dc].sum() * scale
                cost = dp[i][j] + abs(np.log(max(w, 1) / max(want, 1))) + pen
                if cost < dp[i + dr][j + dc]:
                    dp[i + dr][j + dc] = cost
                    back[i + dr][j + dc] = (i, j)
    if dp[n][m] == INF:
        return None
    steps, i, j = [], n, m
    while (i, j) != (0, 0):
        pi, pj = back[i][j]
        steps.append((list(range(pi, i)), list(range(pj, j))))
        i, j = pi, pj
    steps.reverse()
    # A reading that is mostly guesswork is worse than none.
    if dp[n][m] / m > 0.6:
        return None
    return steps


def segment(arr, band, text):
    """-> [(char, bool crop)] for the characters of `text` that one clean run
    each can be attributed to. `arr` is the poster as an RGB array; the crop
    is cut from the line's own two-colour mask, whatever its polarity."""
    runs = sorted(tuple(r) for r in band.get('runs') or [])
    chars = [c for c in text if not c.isspace()]
    steps = align(runs, chars)
    if not steps:
        return []
    H, W = arr.shape[:2]
    x0 = max(0, min(r[0] for r in runs) - 2)
    x1 = min(W - 1, max(r[1] for r in runs) + 2)
    y0 = max(0, min(r[2] for r in runs) - 2)
    y1 = min(H - 1, max(r[3] for r in runs) + 2)
    mask, _ = line_mask(arr, (x0, y0, x1, y1), band['rgb'], band.get('ground'))
    if mask is None:
        return []
    out = []
    for ri, ci in steps:
        if len(ci) != 1:
            continue
        rs = [runs[k] for k in ri]
        bx0, bx1 = min(r[0] for r in rs), max(r[1] for r in rs)
        by0, by1 = min(r[2] for r in rs), max(r[3] for r in rs)
        crop = mask[by0 - y0:by1 - y0 + 1, bx0 - x0:bx1 - x0 + 1]
        # Keep only ink connected to this glyph's own runs: a neighbour's
        # serif reaching into the box is not part of it.
        lab, n = ndimage.label(crop, np.ones((3, 3), bool))
        keep = set()
        for r in rs:
            sub = lab[r[2] - by0:r[3] - by0 + 1, r[0] - bx0:r[1] - bx0 + 1]
            keep.update(np.unique(sub[sub > 0]).tolist())
        crop = np.isin(lab, sorted(keep))
        if crop.sum() >= 12:
            out.append((chars[ci[0]], crop))
    return out

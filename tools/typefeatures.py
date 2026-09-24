"""Characterise a typeface from glyph masks.

The same functions run on crops from a poster and on glyphs rendered from a
font, so the numbers compare directly. Letter-agnostic wherever possible: a
measurement that needs to know it is looking at an H fails on every line that
has no H.

    features([(char, mask, cap_px), ...]) -> dict
    distance(a, b) -> (total, parts)

Every length is divided by the cap height of the line the glyph came from, so
a 20px footer and a 300px headline in one face measure alike.
"""
import numpy as np
from scipy import ndimage

# Letters whose ink reaches cap height and whose stems are upright in an
# upright face: the ones slant is read from.
STEMMED = set('BDEFHIKLMNPRTbdhklpq')
ROUND = set('Oo0')   # not Q: its tail stretches the box
XHEIGHT = set('acemnorsuvwxz')


def stroke_widths(mask, floor=0.25):
    """Stroke widths along the middle of each stroke, in px.

    Points on the medial ridge are local maxima of the distance to the nearest
    edge; twice that distance is the stroke's width there. Works on any glyph,
    any polarity of the original (the mask is the ink), and is untroubled by
    crossbars, serifs and bowls, which defeat a scanline measurement.
    """
    m = np.asarray(mask, bool)
    if not m.any():
        return np.array([])
    e = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
    # A jagged or antialiased edge makes one-pixel-deep local maxima all along
    # the outline; on big display type they outnumber the true ridge and the
    # "stroke" reads as 2px on a 150px capital. Keep points at least a quarter
    # as deep as the deepest -- which still admits the thin strokes of a face
    # with 4:1 contrast.
    ridge = (e >= ndimage.maximum_filter(e, size=3)) & (e >= max(1.0, floor * e.max()))
    return 2.0 * e[ridge]


def weight(mask, cap):
    """Typical stroke width / cap height. ~0.08 hairline, ~0.2 bold, ~0.3 black."""
    w = stroke_widths(mask)
    if not len(w) or not cap:
        return None
    return float(np.percentile(w, 60)) / cap


def contrast(mask):
    """Thin strokes / thick strokes: ~1 monoline, <0.3 a high-contrast didone.

    Needs the hairlines, which the weight measurement's quarter-depth floor
    throws away (a didone's hairline is an eighth of its stem). So a lower
    floor here, with an absolute one to keep edge noise out.
    """
    w = stroke_widths(mask, floor=0.06)
    w = w[w >= 3.0]
    if len(w) < 20:
        return None
    return float(np.percentile(w, 10) / np.percentile(w, 90))


def slant(mask):
    """Degrees of lean, positive leaning right: the shear under which the
    glyph's vertical strokes project most sharply onto the baseline."""
    m = np.asarray(mask, bool)
    ys, xs = np.nonzero(m)
    if len(xs) < 20:
        return None
    h = m.shape[0]
    best, best_a = -1.0, 0.0
    for a in np.arange(-24, 24.5, 1.5):
        # undo a lean of `a` degrees: shift each row left by its height above
        # the foot times tan(a)
        sx = np.round(xs - (h - 1 - ys) * np.tan(np.radians(a))).astype(int)
        prof = np.bincount(sx - sx.min()).astype(float)
        sharp = float((prof ** 2).sum())
        if sharp > best:
            best, best_a = sharp, a
    return best_a


def squareness(mask):
    """Filled area of an O over its box: a circle is 0.785, a square 1.0."""
    m = ndimage.binary_fill_holes(np.asarray(mask, bool))
    return float(m.mean()) if m.size else None


def features(glyphs):
    """glyphs: [(char, bool mask tight to the ink, cap_px of its line)].

    -> {aspect: {char: ink width / cap}, height: {char: ink height / cap},
        weight, contrast, slant, square}
    Missing measurements are None; `distance` skips them.
    """
    aspect, height, ws, cs, sl, sq = {}, {}, [], [], [], []
    for ch, m, cap in glyphs:
        m = np.asarray(m, bool)
        if not m.any() or not cap:
            continue
        ys, xs = np.nonzero(m)
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        aspect.setdefault(ch, []).append(w / cap)
        height.setdefault(ch, []).append(h / cap)
        wt = weight(m, cap)
        if wt:
            ws.append(wt)
        c = contrast(m)
        if c:
            cs.append(c)
        if ch in STEMMED:
            s = slant(m)
            if s is not None:
                sl.append(s)
        if ch in ROUND:
            sq.append(squareness(m))
    med = lambda v: float(np.median(v)) if v else None  # noqa: E731
    caps = [cap for _, _, cap in glyphs if cap]
    return dict(aspect={k: med(v) for k, v in aspect.items()},
                height={k: med(v) for k, v in height.items()},
                weight=med(ws), contrast=med(cs), slant=med(sl), square=med(sq),
                cap=med(caps))


# How much each difference costs. Tuned against bench_fonts.py.
WEIGHTS = dict(width=3.0, height=2.0, weight=12.0, contrast=1.5, slant=0.25, square=6.0)


def distance(a, b, w=WEIGHTS):
    """-> (total, {part: cost}). Only what both sides measured is compared."""
    parts = {}
    common = [c for c in a['aspect'] if c in b['aspect'] and a['aspect'][c] and b['aspect'][c]]
    if common:
        parts['width'] = w['width'] * float(np.mean(
            [abs(np.log(a['aspect'][c] / b['aspect'][c])) for c in common]))
    common = [c for c in a['height'] if c in b['height'] and c in XHEIGHT
              and a['height'][c] and b['height'][c]]
    if common:
        parts['height'] = w['height'] * float(np.mean(
            [abs(np.log(a['height'][c] / b['height'][c])) for c in common]))
    # Contrast needs hairlines, and small type has none to measure: at a
    # 22px cap a serif's thin strokes are a pixel or two and the whole line
    # reads monoline, which handed an upright serif's lines to a sans. Count
    # contrast in full from a 64px cap, not at all below 24px. (The side
    # measured at the lower resolution decides; the catalogue is at 120px.)
    cap = min(c for c in (a.get('cap'), b.get('cap')) if c) if (a.get('cap') or b.get('cap')) else None
    trust = 1.0 if cap is None else min(1.0, max(0.0, (cap - 24) / 40))
    # Stroke weight survives small type better, but reads heavy there (a
    # 1.6px stroke measures 2px); the shape stage compares ink at native size.
    trust_w = 1.0 if cap is None else min(1.0, max(0.3, (cap - 16) / 48))
    for k in ('weight', 'contrast', 'square'):
        if a.get(k) is not None and b.get(k) is not None:
            scale = trust if k == 'contrast' else trust_w if k == 'weight' else 1.0
            parts[k] = w[k] * abs(a[k] - b[k]) * scale
    if a.get('slant') is not None and b.get('slant') is not None:
        parts['slant'] = w['slant'] * abs(a['slant'] - b['slant'])
    return sum(parts.values()), parts

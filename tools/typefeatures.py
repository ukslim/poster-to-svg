"""Characterise a typeface from glyph masks.

The same functions run on crops from a poster and on glyphs rendered from a
font, so the numbers compare directly. Letter-agnostic wherever possible: a
measurement that needs to know it is looking at an H fails on every line that
has no H.
"""
import numpy as np
from scipy import ndimage


def stroke_widths(mask):
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
    ridge = (e >= ndimage.maximum_filter(e, size=3)) & (e >= max(1.0, 0.25 * e.max()))
    return 2.0 * e[ridge]


def weight(mask, cap):
    """Typical stroke width / cap height. ~0.08 hairline, ~0.2 bold, ~0.3 black."""
    w = stroke_widths(mask)
    if not len(w) or not cap:
        return None
    return float(np.percentile(w, 60)) / cap


def contrast(mask):
    """Thin strokes / thick strokes: ~1 monoline, <0.4 a high-contrast didone."""
    w = stroke_widths(mask)
    if len(w) < 20:
        return None
    return float(np.percentile(w, 15) / np.percentile(w, 85))

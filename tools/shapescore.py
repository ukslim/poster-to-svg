#!/usr/bin/env python3
"""Compare two letterform silhouettes.

Chamfer (edge-distance) rather than IoU. Generated type drifts a pixel or two,
strokes shift, counters differ slightly, antialiasing differs -- IoU punishes
all of that as hard as it punishes a structurally different letter. Chamfer
asks the useful question instead: how far is each edge of one shape from the
nearest edge of the other?

Lower is better. Roughly: < 1.5 is the same letterform, > 4 is a different one.
"""
import numpy as np
from scipy import ndimage


def edges(mask):
    return mask & ~ndimage.binary_erosion(mask, np.ones((3, 3), bool))


def normalise(mask, size=64):
    """Scale a glyph's ink box into a fixed square so shapes compare at one
    scale. Aspect is deliberately preserved -- width is a real difference
    between faces, and squashing it away would hide exactly what we test for."""
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    scale = size / max(h, w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    from PIL import Image
    im = Image.fromarray((crop * 255).astype('uint8')).resize((nw, nh), Image.BILINEAR)
    out = np.zeros((size, size), bool)
    arr = np.array(im) > 110
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = arr
    return out


def chamfer(a, b, size=64):
    """Symmetric mean edge distance between two glyph masks."""
    A, B = normalise(np.asarray(a, bool), size), normalise(np.asarray(b, bool), size)
    if A is None or B is None:
        return 99.0
    ea, eb = edges(A), edges(B)
    if not ea.any() or not eb.any():
        return 99.0
    da = ndimage.distance_transform_edt(~ea)
    db = ndimage.distance_transform_edt(~eb)
    return float((da[eb].mean() + db[ea].mean()) / 2)

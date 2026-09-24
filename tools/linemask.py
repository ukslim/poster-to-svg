"""A glyph mask for one line of type that does not care which way round it is.

Detection masks (masks.ink_masks) are built to FIND type: local-luminance
contrast plus fixed colour families. That suits dark type on pale paper and
little else. Measured on mid_century's gig, the white date on a teal panel came
out of the detection mask in 57 pieces for 31 characters, with 30% less ink
than the letters really have -- so its stroke weight read light and it was set
in a light face.

Once a line has been found, we know two colours: its ink and the ground it sits
on. Every pixel can then be placed on the line between them, and cut at the
midpoint. That is unbiased for stroke weight, and identical for dark-on-light,
light-on-dark and colour-on-colour.
"""
import numpy as np


def hex_rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], float)


def ground_of(arr, box, ink, pad=None):
    """Median colour of the pixels round a line that are clearly not its ink.

    `box` is (x0, y0, x1, y1), inclusive. Pads the box so that a line whose ink
    fills its own box still has ground to measure.
    """
    x0, y0, x1, y1 = box
    h = y1 - y0 + 1
    pad = max(4, h // 3) if pad is None else pad
    H, W = arr.shape[:2]
    sub = arr[max(0, y0 - pad):min(H, y1 + pad + 1), max(0, x0 - pad):min(W, x1 + pad + 1)]
    px = sub.reshape(-1, 3).astype(float)
    d = np.linalg.norm(px - ink, axis=1)
    far = px[d > max(40.0, np.percentile(d, 50))]
    if len(far) < 20:
        return None
    return np.median(far, 0)


def line_mask(arr, box, ink, ground=None, min_contrast=30.0):
    """-> (mask, ground) for the pixels of `box` that are the line's ink.

    `ink` and `ground` are RGB (array or '#RRGGBB'). Each pixel is projected
    onto the ink->ground axis and cut at the midpoint. Returns (None, ground)
    when ink and ground are too close to separate.
    """
    ink = hex_rgb(ink) if isinstance(ink, str) else np.asarray(ink, float)
    if ground is None:
        ground = ground_of(arr, box, ink)
    elif isinstance(ground, str):
        ground = hex_rgb(ground)
    if ground is None:
        return None, None
    axis = ground - ink
    n2 = float(axis @ axis)
    if n2 < min_contrast ** 2:
        return None, ground
    x0, y0, x1, y1 = box
    sub = arr[y0:y1 + 1, x0:x1 + 1].astype(float)
    # einsum, not @: numpy on Accelerate raises spurious matmul warnings here
    t = np.einsum("ijk,k->ij", sub - ink, axis) / n2    # 0 at ink, 1 at ground
    return t < 0.5, ground

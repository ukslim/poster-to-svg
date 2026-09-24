"""Type set on a slant: find the angle, and move between the poster and a
levelled copy of it.

A tilted line is measured, assigned and set in a *frame*: the poster rotated
about a centre so that the line is level. Everything that reads pixels for
that line reads the levelled image; the builder sets the line level and
rotates it back into place with an SVG transform.

    frame = {'angle': a, 'cx': x, 'cy': y}

`level(img, frame)` is PIL's rotate(a) about (cx, cy) -- counter-clockwise
by a degrees on screen. Its inverse, back onto the poster, is rotate(-a):
clockwise by a on screen, which is SVG's transform="rotate(a cx cy)".
"""
import numpy as np
from PIL import Image

_CACHE = {}


def level(arr, frame, fill=None):
    """The poster (H x W x 3 int array) rotated level for this frame. Cached."""
    key = (id(arr), frame['angle'], frame['cx'], frame['cy'])
    if key not in _CACHE:
        im = Image.fromarray(np.asarray(arr).clip(0, 255).astype('uint8'))
        if fill is None:
            fill = tuple(int(v) for v in np.median(np.asarray(arr).reshape(-1, 3), 0))
        out = im.rotate(frame['angle'], resample=Image.BICUBIC,
                        center=(frame['cx'], frame['cy']), fillcolor=fill)
        _CACHE[key] = np.array(out).astype(int)
    return _CACHE[key]


def unlevel_mask(mask, frame):
    """A boolean mask in the levelled frame, put back onto the poster."""
    im = Image.fromarray((np.asarray(mask) * 255).astype('uint8'))
    out = im.rotate(-frame['angle'], resample=Image.NEAREST,
                    center=(frame['cx'], frame['cy']), fillcolor=0)
    return np.array(out) > 127


def to_poster(x, y, frame):
    """A point in the levelled frame -> the same point on the poster."""
    a = np.radians(frame['angle'])
    dx, dy = x - frame['cx'], y - frame['cy']
    # inverse of PIL's CCW-by-a (screen, y down): rotate CW by a
    return (frame['cx'] + dx * np.cos(a) + dy * np.sin(a),
            frame['cy'] - dx * np.sin(a) + dy * np.cos(a))


def pixels(arr, line):
    """The array a line's coordinates refer to."""
    f = line.get('frame')
    return level(arr, f) if f else arr


def estimate(arr, box, paper, search=35.0):
    """Angle (degrees, PIL rotate sense) that levels the type in `box`.

    Type makes a projection profile of sharp peaks (its lines) and gaps
    (the leading) only when it is level. So rotate the box's ink through
    candidate angles and keep the one whose row sums are most uneven.
    """
    x0, y0, x1, y1 = box
    sub = np.asarray(arr)[y0:y1 + 1, x0:x1 + 1].astype(float)
    ink = (np.abs(sub - np.array(paper, float)).max(2) > 60).astype('uint8') * 255
    im = Image.fromarray(ink)
    best, best_a = -1.0, 0.0
    for step, span in ((1.0, search), (0.1, 1.0)):
        centre = best_a
        for a in np.arange(centre - span, centre + span + step / 2, step):
            prof = np.asarray(im.rotate(a, resample=Image.BILINEAR, expand=True),
                              float).sum(1)
            sharp = float((prof ** 2).sum())
            if sharp > best:
                best, best_a = sharp, float(a)
    return round(best_a, 2)


def parse(spec):
    """'date,venue@X0,Y0,X1,Y1[@ANGLE]' -> (keys, box, angle or None)."""
    parts = spec.split('@')
    if len(parts) < 2:
        raise SystemExit(f'--tilted {spec!r}: expected KEYS@X0,Y0,X1,Y1[@ANGLE]')
    keys = [k.strip() for k in parts[0].split(',') if k.strip()]
    box = tuple(int(v) for v in parts[1].split(','))
    angle = float(parts[2]) if len(parts) > 2 else None
    return keys, box, angle

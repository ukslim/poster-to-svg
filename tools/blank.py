"""Which pixels of the original are the type being replaced, so the builder
can take them out of the artwork."""
import os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse import modal_colour  # noqa: E402
from masks import ink_masks  # noqa: E402
from bands import find_lines  # noqa: E402


def text_ink_mask(img_path, lines=None, **opts):
    """Boolean mask of exactly the pixels that belong to detected type.

    The builder needs this to blank type out of the artwork. Blanking
    rectangles instead takes a bite out of anything the type overlaps -- a
    headline sitting over a circle removes a square of it.

    `lines` is the bands to blank. The builder passes only those that were
    assigned copy: a band nobody resets is not type we are replacing, and
    blanking it erases part of the artwork -- a catalogue number, or a phantom
    band in a painting -- with nothing put back. Without `lines`, every band
    detected with `opts` is blanked.
    """
    a = np.array(Image.open(img_path).convert('RGB')).astype(int)
    paper = modal_colour(a)
    out = np.zeros(a.shape[:2], bool)
    m = {'lines': lines} if lines is not None else find_lines(img_path, **opts)
    # Lines set on a slant are blanked in their own levelled frame, and the
    # mask rotated back onto the poster.
    framed = [ln for ln in m['lines'] if ln.get('frame')]
    if framed:
        import tempfile
        from tilt import level, unlevel_mask
        by = {}
        for ln in framed:
            by.setdefault(tuple(sorted(ln['frame'].items())), []).append(ln)
        for key, lns in by.items():
            frame = dict(key)
            with tempfile.NamedTemporaryFile(suffix='.png') as t:
                Image.fromarray(level(a, frame, fill=paper).clip(0, 255).astype('uint8')).save(t.name)
                sub, _ = text_ink_mask(t.name, lines=[{k: v for k, v in ln.items() if k != 'frame'}
                                                      for ln in lns], **opts)
            out |= unlevel_mask(sub, frame)
        m = {'lines': [ln for ln in m['lines'] if not ln.get('frame')]}
    raw = ink_masks(a, paper, opts.get('contrast', 40), opts.get('window', 61))
    anyink = np.zeros(a.shape[:2], bool)
    for _m in raw.values():
        anyink |= _m
    from scipy import ndimage
    for colour, mask in raw.items():
        here_lines = [ln for ln in m['lines'] if ln['colour'] == colour]
        if not here_lines:
            continue
        # Punctuation is its own tiny component, and small ones are routinely
        # dropped when runs are grouped into lines -- so the words get blanked
        # and the full stops are left sitting in the artwork, printing a second
        # time under the reset type ("Free entry.. All welcome.."). Anything
        # this small inside a line's own box is punctuation, not artwork.
        lab, n = ndimage.label(mask)
        sizes = ndimage.sum(mask, lab, range(1, n + 1)) if n else []
        boxes = ndimage.find_objects(lab) if n else []
        for ln in here_lines:
            here = np.zeros_like(mask)
            # A glyph's ink is one thing, but the colour masks can split it:
            # a dark green title puts its solid cores in the green mask and its
            # antialiased edges in the neutral one, and blanking only the mask
            # the line was detected in leaves the cores behind -- the original
            # headline ghosts through under the reset type. So take, inside the
            # line's own run boxes, any ink of the line's own measured colour,
            # whichever mask it happens to sit in. Keyed on colour, not on the
            # boxes alone, so a shape the type sits on is left alone.
            lnrgb = np.array([int(ln['rgb'][i:i + 2], 16) for i in (1, 3, 5)])
            near = np.abs(a - lnrgb).max(2) < 60
            mine = anyink & near
            for r in (ln.get('runs') or []):
                sl = (slice(r[2], r[3] + 1), slice(r[0], r[1] + 1))
                here[sl] |= mask[sl] | mine[sl]
            # Light type knocked out of a panel is only partly caught by the
            # light mask -- its lower strokes can fail the contrast test -- so
            # the run boxes stop partway down the letters and the rest ghosts
            # through under the reset line. Over a ground that is NOT the
            # line's own colour, take the line's colour anywhere in its box.
            # Judged against the ground actually under the line, not the page's
            # paper: white type in a teal panel is paper-coloured, but it sits
            # on teal. Over a ground of its own colour this would blank the
            # ground, so skip.
            y0 = max(0, ln['y0'] - int(0.1 * ln['cap']))
            y1 = min(a.shape[0], ln['y1'] + int(0.35 * ln['cap']) + 1)
            sl = (slice(y0, y1), slice(ln['left'], ln['right'] + 1))
            ground = a[sl][~near[sl]]
            if len(ground) and np.abs(np.median(ground, 0) - lnrgb).max() > 60:
                # ...but only ink joined to the line's own letters: the box
                # reaches below the band, and a mark of the same colour under
                # a big title (factory_records' "BW 06-09" and its rule, in
                # the title's green) is not part of it.
                # Marks within the band's own rows stay in: an accent is its
                # own speck, joined to nothing, above the letter it marks.
                nl, k = ndimage.label(near[sl], np.ones((3, 3), bool))
                if k:
                    keep = set(np.unique(nl[here[sl] & (nl > 0)]).tolist())
                    for i, (ys, xs) in enumerate(ndimage.find_objects(nl), start=1):
                        # small, and clear of the box's top edge: an accent,
                        # not the foot of a shape above (wpa's stage floor)
                        if (y0 + ys.stop <= ln['y1'] + 1 and ys.start > 0
                                and xs.stop - xs.start < ln['cap']):
                            keep.add(i)
                    keep.discard(0)
                    here[sl] |= np.isin(nl, list(keep))
            # A line's closing full stop is the one piece of punctuation that
            # is never inside its box: it is too small to be kept as a glyph,
            # so the box ends at the last letter and the stop sits just past
            # it -- printing twice, "welcome..". Reach a little past the end.
            reach = int(round(0.6 * ln['cap']))
            for i, sl in enumerate(boxes):
                if sl is None or sizes[i] > 0.25 * ln['cap'] ** 2:
                    continue
                ys, xs = sl
                if (ys.start >= ln['y0'] and ys.stop <= ln['y1'] + 1
                        and xs.start >= ln['left']
                        and xs.stop <= ln['right'] + 1 + reach):
                    here[sl] |= lab[sl] == i + 1
            # Grow each line's ink in proportion to its size. Antialiasing
            # spreads further round cap-100 display type than round 15px small
            # print, and a flat margin leaves a rim of the original showing.
            grow = max(2, int(round(0.045 * ln['cap'])))
            out |= ndimage.binary_dilation(here, np.ones((2 * grow + 1,) * 2, bool))
    return out, m


"""Ink masks: which pixels are ink, by colour family, and stripping the big
solids and long rules out of them before glyphs are looked for."""
import numpy as np
from PIL import Image, ImageFilter


def ink_masks(a, paper, contrast=40, window=61):
    """Separate ink by colour family so coloured lines are measured apart.

    Neutral ink is found by LOCAL contrast, not an absolute threshold. "Darker
    than 140" is only meaningful on pale paper: on a black poster it matches
    texture noise while missing the white type entirely. Comparing each pixel
    with a blurred version of its surroundings handles dark-on-light,
    light-on-dark, and a poster that does both -- white type in a black panel
    beside black type on the paper.
    """
    from scipy import ndimage
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx, mn = a.max(2), a.min(2)
    far = np.abs(a - np.array(paper)).max(2) > 30
    sat = mx - mn
    lum = a.mean(2)
    bg = ndimage.uniform_filter(lum, size=window)
    neutral = sat < 55
    dark = (bg - lum > contrast) & neutral
    light = (lum - bg > contrast) & neutral
    colours = {
        'blue': far & (b.astype(int) - r > 40) & (b > 70),
        # Red must also be *not* yellow: yellow easily clears r-g and r-b (for
        # #FDC403 they are 57 and 250), so without a cap on green the two
        # merge into one blob and neither can be measured.
        'red': far & (r.astype(int) - g > 50) & (r.astype(int) - b > 50)
               & (r > 90) & (g < 0.62 * r),
        'green': far & (g.astype(int) - np.maximum(r, b) > 30) & (g > 70),
        'yellow': far & (r.astype(int) - b > 55) & (g.astype(int) - b > 45) & (r > 130),
    }
    # One of the two is the poster's ordinary type and the other only exists
    # knocked out of a panel. Saying which way round stops the minority mask
    # from harvesting photographic mid-tones and antialiasing: on pale paper
    # ordinary type is genuinely dark, and light type must be sitting on
    # something dark. On dark paper, the mirror image.
    if float(np.mean(paper)) > 140:
        dark &= lum < 165
        # Light type on pale paper sits in a dark or coloured panel. Ask what
        # SHARE of its surroundings is dark, not how dark they are on average:
        # a teal panel packed with white type averages lighter than 100, and
        # testing the mean found none of it -- the date and venue on both
        # mid-century posters vanished into the panel's artwork.
        #
        # The counters of heavy black lettering pass this too; find_lines
        # rejects them as a band sitting inside dark ink.
        darkfrac = ndimage.uniform_filter((lum < 150).astype(float), size=31)
        light &= darkfrac > 0.5
    else:
        light &= lum > 95
        dark &= bg > 150
    return {'dark': dark, 'light': light, **colours}


def _morph(mask, radius, kind):
    """Erode or dilate by a square of the given radius.

    Square structuring elements compose, so erode-by-5 applied five times is
    erode-by-25 and is far cheaper than one huge kernel.
    """
    step, n = 3, max(1, int(round(radius / 3)))
    img = Image.fromarray((mask * 255).astype('uint8'))
    f = ImageFilter.MinFilter if kind == 'erode' else ImageFilter.MaxFilter
    for _ in range(n):
        img = img.filter(f(2 * step + 1))
    return np.array(img) > 127


def strip_solids(mask, radius=24):
    """Remove big solid artwork from an ink mask, keeping the type.

    Glyphs are thin: a stem is roughly 0.1-0.3 of cap height, so even large
    display type erodes away quickly. A filled circle, bar or panel does not.
    Erode hard, dilate what survives back a little further, and subtract.

    Needed because a solid shape that merely *touches* a line of type joins it
    into one row-band, which then looks far too tall to be text and is thrown
    away -- taking real lines with it. Raise `radius` if unusually heavy
    display type is being eaten; lower it if artwork survives.

    Returns (text_mask, solid_mask).
    """
    if mask.sum() < 500:
        return mask, np.zeros_like(mask)
    core = _morph(mask, radius, 'erode')
    if not core.any():
        return mask, np.zeros_like(mask)
    solid = _morph(core, radius + 8, 'dilate') & mask
    return mask & ~solid, solid


def strip_rules(mask, length=160, thick=5):
    """Remove long thin rules from an ink mask, keeping the type.

    Must happen BEFORE connected components. A hairline grid rule that merely
    grazes a serif joins that glyph into one component spanning the page, which
    is then classified as artwork -- so the glyph is never detected as text,
    and never blanked from the artwork raster. The symptom is a ghost letter
    left behind in the finished SVG.

    A rule is ink that is (a) thin in one direction and (b) very long in the
    other. Both conditions matter: a serif or a crossbar is also long and thin,
    so the length has to exceed anything a single glyph can contain, and the
    removal is confined to ink that is thin perpendicular to the rule -- which
    means a stem can never be eaten no matter how the lengths fall.
    """
    from scipy import ndimage

    def thin_along(axis, span):
        """Ink that is no more than `thick` px deep across `axis`."""
        el = np.ones((span, 1), bool) if axis == 0 else np.ones((1, span), bool)
        core = ndimage.binary_dilation(ndimage.binary_erosion(mask, el), el)
        return mask & ~core

    rules = np.zeros_like(mask)
    # horizontal rules: vertically thin, horizontally long
    rules |= ndimage.binary_opening(thin_along(0, thick + 1),
                                    np.ones((1, length), bool))
    # vertical rules: horizontally thin, vertically long
    rules |= ndimage.binary_opening(thin_along(1, thick + 1),
                                    np.ones((length, 1), bool))
    if not rules.any():
        return mask, rules
    rules = ndimage.binary_dilation(rules, np.ones((3, 3), bool)) & mask
    return mask & ~rules, rules


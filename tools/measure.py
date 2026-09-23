#!/usr/bin/env python3
"""Turn a poster bitmap into measured text lines, and attach the known copy.

Used by solve_type.py. Split out because band-finding and copy-assignment are
the parts most likely to need tuning per poster, and are worth testing alone:

    python3 measure.py poster.webp --event gig
"""
import argparse, collections, os, sys
import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse import glyph_runs, line_metrics, modal_colour, row_bands  # noqa: E402
from components import label, is_artwork, group_lines  # noqa: E402

import sitepaths as poster_site  # noqa: E402


# --------------------------------------------------------------- known copy
def _prompt_labels(event, repo=None):
    """{field: (before, after)} -- the words the prompt wraps round each field.

    events.yaml holds bare values; the prompt template adds labels as it
    builds the text the image model was given ("Tickets: " before the ticket
    prices, "Available from " before the ticket source). The poster prints
    what the prompt said, so the copy must too. Read from the template itself
    rather than repeated here, so the two cannot drift.
    """
    import re
    path = poster_site.prompt_template(repo)
    try:
        tpl = open(path, encoding='utf-8').read()
    except OSError:
        return {}
    out = {}
    pat = r'^(.*?)\{\{\s*site\.data\.events\.' + event + r'\.(\w+)\s*\}\}(.*?)$'
    for m in re.finditer(pat, tpl, re.M):
        before, after = (re.sub(r'\{%.*?%\}', '', g) for g in (m.group(1), m.group(3)))
        out.setdefault(m.group(2), (before, after))
    return out


def load_copy(event, repo=None):
    """The poster's text, in reading order: events.yaml as the prompt set it."""
    import yaml
    data = yaml.safe_load(open(poster_site.events_yaml(repo)))[event]
    labels = _prompt_labels(event, repo)

    def text(k):
        before, after = labels.get(k, ('', ''))
        return f'{before}{data[k]}{after}'

    if event == 'gig':
        keys = ['presenter', 'headliner', 'support', 'date', 'venue',
                'tickets', 'ticket_source', 'footer']
        return [{'key': k, 'text': text(k)} for k in keys if data.get(k)]
    # fete: fixed fields, then a variable-length attractions list.
    #
    # A fete poster does not simply print these strings. It heads the list with
    # a label of its own ("Attractions:"), and it breaks the footer into its
    # separate sentences, often with the beneficiary sitting between them. Both
    # are offered here as optional lines so the aligner can use them when the
    # poster does and skip them when it does not.
    out = [{'key': k, 'text': str(data[k])}
           for k in ('title', 'date', 'venue') if data.get(k)]
    if data.get('beneficiary'):
        out.append({'key': 'beneficiary', 'text': str(data['beneficiary']),
                    'optional': True})
    if data.get('attractions'):
        out.append({'key': 'attractions_label', 'text': 'Attractions:',
                    'optional': True})
    out += [{'key': f'attraction{i}', 'text': str(a), 'optional': True}
            for i, a in enumerate(data['attractions'])]
    for i, sentence in enumerate(_sentences(str(data.get('footer', '')))):
        out.append({'key': f'footer{i}', 'text': sentence, 'optional': True})
    return out


def _sentences(text):
    """Split a run-on footer into the lines a poster would actually set."""
    import re
    parts = [p.strip() for p in re.split(r'(?<=\.)\s+', text) if p.strip()]
    # "Free entry. All welcome." belongs together; a new clause starting with a
    # verb like "Organised by" is a separate line.
    out = []
    for p in parts:
        if out and len(p) < 26 and not re.match(r'^(Organised|Run|Hosted|In aid)', p):
            out[-1] = out[-1] + ' ' + p
        else:
            out.append(p)
    return out


# ------------------------------------------------------------------ masks
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


def measure_stem(mask, x0, x1, y0, y1):
    """Stem/cap of one glyph in the ORIGINAL, comparable with index stem_ratio."""
    sub = mask[y0:y1 + 1, x0:x1 + 1]
    ys = np.where(sub.any(1))[0]
    if len(ys) < 10:
        return None
    top, bot = ys.min(), ys.max()
    cap = bot - top + 1
    mins = []
    for y in range(top + int(cap * 0.08), bot - int(cap * 0.08)):
        runs, n = [], 0
        for v in sub[y]:
            if v:
                n += 1
            elif n:
                runs.append(n); n = 0
        if n:
            runs.append(n)
        if runs:
            mins.append(min(runs))
    return (float(np.median(mins)) / cap) if mins else None


def baseline_fit(runs, cap):
    """(slope in degrees, straightness) of the baseline through a line's glyphs.

    An SVG <text> sits on one straight baseline. A modest slope is fine -- it
    can be a rotate transform. Type arched over a curve, wrapped round a shape
    or thrown into perspective cannot be reset as a real font at all, and shows
    up here as glyph bottoms that no straight line fits: `straightness` is the
    RMS residual as a fraction of cap height, so >0.12 means warped.
    """
    if len(runs) < 4 or not cap:
        return 0.0, 0.0
    # Only glyphs that actually sit on the baseline. Descenders belong below
    # it by design, and including them makes every ordinary line look warped.
    bottoms = np.array([r[3] for r in runs], float)
    sitting = [r for r in runs
               if abs(r[3] - np.median(bottoms)) <= 0.18 * cap]
    if len(sitting) < 4:
        return 0.0, 0.0
    xs = np.array([(r[0] + r[1]) / 2 for r in sitting], float)
    ys = np.array([r[3] for r in sitting], float)
    if xs.max() - xs.min() < 4:
        return 0.0, 0.0
    m, c = np.polyfit(xs, ys, 1)
    resid = ys - (m * xs + c)
    return (float(np.degrees(np.arctan(m))),
            float(np.sqrt(np.mean(resid ** 2)) / cap))


def background_texture(a, line, mask):
    """How textured is the ground this line sits on? 0 = flat, >6 = imagery.

    Decides convertibility before anything else. Type is removed from the
    artwork by painting the background back over it, which works only if the
    background is flat enough to repaint. Over a photograph or a texture there
    is nothing to repaint with -- the pixels behind the letters are unknowable
    -- so masking leaves a scar and the poster cannot honestly be converted.

    Measured as high-frequency energy among the NON-ink pixels inside the
    line's box: the gaps between and around the letters. Two flat colours
    meeting inside the box stay low, because only the seam is busy.
    """
    from scipy import ndimage
    # Sample a margin around the line as well as the gaps inside it. On a small
    # line the dilated ink can cover its own box entirely, leaving nothing to
    # measure -- reporting "flat" for want of any background is how a textured
    # poster sneaks through as convertible.
    pad = max(6, line['cap'])
    x0, x1 = max(0, line['left'] - pad), min(a.shape[1], line['right'] + 1 + pad)
    y0, y1 = max(0, line['y0'] - pad), min(a.shape[0], line['y1'] + 1 + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0
    lum = a[y0:y1, x0:x1].mean(2)
    ink = ndimage.binary_dilation(mask[y0:y1, x0:x1], np.ones((5, 5), bool))
    bgpx = ~ink
    if bgpx.sum() < 40:
        return 0.0
    hi = np.abs(lum - ndimage.uniform_filter(lum, size=7))
    return float(np.median(hi[bgpx]))


# ------------------------------------------------------------ text bands
def in_excluded(box, exclude):
    """Is this component's centre inside one of the excluded boxes?"""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in exclude)


def looks_like_text(runs, band_h, width, density=None):
    """Text has a shared baseline and several glyph-shaped runs."""
    if len(runs) < 2:
        return False, 'too few runs'
    # Within a couple of pixels, not exactly: light type knocked out of a
    # coloured panel antialiases unevenly, and its glyph bottoms scatter by a
    # pixel either side. An exact match found 30% sharing on a line that was
    # plainly set on one baseline.
    bots = [r[3] for r in runs]
    share = max(sum(abs(b - c) <= 2 for c in bots) for b in bots) / len(runs)
    if share < 0.35:
        return False, f'no shared baseline ({share:.0%} of runs)'
    heights = [r[3] - r[2] + 1 for r in runs]
    widths = [r[1] - r[0] + 1 for r in runs]
    if np.median(widths) > 6 * np.median(heights):
        return False, 'runs too wide to be glyphs'
    # A filled panel with type knocked out of it is not a line of type: the
    # ink is the panel and the letters are the holes. It reaches here when the
    # panel is too slight to survive strip_solids -- eroding kills the whole
    # colour mask, and that case deliberately strips nothing. Ink density tells
    # them apart: lettering covers 0.38-0.63 of its line box, including heavy
    # condensed capitals, while a panel covers 0.79. (Its runs are no help --
    # they come out as the narrow slivers of colour between the letters, not as
    # wide slabs.) Rejecting it lets the knocked-out letters be found in the
    # light mask, where they belong, and leaves the panel in the artwork.
    if density is not None and density > 0.72:
        return False, f'a filled panel, not lettering ({density:.0%} ink)'
    if band_h > 0.45 * width:
        return False, 'band too tall to be a line'
    return True, f'{len(runs)} runs, {share:.0%} share a baseline'


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


def eff_width(line):
    """A line's width with its measured tracking taken out: what the copy
    would measure set solid. Tracked lines otherwise look far too wide for
    their text, and the aligner hands them longer copy."""
    return line['width'] - line.get('track', 0.0) * max(0, line.get('n_runs', 1) - 1)


def strip_marker(runs):
    """Drop a list marker -- a bullet or dash -- from the start of a line.

    The copy has no bullets, so a marker measured as the line's first glyph
    is blanked with the line and never comes back: a bulleted list loses its
    bullets. Left out of the line, it stays in the artwork where it belongs,
    and the line's left edge is its first real letter. A marker is small
    against the line's tallest glyph and stands well apart from the next: a
    round bullet measures 0.6 of the line's height, but its gap is over 1.0
    where a word space is 0.3.

    Returns (runs, marker box or None). The builder's ghost sweep is told
    about the marker, or it would take it for a stray letter and blank it.
    """
    if len(runs) < 3:
        return runs, None
    runs = sorted(runs)
    tall = max(r[3] - r[2] + 1 for r in runs)
    x0, x1, y0, y1 = runs[0]
    h, w = y1 - y0 + 1, x1 - x0 + 1
    if (h < 0.7 * tall and w < 1.6 * tall
            and runs[1][0] - x1 - 1 > 0.6 * tall):
        return runs[1:], [int(x0), int(y0), int(x1), int(y1)]
    return runs, None


def find_lines(img_path, min_glyphs=2, solid_radius=24,
               rule_length=160, rule_thick=5, contrast=40, window=61,
               wrap_cap_ratio=1.3, exclude=()):
    """`exclude` is a list of (x0, y0, x1, y1) boxes to ignore when looking for
    type. A finely drawn illustration -- an engraving, a map, a crowd of small
    marks -- breaks into hundreds of components that behave like glyphs: they
    join neighbouring bands, drag a line's left edge across the page and defeat
    the span checks, so copy lands on a label instead of on the headline. No
    measurement separates that reliably from small type, but a glance at the
    poster does, and saying "the drawing is in this box" is the same cheap
    judgement as describing the artwork with `shapes:`. Excluded regions are
    never detected as type, so `mask` artwork keeps them untouched.
    """
    a = np.array(Image.open(img_path).convert('RGB')).astype(int)
    h, w, _ = a.shape
    paper = modal_colour(a)
    raw = ink_masks(a, paper, contrast, window)

    masks, solids = {}, {}
    for colour, m in raw.items():
        m, solid = strip_solids(m, solid_radius) if solid_radius else (m, np.zeros_like(m))
        m, rules = strip_rules(m, rule_length, rule_thick) if rule_length \
            else (m, np.zeros_like(m))
        masks[colour], solids[colour] = m, solid | rules
    lines, rejected, artwork = [], [], []

    def note_art(colour, mask_, comp, kind):
        x0, y0, x1, y1 = comp['box']
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        fill = comp['area'] / max(1, bw * bh)
        sub = mask_[y0:y1 + 1, x0:x1 + 1]
        px = a[y0:y1 + 1, x0:x1 + 1][sub]
        artwork.append(dict(
            colour=colour, kind=kind, px=int(comp['area']),
            box=[int(x0), int(y0), int(x1), int(y1)],
            fill=round(float(fill), 3),
            rgb='#%02X%02X%02X' % tuple(np.median(px, 0).astype(int))
            if len(px) else '#000000'))

    # Each solid shape separately, so a builder can emit one rect or ellipse
    # per shape rather than one box round all of them.
    for c, s in solids.items():
        if s.sum() < 400:
            continue
        for comp in label(s):
            if comp['area'] > 400:
                note_art(c, s, comp, 'solid')

    for colour, mask in masks.items():
        if mask.sum() < 300:
            continue
        comps = label(mask)
        glyphs = []
        for c in comps:
            art, why = is_artwork(c, w, h)
            # An excluded box means "do not look for type here", not "nothing
            # is here": what it holds is artwork, and must stay artwork. Zeroing
            # the mask instead would also erase it from the artwork list, and
            # a shape that is no longer recorded is no longer an obstacle, so
            # a headline would run straight over the bar it should stop at.
            if not art and in_excluded(c['box'], exclude):
                art, why = True, 'inside an excluded box'
            if art:
                if c['area'] > 400:
                    note_art(colour, mask, c, why)
            else:
                glyphs.append(c)
        if not glyphs:
            continue
        for grp in group_lines(glyphs):
            runs, marker = strip_marker(
                [(c['box'][0], c['box'][2], c['box'][1], c['box'][3]) for c in grp])
            lm = line_metrics(runs)
            if not lm or len(grp) < min_glyphs:
                rejected.append(dict(colour=colour, y0=int(min(r[2] for r in runs)),
                                     y1=int(max(r[3] for r in runs)),
                                     why=f'only {len(grp)} glyph components'))
                continue
            dens = float(mask[int(min(r[2] for r in runs)):
                              int(max(r[3] for r in runs)) + 1,
                              lm['left']:lm['right'] + 1].mean())
            ok, why = looks_like_text(runs, lm['base'] - lm['capTop'] + 1, w, dens)
            # Light "type" inside dark ink is the counters of dark lettering,
            # not knocked-out type: white type in a panel sits in the panel's
            # colour, which the neutral dark mask does not include. The same
            # holds for any colour mask: where the poster's ground is itself a
            # colour -- zagreb's yellow upper half -- that colour shows between
            # small black letters and is measured as coloured "type".
            if ok and colour != 'dark':
                y0_, y1_ = int(min(r[2] for r in runs)), int(max(r[3] for r in runs))
                # Ink of any OTHER colour, not just black: the counters of red
                # letters are as much "inside lettering" as those of black
                # ones, and on penny_dreadful they outvoted a red THE FLOOD.
                other = np.zeros_like(mask)
                for c2, m2 in raw.items():
                    if c2 != colour and c2 != 'light':
                        other |= m2
                inked = float(other[y0_:y1_ + 1, lm['left']:lm['right'] + 1].mean())
                # 0.08: small black type on a coloured ground leaves its
                # counters in boxes only 10-30% inked (less when the counters'
                # box runs taller than the type), while real knocked-out type
                # sits in a coloured panel with almost no neutral ink at all.
                if inked > 0.08:
                    # Unless the dark is one mass running the width of the
                    # line: that is a neutral panel (navy, black) the type is
                    # knocked out of, which reads as "dark ink" once dense
                    # light type lifts the local average. Counters sit inside
                    # letters, each dark shape no wider than a glyph.
                    from scipy import ndimage
                    box = other[y0_:y1_ + 1, lm['left']:lm['right'] + 1]
                    lab, n = ndimage.label(box)
                    widest = max((sl[1].stop - sl[1].start
                                  for sl in ndimage.find_objects(lab)), default=0)
                    if widest < 0.6 * box.shape[1]:
                        ok, why = False, f'counters of dark lettering ({inked:.0%} dark ink)'
            if not ok:
                rejected.append(dict(colour=colour, y0=int(lm['capTop']),
                                     y1=int(lm['base']), why=why))
                # A panel is not text, but it is emphatically *something*. Record
                # it as artwork, or nothing downstream knows it is there: the
                # builder's ghost sweep looks for letter-shaped things inside
                # the text block and paints them out, and the counters of the
                # letters knocked out of the panel are exactly that shape. The
                # R, A and O of a reversed strapline lose their holes.
                if 'panel' in why:
                    note_art(colour, mask, dict(
                        box=(int(min(r[0] for r in runs)),
                             int(min(r[2] for r in runs)),
                             int(max(r[1] for r in runs)),
                             int(max(r[3] for r in runs))),
                        area=int(sum((r[1] - r[0] + 1) * (r[3] - r[2] + 1)
                                     for r in runs))), 'panel')
                continue
            y0, y1 = int(min(r[2] for r in runs)), int(max(r[3] for r in runs))
            # Sample the stem from something glyph-shaped. The tallest run can
            # be several touching letters or a rule, which gives a nonsense
            # ratio (stems wider than the cap height).
            singles = [r for r in runs
                       if 0.15 <= (r[1] - r[0] + 1) / max(1, r[3] - r[2] + 1) <= 1.3]
            tall = max(singles or runs, key=lambda r: r[3] - r[2])
            lines.append(dict(
                colour=colour,
                rgb='#%02X%02X%02X' % tuple(
                    np.median(a[y0:y1 + 1][mask[y0:y1 + 1]], 0).astype(int)),
                y0=y0, y1=y1,
                baseline=int(lm['base'] + 1), cap_top=int(lm['capTop']),
                cap=int(lm['cap']), x_height=int(lm['xh']),
                lower_frac=float(lm['lower_frac']),
                left=int(lm['left']), right=int(lm['right']),
                width=int(lm['right'] - lm['left'] + 1),
                n_runs=int(lm['nruns']),
                density=round(float(mask[y0:y1 + 1, lm['left']:lm['right'] + 1].mean()), 4),
                texture=0.0,
                slope=round(baseline_fit(runs, lm['cap'])[0], 2),
                warp=round(baseline_fit(runs, lm['cap'])[1], 3),
                stem_ratio=measure_stem(mask, *tall[:2], tall[2], tall[3]),
                runs=[[int(v) for v in r] for r in runs],
                marker=marker,
                track=tracking(runs, int(lm['cap'])),
            ))
    for ln in lines:
        ln['texture'] = round(background_texture(a, ln, masks[ln['colour']]), 2)
    lines = reading_order(dedupe(lines, rejected))
    return dict(size=[w, h], paper='#%02X%02X%02X' % tuple(paper),
                lines=lines, rejected=rejected, artwork=artwork,
                knockouts=knockout_boxes(a, paper))


def reading_order(lines, block_gap=3.0):
    """Order bands as a reader takes them: down the page, but a column at a time.

    The aligner walks copy and bands together and cannot reorder, so the bands
    must arrive in the order the copy is written. Sorting by baseline is that
    order only while the page is one column. Where two columns sit side by side
    -- a beneficiary beside the attractions list, a list set in two halves --
    their baselines interleave, and a baseline sort deals the lines of one
    column into the other.

    A column zone starts from two bands that share rows but not x: the space
    between them is a gutter. It grows up and down, band by band, for as long
    as each band keeps to one side of the gutter. It stops at a band that
    crosses the gutter (a centred label, a full-width footer) or at a gap of
    more than `block_gap` caps, which is the end of that block of type. The
    left side is read before the right, and each side is ordered the same way,
    so three columns work too.
    """
    lines = sorted(lines, key=lambda l: (l['baseline'], l['left']))

    def pair(a, b):
        """(left, right) if a and b share rows but not x, else None."""
        lo, hi = max(a['y0'], b['y0']), min(a['y1'], b['y1'])
        if hi - lo + 1 < 0.5 * min(a['y1'] - a['y0'], b['y1'] - b['y0']) + 1:
            return None
        left, right = (a, b) if a['left'] < b['left'] else (b, a)
        # A gutter is wide for its type. Two words a word space apart -- a
        # headline with FETE in an accent colour -- are one line, and reading
        # them as columns would put everything below the first word between
        # the two.
        gap = right['left'] - left['right']
        return (left, right) if gap > max(a['cap'], b['cap']) else None

    for i, a in enumerate(lines):
        for k, b in enumerate(lines[i + 1:], start=i + 1):
            p = pair(a, b)
            if not p:
                continue
            gut = [p[0]['right'], p[1]['left']]

            def fits(l):
                # Only a band with a partner across the gutter may narrow it: a
                # long item in the left column runs further right than the
                # pair that found the gutter, and its neighbour on the right
                # proves the gutter is still there. A band on its own -- a
                # centred label -- must clear the gutter as it stands, or it
                # would narrow it round itself and be swallowed by a column.
                for m in lines:
                    q = m is not l and pair(l, m)
                    if q and q[0]['right'] < gut[1] and q[1]['left'] > gut[0]:
                        gut[0] = max(gut[0], q[0]['right'])
                        gut[1] = min(gut[1], q[1]['left'])
                        return gut[0] < gut[1]
                return l['right'] <= gut[0] or l['left'] >= gut[1]

            def near(l, zone, up):
                # Close to the nearest band on its OWN side, not merely to the
                # last band in baseline order. A footer set under the left
                # column is close to the foot of the right column, and would
                # otherwise be read as the end of the left one -- before the
                # whole right column.
                mine = [z for z in zone
                        if (z['right'] <= gut[0]) == (l['right'] <= gut[0])]
                other = [z for z in zone if z not in mine]
                if not mine:
                    return False
                # A band with nothing beside it, clearly beyond the other
                # column's end -- a line height or more -- continues its own
                # column only if that column is already the longer one. Where
                # both columns end level, a line below them is the footer after
                # both, not the last line of the left. A band only a few
                # pixels past the other column is just different leading.
                partnered = any(m is not l and pair(l, m) for m in lines)
                if not partnered and other:
                    tol = 0.5 * l['cap']
                    # "A line height" at the scale of the smaller type: a
                    # giant accent word above small print is far beyond it.
                    step = min(l['cap'], min(z['cap'] for z in other))
                    if not up and l['y0'] - max(z['y1'] for z in other) > step:
                        if max(z['y1'] for z in mine) <= max(z['y1'] for z in other) + tol:
                            return False
                    if up and min(z['y0'] for z in other) - l['y1'] > step:
                        if min(z['y0'] for z in mine) >= min(z['y0'] for z in other) - tol:
                            return False
                z = (min(mine, key=lambda z: z['y0']) if up
                     else max(mine, key=lambda z: z['y1']))
                gap = z['y0'] - l['y1'] if up else l['y0'] - z['y1']
                return gap <= block_gap * max(l['cap'], z['cap'])
            if not all(fits(l) for l in lines[i + 1:k]):
                continue
            j0, j1 = i, k
            while j0 > 0:
                g = list(gut)
                if fits(lines[j0 - 1]) and near(lines[j0 - 1], lines[j0:j1 + 1], True):
                    j0 -= 1
                else:
                    gut[:] = g
                    break
            while j1 + 1 < len(lines):
                g = list(gut)
                if fits(lines[j1 + 1]) and near(lines[j1 + 1], lines[j0:j1 + 1], False):
                    j1 += 1
                else:
                    gut[:] = g
                    break
            zone = lines[j0:j1 + 1]
            return (reading_order(lines[:j0], block_gap)
                    + reading_order([l for l in zone if l['right'] <= gut[0]], block_gap)
                    + reading_order([l for l in zone if l['right'] > gut[0]], block_gap)
                    + reading_order(lines[j1 + 1:], block_gap))
    return lines


def dedupe(lines, rejected):
    """Drop bands that are another band seen through a second colour mask.

    Two masks often catch the same line -- the ink and its halo, or a colour
    that partly satisfies two tests. Keep whichever has more glyph runs, which
    is the mask that segmented the line better -- weighted by cap height, so a
    sliver of counters inside a huge word (5 runs, cap 14) cannot outvote the
    word itself (4 runs, cap 319).
    """
    def overlap(a, b, k0, k1):
        lo, hi = max(a[k0], b[k0]), min(a[k1], b[k1])
        if hi < lo:
            return 0.0
        span = min(a[k1] - a[k0], b[k1] - b[k0]) + 1
        return (hi - lo + 1) / max(1, span)

    kept = []
    for cand in sorted(lines, key=lambda l: -l['n_runs'] * l['cap']):
        dup = None
        for k in kept:
            if (overlap(cand, k, 'y0', 'y1') > 0.5
                    and overlap(cand, k, 'left', 'right') > 0.5):
                dup = k
                break
        if dup:
            rejected.append(dict(colour=cand['colour'], y0=cand['y0'], y1=cand['y1'],
                                 why=f"duplicate of {dup['colour']} band "
                                     f"y{dup['y0']}..{dup['y1']}"))
        else:
            kept.append(cand)
    return kept


# ----------------------------------------------------- copy -> band alignment
def copy_orderings(copy):
    """Plausible orders for the same copy.

    The aligner walks copy and bands together and cannot reorder, but posters
    do move one line about -- a fete commonly sets the beneficiary among the
    footer at the foot of the page rather than under the venue. Offering the
    few orderings a designer would actually use costs one more pass each and
    stops a line being dropped altogether for being in the wrong place.
    """
    yield copy
    # The presenter line is small print and posters put it almost anywhere:
    # above the headline, under it, or down among the ticket details.
    keys0 = [c['key'] for c in copy]
    if 'presenter' in keys0:
        p = keys0.index('presenter')
        rest = copy[:p] + copy[p + 1:]
        for j in range(1, len(rest) + 1):
            if j != p:
                yield rest[:j] + [copy[p]] + rest[j:]
    # A gig poster may run its small lines together at the top -- presenter,
    # then support -- and set the headliner lower down, above the date.
    keys = [c['key'] for c in copy]
    if 'headliner' in keys and 'support' in keys:
        h, s = keys.index('headliner'), keys.index('support')
        if s == h + 1:
            yield copy[:h] + [copy[s], copy[h]] + copy[s + 1:]
    movable = [i for i, c in enumerate(copy) if c['key'] == 'beneficiary']
    if not movable:
        return
    i = movable[0]
    rest = copy[:i] + copy[i + 1:]
    tail = [j for j, c in enumerate(rest) if c['key'].startswith('footer')]
    if tail:
        yield rest[:tail[0]] + [copy[i]] + rest[tail[0]:]      # before the footer
        if len(tail) > 1:
            yield rest[:tail[-1]] + [copy[i]] + rest[tail[-1]:]  # between footer lines
    yield rest + [copy[i]]                                      # last of all


def assign_copy(lines, copy, max_split=3, wrap_cap_ratio=1.3):
    """Align known copy to measured bands, allowing one copy line to wrap
    across consecutive bands (a headline usually does).

    Cost model needs no font: for a copy line of L characters set at cap height
    h, ink width should be about k*L*h, where k is width per character per cap
    height -- roughly constant for a face, whatever the size. Fit k, then pick
    the alignment minimising relative deviation.
    """
    if not lines or not copy:
        return [], 'no lines or no copy', 0.0
    n, m = len(copy), len(lines)

    def block(i, j):
        b = lines[i:j]
        return (sum(eff_width(x) for x in b), float(np.mean([x['cap'] for x in b])))

    # Display and body are usually different faces of different widths, so one
    # global k mis-costs the headline; keep one per size class. Three classes,
    # not two: a poster often sets a mid-sized line -- a date, a strapline --
    # between its headline and its small print, and lumping that in with the
    # headline fits k to display type, which is far tighter per character. The
    # mid-sized line's expected width then comes out short, the aligner decides
    # a wrapped line cannot be wrapped, and half of it is dropped.
    med_cap = float(np.median([l['cap'] for l in lines]))

    def big(idx):
        cap = lines[idx]['cap']
        return 2 if cap > 4.0 * med_cap else 1 if cap > 1.5 * med_cap else 0

    def run(k0):
        """One alignment, starting from k0 and re-fitting k three times."""
        k = {0: k0, 1: k0, 2: k0}
        out = []
        for _ in range(3):
            INF = float('inf')
            dp = [[INF] * (m + 1) for _ in range(n + 1)]
            back = [[None] * (m + 1) for _ in range(n + 1)]
            dp[0][0] = 0.0
            for ci in range(n + 1):
                for bi in range(m + 1):
                    # A band may belong to no copy line at all -- a logo, a
                    # catalogue number, a stray mark. Allow skipping one.
                    if bi and dp[ci][bi - 1] + 0.5 < dp[ci][bi]:
                        dp[ci][bi] = dp[ci][bi - 1] + 0.5
                        back[ci][bi] = ('skip', bi - 1)
                    if dp[ci][bi] == INF or ci == n:
                        continue
                    L = max(1, len(copy[ci]['text']))
                    for span in range(1, max_split + 1):
                        bj = bi + span
                        if bj > m:
                            break
                        # A line that wraps keeps its size, and resumes at the
                        # same margin. Without this a headline band and a
                        # small-print band get glued into one "line".
                        caps = [lines[i]['cap'] for i in range(bi, bj)]
                        if max(caps) > wrap_cap_ratio * min(caps):
                            break
                        # Consecutive lines of one wrapped block sit a leading
                        # apart. Without a limit, a footer will happily "wrap"
                        # onto a plate number 350px down the page because it
                        # happens to share the left margin and the cap height,
                        # stranding the last words of the sentence at the foot
                        # of the poster. Leading runs about 1.2-1.6 of cap.
                        gaps = [lines[i + 1]['baseline'] - lines[i]['baseline']
                                for i in range(bi, bj - 1)]
                        if gaps and max(gaps) > 3.0 * max(caps):
                            break
                        # A word set in an accent colour is a band of its own,
                        # on the same row as the rest of its line: VILLAGE in
                        # blue, then FETE in red, a word space apart. That is
                        # the line continuing, not wrapping, so it is exempt
                        # from the margin test below.
                        def runs_on(i):
                            p, q = lines[i - 1], lines[i]
                            return (min(p['y1'], q['y1']) - max(p['y0'], q['y0'])
                                    > 0.5 * min(p['cap'], q['cap'])
                                    and 0 < q['left'] - p['right']
                                    < 1.0 * max(p['cap'], q['cap']))
                        rows = [i for i in range(bi, bj)
                                if i == bi or not runs_on(i)]
                        # Resumes at the same margin: left, centre, or -- for a
                        # right-aligned title -- the right edge.
                        lefts = [lines[i]['left'] for i in rows]
                        mids = [(lines[i]['left'] + lines[i]['right']) / 2
                                for i in rows]
                        rights = [lines[i]['right'] for i in rows]
                        page = max(l['right'] for l in lines)
                        if (max(lefts) - min(lefts) > 0.06 * page
                                and max(mids) - min(mids) > 0.06 * page
                                and max(rights) - min(rights) > 0.03 * page):
                            break
                        W, h = block(bi, bj)
                        expect = k[big(bi)] * L * h
                        cost = abs(W - expect) / max(expect, 1.0)
                        cost += 0.05 * (span - 1)
                        if dp[ci][bi] + cost < dp[ci + 1][bj]:
                            dp[ci + 1][bj] = dp[ci][bi] + cost
                            back[ci + 1][bj] = ('use', bi, span)
                    if copy[ci].get('optional') and dp[ci][bi] + 0.6 < dp[ci + 1][bi]:
                        dp[ci + 1][bi] = dp[ci][bi] + 0.6
                        back[ci + 1][bi] = ('use', bi, 0)
            if dp[n][m] == float('inf'):
                return None, float('inf')
            out, ci, bi = [], n, m
            while ci > 0 or bi > 0:
                step = back[ci][bi]
                if step is None:
                    break
                if step[0] == 'skip':
                    bi = step[1]
                    continue
                _, prev, span = step
                out.append((ci - 1, prev, span))
                ci, bi = ci - 1, prev
            out.reverse()
            used = [(c, b, s) for c, b, s in out if s]
            for cls in (0, 1, 2):
                num = [sum(eff_width(lines[i]) for i in range(b, b + s))
                       for _, b, s in used if big(b) == cls]
                den = [len(copy[c]['text'])
                       * float(np.mean([lines[i]['cap'] for i in range(b, b + s)]))
                       for c, b, s in used if big(b) == cls]
                if num and float(np.sum(den)) > 0:
                    k[cls] = float(np.sum(num) / float(np.sum(den)))
        matched = max(1, len([1 for _, _, s in out if s]))
        return out, dp[n][m] / matched

    # Iteration only converges on the answer if it starts near it, and a single
    # greedy guess is easily wrong: one line that wraps but was given a single
    # band looks half as wide as it is, which drags the estimate between two
    # populations and mis-costs every line after it. Sweep plausible starting
    # values instead and keep whichever alignment ends up cheapest.
    # The largest type on the page is the display line: a gig poster's
    # headliner, a fête's title. Note this is *not* copy line 0 -- a gig leads
    # with "Pale Light Promotions presents:", which is some of the smallest
    # type on the poster -- so key it on the field, not the position. Without
    # this, a two-line title gives the DP two readings that cost exactly the
    # same (title over both bands, or title on one and the next copy line on
    # the other) and it breaks the tie arbitrarily, putting "Village Fête"
    # where the date should be. Prefer a solution that respects it, but do not
    # insist: a poster that really does set something else larger should still
    # align rather than fail outright.
    biggest = max(range(len(lines)), key=lambda i: lines[i]['cap'])

    big_cap = lines[biggest]['cap']

    def leads(out, variant):
        idx = next((i for i, c in enumerate(variant)
                    if c['key'] in ('headliner', 'title')), None)
        if idx is None:
            return True
        ok = False
        for c, b, s in out:
            if not s:
                continue
            if c == idx:
                ok = ok or b <= biggest < b + s
            # Display type is not merely the biggest on the page, it is
            # distinctly bigger. A band at very nearly the headline's cap is
            # the headline's second line, not the date set enormous -- which
            # is how "Village Fête" ends up labelled as the date on a poster
            # whose title runs to two lines.
            elif any(lines[i]['cap'] > 0.8 * big_cap for i in range(b, b + s)):
                return False
        return ok

    best, best_cost, best_copy = None, float('inf'), copy
    fallback, fallback_cost, fallback_copy = None, float('inf'), copy
    for variant in copy_orderings(copy):
        copy, n = variant, len(variant)
        for k0 in np.arange(0.25, 1.15, 0.05):
            out, cost = run(float(k0))
            if out is None:
                continue
            if cost < fallback_cost:
                fallback, fallback_cost, fallback_copy = out, cost, variant
            if leads(out, variant) and cost < best_cost:
                best, best_cost, best_copy = out, cost, variant
    if best is None:
        best, best_cost, best_copy = fallback, fallback_cost, fallback_copy
    copy, n = best_copy, len(best_copy)
    if best is None:
        return [], f'cannot align {len(best_copy)} copy lines to {m} bands', 0.0

    assigned = []
    for ci, bi, span in best:
        if not span:
            continue
        assigned.append(dict(key=copy[ci]['key'], text=copy[ci]['text'],
                             bands=list(range(bi, bi + span))))
    note = ('ok' if best_cost < 0.25
            else f'weak alignment (mean cost {best_cost:.2f})')
    return assigned, note, best_cost




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
                here[sl] |= near[sl]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--event', default='gig', choices=['gig', 'fete'])
    ap.add_argument('--solid-radius', type=int, default=24,
                    help='0 disables; raise if heavy display type is eaten')
    ap.add_argument('--rule-length', type=int, default=160,
                    help='0 disables rule stripping')
    ap.add_argument('--rule-thick', type=int, default=5)
    ap.add_argument('--min-glyphs', type=int, default=2)
    ap.add_argument('--contrast', type=int, default=40,
                    help='local contrast an ink pixel must clear; raise on noisy art')
    ap.add_argument('--window', type=int, default=61)
    ap.add_argument('--wrap-cap-ratio', type=float, default=1.3,
                    help='how different two bands may be in size and still be '
                         'one wrapped line')
    ap.add_argument('--exclude', action='append', default=[],
                    metavar='X0,Y0,X1,Y1',
                    help='ignore this box when looking for type; repeatable')
    a = ap.parse_args()
    m = find_lines(a.image, a.min_glyphs, a.solid_radius, a.rule_length,
                   a.rule_thick, a.contrast, a.window, a.wrap_cap_ratio,
                   [tuple(int(v) for v in x.split(',')) for x in a.exclude])
    copy = load_copy(a.event)
    assigned, note, cost = assign_copy(m['lines'], copy,
                                       wrap_cap_ratio=a.wrap_cap_ratio)
    print(f"{a.image}  {m['size'][0]}x{m['size'][1]}  paper {m['paper']}")
    print(f"{len(m['lines'])} text bands, {len(m['rejected'])} rejected, "
          f"{len(copy)} copy lines -> {note}")
    for x in assigned:
        b = [m['lines'][i] for i in x['bands']]
        print(f"  {x['key']:14} cap {b[0]['cap']:3} base {b[0]['baseline']:4} "
              f"x {b[0]['left']:4}..{b[-1]['right']:4} {b[0]['rgb']} "
              f"stem {b[0]['stem_ratio'] or 0:.3f}"
              f"{'  (+%d bands)' % (len(b) - 1) if len(b) > 1 else ''}  {x['text'][:44]}")
    unused = set(range(len(m['lines']))) - {i for x in assigned for i in x['bands']}
    for i in sorted(unused):
        l = m['lines'][i]
        print(f"  {'UNASSIGNED':14} cap {l['cap']:3} base {l['baseline']:4} "
              f"x {l['left']:4}..{l['right']:4} {l['rgb']}")


def knockout_boxes(a, paper, min_rows=40, tol=2, area=4000):
    """Paper-coloured boxes that sit IN FRONT of a coloured shape.

    A poster that holds its title in a panel knocked out of a circle gives the
    panel no colour of its own -- it is the paper showing through -- so nothing
    that looks for ink will ever find it. It is still plainly there, because
    the shape behind it stops being the shape it is: a circle's outline follows
    an arc, and where the panel covers it the outline becomes a straight line.
    That straightness is the panel's edge, and it is measurable.

    Masking cannot recover this on its own. The type sits right against the
    edge, so blanking the type eats the edge, and no fill taken from the
    surroundings knows the boundary was ever straight -- it comes back as a
    smear or, once tidied, as a soft organic curve. Knowing the rectangle lets
    the builder repaint it exactly.

    Returns [(x0, y0, x1, y1)] in image coordinates, to be painted in paper.
    """
    from scipy import ndimage
    out = []
    for m in ink_masks(a, paper).values():
        if m.sum() < area:
            continue
        lab, n = ndimage.label(m)
        for i, sl in enumerate(ndimage.find_objects(lab), start=1):
            comp = (lab[sl] == i)
            if comp.sum() < area:
                continue
            ys, xs = sl
            # Only a shape can be occluded. A letter's stem also has a dead
            # straight edge, so without a size floor every B and D on the
            # poster looks like a knockout box. Even a cap-170 capital runs to
            # about 10k pixels; the shapes this is for are several times that.
            if comp.sum() < 20000:
                continue
            rows = comp.shape[0]
            if rows < min_rows * 2:
                continue
            present = comp.any(1)
            left = np.where(present, comp.argmax(1), -1)
            right = np.where(present, comp.shape[1] - 1 - comp[:, ::-1].argmax(1), -1)
            for edge, side in ((left, 'left'), (right, 'right')):
                # A straight run only means something if straightness is
                # unusual for this shape. A circle's edge moves every row, so
                # 270 constant rows can only be something covering it; a star's
                # points and a rectangle's sides are straight everywhere, and
                # flagging those turns every gap between shapes into a panel.
                # Require the run to stand out from the shape's own habits.
                runs_len, rr = [], 0
                while rr < rows:
                    if edge[rr] < 0:
                        rr += 1
                        continue
                    ss = rr
                    while (rr + 1 < rows and edge[rr + 1] >= 0
                           and abs(int(edge[rr + 1]) - int(edge[ss])) <= tol):
                        rr += 1
                    runs_len.append(rr - ss + 1)
                    rr += 1
                runs_len.sort(reverse=True)
                second = runs_len[1] if len(runs_len) > 1 else 0
                r = 0
                while r < rows:
                    if edge[r] < 0:
                        r += 1
                        continue
                    s = r
                    while (r + 1 < rows and edge[r + 1] >= 0
                           and abs(int(edge[r + 1]) - int(edge[s])) <= tol):
                        r += 1
                    run = r - s + 1
                    # A run covering the whole component just means the shape
                    # is a rectangle, which occludes nothing.
                    if (run >= min_rows and run <= 0.8 * present.sum()
                            and run >= 3 * second):
                        ex = int(edge[s]) + xs.start
                        far = (int(left[present].min()) if side == 'left'
                               else int(right[present].max())) + xs.start
                        box = ((far, ys.start + s, ex, ys.start + r) if side == 'left'
                               else (ex, ys.start + s, far, ys.start + r))
                        # Decisive test: the region it claims to occlude must
                        # not be the shape itself. Testing for paper would be
                        # wrong -- the whole point of the panel is that it
                        # holds type -- so test for the shape's own colour
                        # instead. That throws out a straight run along a
                        # rectangle's own side, which occludes nothing.
                        if (30 <= box[2] - box[0] <= 0.5 * a.shape[1]
                                and 30 <= box[3] - box[1] <= 0.8 * a.shape[0]):
                            reg = a[box[1]:box[3] + 1, box[0]:box[2] + 1]
                            own = np.median(a[ys.start:ys.stop,
                                              xs.start:xs.stop][comp], 0)
                            if reg.size and (np.abs(reg - own).max(2)
                                             < 40).mean() < 0.20:
                                out.append(tuple(int(v) for v in box))
                    r += 1
    return out


if __name__ == '__main__':
    main()

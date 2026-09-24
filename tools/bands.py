"""Find the lines of type on a poster: components grouped into bands, each
measured (cap, baseline, stem, tracking, texture), in reading order."""
import os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse import line_metrics, modal_colour, tracking  # noqa: E402
from components import label, is_artwork, group_lines  # noqa: E402
from masks import ink_masks, strip_solids, strip_rules  # noqa: E402
from linemask import line_mask  # noqa: E402


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


def resegment(a, runs, rgb, marker=None):
    """Re-cut a detected line with a mask made from its own two colours.

    Detection masks are built to find type, not to measure it: local contrast
    in a 61px window fragments light type on a coloured panel and thins every
    stroke that sits on a mid-tone ground. Measured on mid_century's gig, the
    white date came out in 57 pieces for 31 characters with 30% of its ink
    missing, so it read as a lighter weight and was set in one.

    Once the band is found its ink colour is known, and so is the ground round
    it: cut every pixel at the midpoint between the two (linemask.line_mask).
    Components are kept only where they touch a run the detection found, so
    artwork of a similar colour in the same box is not swept in -- the runs are
    seeds, the two-colour mask decides their true extent.

    Returns (runs, mask, box, ground_hex, ink_hex), or None to keep the detection's
    measurement: no usable ground, or a result that no longer looks like a
    line of glyphs.
    """
    from scipy import ndimage
    H, W = a.shape[:2]
    x0, x1 = min(r[0] for r in runs), max(r[1] for r in runs)
    y0, y1 = min(r[2] for r in runs), max(r[3] for r in runs)
    pad = max(2, (y1 - y0 + 1) // 8)
    box = (max(0, x0 - pad), max(0, y0 - pad), min(W - 1, x1 + pad), min(H - 1, y1 + pad))
    m, ground = line_mask(a, box, rgb)
    if m is None:
        return None
    # Polarity. A dark mask run over white type on a navy panel finds the navy
    # slivers BETWEEN the letters, and the band is measured from its gaps:
    # navy "ink", widths squashed to 0.6, a handwriting face chosen to fit.
    # Letters are the minority of their line's box (0.38-0.63 even for heavy
    # condensed capitals); a ground is the majority. Say so, and let dedupe
    # prefer the band that found the letters themselves (the light mask
    # usually has them).
    #
    # Density alone is not enough: an ultra-black condensed headline fills
    # 0.7 of its own box too. The difference is what lies beyond the line. A
    # ground carries on past it -- the padding frame round the band is the
    # panel's colour -- while letters stop, and paper surrounds them.
    core = m[y0 - box[1]:y1 - box[1] + 1, x0 - box[0]:x1 - box[0] + 1]
    frame = np.ones_like(m)
    frame[y0 - box[1]:y1 - box[1] + 1, x0 - box[0]:x1 - box[0] + 1] = False
    if core.mean() > 0.5 and frame.any() and m[frame].mean() > 0.5:
        return 'ground'
    ink = rgb
    lab, n = ndimage.label(m, np.ones((3, 3), bool))
    if not n:
        return None
    seeds = set()
    for r in runs:
        sub = lab[r[2] - box[1]:r[3] - box[1] + 1, r[0] - box[0]:r[1] - box[0] + 1]
        seeds.update(np.unique(sub[sub > 0]).tolist())
    objs = ndimage.find_objects(lab)
    if marker:
        # Leave the list marker out -- but only a component that IS the marker.
        # In pixel type the two-colour mask can join a bullet to the whole line,
        # and dropping that component deletes the line.
        mx0, my0, mx1, my1 = marker
        sub = lab[max(0, my0 - box[1]):my1 - box[1] + 1, max(0, mx0 - box[0]):mx1 - box[0] + 1]
        for i in np.unique(sub[sub > 0]).tolist():
            ys, xs = objs[i - 1]
            if box[0] + xs.stop - 1 <= mx1 + 2:
                seeds.discard(i)
    if not seeds:
        return None
    keep = np.isin(lab, sorted(seeds))
    # Specks are ink but not glyphs. A distressed or mimeographed title comes
    # out of the two-colour mask as a few letters and hundreds of flecks, and
    # counting the flecks as runs moves the baseline (the most common run
    # bottom) into the middle of the letters. They stay in the mask, where
    # they count towards stroke weight. The floor scales with the line, so the
    # dot of an i in 20px type (~9px) is kept.
    h0 = y1 - y0 + 1
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    floor = max(4.0, (0.08 * h0) ** 2)
    # The two-colour mask may refine a glyph's edges by a few px, or join the
    # fragments of a light letter back into one, but it must not grow a letter
    # into artwork of the same colour that happens to touch it -- a teal V
    # running into a teal ornament moved mid_century's title margin by 32px and
    # broke its wrap. So each component must stay within the detected runs it
    # grew from, plus a small allowance.
    grow = max(3, int(0.12 * h0))
    new = []
    for i, sl in enumerate(objs, start=1):
        if i in seeds and sl is not None and sizes[i - 1] >= floor:
            ys, xs = sl
            c = (box[0] + xs.start, box[0] + xs.stop - 1,
                 box[1] + ys.start, box[1] + ys.stop - 1)
            src = [r for r in runs if not (r[1] < c[0] or r[0] > c[1]
                                           or r[3] < c[2] or r[2] > c[3])]
            if src and (c[0] < min(r[0] for r in src) - grow
                        or c[1] > max(r[1] for r in src) + grow
                        or c[2] < min(r[2] for r in src) - grow
                        or c[3] > max(r[3] for r in src) + grow):
                return None
            new.append(c)
    if not new:
        return None
    new.sort()
    # Sanity: a seed that grew into a panel or a shape is not a glyph. Fall
    # back rather than measure a line as tall as the artwork it touched.
    # ...and a re-cut that lost part of the line (ink too close to its ground
    # to separate, as with cream type on pale paper) is worse than none.
    # (Not when polarity was swapped: then the detection's extent is the
    # panel's, which runs past the letters, and the letters define the line.)
    span = max(r[1] for r in new) - min(r[0] for r in new) + 1
    if ink == rgb and span < 0.9 * (x1 - x0 + 1):
        return None
    if max(r[3] - r[2] + 1 for r in new) > 1.6 * h0 or \
            max(r[1] - r[0] + 1 for r in new) > max(0.6 * (x1 - x0 + 1), 3 * h0):
        return None
    return new, keep, box, '#%02X%02X%02X' % tuple(int(v) for v in ground), ink


def find_lines(img_path, min_glyphs=2, solid_radius=24,
               rule_length=160, rule_thick=5, contrast=40, window=61,
               wrap_cap_ratio=1.3, exclude=(), reseg=True):
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
        # A short rule -- under the length strip_rules looks for -- is still
        # no glyph: no letter is that thin and several times the height of its
        # neighbours. Left in, a rule between two list columns overlaps two
        # rows and fills the gutter, and glues four items into one band (the
        # round trip's two-column lists). It stays artwork.
        if glyphs:
            hs = sorted(c['box'][3] - c['box'][1] + 1 for c in glyphs)
            med_h = hs[len(hs) // 2]
            keep = []

            def flanked(c, bh):
                # A column rule stands in a gutter: smaller type on BOTH sides
                # of it, within its height. A headline letter's neighbours are
                # its own height; a mark at the page edge has nothing beside it.
                def side(sign):
                    return sum(1 for o in glyphs if o is not c
                               and (o['box'][3] - o['box'][1] + 1) < 0.6 * bh
                               and c['box'][1] <= (o['box'][1] + o['box'][3]) / 2 <= c['box'][3]
                               and sign * ((o['box'][0] + o['box'][2]) / 2
                                           - (c['box'][0] + c['box'][2]) / 2) > 0)
                return side(-1) >= 2 and side(1) >= 2

            def on_paper(c):
                # ...and it is drawn ON the paper: just either side of it is
                # paper. The black gap between two reversed-out letters is
                # thin and tall too, but it has letters beside it, not paper;
                # a mark at the page's edge has nothing on one side at all.
                x0, y0, x1, y1 = c['box']
                if x0 < 4 or x1 > w - 5:
                    return False
                cols = np.concatenate([a[y0:y1 + 1, x0 - 3], a[y0:y1 + 1, x1 + 3]])
                return float((np.abs(cols - np.array(paper)).max(1) < 40).mean()) > 0.6
            for c in glyphs:
                bw = c['box'][2] - c['box'][0] + 1
                bh = c['box'][3] - c['box'][1] + 1
                if bh > 2.5 * med_h and bw < 0.12 * bh and flanked(c, bh) and on_paper(c):
                    if c['area'] > 100:
                        note_art(colour, mask, c, 'rule')
                else:
                    keep.append(c)
            glyphs = keep
        if not glyphs:
            continue
        for grp in group_lines(glyphs):
            runs, marker = strip_marker(
                [(c['box'][0], c['box'][2], c['box'][1], c['box'][3]) for c in grp])
            lm = line_metrics(runs)
            # Count what is left once a list marker is set aside: a marker and
            # two specks is not three glyphs.
            if not lm or len(runs) < min_glyphs:
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
            suspect = False
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
                    # Unless the dark band here has already been shown to be a
                    # ground -- its "ink" filling most of its own line -- in
                    # which case these light shapes are letters knocked out of
                    # it, not holes in it. White type on a navy panel and the
                    # counters of navy type are mirror images; of the pair,
                    # exactly one is the ground.
                    ground_here = any(
                        l.get('is_ground') and l['colour'] == 'dark'
                        and min(l['y1'], y1_) - max(l['y0'], y0_) > 0.5 * (y1_ - y0_)
                        and min(l['right'], lm['right']) - max(l['left'], lm['left'])
                        > 0.5 * (lm['right'] - lm['left'])
                        for l in lines)
                    if widest < 0.6 * box.shape[1] and not ground_here:
                        # Only a suspect: counters are holes INSIDE a line of
                        # other-coloured lettering, so whether there is one is
                        # decided once every colour has been read (below). A
                        # red panel with white type knocked out of it looks
                        # the same here, and has no red lettering over it.
                        suspect = f'counters of dark lettering ({inked:.0%} dark ink)'
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
            rgb = '#%02X%02X%02X' % tuple(np.median(a[y0:y1 + 1][mask[y0:y1 + 1]], 0).astype(int))
            # Measure with the line's own two colours, not the detection mask.
            mmask, mbox, ground = mask, (0, 0), None
            seg = resegment(a, runs, rgb, marker) if reseg else None
            is_ground = seg == 'ground'
            if is_ground:
                seg = None
            if seg:
                runs, mmask, box_, ground, rgb = seg
                mbox = (box_[0], box_[1])
                lm = line_metrics(runs) or lm
                y0, y1 = int(min(r[2] for r in runs)), int(max(r[3] for r in runs))
            ox, oy = mbox

            def region(yy0, yy1, xx0, xx1):
                return mmask[max(0, yy0 - oy):yy1 - oy + 1, max(0, xx0 - ox):xx1 - ox + 1]
            # Sample the stem from something glyph-shaped. The tallest run can
            # be several touching letters or a rule, which gives a nonsense
            # ratio (stems wider than the cap height).
            singles = [r for r in runs
                       if 0.15 <= (r[1] - r[0] + 1) / max(1, r[3] - r[2] + 1) <= 1.3]
            tall = max(singles or runs, key=lambda r: r[3] - r[2])
            lines.append(dict(
                colour=colour,
                rgb=rgb, ground=ground, is_ground=is_ground,
                y0=y0, y1=y1,
                baseline=int(lm['base'] + 1), cap_top=int(lm['capTop']),
                cap=int(lm['cap']), x_height=int(lm['xh']),
                lower_frac=float(lm['lower_frac']),
                left=int(lm['left']), right=int(lm['right']),
                width=int(lm['right'] - lm['left'] + 1),
                n_runs=int(lm['nruns']),
                density=round(float(region(y0, y1, lm['left'], lm['right']).mean()), 4),
                texture=0.0,
                slope=round(baseline_fit(runs, lm['cap'])[0], 2),
                warp=round(baseline_fit(runs, lm['cap'])[1], 3),
                stem_ratio=measure_stem(mmask, tall[0] - ox, tall[1] - ox,
                                        tall[2] - oy, tall[3] - oy),
                runs=[[int(v) for v in r] for r in runs],
                marker=marker,
                track=tracking(runs, int(lm['cap'])),
                counter_suspect=suspect,
            ))
    for ln in lines:
        ln['texture'] = round(background_texture(a, ln, masks[ln['colour']]), 2)
    lines = settle_counter_suspects(lines, rejected)
    lines = reading_order(dedupe(lines, rejected))
    return dict(size=[w, h], paper='#%02X%02X%02X' % tuple(paper),
                lines=lines, rejected=rejected, artwork=artwork,
                knockouts=knockout_boxes(a, paper))


def settle_counter_suspects(lines, rejected):
    """Reject a light band as the counters of dark lettering only where a
    line of other-coloured lettering actually lies over it.

    Found by the round trip: the first line of a venue reversed out of a red
    panel was rejected as "counters" -- its box full of red, the red in slivers
    between the letters -- and the copy shuffled to fill the gap. The counters
    case always has the lettering itself detected as a band; a panel is not.
    """
    kept = []
    for l in lines:
        why = l.pop('counter_suspect', False)
        if not why:
            kept.append(l)
            continue
        h, w = l['y1'] - l['y0'] + 1, l['right'] - l['left'] + 1
        # ...and only a band shaped like one line of type survives: the rule
        # had also been turning away coloured artwork (a 380px swoosh with a
        # "cap" of 94 on contemporary_flat_illustration's gig).
        # And only on a flat ground: type knocked out of a panel sits on a
        # plain colour, while light specks in a photograph or an engraving
        # (a microphone's highlights) sit on texture.
        if h > 1.6 * l['cap'] or l['n_runs'] < 4 or l.get('texture', 0) > 4.0:
            rejected.append(dict(colour=l['colour'], y0=l['cap_top'], y1=l['baseline'], why=why))
            continue
        over = any(
            o is not l and o['colour'] != l['colour'] and not o.get('is_ground')
            and not o.get('counter_suspect')
            and min(o['y1'], l['y1']) - max(o['y0'], l['y0']) > 0.5 * h
            and min(o['right'], l['right']) - max(o['left'], l['left']) > 0.5 * w
            for o in lines)
        if over:
            rejected.append(dict(colour=l['colour'], y0=l['cap_top'], y1=l['baseline'], why=why))
        else:
            kept.append(l)
    return kept


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
    # A band whose "ink" is really the ground between letters (see resegment)
    # loses to any band that found the letters, however many runs it has.
    # ...and a band much taller than its own cap height is not one line of
    # type but a panel or a stack (a slanted panel narrower than the page is
    # not classed as artwork, and would otherwise outvote its own letters).
    # Only against a band of another colour -- a panel and the letters
    # knocked out of it; a tall band of the type's own colour may be two
    # sizes of one line merged (risograph's "with support from / Mena Grace"),
    # and dropping it loses the smaller.
    def tall(l):
        # a panel is mostly ink; two merged lines of type are not
        if (l['y1'] - l['y0'] + 1) <= 1.8 * max(1, l['cap']) or l.get('density', 0) < 0.5:
            return False
        return any(o is not l and o['colour'] != l['colour']
                   and overlap(o, l, 'y0', 'y1') > 0.5 and overlap(o, l, 'left', 'right') > 0.5
                   and (o['y1'] - o['y0'] + 1) <= 1.8 * max(1, o['cap'])
                   for o in lines)
    for cand in sorted(lines, key=lambda l: (l.get('is_ground', False), tall(l),
                                             -l['n_runs'] * l['cap'])):
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


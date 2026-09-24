#!/usr/bin/env python3
"""Work out how a poster's type is set, and write it down as solution.json.

    python3 solve_type.py poster.webp --event gig -o solution.json

Turns the judgement in SKILL.md into arithmetic:

  * measure the bitmap and attach the known copy   (measure.py)
  * group lines that plausibly share a face
  * Stage 1: score every face in the index by width, x-height and stem weight
  * Stage 2: rasterise the finalists and compare letterform SHAPE, per glyph
  * classify any width disagreement as squash / compressed cut / squeeze-to-fit
  * report confidence, and list what a human still needs to decide

The LLM should only have to read the summary and adjudicate the ambiguities.
"""
import collections, re, argparse, json, os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bands import find_lines, eff_width  # noqa: E402
from copytext import load_copy  # noqa: E402
from align import assign_copy, apply_assign  # noqa: E402
from svgkit import Face, render_glyph, top_ratio  # noqa: E402
from shapescore import chamfer  # noqa: E402
from typefeatures import features, distance, weight as ridge_weight  # noqa: E402
from linemask import line_mask  # noqa: E402
from tilt import pixels  # noqa: E402
import catalogue  # noqa: E402
from character import parse as parse_brief, mismatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# Glyphs worth comparing by shape: the ones that most separate families.
DISCRIMINATING = 'RGQaegSWM&2'
# How many styles go on to be fetched and compared by shape.
SHAPE_POOL = 80
# How much the feature distance counts against the width fit in stage 1.
FEATURE_WEIGHT = 0.5
# How a character brief counts: a gate, not a referee. Below CHARACTER_FREE
# (the family is roughly what the brief says) it costs nothing, and shape and
# metrics decide between near neighbours, which Google's tags cannot. Past
# it the cost climbs steeply: a face of the wrong kind -- a sans for a
# playbill wood type, a comic face for a sober grotesque -- is wrong however
# well its widths and weight fit.
CHARACTER_FREE = 0.3
# How much the ink comparison in stage 2 counts: |log(fill ratio)| of 0.2 is
# a weight step (400 against 600) on most faces.
INK_WEIGHT = 3.0
CHARACTER_WEIGHT = 8.0


def load_faces():
    """Every style in the catalogue, in the shape the ranking stages use."""
    out = []
    for r in catalogue.load():
        x = r['metrics'].get('x')
        sub = catalogue.label(r)[len(r['family']) + 1:] if r['source'] == 'google' \
            else r.get('sub', '')
        out.append(dict(family=r['family'], subfamily=sub, weight=r['weight'],
                        embed=r['embed'], path=r.get('path'), index=r.get('index'),
                        cap_ratio=r['cap'] / r['upem'],
                        x_ratio=(x[4] / r['upem']) if x else None,
                        missing=r['missing'], record=r))
    return out


def group_lines_by_face(assigned, lines, arr=None):
    """Split logical lines into sets that plausibly share one face.

    First by size, in three tiers. A poster often sets something mid-sized --
    a date, a strapline -- between its headline and its small print, and a
    two-way split files that with the headline. The group's width ratio is then
    the median of two lines at a 4:1 size ratio, which reads as a good fit
    while neither line fits: on bauhaus_modernist's fete the title wanted 0.65
    and the date 1.29, the pair averaged 0.97, and the face chosen on that set
    the title half as wide again as the poster did.

    Then, within a tier, by what the type itself is: its slant and its stroke
    weight, measured per line on the line's own two-colour mask (so a white
    line on a panel measures like a black one). An italic beneficiary set
    among upright small print is a third face; averaged in, its 10 degrees made
    the whole group read as 5 degrees and an upright serif lost to a sans. Ink
    density, which this replaces, split mid_century's white date from its
    white venue because the detection mask had thinned it.
    """
    caps = [lines[a['bands'][0]]['cap'] for a in assigned]
    med = float(np.median(caps))
    tiers = {}
    for a in assigned:
        cap = lines[a['bands'][0]]['cap']
        kind = ('display' if cap > 4.0 * med
                else 'subhead' if cap > 1.8 * med else 'body')
        tiers.setdefault(kind, []).append(a)
    if arr is None:
        return tiers

    groups = {}
    for tier, members in tiers.items():
        clusters = []          # [name, slant, weight, [lines]]
        for a in members:
            sl, wt = line_profile(a, lines, arr)
            home = None
            for c in clusters:
                slant_ok = sl is None or c[1] is None or abs(sl - c[1]) <= 5.0
                weight_ok = (wt is None or c[2] is None
                             or max(wt, c[2]) / max(1e-6, min(wt, c[2])) <= 1.4)
                if slant_ok and weight_ok:
                    home = c
                    break
            if home is None:
                name = tier
                if clusters:
                    first = clusters[0]
                    if sl is not None and first[1] is not None and abs(sl - first[1]) > 5:
                        name = f'{tier}_italic'
                    elif wt and first[2]:
                        name = f'{tier}_heavy' if wt > first[2] else f'{tier}_light'
                    while name in [c[0] for c in clusters]:
                        name += '2'
                clusters.append([name, sl, wt, [a]])
            else:
                home[3].append(a)
                if sl is not None:
                    home[1] = sl if home[1] is None else (home[1] + sl) / 2
                if wt is not None:
                    home[2] = wt if home[2] is None else (home[2] + wt) / 2
        for name, _, _, ms in clusters:
            groups[name] = ms
    _keep_siblings_together(groups, assigned)
    return groups


def line_profile(a, lines, arr):
    """(slant in degrees, stroke weight / cap) of one copy line, or None for
    what could not be measured. Slant from the upright-stemmed glyphs the
    segmenter can attribute; weight from the whole line's two-colour mask."""
    from glyphs import segment
    from typefeatures import slant, STEMMED
    parts = a.get('parts') or split_text(a['text'], a['bands'], lines) or [a['text']]
    slants, weights = [], []
    for bi, part in zip(a['bands'], parts):
        b = lines[bi]
        for ch, m in segment(arr, b, part):
            if ch in STEMMED:
                v = slant(m)
                if v is not None:
                    slants.append(v)
        m, _ = line_mask(pixels(arr, b), (b['left'], b['y0'], b['right'], b['y1']), b['rgb'], b.get('ground'))
        if m is not None:
            w = ridge_weight(m, b['cap'])
            if w:
                weights.append(w)
    return (float(np.median(slants)) if len(slants) >= 2 else None,
            float(np.median(weights)) if weights else None)


def _keep_siblings_together(groups, assigned):
    """A list is set in one face, so keep sibling copy lines in one group.

    The density split measures ink per unit area, which depends on the words as
    much as on the type: "BBQ and refreshments" is denser than "Ferret racing"
    in the same face, at the same size. Left alone, that puts half a fête's
    attractions in one group and half in another, and they come out set in two
    different faces down a single list. Items sharing a stem -- attraction0..7,
    footer0..1 -- are one list by construction, so move each family wholesale
    to wherever most of it landed.
    """
    order = {a['key']: i for i, a in enumerate(assigned)}
    where = {a['key']: g for g, items in groups.items() for a in items}
    fam = collections.defaultdict(list)
    for key in where:
        stem = re.sub(r'\d+$', '', key)
        if stem != key:
            fam[stem].append(key)
    for keys in fam.values():
        counts = collections.Counter(where[k] for k in keys)
        if len(keys) < 2 or len(counts) < 2:
            continue
        winner = counts.most_common(1)[0][0]
        for k in keys:
            if where[k] == winner:
                continue
            a = next(x for x in groups[where[k]] if x['key'] == k)
            groups[where[k]].remove(a)
            groups[winner].append(a)
            where[k] = winner
    for g in list(groups):
        if groups[g]:
            groups[g].sort(key=lambda a: order[a['key']])
        else:
            del groups[g]


def _shortlist(cands, n=8, extra=4):
    """The candidates worth writing down: best by score, plus the closest
    width matches. The two are not the same list, and a face that sets the
    line at the right width has to be visible to whoever adjudicates -- there
    is no use carrying it through stage 1 only to drop it here."""
    out = list(cands[:n])
    seen = {id(c) for c in out}
    for c in sorted(cands, key=lambda c: abs(np.log(max(c['width_ratio'], 1e-6)))):
        if len(out) >= n + extra:
            break
        if id(c) not in seen:
            out.append(c)
            seen.add(id(c))
    return out


# --------------------------------------------------------------- stage one
def stage1(group, lines, faces, feats=None, keep=SHAPE_POOL, brief=None):
    """Arithmetic scoring of every style against a group of lines.

    Width fit, from each style's stored metrics (no font is loaded), plus the
    distance between the poster's measured features and the style's: stroke
    weight, contrast, slant, per-character proportions. Width alone is weak,
    since scaling can fake it; the features are what scaling cannot.
    """
    text = ''.join(a['text'] for a in group)
    need = set(text) - {' '}
    out = []
    for f in faces:
        if set(f['missing']) & need:
            continue
        face = catalogue.RecordFace(f['record'])
        ratios, xerr = [], []
        for a in group:
            b = lines[a['bands'][0]]
            size = b['cap'] / top_ratio(face, a['text'])
            # Compare per visual line. Measuring the wrapped whole against the
            # unwrapped string counts the spaces that became line breaks, which
            # makes every candidate look too wide.
            parts = a.get('parts') or split_text(a['text'], a['bands'], lines)
            if parts and len(parts) == len(a['bands']):
                natural = sum(face.width(p, lines[i]['cap'] / top_ratio(face, p))
                              for p, i in zip(parts, a['bands']))
            else:
                natural = face.width(a['text'], size)
            measured = sum(eff_width(lines[i]) for i in a['bands'])
            if not natural:
                continue
            ratios.append(measured / natural)
            if f['x_ratio'] and b['x_height'] and b.get('lower_frac', 0) > 0.3:
                xerr.append(abs(f['x_ratio'] * size - b['x_height']) / b['x_height'])
        if not ratios:
            continue
        med = float(np.median(ratios))
        spread = float(np.std(ratios))
        fd, parts_ = (distance(feats, f['record']['features'])
                      if feats else (0.0, {}))
        # System faces carry no tags and never ship; keep them neutral.
        tags = f['record'].get('tags', {})
        ch = (0.25 if f['record']['source'] == 'system'
              else mismatch(brief, tags)) if brief else 0.0
        # Without a brief saying otherwise, a monospace face is an unlikely
        # answer: posters set display and text type proportionally, and a
        # monospace capital line can come out level with a condensed one on
        # shape alone (wpa_new_deal's title went to Geist Mono). A brief that
        # names Monospace -- a typewriter style -- lifts this.
        # A word space over 0.45em marks a monospaced Latin too, tagged or not
        # (the BIZ UD faces: surrealist's headline came out with gaping spaces).
        sp = f['record']['metrics'].get(' ')
        mono = '/Monospace/Monospace' in tags or (sp and sp[0] / f['record']['upem'] > 0.45)
        if mono and not (brief and '/Monospace/Monospace' in brief):
            ch_prior = 0.3
        else:
            ch_prior = 0.0
        score = (abs(np.log(med)) * 0.8
                 + spread * 1.8
                 + (float(np.mean(xerr)) * 1.2 if xerr else 0.0)
                 + FEATURE_WEIGHT * fd
                 + CHARACTER_WEIGHT * max(0.0, ch - CHARACTER_FREE) + ch_prior)
        out.append(dict(face=f, width_ratio=round(med, 4), spread=round(spread, 4),
                        x_err=round(float(np.mean(xerr)), 4) if xerr else None,
                        features=round(fd, 3), feature_parts=parts_,
                        character=round(ch, 3) if brief else None,
                        stem_err=round(parts_['weight'], 4) if 'weight' in parts_ else None,
                        metric_score=round(float(score), 4),
                        score=round(float(score), 4)))
    out.sort(key=lambda r: r['score'])
    # A shortlist of forty Oswalds says nothing. At most two styles a family,
    # so the shape stage sees forty different ideas of what the face is.
    keepers, per = [], collections.Counter()
    for r in out:
        if per[r['face']['family']] < 2:
            keepers.append(r)
            per[r['face']['family']] += 1
        if len(keepers) >= keep:
            break
    # Always carry the closest width matches through, even if the combined
    # score buried them: on bauhaus_modernist's fete title, League Gothic
    # matched the measured width to 1% and still missed the shortlist. Shape
    # scoring decides between them afterwards; this only decides what gets to
    # be looked at.
    seen = {id(r) for r in keepers}
    by_width = sorted(out, key=lambda r: abs(np.log(max(r['width_ratio'], 1e-6))))
    for r in by_width[:6]:
        if id(r) not in seen:
            keepers.append(r)
    return keepers


def fetch_files(cands):
    """Give each shortlisted style a real font file: system faces have one,
    Google styles are fetched as a subset of the copy's characters (cached)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(c):
        try:
            path, index = catalogue.face_file(c['face']['record'])
            return dict(c, face=dict(c['face'], path=path, index=index))
        except Exception:
            return None
    with ThreadPoolExecutor(16) as ex:
        return [c for c in ex.map(one, cands) if c]


def group_features(group, lines, arr, crops):
    """The poster's features for one face group, from its glyph crops; stroke
    weight falls back to the whole lines' two-colour masks when no glyph could
    be cut out, so weight is never missing."""
    feats = features(crops) if crops else features([])
    if feats['weight'] is None:
        ws = []
        for a in group:
            for bi in a['bands']:
                b = lines[bi]
                m, _ = line_mask(pixels(arr, b), (b['left'], b['y0'], b['right'], b['y1']),
                                 b['rgb'], b.get('ground'))
                if m is not None:
                    w = ridge_weight(m, b['cap'])
                    if w:
                        ws.append(w)
        feats['weight'] = float(np.median(ws)) if ws else None
    return feats


# --------------------------------------------------------------- stage two
def split_text(text, bands, lines):
    """Divide one logical line's text across the bands it wraps onto.

    Splits only at word boundaries -- that is where lines wrap -- and picks the
    division whose character counts best match the bands' relative widths. The
    build step needs this too: a wrapped headline is several <text> elements at
    several baselines, not one.
    """
    if len(bands) == 1:
        return [text]
    words = text.split(' ')
    n = len(bands)
    if len(words) < n:
        return None
    widths = np.array([eff_width(lines[b]) for b in bands], float)
    target = widths / widths.sum()

    best, best_cost = None, 1e9
    def walk(start, parts):
        nonlocal best, best_cost
        if len(parts) == n - 1:
            rest = words[start:]
            if not rest:
                return
            cand = parts + [' '.join(rest)]
            lens = np.array([len(p.replace(' ', '')) for p in cand], float)
            if lens.min() < 1:
                return
            cost = float(np.abs(lens / lens.sum() - target).sum())
            if cost < best_cost:
                best, best_cost = cand, cost
            return
        for end in range(start + 1, len(words) - (n - len(parts) - 1) + 1):
            walk(end, parts + [' '.join(words[start:end])])
    walk(0, [])
    return best


def glyph_crops(group, lines, arr, limit=10):
    """(char, ink crop) pairs from the ORIGINAL, for shape comparison.

    glyphs.segment aligns each band's ink runs to its characters, so a line
    still yields crops when an i has a dot, a colon two parts or two letters
    touch -- the old one-run-per-character rule discarded nearly every line.
    Crops come from the line's own two-colour mask, so white type on a panel
    is cut as cleanly as black on paper.
    """
    from glyphs import segment
    crops = []
    for a in group:
        parts = a.get('parts') or split_text(a['text'], a['bands'], lines)
        if not parts:
            continue
        for bi, part in zip(a['bands'], parts):
            seg = segment(arr, lines[bi], part)
            # Proportions are per cap height. A mixed-case band's measured
            # "cap" is its ascender line, a few percent taller, so use the
            # line's own flat capitals where it has any.
            flat = [c.shape[0] for ch, c in seg if ch in 'BDEFHIKLMNPRTUVWXYZ']
            cap = float(np.median(flat)) if flat else lines[bi]['cap']
            for ch, crop in seg:
                if ch.isalnum() and crop.shape[0] > 8:
                    crops.append((ch, crop, cap))
    # Prefer the glyphs that separate families, but take whatever the poster
    # offers: insisting on a short list leaves lines with one usable glyph, and
    # one glyph cannot choose a typeface.
    crops.sort(key=lambda t: (DISCRIMINATING.find(t[0]) < 0,
                              -(t[1].shape[0] * t[1].shape[1])))
    seen, out = set(), []
    for ch, c, cap in crops:
        if ch not in seen:
            seen.add(ch)
            out.append((ch, c, cap))
        if len(out) >= limit:
            break
    return out


def stage2(cands, crops):
    """Chamfer-compare letterforms, then rescore with shape dominant.

    Every surviving candidate must be scored, not just the leaders: shape can
    only ever add to a score, so scoring a subset pushes exactly the compared
    faces down the list and lets uncompared ones win by default.
    """
    if not crops:
        return cands
    for c in cands:
        face = Face(c['face']['path'], index=c['face']['index'])
        scores, inks = [], []
        for ch, crop, _ in crops:
            if not face.has(ch):
                continue
            # Render the glyph at the crop's own height, so shape AND weight
            # are compared at the poster's resolution. The catalogue's
            # features are measured at a 120px cap; on 20px type a 1.6px
            # stroke measures 2px there and every small line reads a weight
            # too heavy. Here both sides are equally coarse.
            x0, y0, x1, y1 = face.bounds(ch)
            if y1 <= y0:
                continue
            g = render_glyph(face, ch, crop.shape[0] * face.upem / (y1 - y0))
            if g is None:
                continue
            g = g > 128
            ys, xs = np.nonzero(g)
            if not len(xs):
                continue
            g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            scores.append(chamfer(crop, g))
            fc, fg = crop.mean(), g.mean()
            if fc > 0 and fg > 0:
                inks.append(abs(np.log(fc / fg)))
        if scores:
            c['shape'] = round(float(np.mean(scores)), 4)
        if inks:
            c['ink'] = round(float(np.mean(inks)), 4)
    seen = [c['shape'] for c in cands if c.get('shape') is not None]
    fallback = (float(np.mean(seen)) + 0.5) if seen else 3.0
    for c in cands:
        # Letterform first, then agreement across lines, then natural width --
        # width alone is the weakest signal because scaling can fake it.
        # Ink: how much of its box each glyph fills, crop against render at
        # the same size -- stroke weight at native resolution.
        c['score'] = round(c.get('shape', fallback) * 1.0
                           + INK_WEIGHT * c.get('ink', 0.3)
                           + c['metric_score'], 4)
    cands.sort(key=lambda r: r['score'])
    return cands


# ------------------------------------------------------------- distortion
def classify(group, lines, best, feats=None):
    """Name any width disagreement, using SKILL.md's three rules."""
    notes = []
    f = best['face']
    for a in group:
        b = lines[a['bands'][0]]
        face = Face(f['path'], index=f['index'])
        size = b['cap'] / top_ratio(face, a['text'])
        measured = sum(eff_width(lines[i]) for i in a['bands'])
        natural = face.width(a['text'], size)
        ratio = measured / natural if natural else 1.0
        if abs(ratio - 1) < 0.06:
            continue
        # A face squashed to fit loses stroke weight with its width; one the
        # generator merely set smaller keeps it. Compare the poster's measured
        # ridge weight with the face's own.
        fw = (f.get('record') or {}).get('features', {}).get('weight')
        pw = (feats or {}).get('weight')
        stem_ok = bool(fw and pw and abs(pw - fw) / fw < 0.18)
        if ratio < 0.94 and stem_ok:
            kind, act = 'squeezed_to_fit', 'resize this line so it fits naturally'
        elif ratio < 0.94:
            kind, act = 'horizontal_squash', 'do NOT reproduce; set at natural width'
        else:
            kind, act = 'wider_than_face', 'do NOT stretch; let the line run short'
        notes.append(dict(key=a['key'], ratio=round(ratio, 3), kind=kind,
                          stem_matches=bool(stem_ok), action=act))
    return notes


def confidence(cands, n_glyphs=None):  # noqa
    """How far the winner leads. Never better than 'low' when fewer than three
    glyphs were compared by shape: the ranking is then width and stem alone,
    which a comic face with the right proportions wins as easily as the right
    face -- Bangers came top of nine posters that way, reported 'high'."""
    if len(cands) < 2:
        return 'low', 0.0
    if n_glyphs is not None and n_glyphs < 3:
        margin = cands[1]['score'] - cands[0]['score']
        return 'low', round(margin / max(cands[0]['score'], 0.05), 3)
    margin = cands[1]['score'] - cands[0]['score']
    rel = margin / max(cands[0]['score'], 0.05)
    if cands[0]['score'] < 0.55 and rel > 0.25:
        return 'high', round(rel, 3)
    if cands[0]['score'] < 1.1 and rel > 0.10:
        return 'medium', round(rel, 3)
    return 'low', round(rel, 3)


# ------------------------------------------------------------------- main
def parse_tilt(spec):
    from tilt import parse
    return parse(spec)


def measure_tilted(image, paper, box, angle, copy, wrap_cap_ratio, measure_opts):
    """Find and assign the copy set on a slant inside `box`.

    The poster is rotated level about the box's centre and measured as if it
    were its own poster, with everything outside the (rotated) box excluded;
    the lines come back in that levelled frame, tagged with it.
    """
    import tempfile
    from tilt import estimate, level, to_poster
    a = np.array(Image.open(image).convert('RGB')).astype(int)
    pap = tuple(int(paper[i:i + 2], 16) for i in (1, 3, 5))
    angle = estimate(a, box, pap, around=angle)
    frame = dict(angle=float(angle), cx=(box[0] + box[2]) / 2, cy=(box[1] + box[3]) / 2)
    lev = level(a, frame, fill=pap)
    # the box's corners in the levelled frame: to_poster's inverse is the same
    # rotation the other way
    inv = dict(frame, angle=-frame['angle'])
    xs, ys = zip(*(to_poster(x, y, inv) for x, y in
                   ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))))
    bx0, by0, bx1, by1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    H, W = lev.shape[:2]
    outside = [(0, 0, W, by0 - 1), (0, by1 + 1, W, H), (0, 0, bx0 - 1, H), (bx1 + 1, 0, W, H)]
    opts = dict(measure_opts)
    opts['exclude'] = outside
    with tempfile.NamedTemporaryFile(suffix='.png') as t:
        Image.fromarray(lev.clip(0, 255).astype('uint8')).save(t.name)
        sm = find_lines(t.name, **opts)
    for ln in sm['lines']:
        ln['frame'] = frame
    return sm['lines'], cover(sm['lines'], copy), frame


def cover(lines, copy, skip=0.6, miss=1.5):
    """Assign `copy` to `lines`, in order, each copy line taking consecutive
    bands of one size (a wrapped line keeps its size). For a box someone said
    holds exactly these lines: a band is skipped only at a cost (a fragment of
    a bar or a speck in the box), a copy line goes unplaced only at a larger
    one, and the lines' widths per character should agree. A general aligner
    with two lines to place has too little to fit its constants on."""
    n, m = len(copy), len(lines)
    if not n or not m:
        return []
    chars = [max(1, len(c['text'].replace(' ', ''))) for c in copy]

    def k(i, j, c):
        w = sum(eff_width(lines[b]) for b in range(i, j))
        cap = float(np.mean([lines[b]['cap'] for b in range(i, j)]))
        return w / chars[c] / max(1, cap)
    # candidate spans: consecutive bands of similar cap
    spans = [(i, j) for i in range(m) for j in range(i + 1, min(m, i + 4) + 1)
             if max(lines[b]['cap'] for b in range(i, j))
             <= 1.3 * min(lines[b]['cap'] for b in range(i, j))]
    best = (float('inf'), None)
    # small enough to enumerate: choose for each copy line a span or nothing,
    # spans in order and disjoint
    def walk(c, pos, chosen):
        nonlocal best
        if c == n:
            used = sum(j - i for i, j in (x for x in chosen if x))
            ks = [k(i, j, ci) for ci, x in enumerate(chosen) if x for i, j in [x]]
            spread = float(np.std(ks) / max(1e-6, np.mean(ks))) if len(ks) > 1 else 0.0
            cost = spread + skip * (m - used) + miss * sum(1 for x in chosen if not x)
            if cost < best[0]:
                best = (cost, list(chosen))
            return
        walk(c + 1, pos, chosen + [None])
        for i, j in spans:
            if i >= pos:
                walk(c + 1, j, chosen + [(i, j)])
    walk(0, 0, [])
    return [dict(key=c['key'], text=c['text'], bands=list(range(x[0], x[1])))
            for c, x in zip(copy, best[1]) if x]


def briefs_for(kind, grp, character):
    """The character brief for a face group: given by group name, or by any
    copy line in it (the headliner's brief is the display group's)."""
    if not character:
        return None
    keys = [kind] + [a['key'] for a in grp]
    specs = [v for k, v in character.items() if k in keys]
    return parse_brief(', '.join(specs)) if specs else None


def solve(image, event, keep=SHAPE_POOL, wrap_cap_ratio=1.3, assign=None,
          character=None, case=None, tilted=None, **measure_opts):
    copy = load_copy(event)
    specs = [parse_tilt(t) for t in (tilted or [])]
    tilted_keys = {k for keys, _, _ in specs for k in keys}
    m = find_lines(image, **measure_opts)
    frames, tilted_found = [], []
    for keys, box, angle in specs:
        tilted_found.append((keys, box) + measure_tilted(
            image, m['paper'], box, angle, [c for c in copy if c['key'] in keys],
            wrap_cap_ratio, measure_opts))
    # The main measurement sees the slanted type as fragments of level bands.
    # Drop those -- a band whose centre lies inside a found tilted line's
    # outline -- but nothing else in the box: a bounding box round a slanted
    # panel also covers level lines just above and below it.
    if tilted_found:
        from tilt import to_poster

        def inside(x, y):
            for _, _, sub_lines, _, frame in tilted_found:
                lx, ly = to_poster(x, y, dict(frame, angle=-frame['angle']))
                for ln in sub_lines:
                    pad = 0.3 * ln['cap']
                    if (ln['left'] - pad <= lx <= ln['right'] + pad
                            and ln['y0'] - pad <= ly <= ln['y1'] + pad):
                        return True
            return False
        m['lines'] = [ln for ln in m['lines']
                      if not inside((ln['left'] + ln['right']) / 2, (ln['y0'] + ln['y1']) / 2)]
    assigned, note, cost = assign_copy(m['lines'], [c for c in copy if c['key'] not in tilted_keys],
                                       wrap_cap_ratio=wrap_cap_ratio)
    for keys, box, sub_lines, sub_assigned, frame in tilted_found:
        off = len(m['lines'])
        m['lines'] += sub_lines
        for x in sub_assigned:
            x['bands'] = [b + off for b in x['bands']]
        assigned += sub_assigned
        frames.append(dict(frame, box=list(box), keys=keys))
        note += f"; {len(sub_assigned)}/{len(keys)} tilted line(s) at {frame['angle']:.1f} deg"
    # the copy's own order, for everything downstream that reads assigned
    order = {c['key']: i for i, c in enumerate(copy)}
    assigned.sort(key=lambda x: order[x['key']])
    assigned, assign_points = apply_assign(m['lines'], copy, assigned, assign)
    if assign_points:
        note = f"{note}; {len(assign_points)} line(s) assigned by hand"
    faces = load_faces()

    # Posters set their copy in the case the design calls for, and plenty set
    # it in full capitals. The copy in events.yaml is sentence case, so reset
    # verbatim those posters come out in the wrong case and every width
    # measured against them is wrong too. Decide it here, before any face is
    # scored, so the ranking sees the letters the poster actually shows.
    #
    # The test is the share of glyphs sitting at x-height. In ALL-CAPS setting
    # nothing does, so it measures 0.00; mixed case runs 0.55 to 0.91. (The
    # cruder test -- modal glyph top versus cap top -- fails on a short word
    # like "Brindlewick", where ascenders and capitals outnumber the x-height
    # letters and the mode lands on the cap line.)
    #
    # Decided per band: a title may set BRINDLEWICK in letter-spaced capitals
    # on one line and "Village Fete" in mixed case on the next. The evidence
    # is where each x-height letter's ink tops out (glyphs.case_of), which
    # survives letters fused into one run; failing that, the share of runs at
    # x-height; and someone who has looked can say, with --case.
    a = np.array(Image.open(image).convert('RGB')).astype(int)
    from glyphs import case_of
    case = case or {}
    for x in assigned:
        if x['text'] == x['text'].upper():
            continue
        L = m['lines']
        parts = split_text(x['text'], x['bands'], L) or [x['text']]
        bands = x['bands'] if len(parts) == len(x['bands']) else x['bands'][:1]
        parts = parts if len(parts) == len(x['bands']) else [x['text']]
        decided = []
        for bi, part in zip(bands, parts):
            # The share of runs at x-height is decisive at its extremes --
            # generated capitals measure 0-0.12, mixed case 0.45 and up. In
            # between (cubist's date, 0.31), where each x-height letter tops
            # out decides (glyphs.case_of); it can be fooled on a short line
            # with an accent, so it never overrules a clear run count.
            c = case.get(x['key'])
            f = L[bi].get('lower_frac', 1.0)
            # ...unless the band is really two lines merged (taller than one
            # line with its descenders): its run count is then meaningless.
            if L[bi]['y1'] - L[bi]['y0'] > 1.7 * L[bi]['cap']:
                f = 0.3
            if c is None:
                c = ('upper' if f < 0.12 else 'mixed' if f >= 0.45
                     else case_of(a, L[bi], part) or ('upper' if f < 0.3 else 'mixed'))
            decided.append(c)
        if all(c == 'upper' for c in decided):
            x['text'] = x['text'].upper()
            x['upper'] = True
        elif any(c == 'upper' for c in decided):
            parts = [p.upper() if c == 'upper' else p for p, c in zip(parts, decided)]
            x['text'] = ' '.join(parts)
            x['parts'] = parts
            x['upper'] = 'partly'

    groups, ambiguities = {}, []

    # Can this poster be converted at all? Type is taken out of the artwork by
    # painting the background back over it. Over a photograph or a texture
    # there is nothing to paint with, so the type cannot be removed without
    # leaving a scar, and the honest answer is the escape hatch. Checked first
    # because every later measurement is noise if this fails.
    tex = [l.get('texture', 0.0) for l in m['lines']]
    med_tex = float(np.median(tex)) if tex else 0.0
    if med_tex > 5.0:
        ambiguities.append(dict(
            kind='type_on_imagery', median_texture=round(med_tex, 2),
            detail='the type sits on a busy ground. Look at the poster and '
                   'decide which kind: over a PHOTOGRAPH or illustration it '
                   'cannot be removed (nothing can reconstruct what is behind '
                   'the letters) and the answer is the escape hatch; over a '
                   'distressed or grainy flat ground it is recoverable, '
                   'because the fill is taken from the surrounding pixels'))
    if not assigned:
        ambiguities.append(dict(kind='no_alignment', detail=note))
    # Candidate knockout panels: paper-coloured rectangles sitting in front of
    # a shape. Reported, never applied. Measured across all 200 posters the
    # straight-edge test is right about one time in three -- posters are full
    # of shapes with straight sides -- so this is a hint for someone who has
    # seen masking damage an edge, not a decision the solver may take.
    for b in (m.get('knockouts') or []):
        if (b[2] - b[0]) * (b[3] - b[1]) >= 15000:
            ambiguities.append(dict(
                kind='possible_knockout', box=list(b),
                detail='a shape has a straight-edged bite out of it, which '
                       'may be a paper panel in front. Look: if it is, pass '
                       f'--knockout {b[0]},{b[1]},{b[2]},{b[3]}'))

    for kind, grp in group_lines_by_face(assigned, m['lines'], a).items():
        crops = glyph_crops(grp, m['lines'], a)
        feats = group_features(grp, m['lines'], a, crops)
        brief = briefs_for(kind, grp, character)
        cands = fetch_files(stage1(grp, m['lines'], faces, feats, keep, brief))
        cands = stage2(cands, crops)
        conf, margin = confidence(cands, len(crops))
        best = cands[0] if cands else None
        groups[kind] = dict(
            lines=[a2['key'] for a2 in grp],
            shape_glyphs=''.join(c for c, _, _ in crops),
            brief=brief,
            features={k: (round(v, 3) if isinstance(v, float) else v)
                      for k, v in feats.items() if k not in ('aspect', 'height')},
            confidence=conf, margin=margin,
            distortions=classify(grp, m['lines'], best, feats) if best else [],
            candidates=[dict(family=c['face']['family'], sub=c['face']['subfamily'],
                             weight=c['face']['weight'], embed=c['face']['embed'],
                             path=c['face']['path'], index=c['face']['index'],
                             cap_ratio=c['face']['cap_ratio'],
                             width_ratio=c['width_ratio'], spread=c['spread'],
                             stem_err=c['stem_err'], shape=c.get('shape'),
                             features=c.get('features'), character=c.get('character'),
                             record=dict(family=c['face']['family'],
                                         weight=c['face']['weight'],
                                         italic=c['face']['record'].get('italic', False),
                                         wdth=c['face']['record'].get('wdth'),
                                         source=c['face']['record']['source']),
                             score=c['score'])
                        for c in _shortlist(cands)])
        if conf != 'high':
            ambiguities.append(dict(kind='face_choice', group=kind, confidence=conf,
                                    top=[c['face']['family'] for c in cands[:3]]))
    # A weak alignment blocks the build -- unless someone has looked at the
    # overlay and said where the lines are, in which case the aligner's cost
    # is no longer the evidence.
    if cost > 0.25 and not assign_points:
        ambiguities.append(dict(kind='weak_alignment', detail=note,
                                mean_cost=round(cost, 3)))

    return dict(image=image, event=event, size=m['size'], paper=m['paper'],
                assign_points=assign_points, frames=frames,
                median_texture=round(med_tex, 2),
                alignment=note, alignment_cost=round(cost, 3),
                lines=m['lines'],   # keeps per-glyph boxes: the builder blanks
                                    # glyph by glyph, not by line rectangle
                assigned=assigned, artwork=m['artwork'],
                rejected=m['rejected'], groups=groups, ambiguities=ambiguities)


def summarise(s):
    print(f"{s['image']}  {s['size'][0]}x{s['size'][1]}  paper {s['paper']}")
    print(f"alignment: {s['alignment']}  ({len(s['assigned'])} logical lines, "
          f"{len(s['lines'])} bands)")
    tex = s.get('median_texture', 0)
    verdict = ('flat, so the type masks out cleanly' if tex <= 5 else
               'BUSY -- look: photograph/illustration means skip, '
               'distressed flat ground is still recoverable')
    print(f'ground under the type: {tex}  {verdict}')
    for kind, g in s['groups'].items():
        print(f"\n[{kind}]  {', '.join(g['lines'])}")
        print(f"  confidence {g['confidence']} (margin {g['margin']})"
              f"   shape glyphs: {g['shape_glyphs'] or 'NONE -- ranked on width and stem only'}")
        if g.get('brief'):
            print('  brief: ' + ', '.join(
                f"{t.split('/')[-2] if t.endswith('*') else t.split('/')[-1]} {v}"
                for t, v in g['brief'].items()))
        ft = g.get('features') or {}
        if ft:
            print('  measured: ' + '  '.join(f'{k} {v}' for k, v in ft.items() if v is not None))
        for c in g['candidates'][:4]:
            print(f"    {c['family'][:28]:29} {c['sub'][:10]:11} w{c['weight']:<4}"
                  f" width {c['width_ratio']:.3f} spread {c['spread']:.3f}"
                  f" feat {c['features'] if c.get('features') is not None else '  -'}"
                  f"{(' char ' + str(c['character'])) if c.get('character') is not None else ''}"
                  f" shape {c['shape'] if c['shape'] is not None else '  -'}"
                  f"  => {c['score']:.3f}{'' if c['embed'] else '   [id-only]'}")
        for d in g['distortions']:
            print(f"    ! {d['key']}: ratio {d['ratio']} {d['kind']} -> {d['action']}")
    if s['ambiguities']:
        print('\nNEEDS A DECISION:')
        for x in s['ambiguities']:
            print('  -', json.dumps(x))
    else:
        print('\nno ambiguities; safe to build')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--event', default='gig', choices=['gig', 'fete'])
    ap.add_argument('-o', '--out')
    ap.add_argument('--keep', type=int, default=80)
    ap.add_argument('--solid-radius', type=int, default=24,
                    help='0 disables; raise if heavy display type is eaten')
    ap.add_argument('--rule-length', type=int, default=160,
                    help='0 disables rule stripping')
    ap.add_argument('--rule-thick', type=int, default=5)
    ap.add_argument('--min-glyphs', type=int, default=2)
    ap.add_argument('--contrast', type=int, default=40)
    ap.add_argument('--window', type=int, default=61)
    ap.add_argument('--wrap-cap-ratio', type=float, default=1.3)
    a = ap.parse_args()
    s = solve(a.image, a.event, a.keep, min_glyphs=a.min_glyphs,
              solid_radius=a.solid_radius, rule_length=a.rule_length,
              rule_thick=a.rule_thick, contrast=a.contrast, window=a.window,
              wrap_cap_ratio=a.wrap_cap_ratio)
    summarise(s)
    if a.out:
        json.dump(s, open(a.out, 'w'), indent=1)
        print(f'\n-> {a.out}')


if __name__ == '__main__':
    main()

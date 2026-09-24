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
from masks import ink_masks  # noqa: E402
from analyse import modal_colour  # noqa: E402
from svgkit import Face, render_glyph  # noqa: E402
from shapescore import chamfer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.normpath(os.path.join(HERE, '..', 'fonts', 'index.json'))

# Glyphs worth comparing by shape: the ones that most separate families.
DISCRIMINATING = 'RGQaegSWM&2'


def load_index():
    return json.load(open(INDEX))['faces']


# ------------------------------------------------------------------ grouping
def group_lines_by_face(assigned, lines):
    """Split logical lines into sets that plausibly share one face.

    Posters overwhelmingly use a display face and a text face. Cap height
    separates them; stem weight then catches a poster that sets its small
    print in a second, lighter face.

    Three tiers by cap, not two. A poster often sets something mid-sized -- a
    date, a strapline -- between its headline and its small print, and a
    two-way split files that with the headline. The group's width ratio is then
    the median of two lines at a 4:1 size ratio, which reads as a good fit
    while neither line fits: on bauhaus_modernist's fete the title wanted 0.65
    and the date 1.29, the pair averaged 0.97, and the face chosen on that set
    the title half as wide again as the poster did.
    """
    caps = [lines[a['bands'][0]]['cap'] for a in assigned]
    med = float(np.median(caps))
    groups = {}
    for a in assigned:
        cap = lines[a['bands'][0]]['cap']
        kind = ('display' if cap > 4.0 * med
                else 'subhead' if cap > 1.8 * med else 'body')
        groups.setdefault(kind, []).append(a)

    # A poster often sets its small print in a second, lighter weight. Ink
    # density separates weights where stem width is too noisy to at small cap
    # heights. Split only when the spread is wide enough to be real.
    body = groups.get('body', [])
    if len(body) >= 4:
        dens = [lines[a['bands'][0]].get('density', 0) for a in body]
        lo, hi = min(dens), max(dens)
        if lo > 0 and hi / lo > 1.45:
            cut = (lo + hi) / 2
            heavy = [a for a, d in zip(body, dens) if d >= cut]
            light = [a for a, d in zip(body, dens) if d < cut]
            if len(heavy) >= 2 and len(light) >= 2:
                groups['body'] = heavy
                groups['body_light'] = light
                _keep_siblings_together(groups, assigned)
    return groups


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
def stage1(group, lines, faces, keep=24):
    """Arithmetic scoring of every face against a group of lines."""
    text = ''.join(a['text'] for a in group)
    out = []
    for f in faces:
        if set(f['missing']) & set(text):
            continue
        try:
            face = Face(f['path'], index=f['index'])
        except Exception:
            continue
        ratios, xerr = [], []
        for a in group:
            b = lines[a['bands'][0]]
            size = b['cap'] / f['cap_ratio']
            # Compare per visual line. Measuring the wrapped whole against the
            # unwrapped string counts the spaces that became line breaks, which
            # makes every candidate look too wide.
            parts = a.get('parts') or split_text(a['text'], a['bands'], lines)
            try:
                if parts and len(parts) == len(a['bands']):
                    natural = sum(face.width(p, lines[i]['cap'] / f['cap_ratio'])
                                  for p, i in zip(parts, a['bands']))
                    measured = sum(eff_width(lines[i]) for i in a['bands'])
                else:
                    natural = face.width(a['text'], size)
                    measured = sum(eff_width(lines[i]) for i in a['bands'])
            except Exception:
                natural = None
            if not natural:
                continue
            ratios.append(measured / natural)
            if f['x_ratio'] and b['x_height']:
                xerr.append(abs(f['x_ratio'] * size - b['x_height']) / b['x_height'])
        if not ratios:
            continue
        med = float(np.median(ratios))
        spread = float(np.std(ratios))
        # A stem under 0.05 of cap height is a failed measurement, not a
        # hairline face: on heavy display type the "narrowest run" is a sliver
        # of antialiasing or a serif, and trusting it ranked weight-100 faces
        # first for six headlines that were plainly bold. Treat it as unknown.
        stems = [lines[a['bands'][0]]['stem_ratio'] for a in group
                 if (lines[a['bands'][0]]['stem_ratio'] or 0) >= 0.05]
        stem_err = (abs(f['stem_ratio'] - float(np.median(stems))) / float(np.median(stems))
                    if stems and f['stem_ratio'] else None)
        # Width alone is weak: a face can be made to fit by scaling. Weight the
        # things scaling cannot fake -- stem weight and x-height proportion --
        # and how consistently one face explains every line in the group.
        score = (abs(np.log(med)) * 0.8
                 + spread * 1.8
                 + (float(np.mean(xerr)) * 1.2 if xerr else 0.0)
                 + (stem_err * 2.0 if stem_err is not None else 0.4))
        out.append(dict(face=f, width_ratio=round(med, 4), spread=round(spread, 4),
                        x_err=round(float(np.mean(xerr)), 4) if xerr else None,
                        stem_err=round(stem_err, 4) if stem_err is not None else None,
                        metric_score=round(float(score), 4),
                        score=round(float(score), 4)))
    out.sort(key=lambda r: r['score'])
    # Always carry the closest width matches through, even if the combined
    # score buried them. The combined score mixes stem weight and x-height
    # with width, and a face can be ranked out while being the one face in the
    # corpus that sets the line at the right width -- on bauhaus_modernist's
    # fete title, League Gothic matched the measured width to 1% and still
    # missed the shortlist, leaving nothing on it narrower than 1.7x. Shape
    # scoring decides between them afterwards; this only decides what gets to
    # be looked at.
    keepers = out[:keep]
    seen = {id(r) for r in keepers}
    by_width = sorted(out, key=lambda r: abs(np.log(max(r['width_ratio'], 1e-6))))
    for r in by_width[:6]:
        if id(r) not in seen:
            keepers.append(r)
    return keepers


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


def glyph_crops(group, lines, mask, limit=6):
    """(char, ink crop) pairs from the ORIGINAL, for shape comparison.

    Usable only where a band's components map one-to-one onto its characters;
    where letters touch, the mapping is ambiguous and that band is skipped.
    """
    crops = []
    for a in group:
        parts = a.get('parts') or split_text(a['text'], a['bands'], lines)
        if not parts:
            continue
        for bi, part in zip(a['bands'], parts):
            b = lines[bi]
            chars = [c for c in part if c != ' ']
            if len(b['runs']) != len(chars):
                continue
            for ch, r in zip(chars, b['runs']):
                if ch.isalnum() and (r[3] - r[2]) > 8:
                    crops.append((ch, mask[r[2]:r[3] + 1, r[0]:r[1] + 1]))
    # Prefer the glyphs that separate families, but take whatever the poster
    # offers: insisting on a short list leaves lines with one usable glyph, and
    # one glyph cannot choose a typeface.
    crops.sort(key=lambda t: (DISCRIMINATING.find(t[0]) < 0,
                              -(t[1].shape[0] * t[1].shape[1])))
    seen, out = set(), []
    for ch, c in crops:
        if ch not in seen:
            seen.add(ch)
            out.append((ch, c))
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
        scores = []
        for ch, crop in crops:
            if not face.has(ch):
                continue
            g = render_glyph(face, ch, crop.shape[0] / c['face']['cap_ratio'])
            if g is None:
                continue
            scores.append(chamfer(crop, g > 128))
        if scores:
            c['shape'] = round(float(np.mean(scores)), 4)
    seen = [c['shape'] for c in cands if c.get('shape') is not None]
    fallback = (float(np.mean(seen)) + 0.5) if seen else 3.0
    for c in cands:
        # Letterform first, then agreement across lines, then natural width --
        # width alone is the weakest signal because scaling can fake it.
        c['score'] = round(c.get('shape', fallback) * 1.0
                           + c['metric_score'], 4)
    cands.sort(key=lambda r: r['score'])
    return cands


# ------------------------------------------------------------- distortion
def classify(group, lines, best):
    """Name any width disagreement, using SKILL.md's three rules."""
    notes = []
    f = best['face']
    for a in group:
        b = lines[a['bands'][0]]
        size = b['cap'] / f['cap_ratio']
        face = Face(f['path'], index=f['index'])
        measured = sum(eff_width(lines[i]) for i in a['bands'])
        natural = face.width(a['text'], size)
        ratio = measured / natural if natural else 1.0
        if abs(ratio - 1) < 0.06:
            continue
        stem_ok = (b['stem_ratio'] and f['stem_ratio']
                   and abs(b['stem_ratio'] - f['stem_ratio']) / f['stem_ratio'] < 0.18)
        if ratio < 0.94 and stem_ok:
            kind, act = 'squeezed_to_fit', 'resize this line so it fits naturally'
        elif ratio < 0.94:
            kind, act = 'horizontal_squash', 'do NOT reproduce; set at natural width'
        else:
            kind, act = 'wider_than_face', 'do NOT stretch; let the line run short'
        notes.append(dict(key=a['key'], ratio=round(ratio, 3), kind=kind,
                          stem_matches=bool(stem_ok), action=act))
    return notes


def confidence(cands):  # noqa
    if len(cands) < 2:
        return 'low', 0.0
    margin = cands[1]['score'] - cands[0]['score']
    rel = margin / max(cands[0]['score'], 0.05)
    if cands[0]['score'] < 0.55 and rel > 0.25:
        return 'high', round(rel, 3)
    if cands[0]['score'] < 1.1 and rel > 0.10:
        return 'medium', round(rel, 3)
    return 'low', round(rel, 3)


# ------------------------------------------------------------------- main
def solve(image, event, keep=24, wrap_cap_ratio=1.3, assign=None, **measure_opts):
    m = find_lines(image, **measure_opts)
    copy = load_copy(event)
    assigned, note, cost = assign_copy(m['lines'], copy,
                                       wrap_cap_ratio=wrap_cap_ratio)
    assigned, assign_points = apply_assign(m['lines'], copy, assigned, assign)
    if assign_points:
        note = f"{note}; {len(assign_points)} line(s) assigned by hand"
    faces = load_index()

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
    for x in assigned:
        fr = [m['lines'][b].get('lower_frac', 1.0) for b in x['bands']]
        if fr and float(np.median(fr)) < 0.12 and x['text'] != x['text'].upper():
            x['text'] = x['text'].upper()
            x['upper'] = True

    a = np.array(Image.open(image).convert('RGB')).astype(int)
    masks = ink_masks(a, modal_colour(a))

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

    for kind, grp in group_lines_by_face(assigned, m['lines']).items():
        cands = stage1(grp, m['lines'], faces, keep)
        colour = m['lines'][grp[0]['bands'][0]]['colour']
        crops = glyph_crops(grp, m['lines'], masks.get(colour, masks['dark']))
        cands = stage2(cands, crops)
        conf, margin = confidence(cands)
        best = cands[0] if cands else None
        groups[kind] = dict(
            lines=[a2['key'] for a2 in grp],
            shape_glyphs=''.join(c for c, _ in crops),
            confidence=conf, margin=margin,
            distortions=classify(grp, m['lines'], best) if best else [],
            candidates=[dict(family=c['face']['family'], sub=c['face']['subfamily'],
                             weight=c['face']['weight'], embed=c['face']['embed'],
                             path=c['face']['path'], index=c['face']['index'],
                             cap_ratio=c['face']['cap_ratio'],
                             width_ratio=c['width_ratio'], spread=c['spread'],
                             stem_err=c['stem_err'], shape=c.get('shape'),
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
                assign_points=assign_points,
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
              f"   shape glyphs: {g['shape_glyphs'] or 'none usable'}")
        for c in g['candidates'][:4]:
            print(f"    {c['family'][:28]:29} {c['sub'][:10]:11} w{c['weight']:<4}"
                  f" width {c['width_ratio']:.3f} spread {c['spread']:.3f}"
                  f" stem_err {c['stem_err'] if c['stem_err'] is not None else '   -'}"
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
    ap.add_argument('--keep', type=int, default=24)
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

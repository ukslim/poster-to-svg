#!/usr/bin/env python3
"""Turn a solution.json into the finished SVG.

    python3 build_svg.py solution.json -o out.svg \
        --face display=Anton --face body="Roboto Condensed" \
        --artwork crop:0,0,1024,940

Placement is deterministic and follows SKILL.md:

  size      from the measured cap height
  baseline  from the measured baseline
  x         measured ink edge with the first glyph's sidebearing backed out,
            then a shared origin where the lines agree on one
  spacing    none -- no word-spacing or scaling; letter-spacing only where the
            measurement found deliberate, even tracking (rounded to 0.05em)

`--face` overrides the solver's pick (by family name, or `group=#2` to take the
second candidate). `--artwork` is the one thing the solver cannot decide:

    crop:x0,y0,x1,y1   cut that box out of the original and embed it
    mask               blank the type out of the original and embed the rest
    shapes:file.svg    a hand-authored vector fragment, for flat geometry
    none
"""
import argparse, base64, io, json, math, os, re, sys
import numpy as np
from fontTools.ttLib import TTFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Face, subset_b64, face_css, data_uri, encode_image, top_ratio  # noqa: E402
from solve_type import split_text  # noqa: E402

def esc(t):
    """XML-escape, and render everything above ASCII as a numeric reference.

    An SVG is XML, so a reader with no encoding declaration is required to
    assume UTF-8 -- but macOS QuickLook reads it as Latin-1 and shows the two
    bytes of a `£` as `Â£`. The file carries a declaration now, and keeping the
    content pure ASCII as well means no consumer has to get that right.
    """
    for a, b in (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;')):
        t = t.replace(a, b)
    return ''.join(c if ord(c) < 128 else f'&#{ord(c)};' for c in t)


def ensure_file(c):
    """A stored solution names a cached subset; if the cache has been cleared,
    fetch it again from the style the candidate records."""
    if c.get('path') and os.path.exists(c['path']):
        return c
    r = c.get('record')
    if not r or r.get('source') != 'google':
        raise SystemExit(f"font file for {c['family']} is gone: {c.get('path')}")
    from fontfetch import fetch
    from catalogue import SPECIMEN
    return dict(c, path=fetch(r['family'], r['weight'], r.get('italic', False),
                              r.get('wdth'), SPECIMEN), index=None)


def pick(group, name):
    return ensure_file(_pick(group, name))


def _pick(group, name):
    """Resolve a --face override against a group's candidate list.

    A system font winning is informative, not an error: it says the generator
    was imitating Helvetica, or Futura, or Didot. But it is licensed and cannot
    be embedded, so fall through to the best open-licensed candidate and say
    which one it stood in for.
    """
    cands = group['candidates']
    if not name:
        for i, c in enumerate(cands):
            if c['embed']:
                if i:
                    blocked = ', '.join(x['family'] for x in cands[:i])
                    print(f"  (closest match was {blocked}, a system font -- "
                          f"shipping {c['family']} instead)")
                return c
        raise SystemExit('no embeddable candidate; widen the corpus')
    if name.startswith('#'):
        return cands[int(name[1:]) - 1]
    if ':' not in name:
        full = lambda c: (c['family'] + ' ' + c['sub']).lower()
        for c in [c for c in cands if full(c) == name.lower()] + cands:
            if name.lower() in full(c):
                return c
    # Not on the shortlist: any style in the catalogue will do, as
    # "Family", "Family:600" or "Family:600i". Someone who has looked at the
    # poster and knows the face should not be limited to what the ranking kept.
    return from_catalogue(name, group)


STYLE_WEIGHTS = {'thin': 100, 'extralight': 200, 'light': 300, 'regular': 400,
                 'book': 400, 'medium': 500, 'semibold': 600, 'demibold': 600,
                 'bold': 700, 'extrabold': 800, 'heavy': 900, 'black': 900}


def from_catalogue(name, group=None):
    """A face named "Family", "Family:600", "Family:600i" or, as older stored
    overrides do, "Family SemiBold" / "Family Bold Italic"."""
    import catalogue
    fam, _, style = name.partition(':')
    italic = style.endswith('i')
    want = int(style.rstrip('i')) if style.rstrip('i').isdigit() else None
    fams = {r['family'].lower() for r in catalogue.load()}
    fam, wdth = fam.strip(), None
    if fam.lower() not in fams:
        # also the names this tool prints: "Radio Canada 700 wdth75",
        # "Sofia Sans Condensed 800 italic"
        words = fam.split()
        while words and (words[-1].lower() in (*STYLE_WEIGHTS, 'italic')
                         or words[-1].isdigit() or re.fullmatch(r'wdth\d+', words[-1])):
            w = words.pop().lower()
            if w == 'italic':
                italic = True
            elif w.startswith('wdth'):
                wdth = int(w[4:])
            elif w.isdigit():
                want = want or int(w)
            elif want is None:
                want = STYLE_WEIGHTS[w]
        fam = ' '.join(words)
    hits = [r for r in catalogue.load() if r['family'].lower() == fam.lower()
            and bool(r.get('italic')) == italic and r.get('wdth') == wdth]
    if not hits:
        raise SystemExit(
            f'--face {name!r} is not on the shortlist or in the Google Fonts '
            f'catalogue (a stored override from the old local corpus?). Choose '
            f'again: python3 tools/sheet.py SOLUTION, then --face group=#N or '
            f'--face group="Family:weight"')
    measured = ((group or {}).get('features') or {}).get('weight')
    if want is None and measured:
        # No weight named: the family's style whose stroke weight is closest
        # to what the poster measured (the old local corpus kept one or two
        # weights, so a bare family name there meant the heavy one).
        r = min(hits, key=lambda r: abs((r['features'].get('weight') or 0) - measured))
    else:
        r = min(hits, key=lambda r: abs(r['weight'] - (want or 400)))
    path, index = catalogue.face_file(r)
    print(f"  (using {catalogue.label(r)} from the catalogue)")
    return dict(family=r['family'], sub=catalogue.label(r)[len(r['family']) + 1:],
                weight=r['weight'], embed=r['embed'], path=path, index=index,
                cap_ratio=r['cap'] / r['upem'])


def obstacles(sol, shapes=None):
    """Artwork a line of type must not run into.

    Each obstacle is (y0, y1, left_at) where left_at(top, bot) gives its
    leftmost edge over those rows. A bounding box answers with one number, but
    a circle does not: on the rows beside its top the real edge can be hundreds
    of pixels right of the box, and blocking on the box shrinks lines that the
    original composed comfortably past the curve. Where the agent has described
    the artwork with `shapes:` we know the geometry exactly, so use it.

    Ignores anything spanning most of the page: a full-bleed ground is not an
    obstacle, and treating it as one would shrink every line to nothing.
    """
    w, h = sol['size']
    if shapes:
        # A described full-bleed ground starts at x=0, and clear_width already
        # ignores any obstacle at or left of the line's own left edge.
        return shapes
    out = []
    for a in sol.get('artwork', []):
        x0, y0, x1, y1 = a['box']
        if (x1 - x0 + 1) * (y1 - y0 + 1) > 0.55 * w * h:
            continue
        if a['px'] < 2000:
            continue
        out.append((y0, y1, lambda t, b, _x=x0: _x))
    return out


def clear_width(left, top, bot, obs, page_w, margin=8, orig_right=None):
    """How much room a line has before it meets artwork, or the page edge.

    Deliberately does not try to infer the design's own right margin. Both the
    obvious proxies are wrong: the left margin, because a headline may be set
    to run much closer to the trim than the body does, and `page - max(right)`,
    because type stopping short of the right side usually means artwork is
    there, not that the margin is 350px wide. Trying either shrank headlines by
    a third on posters that were already right.
    """
    limit = page_w - margin
    for y0, y1, left_at in obs:
        if y1 < top or y0 > bot:          # not on these rows
            continue
        x0 = left_at(max(top, y0), min(bot, y1))
        if x0 is None or x0 <= left:      # the line starts inside/after it
            continue
        # If the original line already ran past this shape, the overlap is the
        # design -- a Bauhaus title set across a circle, type over a bar -- and
        # holding the reset line clear of it shrinks the headline by a quarter
        # to solve a problem the poster does not have. So it may run as far
        # into the shape as the original did -- but no further: a box is
        # coarse (a diagonal bar, a flower on a stem), and a wider reset line
        # given no limit at all ran into the artwork itself (suprematism's
        # title into the bar, botanical's support line into the flower).
        if orig_right is not None and orig_right > x0:
            limit = min(limit, orig_right + margin)
            continue
        limit = min(limit, x0 - margin)
    return max(20.0, limit - left)


def place(sol, chosen, shapes=None):
    """-> [(css_class, text, x, baseline, size, fill)] and the faces used."""
    lines = sol['lines']
    obs = obstacles(sol, shapes)
    out, used = [], {}
    from layout import anchors
    anchor_of, block_of = anchors(lines, sorted({b for a in sol['assigned'] for b in a['bands']}),
                                  sol['size'][0])
    # What a look settles and measurement missed: the edge a line is ranged
    # on (--align), and tracking the measurement did not find (--track).
    knobs = sol.get('knobs') or {}
    align = dict(x.split('=', 1) for x in knobs.get('align') or [])
    track = dict(x.split('=', 1) for x in knobs.get('track') or [])
    # KEY=#RGB colours the line; KEY=#RGB@WORDS colours only those words (a
    # headline whose last word is in the accent colour)
    # KEY=DX,DY,#RGB: a hard drop shadow (an extruded or offset-printed look)
    shadow = dict(x.split('=', 1) for x in knobs.get('shadow') or [])
    # KEY: never wider than the original line -- where no narrower cut of the
    # chosen face exists and the line must stay inside the design's frame
    fit = set(knobs.get('fit') or [])
    fill, word_fill = {}, {}
    for x in knobs.get('fill') or []:
        key, _, spec = x.partition('=')
        colour, _, words = spec.partition('@')
        if words:
            word_fill.setdefault(key, []).append((words, colour))
        else:
            fill[key] = colour
    for gname, g in sol['groups'].items():
        c = chosen[gname]
        if not c['embed']:
            raise SystemExit(
                f"[{gname}] resolves to {c['family']}, which is a system font "
                "and cannot be embedded. Pick an open-licensed equivalent.")
        face = Face(c['path'], index=c['index'])
        used[gname] = (c, face)
        squeeze = {d['key']: d for d in g['distortions']}
        for a in sol['assigned']:
            if a['key'] not in g['lines']:
                continue
            parts = a.get('parts') or split_text(a['text'], a['bands'], lines)
            parts = parts or [a['text']]
            # One copy line wrapped over several bands is one entity: a title
            # set over three lines is one size, and measuring each band on its
            # own turns a pixel of noise into a visibly odd line. Unify them --
            # but only when the bands agree to start with, so a designer who
            # really did step the sizes still gets what they drew.
            # Each band's size from its own tallest letter (see top_ratio),
            # then unify the SIZES: a band topped by capitals and one topped
            # by ascenders measure different "caps" at one size.
            own = [lines[bi]['cap'] / top_ratio(face, part)
                   for bi, part in zip(a['bands'], parts)]
            unified = (float(np.median(own))
                       if len(own) > 1 and max(own) <= 1.12 * min(own) else None)
            sized = []
            for (bi, part), size0 in zip(zip(a['bands'], parts), own):
                b = lines[bi]
                size = unified or size0
                # Deliberate tracking is part of the design, so it is set --
                # but only where the measurement found it wide AND even, never
                # to make up a width. The amount is what this face needs to
                # span the measured line, rounded to 0.05em: letter-spacing is
                # specified in round values, and rounding keeps noise out.
                ls_em = 0.0
                said = track.get(a['key'])
                if said and said != 'fit':
                    ls_em = float(said.rstrip('em'))
                elif (b.get('track') or said == 'fit') and len(part) > 1:
                    solid = face.width(part, size)
                    if solid:
                        need = (b['width'] - solid) / (len(part) - 1) / size
                        ls_em = min(0.6, max(0.0, round(need / 0.05) * 0.05))
                d = squeeze.get(a['key'])
                if d and d['kind'] == 'squeezed_to_fit' and not ls_em:
                    # the generator shrank this line to fit its block; set it at
                    # the size that fits, rather than reproducing a squash
                    nat = face.width(part, size)
                    if nat:
                        size *= min(1.0, b['width'] / nat)
                # Type must not run into the artwork. This outranks setting
                # at the face's natural width: the original was composed round
                # the shapes, and a line that collides with one is simply
                # wrong, however faithful its letterforms.
                lo, hi = face.ink(part, size, track=ls_em * size)
                # artwork and the page edge are in the poster's frame; a
                # tilted line is fitted in its own, where only the measured
                # width means anything
                room = (clear_width(b['left'], b['y0'], b['y1'], obs,
                                    sol['size'][0], orig_right=b['right'])
                        if not b.get('frame') else max(hi - lo, 1.25 * b['width']))
                if a['key'] in fit:
                    room = min(room, b['right'] - b['left'] + 2)
                if hi - lo > room:
                    size *= room / (hi - lo)
                sized.append((b, part, size, ls_em))
            if unified and len(sized) > 1:
                # Having to clear a shape or the trim applies to the entity,
                # not to one band of it: a title whose longest line must come
                # down 8% is a title set 8% smaller, not two sizes of title.
                # Past a point that stops being true -- if one band needs a
                # third off, taking the whole headline down with it is worse
                # than the mismatch, and means the reset is much wider than
                # what it replaces. Say so instead of quietly choosing.
                one = min(s for _, _, s, _ in sized)
                big = max(s for _, _, s, _ in sized)
                if one >= 0.85 * big:
                    sized = [(b, part, one, ls) for b, part, _, ls in sized]
                else:
                    print(f"  ! {a['key']}: bands measure one size but set at "
                          f"{one:.0f}-{big:.0f} -- a band is clamped to clear "
                          f"artwork or the trim because the face runs wider "
                          f"than the original. Check it, or pick a narrower face.")
            if track.get(a['key']) == 'fit' and len(sized) > 1 and unified:
                # one line wrapped is tracked alike on every row; the face's
                # letters differ in width from the generator's row by row
                one = round(float(np.median([ls for *_, ls in sized])) / 0.05) * 0.05
                sized = [(b, part, size, one) for b, part, size, _ in sized]
            sized = keep_leading(sized, face, a['key'])
            for (b, part, size, ls_em), bi in zip(sized, a['bands']):
                # A line set well below its measured size (a wider face
                # clamped to the room it has) keeps its place in the design
                # by staying centred on the original line, not by sitting on
                # the original baseline with the lost height all above it --
                # a gap over a headline and its foot pressed on what is below.
                y = b['baseline']
                shown = top_ratio(face, part) * size
                if shown < 0.9 * b['cap'] and not b.get('frame'):
                    y = round(b['baseline'] - (b['cap'] - shown) / 2, 1)
                out.append(dict(group=gname, key=a['key'], text=part,
                                size=round(size, 2), room=room, ls_em=ls_em,
                                y=y, fill=fill.get(a['key']) or b['rgb'],
                                cap=b['cap'],
                                left=b['left'], right=b['right'],
                                anchor=align.get(a['key']) or anchor_of.get(bi, 'left'),
                                block=block_of.get(bi), frame=b.get('frame'),
                                words=word_fill.get(a['key']),
                                shadow=shadow.get(a['key'])))
    unify_siblings(out, {g: f for g, (c, f) in used.items()})
    return out, used


def keep_leading(sized, face, key):
    """The rows of a wrapped line never closer than the original set them.

    Rows keep their measured baselines, so a face whose letters stand taller
    at the size the bands call for eats the space between them -- and one
    band measured tall (an ampersand or accent standing above the capitals)
    raises the shared size enough to make two rows of a headline touch. Bring
    the entity down until every pair of rows keeps at least the original's
    gap between one row's lowest ink and the next row's highest.

    Accents are left out of that (band-finding leaves them out), but checked
    on their own: a font's circumflex stands far taller than the flattened
    one a generator draws over a capital, and in a tight title the reset
    FÊTE's accent otherwise lands on the letters of the row above.
    """
    k = 1.0
    for (b1, p1, s1, _), (b2, p2, s2, _) in zip(sized, sized[1:]):
        if b1.get('frame') or b2.get('frame') or b2['baseline'] <= b1['baseline']:
            continue
        # side by side on one row (a title's last word in the accent colour),
        # not one row above another: there is no leading between them
        if b2['y0'] < b1['baseline'] - 0.5 * b1['cap']:
            continue
        # letters only: band-finding leaves punctuation out of a band's depth
        low = [face.bounds(c)[1] for c in p1 if c.isalnum() and face.has(c)]
        desc = max(0.0, -min(low) / face.upem) if low else 0.0
        # rows may close up to half the original gap, never touch: a
        # unified size may stand a row a little above its own measure
        room = b2['baseline'] - b1['baseline'] - max(0, b2['y0'] - b1['y1']) / 2
        # every row scaled by one factor, so rows the designer stepped in
        # size keep their proportion. Descenders count only as deep as the
        # original row's own: where it had none, its measured bottom is the
        # baseline, and the face's descenders fall in the gap as they did.
        need = top_ratio(face, p2) * s2 + min(desc * s1, max(0, b1['y1'] - b1['baseline']))
        if need > 0:
            k = min(k, room / need)
        marked = [c for c in p2 if not c.isascii() and c.isalpha() and face.has(c)]
        if marked:
            # the accent may enter the gap, but must clear the row above
            tall = max(face.bounds(c)[3] for c in marked) / face.upem
            # ...and a little short of it: an accent that reaches the row
            # above's baseline touches its letters (de_stijl's FÊTE)
            k = min(k, (b2['baseline'] - b1['baseline']) / (tall * s2 + 0.015 * s1))
    if k >= 1.0:
        return sized
    if k < 0.9:
        print(f"  ! {key}: brought down {100 * (1 - k):.0f}% so its rows keep the "
              f"original's leading -- the face stands taller than the lettering")
    return [(b, part, size * k, ls) for b, part, size, ls in sized]


def unify_siblings(placed, faces):
    """Set a list of sibling copy lines at one size.

    attraction0..7 are items of one list and the poster sets them as one. They
    still come out at different sizes, because the squeeze rule fits each line
    to its own measured block: a short item whose reset runs wider than the
    original gets shrunk, its neighbour does not, and a tidy list acquires an
    18% size range. Take the family's median and give it to all of them --
    median rather than minimum, so one badly fitted line cannot drag the list
    down. A member still may not outgrow the room it has, so anything that
    would then collide is scaled back on its own.
    """
    fam = {}
    for p in placed:
        stem = re.sub(r'\d+$', '', p.get('key', ''))
        if stem and stem != p['key']:
            fam.setdefault((p['group'], stem), []).append(p)
    for (gname, _), members in fam.items():
        # A list is three or more lines, however many pieces each is set in
        # (footer0 set in two colours is one line, not two list items)...
        if len({m['key'] for m in members}) < 3:
            continue
        # ...and a list the original set at one size. A member drawn much
        # bigger or smaller than the rest is not an item of it: raised to
        # its neighbours' size, push_pin's small "Organised by" footer came
        # out at the size of the big "FREE ENTRY" line above it.
        caps = sorted(m['cap'] for m in members)
        cmed = caps[len(caps) // 2]
        members = [m for m in members if 0.8 * cmed <= m['cap'] <= 1.25 * cmed]
        if len({m['key'] for m in members}) < 3:
            continue
        sizes = sorted(m['size'] for m in members)
        med = sizes[len(sizes) // 2]
        # A tracked list is tracked as one: each item measured alone lands on
        # its own rounded step (0.1-0.25em down one list), so give them all the
        # members' median, as with the size.
        spacings = sorted(m.get('ls_em', 0.0) for m in members)
        ls_med = spacings[len(spacings) // 2]
        face = faces.get(gname)
        for m in members:
            size = med
            m['ls_em'] = ls_med
            if face is not None:
                tr = m.get('ls_em', 0.0)
                lo, hi = face.ink(m['text'], size, track=tr * size)
                if m['room'] and hi - lo > m['room']:
                    size *= m['room'] / (hi - lo)
            m['size'] = round(size, 2)


def position(placed, faces, tol=4.0):
    """Give every line its x, from the edge its block keeps.

    A left-ranged line's origin is its measured left ink edge less the first
    glyph's sidebearing (text-anchor start). A centred line is anchored at its
    measured centre and a right-ranged one at its right edge (text-anchor
    middle / end), with the face's own ink offsets backed out -- so however
    wide the reset face runs, the browser keeps the line where the design put
    its edge. Lines of one block that agree on the edge to within `tol` share
    it exactly: the ragged differences are the generator's, not the design's.
    """
    by_block = {}
    for p in placed:
        by_block.setdefault((p.get('block'), p['anchor']), []).append(p)
    for (blk, anchor), members in by_block.items():
        edge = {'left': lambda p: p['left'], 'right': lambda p: p['right'],
                'centre': lambda p: (p['left'] + p['right']) / 2}[anchor]
        vals = [edge(p) for p in members]
        shared = (blk is not None and len(vals) > 1
                  and max(vals) - min(vals) <= tol)
        target = sorted(vals)[len(vals) // 2] if shared else None
        for p, v in zip(members, vals):
            face = faces[p['group']]
            size, tr = p['size'], p.get('ls_em', 0.0) * p['size']
            lo, hi = face.ink(p['text'], size, track=tr)
            adv = (sum(face.adv(ch) for ch in p['text'] if face.has(ch)) * size / face.upem
                   + tr * len(p['text']))
            e = target if shared else v
            if anchor == 'left':
                p['x'] = round(e - lo, 2)
            elif anchor == 'centre':
                p['x'] = round(e - (lo + hi) / 2 + adv / 2, 2)
            else:
                p['x'] = round(e - hi + adv, 2)
    return placed


def embed(im, x0, y0, workdir, quality=76, label='artwork'):
    from PIL import Image
    png = os.path.join(workdir, '_art.png')
    im.save(png)
    out = os.path.join(workdir, '_art.webp')
    size, err = encode_image(png, out, quality=quality)
    print(f'  {label} {im.width}x{im.height} -> {size/1024:.0f}KB webp '
          f'(mean err {err:.2f})')
    return (f'  <image x="{x0}" y="{y0}" width="{im.width}" height="{im.height}"\n'
            f'         xlink:href="{data_uri(out)}"/>')


def _classes(px, tol=40, min_share=0.05):
    """Cluster colours into the few flat regions round a hole. -> centres."""
    q = (px // 32).astype(int)
    keys = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    centres = []
    for v in vals[np.argsort(-counts)]:
        c = np.median(px[keys == v], 0)
        if (keys == v).sum() < min_share * len(px):
            break
        if all(np.abs(c - d).max() > tol for d in centres):
            centres.append(c)
    return centres


def _fit_sides(pts, labels, k, hole_pts):
    """Which class each hole point belongs to, from a straight-line one-vs-rest
    logistic fit to the classes of the surviving pixels round it. Straight,
    because it is fitted in small tiles: a corner is two tiles' lines."""
    def feats(p):
        return np.stack([np.ones(len(p)), p[:, 1], p[:, 0]], 1)
    mu = pts.mean(0)
    sc = max(1.0, float(np.abs(pts - mu).max()))
    F, G = feats((pts - mu) / sc), feats((hole_pts - mu) / sc)
    scores = []
    with np.errstate(all='ignore'):            # Accelerate's spurious matmul warnings
        for c in range(k):
            t = (labels == c).astype(float)
            w = np.zeros(3)
            for _ in range(12):                   # IRLS, lightly regularised
                z = np.clip(F @ w, -30, 30)
                p = 1 / (1 + np.exp(-z))
                r = p * (1 - p) + 1e-6
                A = F.T @ (F * r[:, None]) + 1e-3 * np.eye(3)
                w = w + np.linalg.solve(A, F.T @ (t - p))
            scores.append(G @ w)
    return np.argmax(np.stack(scores, 1), 1)


def inpaint(arr, hole, tile=24):
    """Fill `hole` (the blanked type) from the artwork round it, keeping edges.

    Painting a flat colour over the type is wrong twice over: type on a
    coloured panel is blanked to the paper colour, and type on a distressed
    ground leaves a smooth patch. So the fill comes from the surviving pixels
    -- but which ones matters where a letter crosses an edge between two flat
    colours. Taking each pixel from its NEAREST survivor makes the boundary
    run down the middle of the stroke: the round trip, which knows the true
    artwork, showed a triangle's straight edge coming back scalloped wherever
    a headline crossed it (act_up's "wedges of the pink triangle").

    So, tile by tile (about a stroke across): sort the surviving pixels round
    the tile into their flat colours -- sampled a couple of pixels clear of
    the hole, where antialiasing has not blended them -- fit the straight
    boundary between them, and give each blanked pixel the class on its side;
    its colour then comes from the nearest survivor OF THAT CLASS, which
    carries texture and gradients without crossing the edge. Small tiles make
    straight lines enough: a corner is two tiles. One colour round a tile is
    the plain case, nearest survivor, as before.

    It still cannot invent structure. Over a photograph the fill is a smear of
    stretched neighbours, which is why type on imagery is a skip rather than a
    conversion.
    """
    from scipy import ndimage
    if not hole.any():
        return arr.copy()
    out = arr.copy()
    # Survivors a few px clear of the hole: right beside a letter the pixels
    # are its antialiasing and compression ringing, and copying them leaves a
    # pale ghost of the letter on plain paper.
    clear = ndimage.distance_transform_edt(~hole) >= 3
    idx = ndimage.distance_transform_edt(~clear, return_distances=False,
                                         return_indices=True)
    fill_from = hole | (~clear & ~hole)            # the hole and its rim
    out[fill_from] = arr[tuple(idx)][fill_from]    # the plain fill, everywhere
    H, W = hole.shape
    hy_all, hx_all = np.nonzero(hole)
    tiles = {}
    for y, x in zip(hy_all // tile, hx_all // tile):
        tiles[(y, x)] = True
    m = tile
    for ty, tx in tiles:
        y0, x0 = ty * tile, tx * tile
        wy0, wy1 = max(0, y0 - m), min(H, y0 + tile + m)
        wx0, wx1 = max(0, x0 - m), min(W, x0 + tile + m)
        survive = clear[wy0:wy1, wx0:wx1]
        ky, kx = np.nonzero(survive)
        if len(ky) < 20:
            continue
        a = arr[wy0:wy1, wx0:wx1]
        px = a[ky, kx].astype(float)
        centres = _classes(px, min_share=0.08)[:3]
        if len(centres) < 2:
            continue
        cls = np.argmin(np.stack([np.abs(px - c).max(1) for c in centres], 1), 1)
        h = hole[y0:min(H, y0 + tile), x0:min(W, x0 + tile)]
        hy, hx = np.nonzero(h)
        hy, hx = hy + (y0 - wy0), hx + (x0 - wx0)        # window coordinates
        side = _fit_sides(np.stack([ky, kx], 1).astype(float), cls, len(centres),
                          np.stack([hy, hx], 1).astype(float))
        for c in range(len(centres)):
            sel = side == c
            if not sel.any():
                continue
            src = np.zeros(survive.shape, bool)
            src[ky[cls == c], kx[cls == c]] = True
            ii = ndimage.distance_transform_edt(~src, return_distances=False,
                                                return_indices=True)
            out[wy0 + hy[sel], wx0 + hx[sel]] = a[ii[0][hy[sel], hx[sel]],
                                                  ii[1][hy[sel], hx[sel]]]
    return out


def parse_shapes(spec):
    """A tiny vocabulary for describing artwork by hand.

        circle CX CY R #RGB
        ellipse CX CY RX RY #RGB
        rect X Y W H #RGB
        poly #RGB X1,Y1 X2,Y2 ...

    Separated by ';'. This is the cheapest place in the whole pipeline to spend
    a moment of judgement: a glance at the poster that says "one red circle,
    clipped at the right edge" replaces masking the type out, inpainting behind
    it and embedding a raster -- and gives a smaller, sharper, fully vector
    poster. The solver prints the measured colour, bounding box and fill ratio
    of every shape it found, which is usually enough to write the spec without
    measuring anything yourself.
    """
    out = []
    for kind, bits in _shapes(spec):
        if kind == 'circle':
            x, y, r, col = bits[1], bits[2], bits[3], bits[4]
            out.append(f'    <circle cx="{x}" cy="{y}" r="{r}" fill="{col}"/>')
        elif kind == 'ellipse':
            x, y, rx, ry, col = bits[1:6]
            out.append(f'    <ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" '
                       f'fill="{col}"/>')
        elif kind == 'rect':
            x, y, w, h, col = bits[1:6]
            out.append(f'    <rect x="{x}" y="{y}" width="{w}" height="{h}" '
                       f'fill="{col}"/>')
        elif kind == 'poly':
            col, pts = bits[1], ' '.join(bits[2:])
            out.append(f'    <polygon points="{pts}" fill="{col}"/>')
    return '\n'.join(out)


def _shapes(spec):
    """Split a shape spec into (kind, tokens), checking the kind is known."""
    for part in spec.split(';'):
        bits = part.split()
        if not bits:
            continue
        kind = bits[0].lower()
        if kind not in ('circle', 'ellipse', 'rect', 'poly') or len(bits) < 3:
            raise SystemExit(f'cannot read shape {part.strip()!r}; expected e.g. '
                             '"circle 974 753 442 #E8112D"')
        yield kind, bits


def shape_obstacles(spec):
    """Obstacles that follow a described shape's real edge, row by row."""
    out = []
    try:
        for kind, bits in _shapes(spec):
            n = [float(v) for v in bits[1:5] if re.fullmatch(r'-?[\d.]+', v)]
            if kind in ('circle', 'ellipse'):
                cx, cy = n[0], n[1]
                rx = n[2]
                ry = n[3] if kind == 'ellipse' and len(n) > 3 else n[2]

                def left_at(t, b, cx=cx, cy=cy, rx=rx, ry=ry):
                    # widest reach is on the row nearest the centre
                    y = min(max(cy, t), b)
                    f = 1 - ((y - cy) / ry) ** 2
                    return cx - rx * math.sqrt(f) if f > 0 else None

                out.append((cy - ry, cy + ry, left_at))
            elif kind == 'rect':
                x, y, w, h = n[0], n[1], n[2], n[3]
                out.append((y, y + h, lambda t, b, _x=x: _x))
            elif kind == 'poly':
                pts = [tuple(float(v) for v in p.split(','))
                       for p in bits[2:] if ',' in p]
                if len(pts) < 3:
                    continue
                ys = [p[1] for p in pts]

                def left_at(t, b, pts=pts):
                    # leftmost point of any edge crossing these rows
                    best = None
                    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                        for y in (max(t, min(y1, y2)), min(b, max(y1, y2))):
                            if min(y1, y2) <= y <= max(y1, y2) and t <= y <= b:
                                x = (x1 if y1 == y2 else
                                     x1 + (x2 - x1) * (y - y1) / (y2 - y1))
                                best = x if best is None else min(best, x)
                    return best

                out.append((min(ys), max(ys), left_at))
    except (IndexError, ValueError):
        return []
    return out


def merge_fragments(shapes, tol=6):
    """Rejoin one shape that another shape cut into pieces.

    A bar running behind a circle is detected as two fragments with the same
    x-range and a gap between them. Emitted as two rects it leaves a wedge of
    missing colour wherever the occluder's edge is not straight -- the eye
    reads one continuous bar, so the SVG has to contain one. Merged shapes are
    also, by definition, the ones behind, so they are drawn first.
    """
    def near(p, q):
        a = [int(p[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(q[i:i + 2], 16) for i in (1, 3, 5)]
        return max(abs(x - y) for x, y in zip(a, b)) <= 12

    out, used = [], set()
    for i, s in enumerate(shapes):
        if i in used:
            continue
        x0, y0, x1, y1 = s['box']
        joined = False
        for j, t in enumerate(shapes):
            if j <= i or j in used or not near(t['rgb'], s['rgb']):
                continue
            u0, v0, u1, v1 = t['box']
            same_col = abs(u0 - x0) <= tol and abs(u1 - x1) <= tol
            same_row = abs(v0 - y0) <= tol and abs(v1 - y1) <= tol
            if same_col or same_row:
                x0, y0 = min(x0, u0), min(y0, v0)
                x1, y1 = max(x1, u1), max(y1, v1)
                used.add(j)
                joined = True
        out.append({**s, 'box': [x0, y0, x1, y1], 'occluded': joined})
    # occluded shapes sit behind whatever cut them
    return sorted(out, key=lambda s: (not s['occluded'], -(s['box'][2] - s['box'][0])
                                      * (s['box'][3] - s['box'][1])))


def vector_artwork(sol, min_cover=0.92):
    """Redraw flat artwork as SVG shapes. None if it cannot be explained."""
    import numpy as np
    from PIL import Image
    shapes = [s for s in sol['artwork'] if s['px'] > 800 and s['kind'] != 'hairline']
    if not shapes:
        return None
    from PIL import ImageDraw
    w_pg, h_pg = sol['size']
    proof = Image.new('1', (w_pg, h_pg), 0)
    pen = ImageDraw.Draw(proof)
    body = []
    for s in merge_fragments(shapes):
        x0, y0, x1, y1 = s['box']
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if s['fill'] >= 0.90:
            body.append(f'    <rect x="{x0}" y="{y0}" width="{w}" height="{h}" '
                        f'fill="{s["rgb"]}"/>')
            pen.rectangle([x0, y0, x1, y1], fill=1)
        elif 0.68 <= s['fill'] <= 0.89:
            body.append(f'    <ellipse cx="{x0 + w / 2:.1f}" cy="{y0 + h / 2:.1f}" '
                        f'rx="{w / 2:.1f}" ry="{h / 2:.1f}" fill="{s["rgb"]}"/>')
            pen.ellipse([x0, y0, x1, y1], fill=1)
        else:
            return None            # not a shape we can name; use the raster

    # Prove it by drawing the shapes and comparing with the real artwork.
    # Checking bounding boxes would be meaningless -- a box always covers its
    # own content. This catches a half-disc emitted as a full ellipse, which
    # has the right box and the wrong shape.
    a = np.array(Image.open(sol['image']).convert('RGB')).astype(int)
    paper = tuple(int(sol['paper'][i:i + 2], 16) for i in (1, 3, 5))
    ink = np.abs(a - np.array(paper)).max(2) > 26
    for ln in sol['lines']:
        ink[max(0, ln['y0'] - 4):ln['y1'] + 5, max(0, ln['left'] - 4):ln['right'] + 5] = False
    drawn = np.array(proof, bool)
    iou = (ink & drawn).sum() / max(1, (ink | drawn).sum())
    print(f'  vector artwork: {len(body)} shapes, IoU vs real artwork {iou:.3f}')
    return '\n'.join(body) if iou >= min_cover else None


def artwork_svg(spec, sol, workdir, quality=76):
    if not spec or spec == 'none':
        return ''
    if spec.startswith('shapes:'):
        body = spec.split(':', 1)[1]
        if os.path.exists(body):
            return '  ' + open(body).read().strip()
        return parse_shapes(body)
    if spec == 'auto':
        v = vector_artwork(sol)
        if v:
            return v
        print('  (artwork not explainable as shapes; falling back to mask)')
        print('   measured shapes -- describe them with --artwork "shapes:..." '
              'for a fully vector poster:')
        for sh in sorted(sol.get('artwork', []), key=lambda x: -x['px'])[:6]:
            x0, y0, x1, y1 = sh['box']
            print(f"     {sh['rgb']}  box {x0},{y0} {x1 - x0 + 1}x{y1 - y0 + 1}"
                  f"  fill {sh['fill']}  ({sh['kind']})")
        spec = 'mask'
    from PIL import Image, ImageDraw
    src = sol['image']
    if spec.startswith('crop:'):
        x0, y0, x1, y1 = [int(v) for v in spec.split(':', 1)[1].split(',')]
        im = Image.open(src).convert('RGB').crop((x0, y0, x1, y1))
        return embed(im, x0, y0, workdir, quality)
    if spec.startswith('mask'):
        # Blank every measured line of type and keep everything else. The
        # general case: it works whether the artwork sits beside the type,
        # behind it, or in its own band.
        im = Image.open(src).convert('RGB')
        d = ImageDraw.Draw(im)
        fixed = int(spec.split(':', 1)[1]) if ':' in spec else None
        # Blank the type's own ink, dilated a little, rather than boxes round
        # it. Where a headline sits over artwork, a box would cut a square hole
        # in whatever is behind; the ink itself only costs a thin outline.
        import numpy as np
        from scipy import ndimage
        from blank import text_ink_mask
        # Blank exactly the bands the solution assigned copy to -- the lines
        # it is about to reset -- rather than re-detecting. Re-detection could
        # disagree with the solver (dropping `exclude` once blanked the labels
        # off a botanical plate), and it blanked unassigned bands too, erasing
        # artwork nobody replaced. The knobs still set the ink masks' contrast.
        opts = {k: v for k, v in (sol.get('knobs') or {}).items()
                if k in ('min_glyphs', 'solid_radius', 'rule_length',
                         'rule_thick', 'contrast', 'window', 'wrap_cap_ratio',
                         'exclude')}
        opts['exclude'] = [tuple(int(v) for v in x.split(',')) if isinstance(x, str)
                           else tuple(x) for x in (opts.get('exclude') or [])]
        used = sorted({b for x in sol['assigned'] for b in x['bands']})
        ink_txt, _ = text_ink_mask(src, lines=[sol['lines'][b] for b in used], **opts)
        if fixed is not None:
            ink_txt = ndimage.binary_dilation(
                ink_txt, np.ones((2 * fixed + 1,) * 2, bool))
        arr0 = np.array(im).astype(float)
        arr0 = inpaint(arr0, ink_txt)
        # Repaint any knockout panel that was named on the command line. The
        # panel is paper showing through a shape, so blanking the type that
        # sits in it eats its edge, and no fill taken from the surroundings
        # knows the boundary was straight. Confined to pixels that were paper
        # to begin with or were blanked, so nothing else inside is disturbed.
        ko = [tuple(int(v) for v in k.split(',')) if isinstance(k, str) else tuple(k)
              for k in ((sol.get('knobs') or {}).get('knockout') or [])]
        if ko:
            pap = np.array([int(sol['paper'][i:i + 2], 16) for i in (1, 3, 5)])
            was_paper = np.abs(np.array(Image.open(src).convert('RGB')).astype(int)
                               - pap).max(2) < 26
            for x0, y0, x1, y1 in ko:
                sel = np.zeros(arr0.shape[:2], bool)
                sel[y0:y1 + 1, x0:x1 + 1] = True
                sel &= (was_paper | ink_txt)
                arr0[sel] = pap
            print(f'  repainted {len(ko)} knockout panel(s) in the paper colour')
        im = Image.fromarray(arr0.round().clip(0, 255).astype('uint8'))
        d = ImageDraw.Draw(im)

        import numpy as np
        paper = tuple(int(sol['paper'][i:i + 2], 16) for i in (1, 3, 5))

        # Sweep up ghosts. If a glyph was never detected as text -- because a
        # rule grazed it and the pair got classified as artwork, say -- it
        # survives the blanking above and shows up in the finished SVG as a
        # stray letter beside the reset line. Anything letter-shaped sitting
        # inside the text block is type we missed, so blank it too.
        flat = [l for l in sol['lines'] if not l.get('frame')]
        if flat:
            from scipy import ndimage
            caps = [l['cap'] for l in flat]
            med = float(np.median(caps))
            tx0 = min(l['left'] for l in flat) - 2 * med
            tx1 = max(l['right'] for l in flat) + 2 * med
            ty0 = min(l['y0'] for l in flat) - med
            ty1 = max(l['y1'] for l in flat) + med
            arr = np.array(im).astype(int)
            left = np.abs(arr - np.array(paper)).max(2) > 26
            # Detach rules before labelling -- purely to see what is behind
            # them. A missed glyph is usually missed precisely because a rule
            # grazes it, so while they are fused the pair spans the page and
            # looks nothing like a letter. The rules stay in the artwork; only
            # the labelling is done without them.
            from masks import strip_rules
            detached, _ = strip_rules(left, 120, 5)
            lab, n = ndimage.label(detached, np.ones((3, 3), bool))
            excl = (opts['exclude'] + [tuple(f['box']) for f in sol.get('frames') or []]
                    + [tuple(a['box']) for a in sol.get('art') or []])
            swept = 0
            for i, sl in enumerate(ndimage.find_objects(lab), start=1):
                ys, xs = sl
                bh, bw = ys.stop - ys.start, xs.stop - xs.start
                if not (0.25 * min(caps) <= bh <= 2.2 * max(caps)):
                    continue
                if bw > 6 * bh:                     # a rule, not a letter
                    continue
                # ...nor a vertical one: no letter is both that thin and much
                # taller than a capital. A short rule between list columns
                # (wpa_new_deal, east_german_defa) escapes the rule detector
                # at under 120px and was being painted out as a stray glyph.
                if bh > 1.5 * med and bw < 0.12 * bh:
                    continue
                if not (tx0 <= xs.start and xs.stop <= tx1
                        and ty0 <= ys.start and ys.stop <= ty1):
                    continue
                # Only beside a line that was reset: a stray of ITS lettering
                # sits on its rows. A mark on rows no reset line covers is a
                # line nothing was reset over -- undetected copy, left as the
                # original's pixels -- and sweeping it erases that copy
                # (blaxploitation's "Ferret racing", reggae's list).
                if not any(sol['lines'][b]['y0'] - 0.5 * sol['lines'][b]['cap'] <= (ys.start + ys.stop) / 2
                           <= sol['lines'][b]['y1'] + 0.5 * sol['lines'][b]['cap']
                           for b in used if not sol['lines'][b].get('frame')):
                    continue
                # A small solid mark of a colour none of the reset lines beside
                # it are set in is part of the design, not a stray letter: a
                # green bullet between a date's two halves (dnb_fractal_flyer).
                # Only a compact blob, well under a letter's height: letters
                # in an accent colour (a blue FLOOD) and glitch fringes are
                # not that, and are still swept.
                px = arr[sl][lab[sl] == i]
                beside = [sol['lines'][b] for b in used if not sol['lines'][b].get('frame')
                          and sol['lines'][b]['y0'] - 0.5 * sol['lines'][b]['cap']
                          <= (ys.start + ys.stop) / 2
                          <= sol['lines'][b]['y1'] + 0.5 * sol['lines'][b]['cap']]
                blob = (0.6 <= bw / max(bh, 1) <= 1.6 and bh < 0.7 * min(L['cap'] for L in beside or [{'cap': 1e9}])
                        and (lab[sl] == i).mean() > 0.6)
                if len(px) and beside and blob:
                    ink = np.median(px, 0)
                    drawn = [L['rgb'] for L in beside] + [
                        f.partition('=')[2].partition('@')[0]
                        for f in (sol.get('knobs') or {}).get('fill') or []]
                    if all(np.abs(ink - np.array([int(c[j:j + 2], 16) for j in (1, 3, 5)])).max() > 80
                           for c in drawn if c.startswith('#') and len(c) == 7):
                        continue
                # Never bite into something the solver already identified as
                # artwork: a corner of a circle or bar can pass the size test.
                if any(not (xs.stop < w0 or xs.start > w1
                            or ys.stop < v0 or ys.start > v1)
                       for w0, v0, w1, v1 in
                       (s['box'] for s in sol.get('artwork', []))):
                    continue
                # Nor anything in a box the agent excluded: that says "this is
                # artwork, do not look for type here", and a botanical plate's
                # Latin labels are letter-shaped and inside the text block.
                cx, cy = (xs.start + xs.stop) / 2, (ys.start + ys.stop) / 2
                if any(e0 <= cx <= e2 and e1 <= cy <= e3 for e0, e1, e2, e3 in excl):
                    continue
                # Nor type the solver found but nothing is reset over (copy
                # left as the original's pixels with --assign KEY=none, or an
                # illustration's own label): it is artwork by decision, and
                # sweeping it erased nasa_worm's whole small-print block.
                if any(not (xs.stop <= L['left'] or xs.start > L['right']
                            or ys.stop <= L['y0'] or ys.start > L['y1'])
                       for bi, L in enumerate(sol['lines'])
                       if bi not in used and not L.get('frame')):
                    continue
                # Nor a list marker: a bullet is letter-sized and sits in the
                # text block, but the solver set it aside as artwork on purpose.
                if any(l.get('marker') and not (
                        xs.stop <= l['marker'][0] or xs.start > l['marker'][2]
                        or ys.stop <= l['marker'][1] or ys.start > l['marker'][3])
                       for l in sol['lines']):
                    continue
                d.rectangle([xs.start - 2, ys.start - 2, xs.stop + 1, ys.stop + 1],
                            fill=sol['paper'])
                swept += 1
            if swept:
                print(f'  swept {swept} undetected glyph(s) out of the artwork')

        # crop to what is actually left, so we do not embed empty paper
        arr = np.array(im).astype(int)
        ink = np.abs(arr - np.array(paper)).max(2) > 26
        if not ink.any():
            return ''
        ys, xs = np.where(ink.any(1))[0], np.where(ink.any(0))[0]
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return embed(im.crop(box), box[0], box[1], workdir, quality, 'artwork (masked)')
    raise SystemExit(f'unknown artwork spec {spec!r}')


def coloured(p):
    """A line's text, with any words given their own colour as tspans."""
    out = esc(p['text'])
    for words, colour in p.get('words') or []:
        w = esc(words)
        if w in out:
            out = out.replace(w, f'<tspan fill="{colour}">{w}</tspan>', 1)
    return out


def build(sol, chosen, artwork, workdir, title=''):
    # Where the artwork has been described, the type can be fitted against the
    # shapes themselves rather than their bounding boxes.
    shapes = (shape_obstacles(artwork.split(':', 1)[1])
              if artwork and artwork.startswith('shapes:')
              and not os.path.exists(artwork.split(':', 1)[1]) else None)
    placed, used = place(sol, chosen, shapes)
    placed = position(placed, {g: f for g, (c, f) in used.items()})
    w, h = sol['size']

    # Each face is subset to just the glyphs its groups use, so two different
    # subsets must never share a family name: the browser would match both
    # classes to one of them and fall back to a system font for every glyph the
    # other group needed -- a word like "noon" coming out half Archivo, half
    # Helvetica. Groups that resolved to the same font file share one face and
    # the union of their text; anything else gets a name of its own.
    faces = {}
    for gname, (c, face) in used.items():
        f = faces.setdefault((c['path'], c['index'], c['weight']),
                             {'cand': c, 'groups': [], 'text': ''})
        f['groups'].append(gname)
        f['text'] += ''.join(p['text'] for p in placed if p['group'] == gname)

    css, classes, taken = [], {}, set()
    for i, f in enumerate(faces.values()):
        c = f['cand']
        # Name the @font-face after the real family. Opening the finished SVG
        # should tell you what it is set in.
        base = re.sub(r'[^A-Za-z0-9]', '', f"{c['family']}{c['sub']}") or f'Face{i}'
        fam, n = base, 2
        while fam in taken:
            fam, n = f'{base}{n}', n + 1
        taken.add(fam)
        for gname in f['groups']:
            classes[gname] = fam
        b64, nbytes, feats = subset_b64(c['path'], f['text'], index=c['index'])
        print(f"  [{', '.join(f['groups'])}] {c['family']} {c['sub']} -> "
              f"{nbytes/1024:.1f}KB subset, features {feats}")
        css.append(face_css(fam, b64, c['weight'], c['path']))
        cmap = TTFont(io.BytesIO(base64.b64decode(b64))).getBestCmap()
        gone = sorted({ch for ch in f['text'] if ord(ch) not in cmap})
        if gone:
            print(f"    WARNING: embedded subset lacks {''.join(gone)!r} -- "
                  f"the browser will substitute a system font per character")
        # Pin the weight on the class too, so matching never has to guess.
        css.append(f'      .{fam.lower()} {{ font-family: "{fam}", sans-serif; '
                   f'font-weight: {c["weight"]}; }}')

    css.append('      text { white-space: pre; }')

    # One filter per distinct shadow, applied to the <text> itself: the line
    # stays a single element, so nothing that reads the SVG sees it twice.
    shadows, filters = {}, []
    for p in placed:
        if p.get('shadow') and p['shadow'] not in shadows:
            dx, dy, colour = p['shadow'].split(',')
            n = f'p2svg-shadow{len(shadows)}'
            shadows[p['shadow']] = n
            filters.append(f'    <filter id="{n}" x="-5%" y="-20%" width="115%" height="150%">'
                           f'<feDropShadow dx="{dx}" dy="{dy}" stdDeviation="0" '
                           f'flood-color="{colour}" flood-opacity="1"/></filter>')

    body = '\n'.join(
        f'    <text class="{classes[p["group"]].lower()}" x="{p["x"]}" '
        + ({'centre': 'text-anchor="middle" ', 'right': 'text-anchor="end" '}
           .get(p['anchor'], '')) +
        f'y="{p["y"]}" font-size="{p["size"]}" fill="{p["fill"]}"'
        + (f' letter-spacing="{p["ls_em"]:g}em"' if p.get('ls_em') else '')
        + (f' filter="url(#{shadows[p["shadow"]]})"' if p.get('shadow') else '')
        + (f' transform="rotate({p["frame"]["angle"]:.2f} {p["frame"]["cx"]:.1f} '
           f'{p["frame"]["cy"]:.1f})"' if p.get('frame') else '') + '>'
        f'{coloured(p)}</text>' for p in placed)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"
     aria-label="{esc(title or 'Gig poster')}">
  <title>{esc(title or 'Poster')}</title>
  <desc>{esc(' / '.join(c['text'] for c in sol.get('copy') or []))}</desc>
  <defs>
    <!-- Subset to the glyphs used; kerning retained, so the browser sets each
         line itself. No tracking is applied anywhere. -->
    <style type="text/css"><![CDATA[
{chr(10).join(css)}
    ]]></style>
{chr(10).join(filters)}
  </defs>

  <rect width="{w}" height="{h}" fill="{sol['paper']}"/>
{artwork_svg(artwork, sol, workdir)}

{body}
</svg>
'''


def build_file(sol, out, faces=(), artwork='none', workdir='/tmp', title=''):
    """Resolve faces, build, write `out`. Returns the SVG text.

    `faces` are `group=Name` overrides, as on the command line.
    """
    overrides = dict(x.split('=', 1) for x in faces)
    # An override may name a copy line instead of a group: that line leaves
    # its group and is set in a face of its own -- one date in a semibold
    # where the rest of the small print is regular.
    for key in [k for k in overrides if k not in sol['groups']]:
        home = next((g for g, v in sol['groups'].items() if key in v['lines']), None)
        if home is None:
            # Group names follow the measurement: a line kept as art, or a
            # case decided, regroups the rest and a stored group name can
            # vanish. Say so and carry on; meta.py --reviewed refuses a
            # poster with a group whose face nobody chose, so nothing ships
            # unexamined.
            print(f'  ! --face {key}=...: no face group or copy line {key!r} now '
                  f'(groups: {", ".join(sol["groups"])}); ignored')
            del overrides[key]
            continue
        parent = sol['groups'][home]
        parent['lines'] = [k for k in parent['lines'] if k != key]
        sol['groups'][key] = dict(parent, lines=[key],
                                  distortions=[d for d in parent.get('distortions', [])
                                               if d['key'] == key])
        if not parent['lines']:
            del sol['groups'][home]
    chosen = {g: pick(sol['groups'][g], overrides.get(g)) for g in sol['groups']}
    svg = build(sol, chosen, artwork, workdir, title)
    open(out, 'w').write(svg)
    print(f'-> {out}  ({len(svg)/1024:.0f}KB)')
    return svg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('solution')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--face', action='append', default=[],
                    help='group=FamilyName or group=#2')
    ap.add_argument('--artwork', default='none')
    ap.add_argument('--title', default='')
    ap.add_argument('--workdir', default='/tmp')
    a = ap.parse_args()
    build_file(json.load(open(a.solution)), a.out, a.face, a.artwork, a.workdir, a.title)


if __name__ == '__main__':
    main()

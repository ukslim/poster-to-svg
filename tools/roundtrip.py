#!/usr/bin/env python3
"""Round trip: draw a poster whose answer is known, convert it, grade it.

    python3 roundtrip.py                  # 6 synthetic gig posters
    python3 roundtrip.py --n 12 --seed 4 --keep   # keep the work for a look

The real posters have no answer key -- only judgement. A synthetic one does:
it is drawn from the gig copy in two known faces (a display face for the
headliner, a text face for the rest), at known sizes and positions, with a
coloured panel holding reversed-out type and a flat shape for artwork. Then
the ordinary pipeline runs on it (convert.py, against a throwaway site) and
every stage is graded against what was drawn:

  assignment  each copy line on the band it was drawn in
  face        where the true family ranks in each group's shortlist, and
              whether it is the one the builder would ship
  build       check_svg passes; audit.py's leads (none expected but width)

It degrades the drawing the way generated bitmaps are degraded -- softened,
WebP-compressed -- but it cannot imitate a generator's drifting letterforms,
so a pass here is necessary, not sufficient.
"""
import argparse, io, json, os, random, shutil, subprocess, sys, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalogue  # noqa: E402
import sitepaths  # noqa: E402

W, H = 1024, 1536


def pick_faces(rng):
    """A display face and a text face, from families that can set the copy."""
    # Families Google classifies as ordinary text or display type, with no
    # theme: a dot-matrix or stencil face is a fair test, but a poor use of a
    # small sample.
    def plain(r):
        t = r.get('tags', {})
        return (any(k.startswith(('/Sans/', '/Serif/', '/Slab/')) for k in t)
                and not any(k.startswith('/Theme/') for k in t))
    ok = [r for r in catalogue.load() if r['embed'] and not r['missing']
          and not r.get('italic') and not r.get('wdth') and plain(r)]
    display = [r for r in ok if r.get('category') in ('Sans Serif', 'Display', 'Serif')
               and r['weight'] >= 600]
    text = [r for r in ok if r.get('category') in ('Sans Serif', 'Serif')
            and 400 <= r['weight'] <= 700]
    d = rng.choice(display)
    t = rng.choice([r for r in text if r['family'] != d['family']])
    return d, t


def draw(rng, copy, d, t):
    """-> (PIL image, truth). truth[key] = dict(boxes, cap, family, weight)."""
    paper = rng.choice([(250, 246, 238), (255, 255, 255), (244, 238, 222)])
    ink = rng.choice([(24, 24, 24), (30, 40, 70), (60, 30, 30)])
    panel = rng.choice([(20, 110, 110), (170, 40, 40), (30, 50, 110)])
    accent = rng.choice([(230, 180, 40), (220, 90, 60), (90, 150, 90)])
    im = Image.new('RGB', (W, H), paper)
    dr = ImageDraw.Draw(im)
    centred = rng.random() < 0.5
    margin = 60
    truth = {}
    fd = catalogue.face_file(d)[0]
    ft = catalogue.face_file(t)[0]

    def put(key, text, face_path, rec, cap, y_base, colour, parts=None):
        size = cap * rec['upem'] / rec['cap']
        fnt = ImageFont.truetype(face_path, int(round(size)))
        # Check every line fits before drawing any: shrinking after drawing
        # the first line left it on the page twice.
        for part in (parts or [text]):
            bb = fnt.getbbox(part, anchor='ls')
            if bb[2] - bb[0] > W - 2 * margin:
                return put(key, text, face_path, rec, int(cap * 0.85), y_base,
                           colour, parts) if cap > 12 else None
        boxes, base = [], y_base
        for part in (parts or [text]):
            bb = fnt.getbbox(part, anchor='ls')
            w = bb[2] - bb[0]
            x = (W - w) / 2 - bb[0] if centred else margin - bb[0]
            dr.text((x, base), part, font=fnt, fill=colour, anchor='ls')
            boxes.append([int(x + bb[0]), int(base + bb[1]), int(x + bb[2]), int(base + bb[3])])
            base += int(cap * 1.25)
        truth[key] = dict(boxes=boxes, cap=cap, family=rec['family'], weight=rec['weight'],
                          size=int(round(size)), bases=[y_base + int(cap * 1.25) * k
                                                        for k in range(len(boxes))])
        return base

    # flat artwork: a circle off to one side, clear of the type
    r = rng.randint(120, 200)
    cx = W - r // 2 if not centred else rng.choice([r // 2, W - r // 2])
    dr.ellipse([cx - r, 700 - r, cx + r, 700 + r], fill=accent)
    # panel for the date and venue, type reversed out of it
    dr.rectangle([0, 1180, W, 1340], fill=panel)

    text = {c['key']: c['text'] for c in copy}
    y = put('presenter', text['presenter'], ft, t, 24, 90, ink)
    head_cap = rng.randint(110, 150)
    words = text['headliner'].split(' ')
    cut = len(words) // 2
    parts = [' '.join(words[:cut]), ' '.join(words[cut:])]
    y = None
    while y is None and head_cap > 60:
        y = put('headliner', text['headliner'], fd, d, head_cap, 150 + head_cap, ink, parts)
        head_cap -= 10
    put('support', text['support'], ft, t, 34, y + 20, ink)
    put('date', text['date'], ft, t, 40, 1245, (250, 250, 250))
    put('venue', text['venue'], ft, t, 28, 1300, (250, 250, 250))
    put('tickets', text['tickets'], ft, t, 26, 1395, ink)
    put('ticket_source', text['ticket_source'], ft, t, 22, 1440, ink)
    put('footer', text['footer'], ft, t, 22, 1485, ink)
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
    buf = io.BytesIO()
    im.save(buf, 'WEBP', quality=rng.randint(75, 90))
    return Image.open(buf).convert('RGB'), truth


def grade_assignment(sol, truth):
    """How many copy lines landed on the band they were drawn in."""
    L = sol['lines']
    got = {a['key']: [L[b] for b in a['bands']] for a in sol['assigned']}
    ok, bad = 0, []
    for key, tr in truth.items():
        bands = got.get(key)
        if not bands or len(bands) != len(tr['boxes']):
            bad.append(key)
            continue
        hit = all(abs(b['baseline'] - (bx[3] if False else bx[1] + tr['cap'])) < 0.4 * tr['cap']
                  and min(b['right'], bx[2]) - max(b['left'], bx[0]) > 0.6 * (bx[2] - bx[0])
                  for b, bx in zip(bands, tr['boxes']))
        ok += hit
        if not hit:
            bad.append(key)
    return ok, bad


def grade_sizes(svg_path, truth):
    """Set font-size against drawn size, per line, where the shipped face is
    the drawn one (another face at the same cap is a different size)."""
    import re, html
    texts = [(float(y), float(fs)) for y, fs in
             re.findall(r'<text[^>]*?y="([\d.]+)" font-size="([\d.]+)"', open(svg_path).read())]
    errs = []
    for key, tr in truth.items():
        for base in tr['bases']:
            near = [fs for y, fs in texts if abs(y - base) < 0.3 * tr['cap']]
            if near:
                errs.append((key, near[0] / tr['size'] - 1))
    return errs


def grade_faces(sol, truth):
    """For each face group: rank of the true family, and whether it ships."""
    out = []
    for g, v in sol['groups'].items():
        fams = [truth[k]['family'] for k in v['lines'] if k in truth]
        if not fams:
            continue
        want = max(set(fams), key=fams.count)
        order = []
        for c in v['candidates']:
            if c['family'] not in order:
                order.append(c['family'])
        rank = order.index(want) + 1 if want in order else None
        shipped = next((c['family'] for c in v['candidates'] if c['embed']), None)
        out.append(dict(group=g, want=want, rank=rank, shipped=shipped,
                        glyphs=len(v['shape_glyphs'])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--keep', action='store_true', help='keep the throwaway site')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    real = sitepaths.root()
    site = tempfile.mkdtemp(prefix='p2svg-roundtrip-')
    os.makedirs(os.path.join(site, 'assets', 'poster-examples'))
    os.symlink(os.path.join(real, '_data'), os.path.join(site, '_data'))
    os.symlink(os.path.join(real, '_includes'), os.path.join(site, '_includes'))
    from copytext import load_copy
    copy = load_copy('gig')
    tot = dict(lines=0, ok=0, groups=0, top1=0, top5=0, shipped=0, built=0)
    for i in range(a.n):
        d, t = pick_faces(rng)
        im, truth = draw(rng, copy, d, t)
        style = f'synthetic_{a.seed}_{i}'
        im.save(os.path.join(site, 'assets', 'poster-examples', f'{style}-gig-v2.webp'))
        env = dict(os.environ, POSTER_SITE=site)
        p = subprocess.run([sys.executable, os.path.join(HERE, 'convert.py'), style, 'gig'],
                           capture_output=True, text=True, env=env)
        soln = f'/tmp/p2svg-{style}-gig/solution.json'
        if not os.path.exists(soln):
            print(f'{style}: no solution\n{p.stdout[-400:]}{p.stderr[-400:]}')
            continue
        sol = json.load(open(soln))
        ok, bad = grade_assignment(sol, truth)
        faces = grade_faces(sol, truth)
        built = os.path.exists(os.path.join(site, 'assets', 'poster-svg', f'{style}-gig-v2.svg'))
        tot['lines'] += len(truth)
        tot['ok'] += ok
        tot['built'] += built
        for f in faces:
            tot['groups'] += 1
            tot['top1'] += f['rank'] == 1
            tot['top5'] += bool(f['rank'] and f['rank'] <= 5)
            tot['shipped'] += f['shipped'] == f['want']
        print(f"{style}: {catalogue.label(d)} / {catalogue.label(t)}")
        if built:
            shipped = {f['group']: f['shipped'] == f['want'] for f in faces}
            right_face = {k for g, v in sol['groups'].items() if shipped.get(g)
                          for k in v['lines']}
            errs = [(k, e) for k, e in grade_sizes(
                os.path.join(site, 'assets', 'poster-svg', f'{style}-gig-v2.svg'), truth)
                if k in right_face]
            if errs:
                worst = max(errs, key=lambda x: abs(x[1]))
                med = float(np.median([abs(e) for _, e in errs]))
                tot.setdefault('size_errs', []).extend(abs(e) for _, e in errs)
                print(f"    size: median error {med:.1%}, worst {worst[0]} {worst[1]:+.1%}")
        print(f"    assignment {ok}/{len(truth)}" + (f"  wrong: {', '.join(bad)}" if bad else '')
              + f"   built: {'yes' if built else 'NO'}")
        for f in faces:
            print(f"    [{f['group']}] {f['want']}: rank {f['rank'] or '-'} of shortlist, "
                  f"ships {f['shipped']}  ({f['glyphs']} glyphs)")
    n = max(1, tot['groups'])
    print(f"\nassignment {tot['ok']}/{tot['lines']} lines; built {tot['built']}/{a.n}; "
          f"faces: true family first {tot['top1']}/{n}, top-5 {tot['top5']}/{n}, "
          f"shipped {tot['shipped']}/{n}")
    if tot.get('size_errs'):
        e = tot['size_errs']
        print(f"sizes (where the right face shipped): median error {float(np.median(e)):.1%}, "
              f"within 3%: {sum(1 for x in e if x <= 0.03)}/{len(e)}")
    if a.keep:
        print(f'site kept: {site}')
    else:
        shutil.rmtree(site, ignore_errors=True)


if __name__ == '__main__':
    main()

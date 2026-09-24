#!/usr/bin/env python3
"""How well does the ranking identify a typeface? A benchmark to tune against.

    python3 bench_fonts.py                 # synthetic: known faces, degraded
    python3 bench_fonts.py --n 20 --seed 3

Takes styles from the catalogue whose answer is known, sets two lines of the
posters' copy in each -- a headline and a line of small print -- and degrades
them the way a generated bitmap is degraded: softened, compressed to WebP, and
squeezed or stretched by up to 8%. Then runs the same ranking the solver runs
and reports where the true family landed: top-1, top-5, and top-40 (the pool
that reaches shape scoring).

Tuning a weight in typefeatures.WEIGHTS or solve_type.FEATURE_WEIGHT should
move these numbers up, never just move one poster's answer.
"""
import argparse, io, os, random, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalogue  # noqa: E402

LINES = [('CARVER & THE FLOOD', 120), ('Friday 19th September, doors 7:30pm', 34)]


def specimen_image(path, squeeze, rng):
    """A page with the two lines, degraded; -> (RGB array, [band dicts])."""
    W, H = 1400, 420
    im = Image.new('RGB', (W, H), (250, 246, 238))
    d = ImageDraw.Draw(im)
    from fontTools.ttLib import TTFont
    f = TTFont(path)
    cap = f['glyf']['H'].yMax if 'glyf' in f else None
    upem = f['head'].unitsPerEm
    if not cap:
        from svgkit import Face
        cap = Face(path).bounds('H')[3]
    bases = [170, 330]
    for (text, cap_px), base in zip(LINES, bases):
        size = int(round(cap_px * upem / cap))
        d.text((40, base), text, font=ImageFont.truetype(path, size),
               fill=(25, 25, 25), anchor='ls')
    if abs(squeeze - 1) > 1e-3:
        im = im.resize((int(W * squeeze), H), Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    buf = io.BytesIO()
    im.save(buf, 'WEBP', quality=rng.randint(70, 90))
    return np.array(Image.open(buf).convert('RGB')).astype(int)


def bands_for(arr):
    """Measure the synthetic page the way the solver measures a poster."""
    import tempfile
    from bands import find_lines
    with tempfile.NamedTemporaryFile(suffix='.png') as t:
        Image.fromarray(arr.astype('uint8')).save(t.name)
        m = find_lines(t.name, min_glyphs=3)
    return m['lines']


def rank_one(r, rng, faces):
    from solve_type import glyph_crops, group_features, stage1, fetch_files, stage2
    path, _ = catalogue.face_file(r)
    squeeze = rng.uniform(0.92, 1.08)
    arr = specimen_image(path, squeeze, rng)
    lines = bands_for(arr)
    if len(lines) < 2:
        return None
    lines = sorted(lines, key=lambda l: l['y0'])[:2]
    group = [dict(key=f'l{i}', text=t, bands=[i]) for i, (t, _) in enumerate(LINES)]
    crops = glyph_crops(group, lines, arr)
    feats = group_features(group, lines, arr, crops)
    pool = stage1(group, lines, faces, feats)
    fams = [c['face']['family'] for c in pool]
    in_pool = r['family'] in fams
    ranked = stage2(fetch_files(pool), crops)
    order = []
    for c in ranked:
        if c['face']['family'] not in order:
            order.append(c['face']['family'])
    pos = order.index(r['family']) + 1 if r['family'] in order else None
    # A different family with the same letterforms is as good an answer for
    # a poster: Anek Kannada's Latin IS Anek Latin, Assistant is derived from
    # Source Sans. Count the top pick as equivalent when its glyphs match the
    # truth's as closely as a family matches itself across the degradation.
    equiv = pos == 1 or (ranked and same_letterforms(path, ranked[0]['face']['path']))
    return dict(family=r['family'], weight=r['weight'], squeeze=round(squeeze, 3),
                glyphs=len(crops), in_pool=in_pool, rank=pos, top=order[:3],
                equiv=bool(equiv))


def same_letterforms(a, b, chars='RGQaegSWMhnt', tol=0.8):
    """Mean chamfer between the two faces' glyphs, rendered alike, < tol."""
    from svgkit import Face, render_glyph
    from shapescore import chamfer
    fa, fb = Face(a), Face(b)
    ds = []
    for ch in chars:
        if fa.has(ch) and fb.has(ch):
            ga, gb = render_glyph(fa, ch, 160), render_glyph(fb, ch, 160)
            if ga is not None and gb is not None:
                ds.append(chamfer(ga > 128, gb > 128))
    return bool(ds) and float(np.mean(ds)) < tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    from solve_type import load_faces
    faces = load_faces()
    pool = [r for r in catalogue.load()
            if r['embed'] and not r.get('italic') and not r.get('wdth')
            and r.get('category') in ('Sans Serif', 'Serif', 'Display')
            and not r['missing']]
    picks = rng.sample(pool, min(a.n, len(pool)))
    res = []
    for r in picks:
        try:
            out = rank_one(r, rng, faces)
        except Exception as e:
            print(f"  {catalogue.label(r)}: failed ({e})")
            continue
        if out:
            res.append(out)
            mark = out['rank'] if out['rank'] else ('pool' if out['in_pool'] else '-')
            if out['equiv'] and out['rank'] != 1:
                mark = f'{mark}=eq'  # top pick has the same letterforms
            print(f"  {catalogue.label(r):38} squeeze {out['squeeze']:.2f} glyphs "
                  f"{out['glyphs']:2}  rank {mark!s:4}  top: {', '.join(out['top'])}")
    n = len(res)
    if not n:
        return
    top1 = sum(1 for x in res if x['rank'] == 1)
    top5 = sum(1 for x in res if x['rank'] and x['rank'] <= 5)
    pool_ = sum(1 for x in res if x['in_pool'])
    eq = sum(1 for x in res if x['equiv'])
    print(f'\n{n} faces: top-1 {top1} ({top1/n:.0%}), top-1 or same letterforms '
          f'{eq} ({eq/n:.0%}), top-5 {top5} ({top5/n:.0%}), '
          f'reached shape scoring {pool_} ({pool_/n:.0%})')


if __name__ == '__main__':
    main()

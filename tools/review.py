#!/usr/bin/env python3
"""The one image the type review needs: each face group, original above rebuild.

    python3 review.py mid_century_modern_graphic-gig
    python3 review.py surrealist-gig -o /tmp/r.png

At the top, the original and the published SVG whole. Below, for each face
group, a full-width strip of the original over the same strip of the
rebuild -- full width so that centring, size and the leading between rows
show as plainly as the letterforms. Each strip is captioned with the face
the build used, whether anyone chose it (`--face`) or the ranking did, the
brief, and the next candidates to try.

It measures nothing; it is for looking (SKILL.md step 6). Read it against
the checklist there: genre, signature letterforms, tone, layout.
"""
import argparse, json, os, shutil, sys
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402
from sheet import _label_font  # noqa: E402

W = 1000


def chosen_faces(sol):
    """{group: (label, chosen by eye?)} from the --face knobs, falling back to
    what build_svg.pick takes without one: the first embeddable candidate."""
    over = dict(f.split('=', 1) for f in (sol.get('knobs') or {}).get('face') or [])
    out = {}
    for g, v in sol['groups'].items():
        name = over.get(g) or next((over[k] for k in v['lines'] if k in over), None)
        if name and name.startswith('#'):
            c = v['candidates'][int(name[1:]) - 1]
            out[g] = (f"{c['family']} {c['sub']}", True)
        elif name:
            out[g] = (name, True)
        else:
            c = next((c for c in v['candidates'] if c['embed']), None)
            out[g] = (f"{c['family']} {c['sub']}" if c else '?', False)
    return out


def unchosen(sol):
    """Face groups nobody has chosen a face for."""
    return [g for g, (_, by_eye) in chosen_faces(sol).items() if not by_eye]


def strip_rows(sol, g):
    """(y0, y1) of the strip that shows group g: the group's largest band and
    its neighbours in the group (up to three rows, so leading shows)."""
    L = sol['lines']
    tilted = {k for f in sol.get('frames') or [] for k in f['keys']}
    keys = set(sol['groups'][g]['lines']) - tilted
    bands = sorted({bi for a in sol['assigned'] if a['key'] in keys for bi in a['bands']},
                   key=lambda i: L[i]['y0'])
    if not bands:
        return None
    big = max(range(len(bands)), key=lambda j: L[bands[j]]['cap'])
    win = bands[max(0, big - 1):big + 2]
    while len(win) > 1 and L[win[-1]]['y1'] - L[win[0]]['y0'] > 420:
        win = win[:-1] if win[-1] != bands[big] else win[1:]
    cap = max(L[i]['cap'] for i in win)
    return (max(0, min(L[i]['y0'] for i in win) - int(0.6 * cap)),
            min(sol['size'][1], max(L[i]['y1'] for i in win) + int(0.6 * cap)))


def review(name, out=None):
    from svgkit import render_svg
    svg = os.path.join(poster_site.svgs(), name + '-v2.svg')
    sol = json.load(open(os.path.join(poster_site.solutions(), name + '-v2.json')))
    src = os.path.join(poster_site.examples(), name + '-v2.webp')
    work = f'/tmp/p2svg-review-{name}'
    os.makedirs(work, exist_ok=True)
    shutil.copy(svg, os.path.join(work, 'svg.svg'))
    w, h = sol['size']
    png = render_svg(os.path.join(work, 'svg.svg'), os.path.join(work, 'render.png'), w, h,
                     workdir=work)
    orig, rend = Image.open(src).convert('RGB'), Image.open(png).convert('RGB')
    font = _label_font(16)

    half = (W - 8) // 2
    top = Image.new('RGB', (W, int(h * half / w) + 22), 'white')
    for i, im in enumerate((orig, rend)):
        top.paste(im.resize((half, int(h * half / w))), (i * (half + 8), 22))
    ImageDraw.Draw(top).text((4, 3), f'{name}: original | rebuild', fill='black', font=font)
    parts = [top]

    faces = chosen_faces(sol)
    for g, v in sol['groups'].items():
        rows = strip_rows(sol, g)
        if rows is None:
            continue
        y0, y1 = rows
        s = W / w
        sh = int((y1 - y0) * s)
        label, by_eye = faces[g]
        alts = ', '.join(f"#{i} {c['family']} {c['sub']}"
                         for i, c in enumerate(v['candidates'][:4], start=1))
        brief = ', '.join(f'{k} {n}' for k, n in (v.get('brief') or {}).items()) or 'none'
        caps = [f"[{g}] {', '.join(v['lines'])}",
                f"face: {label}  ({'chosen by eye' if by_eye else 'RANKING -- not chosen'})"
                f"   brief: {brief}",
                f"candidates: {alts}"]
        p = Image.new('RGB', (W, 2 * sh + 6 + 20 * len(caps) + 4), 'white')
        d = ImageDraw.Draw(p)
        for j, c in enumerate(caps):
            d.text((4, 2 + 20 * j), c, fill='black', font=font)
        y = 20 * len(caps) + 4
        for im in (orig, rend):
            p.paste(im.crop((0, y0, w, y1)).resize((W, sh)), (0, y))
            y += sh + 6
        parts.append(p)

    img = Image.new('RGB', (W, sum(p.height + 10 for p in parts)), '#999999')
    y = 0
    for p in parts:
        img.paste(p, (0, y))
        y += p.height + 10
    out = out or os.path.join(work, 'review.png')
    img.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='+', help='style-event, e.g. surrealist-gig')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    for n in a.names:
        n = n[:-3] if n.endswith('-v2') else n
        print(review(n, a.out if len(a.names) == 1 else None))


if __name__ == '__main__':
    main()

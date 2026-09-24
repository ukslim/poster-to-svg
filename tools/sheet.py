#!/usr/bin/env python3
"""A contact sheet for choosing a face: the original line beside the shortlist.

    python3 sheet.py /tmp/p2svg-mid_century_modern_graphic-gig/solution.json
    python3 sheet.py SOLUTION --group display --n 8 -o sheet.png

For each face group, the group's largest line cropped from the poster, then
the same words set in each of the top candidates at the measured cap height.
The numbers can shortlist; choosing between faces that agree to a few percent
is a judgement by eye, and this is the one image that judgement needs.
Pick with `convert.py --face group=#N` (N as numbered here), or name any
catalogue face: `--face group="Family:600"`.
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

W = 900


def _label_font(size=15):
    for p in ('/System/Library/Fonts/Helvetica.ttc',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def tile(img, caption, h):
    """Scale `img` to height h (capping width at W), caption beneath."""
    s = h / img.height
    img = img.resize((max(1, int(img.width * s)), h))
    if img.width > W:
        img = img.crop((0, 0, W, h))
    out = Image.new('RGB', (W, h + 20), 'white')
    out.paste(img, (0, 0))
    ImageDraw.Draw(out).text((4, h + 2), caption, fill='black', font=_label_font())
    return out


def group_sheet(sol, gname, n=6):
    from build_svg import pick
    from solve_type import split_text
    g = sol['groups'][gname]
    L = sol['lines']
    # the group's biggest band, and the words on it
    best = None
    for a in sol['assigned']:
        if a['key'] not in g['lines']:
            continue
        parts = split_text(a['text'], a['bands'], L) or [a['text']]
        for bi, part in zip(a['bands'], parts):
            if best is None or L[bi]['cap'] > L[best[0]]['cap']:
                best = (bi, part)
    if best is None:
        return None
    bi, text = best
    b = L[bi]
    src = sol['image']
    im = Image.open(src).convert('RGB')
    pad = int(0.35 * b['cap'])
    orig = im.crop((max(0, b['left'] - pad), max(0, b['y0'] - pad),
                    min(im.width, b['right'] + pad), min(im.height, b['y1'] + pad)))
    h = 90 if b['cap'] > 40 else 60
    tiles = [tile(orig, f"[{gname}] original, cap {b['cap']}: {text[:50]}", h)]
    ink = tuple(int(b['rgb'][i:i + 2], 16) for i in (1, 3, 5))
    ground = b.get('ground') or sol['paper']
    ground = tuple(int(ground[i:i + 2], 16) for i in (1, 3, 5))
    for i, c in enumerate(g['candidates'][:n], start=1):
        try:
            c = pick(g, f'#{i}')
        except SystemExit:
            continue
        from svgkit import Face, top_ratio
        size = b['cap'] / top_ratio(Face(c['path'], index=c.get('index')), text)
        kw = {'index': c['index']} if c.get('index') is not None else {}
        f = ImageFont.truetype(c['path'], max(4, int(round(size))), **kw)
        bb = f.getbbox(text, anchor='ls')
        img = Image.new('RGB', (bb[2] - bb[0] + 2 * pad, orig.height), ground)
        ImageDraw.Draw(img).text((pad - bb[0], pad + b['baseline'] - b['y0']), text,
                                 font=f, fill=ink, anchor='ls')
        cap = f"#{i} {c['family']} {c['sub']}{'' if c['embed'] else '  [id-only]'}"
        tiles.append(tile(img, cap, h))
    sheet = Image.new('RGB', (W, sum(t.height + 4 for t in tiles)), '#DDDDDD')
    y = 0
    for t in tiles:
        sheet.paste(t, (0, y))
        y += t.height + 4
    return sheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('solution')
    ap.add_argument('--group', action='append', help='default: every group')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    sol = json.load(open(a.solution))
    sheets = [s for s in (group_sheet(sol, g, a.n) for g in (a.group or sol['groups']))
              if s is not None]
    out = Image.new('RGB', (W * len(sheets) + 8 * (len(sheets) - 1),
                            max(s.height for s in sheets)), 'white')
    x = 0
    for s in sheets:
        out.paste(s, (x, 0))
        x += s.width + 8
    path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.solution)), 'faces.png')
    out.save(path)
    print(path)


if __name__ == '__main__':
    main()

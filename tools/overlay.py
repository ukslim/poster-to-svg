#!/usr/bin/env python3
"""Draw what the solver thinks a poster says, for a person (or Claude) to judge.

    python3 overlay.py swiss_international-fete            # current measurement
    python3 overlay.py risograph-gig --vs-solution         # stored | current, side by side
    python3 overlay.py a-gig b-fete c-gig -o sheet.png     # several in one sheet

Each band is numbered (#N) and, if assigned, boxed and labelled with its copy
key; bands nobody was assigned are shaded grey with '#N?'. The numbers are what
convert.py --assign takes: `--assign presenter=#0`. One small image replaces reading a table of band
coordinates against a look at the poster, which is where misassigned copy
hides. Deliberately small (default 380px per panel) to keep image tokens down.
"""
import argparse, json, os, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402

PALETTE = ['#E6194B', '#3CB44B', '#4363D8', '#F58231', '#911EB4', '#42D4F4',
           '#F032E6', '#9A6324', '#800000', '#469990', '#000075', '#808000']


def _font(size):
    for p in ('/System/Library/Fonts/Helvetica.ttc',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def panel(image_path, lines, assigned, width=380, title='', missing=()):
    """One annotated, downscaled poster. `missing` names copy lines that got no
    band -- the one failure a box cannot show, because there is nothing to box."""
    im = Image.open(image_path).convert('RGB')
    s = width / im.width
    im = im.resize((width, int(im.height * s)))
    # fade the poster so the boxes read
    im = Image.blend(im, Image.new('RGB', im.size, 'white'), 0.35)
    d = ImageDraw.Draw(im, 'RGBA')
    f = _font(12)
    used = set()
    for i, a in enumerate(assigned):
        col = PALETTE[i % len(PALETTE)]
        for j, b in enumerate(a['bands']):
            used.add(b)
            ln = lines[b]
            box = [ln['left'] * s - 1, ln['y0'] * s - 1, ln['right'] * s + 1, ln['y1'] * s + 1]
            d.rectangle(box, outline=col, width=2)
            # every band is labelled, continuation bands with their index, so a
            # wrapped line that lost or gained a band is visible at a glance
            label = f"#{b} " + (a['key'] if j == 0 else f"{a['key']} +{j}")
            tw = d.textlength(label, font=f)
            ty = max(0, box[1] - 13)
            d.rectangle([box[0], ty, box[0] + tw + 2, ty + 13], fill=(255, 255, 255, 210))
            d.text((box[0] + 1, ty), label, fill=col, font=f)
    for b, ln in enumerate(lines):
        if b in used:
            continue
        box = [ln['left'] * s, ln['y0'] * s, ln['right'] * s, ln['y1'] * s]
        d.rectangle(box, fill=(0, 0, 0, 70), outline=(0, 0, 0, 200), width=1)
        d.text((box[0] + 1, box[1]), f'#{b}?', fill='black', font=f)
    if title:
        d.rectangle([0, 0, width, 14], fill='white')
        d.text((2, 1), title, fill='black', font=f)
    if missing:
        msg = 'no band: ' + ', '.join(missing)
        d.rectangle([0, im.height - 15, width, im.height], fill='white')
        d.text((2, im.height - 14), msg, fill='#C00000', font=f)
    return im


def missing_keys(name, assigned):
    from measure import load_copy
    got = {a['key'] for a in assigned}
    return [c['key'] for c in load_copy(name.rsplit('-', 2)[1]) if c['key'] not in got]


def current(name):
    """Measure and assign now, with the stored solution's knobs if there is one."""
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    from regress import knobs_of
    from measure import find_lines, load_copy, assign_copy
    soln = os.path.join(poster_site.solutions(), name + '.json')
    if os.path.exists(soln):
        k = knobs_of(json.load(open(soln)))
    else:
        # no solution yet: number the bands exactly as convert.py will find
        # them, or --assign #N points at the wrong band
        from convert import DEFAULTS
        k = knobs_of({'knobs': DEFAULTS})
    src = os.path.join(poster_site.examples(), name + '.webp')
    m = find_lines(src, min_glyphs=k['min_glyphs'], solid_radius=k['solid_radius'],
                   rule_length=k['rule_length'], rule_thick=k['rule_thick'],
                   contrast=k['contrast'], window=k['window'],
                   wrap_cap_ratio=k['wrap_cap_ratio'], exclude=k['exclude'])
    assigned, note, cost = assign_copy(m['lines'], load_copy(name.rsplit('-', 2)[1]),
                                       wrap_cap_ratio=k['wrap_cap_ratio'])
    return src, m['lines'], assigned, f'now {cost:.2f}'


def stored(name):
    s = json.load(open(os.path.join(poster_site.solutions(), name + '.json')))
    return s['image'] if os.path.exists(s['image']) else os.path.join(
        poster_site.examples(), name + '.webp'), s['lines'], s['assigned'], \
        f"stored {s.get('alignment_cost', 0):.2f}"


def baseline(name):
    """The regress.py baseline snapshot, drawn as if it were a solution. It
    keeps only the assigned boxes, so unassigned bands are not shown."""
    from regress import BASELINE
    snap = json.load(open(BASELINE))[name]
    lines, assigned = [], []
    for key, boxes in snap['assigned'].items():
        idx = []
        for y0, y1, l, r in boxes:
            idx.append(len(lines))
            lines.append(dict(y0=y0, y1=y1, left=l, right=r))
        assigned.append(dict(key=key, bands=idx))
    assigned.sort(key=lambda a: lines[a['bands'][0]]['y0'])
    return (os.path.join(poster_site.examples(), name + '.webp'), lines, assigned,
            f"baseline {snap['cost']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='+', help='style-event, e.g. risograph-gig')
    ap.add_argument('--vs-solution', action='store_true',
                    help='stored assignment beside the current one')
    ap.add_argument('--vs-baseline', action='store_true',
                    help='the regress.py baseline beside the current one')
    ap.add_argument('--width', type=int, default=380)
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args()
    panels = []
    for n in a.names:
        name = n if n.endswith('-v2') else n + '-v2'
        short = name[:-3]
        for flag, fn in ((a.vs_solution, stored), (a.vs_baseline, baseline)):
            if flag:
                src, L, A, t = fn(name)
                panels.append(panel(src, L, A, a.width, f'{short}  {t}', missing_keys(name, A)))
        src, L, A, t = current(name)
        panels.append(panel(src, L, A, a.width, f'{short}  {t}', missing_keys(name, A)))
    w = sum(p.width for p in panels) + 6 * (len(panels) - 1)
    sheet = Image.new('RGB', (w, max(p.height for p in panels)), 'white')
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width + 6
    out = a.out or os.path.join('/tmp', f'overlay-{a.names[0]}.png')
    sheet.save(out)
    print(out)


if __name__ == '__main__':
    main()

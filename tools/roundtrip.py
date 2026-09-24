#!/usr/bin/env python3
"""Round trip: draw posters whose answers are known, convert them, grade them.

    python3 roundtrip.py --suite                 # the fixed suite; compare with its baseline
    python3 roundtrip.py --suite --save-baseline # record today's scorecard as the baseline
    python3 roundtrip.py --n 6 --seed 7 --keep   # ad hoc; keep the work to look at
    python3 roundtrip.py --suite --briefs        # also give convert.py character briefs

The real posters have no answer key -- only judgement. A synthetic one does.
Each is drawn from the real gig or fete copy, and records, per line, what was
drawn: the band, the face (family, weight, italic), the size, the case, the
letter-spacing, the alignment of its block, and whether it is reversed out of
a panel. The drawings vary what the real posters vary: display and text
faces of every classification, all-caps and letter-spaced lines, an italic
beneficiary (a third face), a list in one or two columns with or without
bullets and a rule between the columns, left, centred and right blocks, type
reversed out of a coloured panel, flat shapes for artwork. They are softened
and WebP-compressed as a generated bitmap is.

The ordinary pipeline (convert.py, against a throwaway site) runs on each,
and every stage is graded against what was drawn:

  assign    copy lines on the band they were drawn in
  face      per line: the shipped family is the drawn one (or has the same
            letterforms), weight within 100, italic matching
  size      set font-size within 3% (or 1.5px of cap) of the drawn size, where
            the face is right
  case      capitals where capitals were drawn, and not where they were not
  track     letter-spacing set where it was drawn, and not where it was not
  audit     audit.py's leads on the result: alignment, ghosts, damage

It cannot imitate a generator's drifting letterforms, so passing here is
necessary, not sufficient. But unlike the real posters, every failure here is
unambiguous.
"""
import argparse, html, io, json, os, random, re, shutil, subprocess, sys, tempfile
from multiprocessing import Pool
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalogue  # noqa: E402

W, H = 1024, 1536
SUITE = list(range(100, 112))      # 6 gig and 6 fete, alternating
BASELINE = os.path.join(os.path.dirname(HERE), 'tests', 'roundtrip_baseline.json')


# ------------------------------------------------------------------ faces
def _styles():
    """Plain faces that can set the copy: classified as sans, serif or slab by
    Google, no theme (a dot-matrix face is a fair test but a poor use of a
    small suite)."""
    def plain(r):
        t = r.get('tags', {})
        return (any(k.startswith(('/Sans/', '/Serif/', '/Slab/')) for k in t)
                and not any(k.startswith('/Theme/') for k in t))
    return [r for r in catalogue.load() if r['embed'] and not r['missing']
            and not r.get('wdth') and plain(r)]


def pick_faces(rng):
    """display, text, and the text family's italic if it has one."""
    ok = _styles()
    upright = [r for r in ok if not r.get('italic')]
    display = rng.choice([r for r in upright if r['weight'] >= 600])
    text = rng.choice([r for r in upright if 400 <= r['weight'] <= 700
                       and r['family'] != display['family']])
    italics = [r for r in ok if r.get('italic') and r['family'] == text['family']]
    italic = min(italics, key=lambda r: abs(r['weight'] - text['weight'])) if italics else None
    return display, text, italic


def top_cap(rec, text):
    """The tallest letter of `text` in font units: what a band measures as its
    cap, and what size must be computed from (see svgkit.top_ratio)."""
    tops = [rec['metrics'][c][4] for c in text
            if c.isascii() and c.isalnum() and c not in 'ij' and c in rec['metrics']]
    return max(tops) if tops else rec['cap']


# ----------------------------------------------------------------- drawing
class Page:
    def __init__(self, rng):
        self.rng = rng
        self.paper = rng.choice([(250, 246, 238), (255, 255, 255), (244, 238, 222),
                                 (236, 240, 244)])
        self.ink = rng.choice([(24, 24, 24), (30, 40, 70), (60, 30, 30), (20, 60, 50)])
        self.im = Image.new('RGB', (W, H), self.paper)
        self.dr = ImageDraw.Draw(self.im)
        self.truth = {}

    @staticmethod
    def measure(fnt, text, track):
        bb = fnt.getbbox(text, anchor='ls')
        return bb[2] - bb[0] + track * max(0, len(text) - 1), bb

    def line(self, key, text, rec, cap, base, x0, x1, align, colour=None,
             parts=None, track_em=0.0, reversed_=False, bullet=False):
        """Draw one copy line (wrapped over `parts` if given), shrinking it
        until every part fits between x0 and x1. Records the truth. -> the
        next baseline."""
        colour = colour or self.ink
        parts = parts or [text]
        path = catalogue.face_file(rec)[0]
        room = x1 - x0 - (cap if bullet else 0)
        while True:
            size = cap * rec['upem'] / top_cap(rec, text)
            fnt = ImageFont.truetype(path, max(6, int(round(size))))
            track = track_em * size
            if all(self.measure(fnt, p, track)[0] <= room for p in parts) or cap <= 10:
                break
            cap = int(cap * 0.9)
        boxes, b = [], base
        for part in parts:
            w, bb = self.measure(fnt, part, track)
            lx = x0 + (cap if bullet else 0)
            x = {'left': lx, 'centre': (x0 + x1 - w) / 2, 'right': x1 - w}[align] - bb[0]
            if bullet:
                r = max(2, cap // 5)
                cx = x + bb[0] - cap * 0.6
                self.dr.ellipse([cx - r, b - cap / 2 - r, cx + r, b - cap / 2 + r], fill=colour)
            if track:
                for i, ch in enumerate(part):
                    self.dr.text((x + fnt.getlength(part[:i]) + i * track, b), ch,
                                 font=fnt, fill=colour, anchor='ls')
            else:
                self.dr.text((x, b), part, font=fnt, fill=colour, anchor='ls')
            boxes.append([int(x + bb[0]), int(b + bb[1]), int(x + bb[0] + w), int(b + bb[3])])
            b += int(cap * 1.3)
        self.truth[key] = dict(text=text, boxes=boxes, cap=cap, size=round(size, 2),
                               family=rec['family'], weight=rec['weight'],
                               italic=bool(rec.get('italic')), upper=text.isupper(),
                               track=track_em, align=align, reversed=reversed_,
                               bases=[base + int(cap * 1.3) * k for k in range(len(parts))])
        return b


def maybe_upper(rng, text, p):
    return text.upper() if rng.random() < p else text


def draw_gig(rng, copy, d, t, it):
    P = Page(rng)
    txt = {c['key']: c['text'] for c in copy}
    align = rng.choice(['left', 'centre', 'right'])
    x0, x1 = 64, W - 64
    # artwork: a circle, off to the side the text is not ranged against
    r = rng.randint(120, 200)
    cx = W - r // 3 if align != 'right' else r // 3
    P.dr.ellipse([cx - r, 720 - r, cx + r, 720 + r],
                 fill=rng.choice([(230, 180, 40), (220, 90, 60), (90, 150, 90)]))
    track = rng.choice([0.0, 0.0, 0.3])
    P.line('presenter', maybe_upper(rng, txt['presenter'], 0.6 if track else 0.0),
           t, 22, 90, x0, x1, align, track_em=track)
    head = txt['headliner']
    words = head.split(' ')
    parts = [' '.join(words[:len(words) // 2]), ' '.join(words[len(words) // 2:])]
    cap = rng.randint(100, 150)
    y = P.line('headliner', head, d, cap, 140 + cap, x0, x1, align, parts=parts)
    P.line('support', maybe_upper(rng, txt['support'], 0.25), t, 32, y + 10, x0, x1, align)
    # the date and venue reversed out of a panel
    P.dr.rectangle([0, 1170, W, 1335], fill=rng.choice([(20, 110, 110), (170, 40, 40),
                                                        (30, 50, 110)]))
    light = (250, 248, 240)
    P.line('date', maybe_upper(rng, txt['date'], 0.3), t, 40, 1240, x0, x1, align,
           colour=light, reversed_=True)
    P.line('venue', txt['venue'], t, 28, 1295, x0, x1, align, colour=light, reversed_=True)
    P.line('tickets', txt['tickets'], t, 26, 1395, x0, x1, align)
    P.line('ticket_source', txt['ticket_source'], t, 22, 1440, x0, x1, align)
    P.line('footer', txt['footer'], t, 22, 1485, x0, x1, align)
    return P


def draw_fete(rng, copy, d, t, it):
    P = Page(rng)
    txt = {c['key']: c['text'] for c in copy}
    align = rng.choice(['left', 'centre'])
    x0, x1 = 60, W - 60
    title = maybe_upper(rng, txt['title'], 0.5)
    words = title.split(' ')
    cap = rng.randint(90, 130)
    y = P.line('title', title, d, cap, 60 + cap, x0, x1, align,
               parts=[words[0], ' '.join(words[1:])])
    y = P.line('date', maybe_upper(rng, txt['date'], 0.2), t, 36, y + 20, x0, x1, align)
    y = P.line('venue', txt['venue'], t, 26, y + 5, x0, x1, align)
    ben_face = it if (it and rng.random() < 0.7) else t
    y = P.line('beneficiary', txt['beneficiary'], ben_face, 22, y + 10, x0, x1, align)
    # an artwork band between the header and the list, a hole cut in it
    P.dr.rectangle([0, y + 20, W, y + 260],
                   fill=rng.choice([(230, 180, 40), (220, 90, 60), (90, 150, 90), (60, 110, 170)]))
    r = 80
    P.dr.ellipse([W // 2 - r, y + 140 - r, W // 2 + r, y + 140 + r], fill=P.paper)
    y += 330
    list_align = 'left' if align == 'left' else 'centre'
    label_track = rng.choice([0.0, 0.25])
    y = P.line('attractions_label',
               maybe_upper(rng, txt['attractions_label'], 0.6 if label_track else 0.3),
               t, 28, y, x0, x1, list_align, track_em=label_track)
    cols, bullets = rng.choice([1, 2]), rng.random() < 0.5
    step = 42
    items = [txt[f'attraction{i}'] for i in range(8)]
    # A list is set at one size: the largest at which every item fits its
    # column. Shrinking only the long items is not something a designer does.
    col_w = (W // 2 - 24 - x0) if cols == 2 else (x1 - x0)
    cap = 22
    while cap > 10:
        ok = True
        for item in items:
            size = cap * t['upem'] / top_cap(t, item)
            fnt = ImageFont.truetype(catalogue.face_file(t)[0], int(round(size)))
            if Page.measure(fnt, item, 0)[0] > col_w - (cap if bullets else 0):
                ok = False
                break
        if ok:
            break
        cap -= 1
    if cols == 1:
        for i, item in enumerate(items):
            P.line(f'attraction{i}', item, t, cap, y + 10 + i * step, x0, x1,
                   list_align, bullet=bullets)
        y += 10 + 8 * step
    else:
        mid = W // 2
        if rng.random() < 0.5:           # a rule between the columns
            P.dr.line([mid, y - 10, mid, y + 4 * step], fill=P.ink, width=2)
        for i, item in enumerate(items):
            col, row = divmod(i, 4)
            cx0, cx1 = (x0, mid - 24) if col == 0 else (mid + 24, x1)
            P.line(f'attraction{i}', item, t, cap, y + 10 + row * step, cx0, cx1,
                   'left', bullet=bullets)
        y += 10 + 4 * step
    y = P.line('footer0', maybe_upper(rng, txt['footer0'], 0.3), t, 26, max(y + 40, 1400),
               x0, x1, align)
    P.line('footer1', txt['footer1'], t, 18, y + 12, x0, x1, align)
    return P


def degrade(im, rng):
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
    buf = io.BytesIO()
    im.save(buf, 'WEBP', quality=rng.randint(75, 90))
    return Image.open(buf).convert('RGB')


# ------------------------------------------------------------------ grading
def same_letterforms(c, tr):
    """The shipped face and the drawn one are the same design (Anek Latin for
    Anek Kannada): glyphs match as closely as a face matches itself."""
    try:
        from bench_fonts import same_letterforms as sl
        drawn = next(r for r in catalogue.load() if r['family'] == tr['family']
                     and r['weight'] == tr['weight'] and bool(r.get('italic')) == tr['italic'])
        return sl(catalogue.face_file(drawn)[0], c['path'])
    except Exception:
        return False


def svg_texts(svg):
    out = []
    for m in re.finditer(r'<text([^>]*)>(.*?)</text>', svg, re.S):
        a = m.group(1)
        y = re.search(r'\by="([\d.]+)"', a)
        fs = re.search(r'font-size="([\d.]+)"', a)
        ls = re.search(r'letter-spacing="([\d.]+)em"', a)
        out.append(dict(y=float(y.group(1)) if y else 0.0,
                        size=float(fs.group(1)) if fs else 0.0,
                        ls=float(ls.group(1)) if ls else 0.0,
                        text=html.unescape(re.sub(r'<[^>]+>', '', m.group(2)))))
    return out


def grade(sol, svg_path, truth, workdir):
    L = sol['lines']
    got = {a['key']: [L[b] for b in a['bands']] for a in sol['assigned']}
    card = {k: [0, 0] for k in ('assign', 'face', 'size', 'case', 'track')}
    notes = []

    # assign: every drawn line on the band it was drawn in
    for key, tr in truth.items():
        card['assign'][1] += 1
        bands = got.get(key)
        ok = bool(bands) and len(bands) == len(tr['boxes']) and all(
            min(b['right'], bx[2]) - max(b['left'], bx[0]) > 0.6 * (bx[2] - bx[0])
            and abs(b['baseline'] - base) < 0.5 * tr['cap']
            for b, bx, base in zip(bands, tr['boxes'], tr['bases']))
        card['assign'][0] += ok
        if not ok:
            notes.append(f'assign: {key}')

    # face, per line: what the builder would ship for the line's group
    from build_svg import pick
    over = dict(f.split('=', 1) for f in (sol.get('knobs') or {}).get('face', []))
    shipped = {}
    for g, v in sol['groups'].items():
        try:
            c = pick(v, over.get(g))
        except SystemExit:
            continue
        for k in v['lines']:
            shipped[k] = c
    for key, tr in truth.items():
        c = shipped.get(key)
        if not c:
            continue
        card['face'][1] += 1
        italic = bool((c.get('record') or {}).get('italic'))
        ok = ((c['family'] == tr['family'] or same_letterforms(c, tr))
              and abs(c['weight'] - tr['weight']) <= 100 and italic == tr['italic'])
        card['face'][0] += ok
        if not ok:
            notes.append(f"face: {key} drawn {tr['family']} {tr['weight']}"
                         f"{' italic' if tr['italic'] else ''}, ships {c['family']} "
                         f"{c['weight']}{' italic' if italic else ''}")

    # size, case, track: from the SVG's own <text> elements
    texts = svg_texts(open(svg_path).read())
    for key, tr in truth.items():
        for base in tr['bases']:
            words = set(tr['text'].lower().split())
            near = [t for t in texts if abs(t['y'] - base) < 0.4 * tr['cap']
                    and set(t['text'].lower().split()) & words]
            if not near:
                continue
            t = near[0]
            letters = [ch for ch in t['text'] if ch.isalpha()]
            is_upper = bool(letters) and all(ch.isupper() for ch in letters)
            card['case'][1] += 1
            if is_upper == tr['upper']:
                card['case'][0] += 1
            else:
                notes.append(f"case: {key} drawn {'CAPS' if tr['upper'] else 'mixed'}, "
                             f"set {'CAPS' if is_upper else 'mixed'}")
            card['track'][1] += 1
            if (t['ls'] > 0.02) == (tr['track'] > 0.02):
                card['track'][0] += 1
            else:
                notes.append(f"track: {key} drawn {tr['track']:.2f}em, set {t['ls']:.2f}em")
            c = shipped.get(key)
            if c and c['family'] == tr['family'] and abs(c['weight'] - tr['weight']) <= 100:
                card['size'][1] += 1
                err = t['size'] / tr['size'] - 1
                # 3%, or a pixel and a half of cap on small type, where one
                # pixel is already 5%
                if abs(err) <= max(0.03, 1.5 / tr['cap']):
                    card['size'][0] += 1
                else:
                    notes.append(f'size: {key} {err:+.0%}')
            break

    # audit leads on the result
    from audit import audit
    leads = audit(svg_path, sol, workdir)
    kinds = {'alignment': 'aligned in the original', 'ghost': 'still in the artwork',
             'damage': 'repainted', 'leftover type': 'type-like band'}
    card['audit'] = {k: sum(1 for l in leads if pat in l) for k, pat in kinds.items()}
    notes += [f'audit: {l}' for l in leads if any(p in l for p in kinds.values())]
    return card, notes


# --------------------------------------------------------------------- run
def brief_from_tags(rec):
    """What a perfect reader would say: the face's own top classification and
    its two strongest expressive tags."""
    tags = rec.get('tags', {})
    cls = sorted(((v, k) for k, v in tags.items()
                  if k.split('/')[1] in ('Sans', 'Serif', 'Slab')), reverse=True)[:1]
    exp = sorted(((v, k) for k, v in tags.items() if k.startswith('/Expressive/')),
                 reverse=True)[:2]
    return ', '.join(f"{k.strip('/')} {int(v)}" for v, k in cls + exp) or 'Sans 50'


def one(job):
    seed, site, briefs = job
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    os.environ['POSTER_SITE'] = site
    rng = random.Random(seed)
    event = 'gig' if seed % 2 == 0 else 'fete'
    from copytext import load_copy
    copy = load_copy(event)
    d, t, it = pick_faces(rng)
    P = (draw_gig if event == 'gig' else draw_fete)(rng, copy, d, t, it)
    style = f'synthetic_{seed}'
    degrade(P.im, rng).save(os.path.join(site, 'assets', 'poster-examples',
                                         f'{style}-{event}-v2.webp'))
    cmd = [sys.executable, os.path.join(HERE, 'convert.py'), style, event, '--fresh']
    if briefs:
        cmd += ['--character', f"{'headliner' if event == 'gig' else 'title'}={brief_from_tags(d)}",
                '--character', f'date={brief_from_tags(t)}']
    p = subprocess.run(cmd, capture_output=True, text=True,
                       env=dict(os.environ, POSTER_SITE=site))
    ital = P.truth.get('beneficiary', {}).get('italic')
    head = (f'{style} ({event}): {catalogue.label(d)} / {catalogue.label(t)}'
            + (f' / {catalogue.label(it)}' if ital else ''))
    soln = f'/tmp/p2svg-{style}-{event}/solution.json'
    svg = os.path.join(site, 'assets', 'poster-svg', f'{style}-{event}-v2.svg')
    if not os.path.exists(soln):
        return seed, head, None, [f'no solution: {(p.stdout + p.stderr)[-300:]}']
    if not os.path.exists(svg):
        last = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()][-1:] or ['']
        return seed, head, None, [f'not built: {last[0][:160]}']
    sol = json.load(open(soln))
    with tempfile.TemporaryDirectory() as wd:
        card, notes = grade(sol, svg, P.truth, wd)
    return seed, head, card, notes


def totals(results):
    tot = {k: [0, 0] for k in ('assign', 'face', 'size', 'case', 'track')}
    tot['audit'] = {}
    built = 0
    for _, _, card, _ in results:
        if not card:
            continue
        built += 1
        for k in ('assign', 'face', 'size', 'case', 'track'):
            tot[k][0] += card[k][0]
            tot[k][1] += card[k][1]
        for k, v in card['audit'].items():
            tot['audit'][k] = tot['audit'].get(k, 0) + v
    tot['built'] = [built, len(results)]
    return tot


def show(tot):
    parts = [f"built {tot['built'][0]}/{tot['built'][1]}"]
    parts += [f'{k} {tot[k][0]}/{tot[k][1]}' for k in ('assign', 'face', 'size', 'case', 'track')]
    au = ', '.join(f'{k} {v}' for k, v in tot['audit'].items() if v) or 'none'
    return '  '.join(parts) + f'   audit leads: {au}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--suite', action='store_true',
                    help=f'the fixed seeds {SUITE[0]}..{SUITE[-1]}')
    ap.add_argument('--save-baseline', action='store_true')
    ap.add_argument('--seeds', type=int, nargs='+', help='run these seeds (e.g. from the suite)')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--briefs', action='store_true',
                    help="give convert.py character briefs from the drawn faces' tags")
    ap.add_argument('--keep', action='store_true', help='keep the throwaway site')
    ap.add_argument('-j', type=int, default=4)
    a = ap.parse_args()
    import sitepaths
    real = sitepaths.root()
    site = tempfile.mkdtemp(prefix='p2svg-roundtrip-')
    os.makedirs(os.path.join(site, 'assets', 'poster-examples'))
    os.symlink(os.path.join(real, '_data'), os.path.join(site, '_data'))
    os.symlink(os.path.join(real, '_includes'), os.path.join(site, '_includes'))
    seeds = (a.seeds or SUITE) if (a.suite or a.seeds) else [a.seed * 1000 + i for i in range(a.n)]
    with Pool(a.j) as pool:
        results = sorted(pool.map(one, [(s, site, a.briefs) for s in seeds]))
    for seed, head, card, notes in results:
        print(head + ('' if card else '  FAILED'))
        for n in notes:
            print(f'    {n}')
    tot = totals(results)
    print('\n' + show(tot))
    if a.suite:
        key = 'briefs' if a.briefs else 'plain'
        base = json.load(open(BASELINE)) if os.path.exists(BASELINE) else {}
        if a.save_baseline:
            base[key] = tot
            json.dump(base, open(BASELINE, 'w'), indent=1)
            print(f'baseline ({key}) -> {BASELINE}')
        elif key in base:
            print('baseline: ' + show(base[key]))
    if a.keep:
        print(f'site kept: {site}')
    else:
        shutil.rmtree(site, ignore_errors=True)


if __name__ == '__main__':
    main()

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
  split     a wrapped line breaks at the words the original broke at
  blank     under each line, the SVG's artwork layer matches the true artwork
            (drawn separately, without type): no ghost, no damage
  art       away from the type, the artwork layer is the artwork (per poster)
  artkeep   (--art) lettering kept as art is untouched in the SVG
  artdesc   (--art) its words are in the SVG's <desc>
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
# --tilt runs the gig seeds of the suite with their date/venue panel on a slant
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
class Both:
    """Draw artwork on the poster and on the text-free artwork layer alike."""
    def __init__(self, *draws):
        self.draws = draws

    def __getattr__(self, name):
        return lambda *a, **k: [getattr(d, name)(*a, **k) for d in self.draws]


class Page:
    def __init__(self, rng):
        self.rng = rng
        self.paper = rng.choice([(250, 246, 238), (255, 255, 255), (244, 238, 222),
                                 (236, 240, 244)])
        self.ink = rng.choice([(24, 24, 24), (30, 40, 70), (60, 30, 30), (20, 60, 50)])
        self.im = Image.new('RGB', (W, H), self.paper)
        self.dr = ImageDraw.Draw(self.im)
        # The artwork alone, drawn in step with the poster but never with any
        # type: the answer to "what is behind the letters", which no real
        # poster can give. Graded against the SVG's own artwork layer.
        self.art_im = Image.new('RGB', (W, H), self.paper)
        self.art = Both(self.dr, ImageDraw.Draw(self.art_im))
        self.truth = {}

    tilt = False
    art_lettering = False

    def lettering(self, key, parts, rec, cap, base, x0, x1):
        """Draw `parts` as lettering no font can reset -- each letter its own
        colour, size and angle (orphism's headline) -- and record it as art:
        its pixels must survive into the SVG untouched. -> next baseline."""
        path = catalogue.face_file(rec)[0]
        cols = [(230, 60, 50), (240, 150, 30), (60, 160, 70), (40, 120, 200), (130, 70, 170)]
        # fit the widest line, allowing for the 15% size wobble
        while cap > 30:
            fnt = ImageFont.truetype(path, int(cap * 1.15 * rec['upem'] / rec['cap']))
            if max(fnt.getlength(p) for p in parts) * 1.05 <= x1 - x0:
                break
            cap = int(cap * 0.9)
        b = base
        layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        for part in parts:
            x = x0
            for ch in part:
                c = int(cap * self.rng.uniform(0.85, 1.15))
                fnt = ImageFont.truetype(path, max(8, int(c * rec['upem'] / rec['cap'])))
                if ch != ' ':
                    g = Image.new('RGBA', (int(c * 1.6), int(c * 1.6)), (0, 0, 0, 0))
                    ImageDraw.Draw(g).text((c * 0.3, c * 1.3), ch, font=fnt,
                                           fill=self.rng.choice(cols), anchor='ls')
                    g = g.rotate(self.rng.uniform(-12, 12), resample=Image.BICUBIC)
                    layer.alpha_composite(g, (int(x), max(0, int(b - c * 1.3))))
                x += fnt.getlength(ch) * 1.02
            b += int(cap * 1.3)
        # the box is the lettering's actual ink, a little padded
        x0_, y0_, x1_, y1_ = layer.getbbox()
        box = [max(0, x0_ - 6), max(0, y0_ - 6), min(W - 1, x1_ + 6), min(H - 1, y1_ + 6)]
        base_im = self.im.convert('RGBA')
        base_im.alpha_composite(layer)
        self.im = base_im.convert('RGB')
        self.dr = ImageDraw.Draw(self.im)
        self.art = Both(self.dr, ImageDraw.Draw(self.art_im))
        self.art_box = (key, box)
        self.art_text = ' '.join(parts)
        # the next line starts clear of the lettering's lowest ink
        return max(b, box[3] + int(0.5 * cap))

    def begin_layer(self):
        """Draw what follows on transparent layers (poster and artwork),
        to be rotated as one by end_layer."""
        saved = (self.im, self.dr, self.art_im, self.art)
        self.im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        self.dr = ImageDraw.Draw(self.im)
        self.art_im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        self.art = Both(self.dr, ImageDraw.Draw(self.art_im))
        return saved

    def end_layer(self, saved, angle, centre, keys):
        """Rotate the layers by `angle` (PIL sense, counter-clockwise) about
        `centre` and composite them. The truth for `keys` stays in the drawing's
        own coordinates -- which are the levelled frame the tool should find --
        plus the angle and the box on the poster."""
        im, art = self.im, self.art_im
        self.im, self.dr, self.art_im, self.art = saved
        for src, dst in ((im, 'im'), (art, 'art_im')):
            rot = src.rotate(angle, resample=Image.BICUBIC, center=centre)
            base = getattr(self, dst).convert('RGBA')
            base.alpha_composite(rot)
            setattr(self, dst, base.convert('RGB'))
        self.dr = ImageDraw.Draw(self.im)
        self.art = Both(self.dr, ImageDraw.Draw(self.art_im))
        ys, xs = __import__('numpy').nonzero(__import__('numpy').array(
            art.rotate(angle, center=centre))[:, :, 3] > 0)
        box = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        for k in keys:
            self.truth[k]['tilt'] = angle
            self.truth[k]['poster_box'] = box
        self.tilted = (keys, box)

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
                self.art.ellipse([cx - r, b - cap / 2 - r, cx + r, b - cap / 2 + r], fill=colour)
            if track:
                for i, ch in enumerate(part):
                    self.dr.text((x + fnt.getlength(part[:i]) + i * track, b), ch,
                                 font=fnt, fill=colour, anchor='ls')
            else:
                self.dr.text((x, b), part, font=fnt, fill=colour, anchor='ls')
            boxes.append([int(x + bb[0]), int(b + bb[1]), int(x + bb[0] + w), int(b + bb[3])])
            b += int(cap * 1.3)
        self.truth[key] = dict(text=text, parts=list(parts), boxes=boxes, cap=cap,
                               size=round(size, 2),
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
    P.art.ellipse([cx - r, 720 - r, cx + r, 720 + r],
                 fill=rng.choice([(230, 180, 40), (220, 90, 60), (90, 150, 90)]))
    track = rng.choice([0.0, 0.0, 0.3])
    P.line('presenter', maybe_upper(rng, txt['presenter'], 0.6 if track else 0.0),
           t, 22, 90, x0, x1, align, track_em=track)
    if rng.random() < 0.5:
        # a shape the headline crosses (act_up's pink triangle): the type must
        # come out of it without leaving wedges or eating its edge
        tri = rng.choice([(230, 120, 160), (240, 190, 60), (120, 170, 210)])
        P.art.polygon([(W * 0.55, 150), (W - 20, 150), (W * 0.78, 520)], fill=tri)
    head = txt['headliner']
    words = head.split(' ')
    parts = [' '.join(words[:len(words) // 2]), ' '.join(words[len(words) // 2:])]
    cap = rng.randint(100, 150)
    if P.art_lettering:
        y = P.lettering('headliner', parts, d, cap, 140 + cap, x0, x1)
    else:
        y = P.line('headliner', head, d, cap, 140 + cap, x0, x1, align, parts=parts)
    P.line('support', maybe_upper(rng, txt['support'], 0.25), t, 32, y + 10, x0, x1, align)
    # the date and venue reversed out of a panel -- on a slant, for a tilt run
    panel_col = rng.choice([(20, 110, 110), (170, 40, 40), (30, 50, 110)])
    tilt = rng.choice([-1, 1]) * rng.uniform(5, 12) if P.tilt else 0.0
    layers = P.begin_layer() if tilt else None
    # narrow enough that its rotated corners clear the ticket lines below
    px0, px1 = (150, W - 150) if tilt else (0, W)
    P.art.rectangle([px0, 1170, px1, 1335], fill=panel_col)
    light = (250, 248, 240)
    tx0, tx1 = (px0 + 30, px1 - 30) if tilt else (x0, x1)
    P.line('date', maybe_upper(rng, txt['date'], 0.3), t, 40, 1240, tx0, tx1, align,
           colour=light, reversed_=True)
    venue_parts = (txt['venue'].split(', ', 1) if rng.random() < 0.4 else None)
    if venue_parts:
        venue_parts = [venue_parts[0] + ',', venue_parts[1]]
    P.line('venue', txt['venue'], t, 26 if venue_parts else 28, 1290, tx0, tx1, align,
           colour=light, reversed_=True, parts=venue_parts)
    if tilt:
        P.end_layer(layers, tilt, (W / 2, (1170 + 1335) / 2), ['date', 'venue'])
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
    date = maybe_upper(rng, txt['date'], 0.2)
    dparts = [date.split(', ')[0] + ',', date.split(', ', 1)[1]] if rng.random() < 0.5 else None
    y = P.line('date', date, t, 36, y + 20, x0, x1, align, parts=dparts)
    vparts = ([txt['venue'].split(', ')[0] + ',', txt['venue'].split(', ', 1)[1]]
              if rng.random() < 0.5 else None)
    y = P.line('venue', txt['venue'], t, 26, y + 5, x0, x1, align, parts=vparts)
    ben_face = it if (it and rng.random() < 0.7) else t
    y = P.line('beneficiary', txt['beneficiary'], ben_face, 22, y + 10, x0, x1, align)
    # an artwork band between the header and the list, a hole cut in it
    P.art.rectangle([0, y + 20, W, y + 260],
                   fill=rng.choice([(230, 180, 40), (220, 90, 60), (90, 150, 90), (60, 110, 170)]))
    r = 80
    P.art.ellipse([W // 2 - r, y + 140 - r, W // 2 + r, y + 140 + r], fill=P.paper)
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
        if rng.random() < 0.6:           # a rule between the columns
            # sometimes a short one: under 120px it escapes the rule detector
            # and must not be swept up as a stray letter (east_german, wpa)
            length = rng.choice([4 * step + 10, 90])
            P.art.line([mid, y - 10 + (4 * step + 10 - length) // 2, mid,
                        y - 10 + (4 * step + 10 + length) // 2], fill=P.ink, width=2)
        for i, item in enumerate(items):
            col, row = divmod(i, 4)
            cx0, cx1 = (x0, mid - 24) if col == 0 else (mid + 24, x1)
            P.line(f'attraction{i}', item, t, cap, y + 10 + row * step, cx0, cx1,
                   'left', bullet=bullets)
        y += 10 + 4 * step
    y = P.line('footer0', maybe_upper(rng, txt['footer0'], 0.3), t, 26, max(y + 40, 1400),
               x0, x1, align)
    f1 = txt['footer1']
    f1parts = ([' '.join(f1.split()[:3]), ' '.join(f1.split()[3:])]
               if rng.random() < 0.4 else None)
    P.line('footer1', f1, t, 18, y + 12, x0, x1, align, parts=f1parts)
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


def grade(sol, svg_path, truth, workdir, art=None):
    notes = []
    L = sol['lines']
    L = sol['lines']
    got = {a['key']: [L[b] for b in a['bands']] for a in sol['assigned']}
    card = {k: [0, 0] for k in ('assign', 'face', 'size', 'case', 'track', 'split', 'blank', 'tilt')}
    # tilt: a slanted line is set in a frame at the angle it was drawn at
    for key, tr in truth.items():
        if 'tilt' not in tr:
            continue
        card['tilt'][1] += 1
        fr = [L[b].get('frame') for b in (next((a['bands'] for a in sol['assigned']
                                               if a['key'] == key), []))]
        # the tool levels with rotate(a); the drawing was rotated by tilt, so a = -tilt
        if fr and fr[0] and abs(fr[0]['angle'] + tr['tilt']) < 1.0:
            card['tilt'][0] += 1
        else:
            notes.append(f"tilt: {key} drawn at {tr['tilt']:.1f} deg, measured "
                         f"{(-fr[0]['angle']) if fr and fr[0] else None}")

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
        sized = False
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
            if (not sized and c and c['family'] == tr['family']
                    and abs(c['weight'] - tr['weight']) <= 100):
                sized = True
                card['size'][1] += 1
                err = t['size'] / tr['size'] - 1
                # 3%, or a pixel and a half of cap on small type, where one
                # pixel is already 5%
                if abs(err) <= max(0.03, 1.5 / tr['cap']):
                    card['size'][0] += 1
                else:
                    notes.append(f'size: {key} {err:+.0%}')
            # every band of a wrapped line is graded for case and tracking

    # split: a wrapped line breaks at the words the original broke at
    norm = lambda t: ' '.join(t.lower().split())  # noqa: E731
    for key, tr in truth.items():
        if len(tr['parts']) < 2:
            continue
        card['split'][1] += 1
        got = [norm(t['text']) for base in tr['bases'] for t in texts
               if abs(t['y'] - base) < 0.4 * tr['cap']]
        want = [norm(p) for p in tr['parts']]
        if got[:len(want)] == want:
            card['split'][0] += 1
        else:
            notes.append(f"split: {key} drawn {' / '.join(tr['parts'])!r}, set "
                         f"{' / '.join(got)!r}")

    # blank: under each line, the SVG's artwork layer (its text stripped) is
    # the true artwork -- no ghost of the original type, no damage to what
    # was behind it
    if art is not None:
        from audit import render, strip_text
        import numpy as np
        from scipy import ndimage
        shipped_art = render(strip_text(open(svg_path).read()), workdir, 'artonly', W, H)
        truth_art = np.array(art[1]).astype(int)
        drawn = np.array(art[0]).astype(int)
        # where type was drawn: the poster differs from its own artwork layer
        typed = ndimage.binary_dilation(np.abs(drawn - truth_art).max(2) > 30, iterations=2)
        for key, tr in truth.items():
            card['blank'][1] += 1
            bad = 0.0
            for bx in ([tr['poster_box']] if 'poster_box' in tr else tr['boxes']):
                pad = max(2, int(0.2 * tr['cap']))
                x0, y0 = max(0, bx[0] - pad), max(0, bx[1] - pad)
                x1, y1 = min(W, bx[2] + pad), min(H, bx[3] + pad)
                t = typed[y0:y1, x0:x1]
                if not t.any():
                    continue
                d = np.abs(shipped_art[y0:y1, x0:x1] - truth_art[y0:y1, x0:x1]).max(2)
                # faint differences count: a ghost is a pale letter, not a dark one
                bad = max(bad, float((d[t] > 35).mean()))
            if bad < 0.03:
                card['blank'][0] += 1
            else:
                notes.append(f'blank: {key} {bad:.0%} of the type area differs from the '
                             f'true artwork (ghost or damage)')

        # art: away from the type, the artwork layer IS the artwork -- nothing
        # the ghost sweep or the blanking took with it (a rule between list
        # columns, a shape's edge)
        away = ~ndimage.binary_dilation(typed, iterations=6)
        # ...and away from the artwork's own edges, which the degraded poster
        # (softened before conversion) has moved by a pixel or so
        grad = np.abs(np.diff(truth_art, axis=0, prepend=truth_art[:1])).max(2) + \
            np.abs(np.diff(truth_art, axis=1, prepend=truth_art[:, :1])).max(2)
        away &= ~ndimage.binary_dilation(grad > 30, iterations=2)
        d = np.abs(shipped_art - truth_art).max(2) > 60
        lost = float(d[away].mean()) if away.any() else 0.0
        card['art'] = [int(lost < 0.002), 1]
        if lost >= 0.002:
            lab, n = ndimage.label(d & away)
            big = sorted((sl for sl in ndimage.find_objects(lab)),
                         key=lambda sl: -(sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start))[:2]
            where = '; '.join(f'x{sl[1].start}-{sl[1].stop} y{sl[0].start}-{sl[0].stop}' for sl in big)
            notes.append(f'art: {lost:.2%} of the artwork away from the type differs ({where})')

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


def grade_art(card, notes, P, svg_path, workdir):
    """artkeep: inside the art box the SVG's artwork layer IS the drawn
    lettering (not blanked, not swept); artdesc: its words are in <desc>."""
    import numpy as np
    from audit import render, strip_text
    key, (x0, y0, x1, y1) = P.art_box
    svg = open(svg_path).read()
    shipped = render(strip_text(svg), workdir, 'artkeep', W, H)[y0:y1, x0:x1]
    drawn = np.array(P.im).astype(int)[y0:y1, x0:x1]
    diff = float((np.abs(shipped - drawn).max(2) > 60).mean())
    # 2%: the artwork layer is re-encoded as WebP, which moves the edges of
    # saturated lettering; blanking or sweeping it changes far more
    card['artkeep'] = [int(diff < 0.02), 1]
    if diff >= 0.02:
        notes.append(f'artkeep: {diff:.1%} of the art lettering changed')
    desc = re.search(r'<desc>(.*?)</desc>', svg, re.S)
    ok = bool(desc) and P.art_text.lower() in html.unescape(desc.group(1)).lower()
    card['artdesc'] = [int(ok), 1]
    if not ok:
        notes.append('artdesc: the art lettering is not in <desc>')


def one(job):
    seed, site, briefs, tiltrun = job
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    os.environ['POSTER_SITE'] = site
    rng = random.Random(seed)
    event = 'gig' if seed % 2 == 0 else 'fete'
    from copytext import load_copy
    copy = load_copy(event)
    d, t, it = pick_faces(rng)
    Page.tilt = tiltrun == 'tilt' and event == 'gig'
    Page.art_lettering = tiltrun == 'art' and event == 'gig'
    P = (draw_gig if event == 'gig' else draw_fete)(rng, copy, d, t, it)
    style = f'synthetic_{seed}'
    degrade(P.im, rng).save(os.path.join(site, 'assets', 'poster-examples',
                                         f'{style}-{event}-v2.webp'))
    cmd = [sys.executable, os.path.join(HERE, 'convert.py'), style, event, '--fresh']
    if getattr(P, 'art_box', None):
        k, box = P.art_box
        cmd += ['--art', f"{k}@{','.join(str(v) for v in box)}"]
    if getattr(P, 'tilted', None):
        keys, box = P.tilted
        cmd += ['--tilted', f"{','.join(keys)}@{','.join(str(v) for v in box)}"]
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
        last = [l for l in (p.stdout + p.stderr).splitlines()
                if l.strip() and 'Warning' not in l and not l.startswith('  ')][-1:] or ['']
        return seed, head, None, [f'not built: {last[0][:160]}']
    sol = json.load(open(soln))
    with tempfile.TemporaryDirectory() as wd:
        card, notes = grade(sol, svg, P.truth, wd, (P.im, P.art_im))
        if getattr(P, 'art_box', None):
            grade_art(card, notes, P, svg, wd)
    return seed, head, card, notes


def totals(results):
    tot = {k: [0, 0] for k in ('assign', 'face', 'size', 'case', 'track', 'split', 'blank', 'art', 'tilt', 'artkeep', 'artdesc')}
    tot['audit'] = {}
    built = 0
    for _, _, card, _ in results:
        if not card:
            continue
        built += 1
        for k in ('assign', 'face', 'size', 'case', 'track', 'split', 'blank', 'art', 'tilt', 'artkeep', 'artdesc'):
            if k not in card:
                continue
            tot[k][0] += card[k][0]
            tot[k][1] += card[k][1]
        for k, v in card['audit'].items():
            tot['audit'][k] = tot['audit'].get(k, 0) + v
    tot['built'] = [built, len(results)]
    return tot


def show(tot):
    parts = [f"built {tot['built'][0]}/{tot['built'][1]}"]
    parts += [f'{k} {tot[k][0]}/{tot[k][1]}' for k in ('assign', 'face', 'size', 'case', 'track', 'split', 'blank', 'art', 'tilt', 'artkeep', 'artdesc')
              if k in tot and (k not in ('tilt', 'artkeep', 'artdesc') or tot[k][1])]
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
    ap.add_argument('--tilt', action='store_true',
                    help='gig posters set their date and venue panel on a slant')
    ap.add_argument('--art', action='store_true',
                    help="gig posters draw their headline as lettering no font can reset")
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
    if (a.tilt or a.art) and a.suite and not a.seeds:
        seeds = [s for s in SUITE if s % 2 == 0]
    with Pool(a.j) as pool:
        mode = 'tilt' if a.tilt else 'art' if a.art else None
        results = sorted(pool.map(one, [(s, site, a.briefs, mode) for s in seeds]))
    for seed, head, card, notes in results:
        print(head + ('' if card else '  FAILED'))
        for n in notes:
            print(f'    {n}')
    tot = totals(results)
    print('\n' + show(tot))
    if a.suite:
        key = ('briefs' if a.briefs else 'plain') + ('+tilt' if a.tilt else '') + ('+art' if a.art else '')
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

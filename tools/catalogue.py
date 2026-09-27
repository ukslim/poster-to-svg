#!/usr/bin/env python3
"""A searchable description of every Google Fonts style, and the system faces.

    python3 catalogue.py build              # ~6,500 styles; a few minutes
    python3 catalogue.py build --limit 40   # a quick sample, for testing
    python3 catalogue.py show "League Gothic"

Replaces a hand-picked local font corpus. For each style it stores what
ranking needs -- never the font itself:

  metrics   advance and ink bounds of every character the posters' copy uses,
            so a line's natural width at any size is arithmetic
  features  per-character width and height over cap, stroke weight and
            contrast, slant, squareness, corner roundness and bend
            (typefeatures.py): measured the same
            way as on the poster's own glyph crops, so the two compare
  tags      Google's own classification (/Sans/Grotesque, /Serif/Didone,
            /Theme/Pixel ...) and expressive scores (/Expressive/Playful ...)

Each style is measured from a specimen subset fetched with the CSS API's
`text=` parameter (fontfetch.py) -- the copy's own characters, a few KB --
which is then discarded. The macOS system faces generators often imitate
(Helvetica, Futura, Didot) are measured from their local files and marked
`embed: false`: useful for identifying a face, never shipped.

Output: fonts/catalogue.json (gitignored; rebuild any time).
"""
import argparse, csv, io, json, os, sys, tempfile
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FONTS = os.path.normpath(os.path.join(HERE, '..', 'fonts'))
CATALOGUE = os.path.join(FONTS, 'catalogue.json')
META_URL = 'https://fonts.google.com/metadata/fonts'
TAGS_URL = 'https://raw.githubusercontent.com/google/fonts/main/tags/all/families.csv'

# Every character the gig and fete copy can set, in either case.
SPECIMEN = (" &',./0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz£Êê–’-!?")
# Rendered to measure weight, contrast, slant, squareness and corner shape
# (C D G Q U P: typefeatures.CORNERS).
RENDERED = 'HEOIRSaegnodlCDGQUP'
TAG_PREFIXES = ('/Sans/', '/Serif/', '/Slab/', '/Script/', '/Monospace/',
                '/Theme/', '/Expressive/')

SYSTEM = [
    ('/System/Library/Fonts/Helvetica.ttc', [0, 1]),
    ('/System/Library/Fonts/HelveticaNeue.ttc', [0, 1, 4, 9, 10]),
    ('/System/Library/Fonts/Supplemental/Arial.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Arial Bold.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Arial Narrow.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Arial Black.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Futura.ttc', [0, 1]),
    ('/System/Library/Fonts/Supplemental/Times New Roman.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf', [None]),
    ('/System/Library/Fonts/Supplemental/Didot.ttc', [0, 1]),
    ('/System/Library/Fonts/Supplemental/Copperplate.ttc', [0, 1]),
    ('/System/Library/Fonts/Supplemental/Bodoni 72.ttc', [0, 1]),
    ('/System/Library/Fonts/Avenir.ttc', [0, 5]),
    ('/System/Library/Fonts/Supplemental/Impact.ttf', [None]),
]


# ------------------------------------------------------------------ measure
def describe(path, index=None):
    """Metrics and features of one font file, or None if it cannot set the
    copy (no H to take a cap height from, or most of the specimen missing)."""
    import numpy as np
    from svgkit import Face, render_glyph
    from typefeatures import features
    f = Face(path, index=index)
    if not f.has('H'):
        return None
    upem, cap = f.upem, f.bounds('H')[3]
    if cap <= 0:
        return None
    metrics, missing = {}, []
    for ch in SPECIMEN:
        if not f.has(ch):
            missing.append(ch)
            continue
        x0, y0, x1, y1 = f.bounds(ch)
        metrics[ch] = [f.adv(ch), int(x0), int(y0), int(x1), int(y1)]
    if len(missing) > 12:
        return None
    size = 120 * upem / cap                   # render with a 120px cap
    glyphs = []
    for ch in RENDERED:
        if ch in metrics and metrics[ch][3] > metrics[ch][1]:
            g = render_glyph(f, ch, size)
            if g is not None:
                m = g > 128
                ys, xs = np.nonzero(m)
                glyphs.append((ch, m[ys.min():ys.max() + 1, xs.min():xs.max() + 1], 120))
    feat = features(glyphs)
    # Width and height of EVERY character come from the outlines, exactly;
    # rendering is only needed for what outlines do not state directly.
    feat['aspect'] = {ch: round((m[3] - m[1]) / cap, 4) for ch, m in metrics.items()
                      if m[3] > m[1]}
    feat['height'] = {ch: round((m[4] - m[2]) / cap, 4) for ch, m in metrics.items()
                      if m[4] > m[2]}
    for k in ('weight', 'contrast', 'square', 'corner', 'bend'):
        if feat.get(k) is not None:
            feat[k] = round(feat[k], 4)
    return dict(upem=upem, cap=cap, metrics=metrics, missing=''.join(missing),
                kern='GPOS' in f.f, features=feat)


# ------------------------------------------------------------ google fonts
def google_families(meta, tags):
    """-> [(family, record fields, [style keys])] for Latin, open families."""
    out = []
    for fam in meta['familyMetadataList']:
        if 'latin' not in fam.get('subsets', []) or fam.get('isBrandFont'):
            continue
        if fam.get('isOpenSource') is False:
            continue
        styles = []
        for k in fam['fonts']:
            italic = k.endswith('i')
            try:
                w = int(k.rstrip('i'))
            except ValueError:
                continue
            styles.append((1 if italic else 0, None, w))
        # A width axis multiplies the choices; sample its narrowest end at a
        # text and a display weight -- condensed cuts are what posters need.
        for ax in fam.get('axes', []):
            if ax['tag'] == 'wdth' and ax['min'] < 100:
                ws = sorted({s[2] for s in styles if not s[0]})
                for w in {min(ws, key=lambda v: abs(v - 400)), max(ws)} if ws else ():
                    styles.append((0, int(ax['min']), w))
        info = dict(family=fam['family'], category=fam.get('category'),
                    stroke=fam.get('stroke'),
                    tags=tags.get(fam['family'], {}))
        out.append((fam['family'], info, styles))
    return out


def load_tags(raw):
    """families.csv -> {family: {tag: score}}, keeping the classification,
    theme and expressive tags and the highest score across axis positions."""
    out = {}
    for row in csv.reader(io.StringIO(raw)):
        if len(row) < 4:
            continue
        fam, _, tag, score = row[:4]
        if not tag.startswith(TAG_PREFIXES):
            continue
        try:
            v = float(score)
        except ValueError:
            continue
        d = out.setdefault(fam, {})
        d[tag] = max(v, d.get(tag, 0))
    return out


def measure_family(job):
    """Fetch and describe every style of one family. Top level for Pool."""
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    from fontfetch import css_urls, http, to_ttf
    family, info, styles = job
    out, errors = [], []
    try:
        plain = [s for s in styles if not s[1]]
        urls = css_urls(family, plain, SPECIMEN) if plain else {}
        urls.update(css_urls(family, [s for s in styles if s[1]], SPECIMEN)
                    if any(s[1] for s in styles) else {})
    except Exception as e:
        return [], [f'{family}: {e}']
    with tempfile.TemporaryDirectory() as td:
        for key, url in sorted(urls.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2])):
            p = os.path.join(td, 'f.ttf')
            try:
                to_ttf(http(url), p)
                d = describe(p)
            except Exception as e:
                errors.append(f'{family} {key}: {e}')
                continue
            if d:
                out.append(dict(info, weight=key[2], italic=bool(key[0]), wdth=key[1],
                                embed=True, source='google', **d))
    return out, errors


def measure_system(job):
    path, index = job
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    from svgkit import Face
    try:
        d = describe(path, index)
        f = Face(path, index=index).f
    except Exception as e:
        return [], [f'{path}[{index}]: {e}']
    if not d:
        return [], []
    name = f['name']
    family = str(name.getDebugName(16) or name.getDebugName(1))
    sub = str(name.getDebugName(17) or name.getDebugName(2) or '')
    try:
        weight = int(f['OS/2'].usWeightClass)
    except Exception:
        weight = 400
    return [dict(family=family, sub=sub, category=None, stroke=None, tags={},
                 weight=weight, italic='italic' in sub.lower(), wdth=None,
                 embed=False, source='system', path=path, index=index, **d)], []


def _key(r):
    return (r['family'], r['weight'], bool(r.get('italic')), r.get('wdth'),
            r.get('source'), r.get('path'), r.get('index'))


def stable_order(records):
    """`records` in the existing catalogue's order; styles it lacks after
    them, sorted, so the same inputs always give the same list."""
    try:
        old = json.load(open(CATALOGUE))['styles']
    except (OSError, ValueError, KeyError):
        old = []
    pos = {_key(r): i for i, r in enumerate(old)}
    known = sorted((r for r in records if _key(r) in pos), key=lambda r: pos[_key(r)])
    new = sorted((r for r in records if _key(r) not in pos),
                 key=lambda r: tuple(str(v) for v in _key(r)))
    return known + new


def build(limit=None, jobs=10):
    from fontfetch import http
    meta_raw = http(META_URL).decode()
    meta = json.loads(meta_raw[meta_raw.index('{'):])
    tags = load_tags(http(TAGS_URL).decode())
    fams = google_families(meta, tags)
    if limit:
        fams = fams[:limit]
    n_styles = sum(len(s) for _, _, s in fams)
    print(f'{len(fams)} families, {n_styles} styles to measure')
    records, errors = [], []
    with Pool(jobs) as pool:
        for i, (recs, errs) in enumerate(pool.imap_unordered(measure_family, fams), 1):
            records += recs
            errors += errs
            if i % 100 == 0:
                print(f'  {i}/{len(fams)} families, {len(records)} styles')
        sys_jobs = [(p, i) for p, idxs in SYSTEM if os.path.exists(p) for i in idxs]
        for recs, errs in pool.imap_unordered(measure_system, sys_jobs):
            records += recs
            errors += errs
    os.makedirs(FONTS, exist_ok=True)
    # Keep the order of the catalogue being replaced, new styles after it.
    # The styles are measured in parallel and finish in any order, and the
    # round-trip suite and bench_fonts draw their faces from this list by
    # seeded position: a reshuffle alone swapped every synthetic poster's
    # fonts and made the suite look 20 faces worse with nothing changed.
    records = stable_order(records)
    json.dump({'specimen': SPECIMEN, 'styles': records}, open(CATALOGUE, 'w'),
              separators=(',', ':'))
    print(f'\n{len(records)} styles ({sum(1 for r in records if r["embed"])} '
          f'embeddable), {len(errors)} errors -> {CATALOGUE}')
    for e in errors[:10]:
        print('  ', e)


# -------------------------------------------------------------------- use
_CACHE = None


def load():
    global _CACHE
    if _CACHE is None:
        if not os.path.exists(CATALOGUE):
            raise SystemExit(f'no font catalogue at {CATALOGUE}: '
                             'run `python3 tools/catalogue.py build`')
        _CACHE = json.load(open(CATALOGUE))['styles']
    return _CACHE


def label(r):
    """'Oswald 600', 'EB Garamond 400 italic', 'League Gothic 400 wdth75'."""
    s = f"{r['family']} {r['weight']}"
    if r.get('italic'):
        s += ' italic'
    if r.get('wdth'):
        s += f" wdth{r['wdth']}"
    return s


class RecordFace:
    """The parts of svgkit.Face that ranking needs, from stored metrics.

    Advance widths without kerning, as svgkit.Face computes them: ranking
    compares widths within a few percent, and kerning moves a line by less.
    """

    def __init__(self, r):
        self.r, self.upem = r, r['upem']
        self.m = r['metrics']

    def has(self, ch):
        return ch in self.m or ch == ' '

    def missing(self, text):
        return sorted({c for c in text if not self.has(c)})

    def bounds(self, ch):
        if ch == ' ' and ' ' not in self.m:
            return (0, 0, 0, 0)
        a, x0, y0, x1, y1 = self.m[ch]
        return (x0, y0, x1, y1)

    def adv(self, ch):
        if ch == ' ' and ' ' not in self.m:
            return 0.25 * self.upem
        return self.m[ch][0]

    def cap_ratio(self):
        return self.r['cap'] / self.upem

    def ink(self, text, size, track=0.0, wordspace=0.0):
        s, pen, lo, hi = size / self.upem, 0.0, None, None
        for ch in text:
            if not self.has(ch):
                continue
            x0, _, x1, _ = self.bounds(ch)
            if x1 > x0:
                lo = pen + x0 * s if lo is None else min(lo, pen + x0 * s)
                hi = pen + x1 * s if hi is None else max(hi, pen + x1 * s)
            pen += self.adv(ch) * s + track + (wordspace if ch == ' ' else 0.0)
        return lo, hi

    def width(self, text, size, track=0.0, wordspace=0.0):
        lo, hi = self.ink(text, size, track, wordspace)
        return (hi - lo) if lo is not None else 0.0


def face_file(r, text=None):
    """A real font file for this style: the local file for a system face, or
    a subset fetched for `text` (default: the whole specimen)."""
    if r['source'] == 'system':
        return r['path'], r['index']
    from fontfetch import fetch
    return fetch(r['family'], r['weight'], r.get('italic', False), r.get('wdth'),
                 text or SPECIMEN), None


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--limit', type=int)
    b.add_argument('-j', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    s = sub.add_parser('show')
    s.add_argument('family')
    a = ap.parse_args()
    if a.cmd == 'build':
        build(a.limit, a.j)
    else:
        for r in load():
            if r['family'].lower() == a.family.lower():
                f = r['features']
                print(f"{label(r):40} weight {f.get('weight')} contrast {f.get('contrast')} "
                      f"slant {f.get('slant')} square {f.get('square')} "
                      f"{'' if r['embed'] else '[id-only]'}")
                if r.get('tags'):
                    print('   ', ', '.join(f'{t}:{v:.0f}' for t, v in
                                           sorted(r['tags'].items(), key=lambda kv: -kv[1])[:6]))


if __name__ == '__main__':
    main()

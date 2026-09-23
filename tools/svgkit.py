#!/usr/bin/env python3
"""Shared machinery for turning a bitmap poster into an SVG.

Face      font metrics that work for both TrueType and CFF outlines
match_*   decide which real typeface the original is set in
subset    cut a font down to the glyphs used and base64 it for @font-face
probe     ask Chrome where each character actually lands (kerning included)
render    headless Chrome screenshot of an SVG, and a regional diff
"""
import base64, io, json, os, re, subprocess
import numpy as np
from PIL import Image
from fontTools.ttLib import TTFont, TTCollection
from fontTools.subset import Subsetter, Options
from fontTools.pens.boundsPen import BoundsPen

CHROME = os.environ.get(
    'CHROME', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


# --------------------------------------------------------------------- metrics
class Face:
    """Glyph metrics for one font file (or one face of a .ttc)."""

    def __init__(self, path, index=None, axes=None):
        if index is not None:
            self.f = TTFont(path, fontNumber=index)
        else:
            self.f = TTFont(path)
        if axes:
            from fontTools.varLib.instancer import instantiateVariableFont
            self.f = instantiateVariableFont(self.f, axes, inplace=False,
                                             updateFontNames=False)
        self.path, self.index, self.axes = path, index, axes
        self.upem = self.f['head'].unitsPerEm
        self.cmap = self.f.getBestCmap()
        self.gs = self.f.getGlyphSet()
        self.hmtx = self.f['hmtx']
        self._b = {}

    def has(self, ch):
        return ord(ch) in self.cmap

    def missing(self, text):
        return sorted({c for c in text if not self.has(c)})

    def bounds(self, ch):
        n = self.cmap[ord(ch)]
        if n not in self._b:
            bp = BoundsPen(self.gs)
            self.gs[n].draw(bp)
            self._b[n] = bp.bounds or (0, 0, 0, 0)
        return self._b[n]

    def adv(self, ch):
        return self.hmtx[self.cmap[ord(ch)]][0]

    def cap_ratio(self):
        return self.bounds('H')[3] / self.upem

    def tallest(self, text):
        return max(self.bounds(c)[3] for c in text if c != ' ' and self.has(c))

    def size_for_cap(self, cap_px):
        """font-size that puts flat capitals at cap_px tall."""
        return cap_px / self.cap_ratio()

    def size_for_inktop(self, text, height_px):
        """font-size that puts the string's highest ink at height_px above the baseline."""
        return height_px * self.upem / self.tallest(text)

    def ink(self, text, size, track=0.0, wordspace=0.0):
        """(first ink x, last ink x) relative to the text origin, in px."""
        s, pen, lo, hi = size / self.upem, 0.0, None, None
        for ch in text:
            x0, _, x1, _ = self.bounds(ch)
            if x1 > x0:
                lo = pen + x0 * s if lo is None else min(lo, pen + x0 * s)
                hi = pen + x1 * s if hi is None else max(hi, pen + x1 * s)
            pen += self.adv(ch) * s + track + (wordspace if ch == ' ' else 0.0)
        return lo, hi

    def width(self, text, size, track=0.0, wordspace=0.0):
        lo, hi = self.ink(text, size, track, wordspace)
        return hi - lo


# ------------------------------------------------------------- font matching
def aspect_ratios(face, measured):
    """measured = {'T': (w_px, h_px), ...} straight off the original.

    Returns (per-glyph ratio, mean, spread). Mean ~1.0 means the face has the
    original's proportions. A LOW SPREAD with a mean well off 1.0 means the
    original was uniformly stretched or squashed -- an image-generator
    artefact, not a different typeface. A HIGH spread (>0.05) means the face
    genuinely has different proportions, so it is the wrong face.
    """
    out = {}
    for ch, (ow, oh) in measured.items():
        if not face.has(ch):
            continue
        x0, y0, x1, y1 = face.bounds(ch)
        if x1 <= x0 or y1 <= y0:
            continue
        out[ch] = (ow / oh) / ((x1 - x0) / (y1 - y0))
    v = np.array(list(out.values()))
    return out, float(v.mean()), float(v.std())


def width_ratio(face, text, cap_px, measured_px):
    """measured width / the face's natural width at that cap height.

    ~1.00 across several lines is strong evidence you have the right face at
    the right size, and that the original carries no tracking.
    """
    return measured_px / face.width(text, face.size_for_cap(cap_px))


def pil_font(face, size):
    from PIL import ImageFont
    kw = {}
    if face.index is not None:
        kw['index'] = face.index
    f = ImageFont.truetype(face.path, max(2, int(round(size))), **kw)
    if face.axes:
        f.set_variation_by_axes(list(face.axes.values()))
    return f


def render_line(face, text, size, width_px, height_px, baseline_px, track=None):
    """Rasterise one line for shape comparison against the original.

    Draws the whole string in one call when untracked, so pair kerning applies.
    Drawing character by character defeats it: getlength('A') + getlength('V')
    is not getlength('AV') in a font with a kern table, and the shaper never
    gets to see the pair. When tracking is needed, each glyph's x still comes
    from the *prefix* advance, which keeps the kerning the shaper applied.
    """
    from PIL import ImageDraw
    f = pil_font(face, size)
    bb = f.getbbox(text, anchor='ls')
    img = Image.new('L', (width_px + 8, height_px + 8), 0)
    d = ImageDraw.Draw(img)
    if track is None and width_px:
        track = (width_px - (bb[2] - bb[0])) / max(1, len(text) - 1)
    if not track:
        d.text((-bb[0], baseline_px), text, font=f, fill=255, anchor='ls')
    else:
        for i, ch in enumerate(text):
            x = -bb[0] + f.getlength(text[:i]) + i * track
            d.text((x, baseline_px), ch, font=f, fill=255, anchor='ls')
    return np.array(img)


def has_shaping():
    """True if PIL can apply GPOS (kerning) when it rasterises.

    Without libraqm, PIL's BASIC layout engine ignores GPOS entirely --
    getlength('AV') == getlength('A') + getlength('V') even in a font with
    hundreds of kern pairs. So a PIL-rendered LINE is never kerned and must not
    be compared against the original as if it were. Two consequences, both
    handled elsewhere in this kit:

      * shape scoring compares single glyphs (`render_glyph`), where there are
        no pairs and kerning cannot apply;
      * where true kerned positions are needed, they come from Chrome via
        `probe_pens`, which uses the same shaper that will render the SVG.
    """
    from PIL import features
    return features.check('raqm')


def kern_pairs(face):
    """Kern pairs the FONT declares, read from GPOS -- independent of PIL."""
    n = 0
    gp = face.f.get('GPOS')
    if not gp:
        return 0
    for lookup in gp.table.LookupList.Lookup:
        if lookup.LookupType == 2:
            for st in lookup.SubTable:
                if hasattr(st, 'Coverage') and st.Coverage:
                    n += len(st.Coverage.glyphs)
    return n


def stem_ratio(face, ch='H', size=400):
    """Vertical stem width / cap height.

    The measurement SKILL.md §3 (b2) turns on: a face squashed horizontally
    loses stem weight in proportion, a genuinely compressed cut does not.
    Sampled over many scanlines, taking the narrowest ink run on each and then
    the MEDIAN of those. A single mid-height scanline is wrong: it crosses H's
    crossbar, so stem-bar-stem merges into one run spanning the whole glyph.
    Most rows are stem-only, so the median lands on the stem and the few rows
    through a crossbar or serif are outvoted.
    """
    g = render_glyph(face, ch, size)
    if g is None:
        return None
    ink = g > 128
    ys = np.where(ink.any(1))[0]
    if len(ys) < 10:
        return None
    top, bot = ys.min(), ys.max()
    cap = bot - top + 1
    mins = []
    for y in range(top + int(cap * 0.08), bot - int(cap * 0.08)):
        runs, n = [], 0
        for v in ink[y]:
            if v:
                n += 1
            elif n:
                runs.append(n); n = 0
        if n:
            runs.append(n)
        if runs:
            mins.append(min(runs))
    if not mins:
        return None
    return float(np.median(mins)) / cap


def counter_ratio(face, ch='O', size=400):
    """Counter width / glyph width -- narrow counters mark a compressed cut."""
    g = render_glyph(face, ch, size)
    if g is None:
        return None
    ink = g > 128
    ys = np.where(ink.any(1))[0]
    if not len(ys):
        return None
    row = ink[(ys.min() + ys.max()) // 2]
    xs = np.where(row)[0]
    if len(xs) < 2:
        return None
    width = xs.max() - xs.min() + 1
    gaps, n = [], 0
    for v in row[xs.min():xs.max() + 1]:
        if not v:
            n += 1
        elif n:
            gaps.append(n); n = 0
    return (max(gaps) / width) if gaps else 0.0


def render_glyph(face, ch, size, pad=6):
    """Rasterise ONE glyph, tight-cropped to its ink. The unit of shape
    comparison: no pairs, so no shaping or kerning is involved."""
    from PIL import ImageDraw
    f = pil_font(face, size)
    bb = f.getbbox(ch, anchor='ls')
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return None
    img = Image.new('L', (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad - bb[0], pad - bb[1]), ch,
                             font=f, fill=255, anchor='ls')
    return np.array(img)


def iou(a, b):
    n = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:n[0], :n[1]] > 128, b[:n[0], :n[1]]
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


# ----------------------------------------------------------------- subsetting
def subset_b64(path, text, index=None, axes=None, keep=('c2sc', 'case')):
    """Subset a font to the glyphs in `text`, keep kerning, return (b64, bytes, features).

    fontTools' default feature set already keeps `kern`. Never strip layout
    features wholesale -- that kills kerning, which is the point of letting the
    browser set the type.
    """
    if index is not None:
        font = TTCollection(path).fonts[index]
    else:
        font = TTFont(path)
    if axes:
        from fontTools.varLib.instancer import instantiateVariableFont
        font = instantiateVariableFont(font, axes, inplace=False, updateFontNames=False)
    opts = Options()
    opts.notdef_outline = False
    opts.layout_features = sorted(set(opts.layout_features) | set(keep))
    s = Subsetter(options=opts)
    s.populate(text=''.join(sorted(set(text))))
    s.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    raw = buf.getvalue()
    feats = sorted({r.FeatureTag for t in ('GPOS', 'GSUB') if t in font
                    for r in font[t].table.FeatureList.FeatureRecord})
    return base64.b64encode(raw).decode(), len(raw), feats


def font_format(path):
    return ('opentype', 'otf') if path.lower().endswith(('.otf', '.ttc')) else ('truetype', 'ttf')


def face_css(family, b64, weight, path):
    fmt, mime = font_format(path)
    return (f'      @font-face {{\n'
            f'        font-family: "{family}";\n'
            f'        src: url(data:font/{mime};base64,{b64}) format("{fmt}");\n'
            f'        font-weight: {weight}; font-style: normal;\n'
            f'      }}')


# ---------------------------------------------------------------- image asset
def encode_image(png_path, out_path, quality=76, fmt='webp'):
    """Compress the cut-out artwork. WebP usually beats JPEG on both size and
    error for painterly art; use PNG only for flat line work."""
    subprocess.run(['magick', png_path, '-quality', str(quality),
                    '-define', 'webp:method=6', out_path], check=True)
    o = np.array(Image.open(png_path).convert('RGB')).astype(int)
    c = np.array(Image.open(out_path).convert('RGB')).astype(int)
    import os
    return os.path.getsize(out_path), float(np.abs(o - c).mean())


def data_uri(path):
    mime = {'webp': 'image/webp', 'png': 'image/png',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}[path.rsplit('.', 1)[1].lower()]
    return f'data:{mime};base64,' + base64.b64encode(open(path, 'rb').read()).decode()


# ------------------------------------------------------- browser probe/render
def probe_pens(items, css, workdir='.', width=8000):
    """Ask Chrome for the pen x of every character, so kerning and any
    letter-/word-spacing you applied are accounted for exactly.

    items: [(id, svg_text_element_string)] -- each <text> needs data-id.
    Returns {id: [x per character]}.
    """
    html = f'''<!doctype html><meta charset="utf-8">
<style>{css}</style>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="600">
{chr(10).join(t for _, t in items)}
</svg><pre id="out"></pre>
<script>
document.fonts.ready.then(() => {{
  const out = {{}};
  document.querySelectorAll('text[data-id]').forEach(t => {{
    const xs = [];
    for (let i = 0; i < t.getNumberOfChars(); i++) xs.push(t.getStartPositionOfChar(i).x);
    out[t.dataset.id] = xs;
  }});
  document.getElementById('out').textContent = JSON.stringify(out);
}});
</script>'''
    p = f'{workdir}/_probe.html'
    open(p, 'w').write(html)
    dom = subprocess.run([CHROME, '--headless', '--disable-gpu', '--dump-dom',
                          '--virtual-time-budget=8000', p],
                         capture_output=True, text=True).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m:
        raise RuntimeError('Chrome probe returned no data')
    return json.loads(m.group(1).replace('&quot;', '"'))


def render_svg(svg_path, out_png, w=1024, h=1536, workdir='.'):
    page = f'{workdir}/_page.html'
    open(page, 'w').write(
        '<!doctype html><meta charset="utf-8">'
        '<style>html,body{margin:0;padding:0}'
        f'img{{display:block;width:{w}px;height:{h}px}}</style>'
        f'<img src="{svg_path.rsplit("/", 1)[-1]}">')
    subprocess.run([CHROME, '--headless', '--disable-gpu', '--hide-scrollbars',
                    '--force-device-scale-factor=1', f'--window-size={w},{h}',
                    f'--screenshot={out_png}', '--virtual-time-budget=9000', page],
                   capture_output=True)
    return out_png


def diff(render_png, original_png, regions=None):
    """mean absolute difference, overall and per named region (y0, y1)."""
    a = np.array(Image.open(render_png).convert('RGB')).astype(int)
    b = np.array(Image.open(original_png).convert('RGB')).astype(int)
    n = min(a.shape[0], b.shape[0])
    a, b = a[:n], b[:n]
    out = {'all': round(float(np.abs(a - b).mean()), 2)}
    for name, (y0, y1) in (regions or {}).items():
        d = np.abs(a[y0:y1] - b[y0:y1])
        out[name] = round(float(d.mean()), 2)
    return out

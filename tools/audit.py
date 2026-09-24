#!/usr/bin/env python3
"""Check a finished poster SVG against what the original MEANT, line by line.

    python3 audit.py                                  # every converted poster
    python3 audit.py swiss_international-fete risograph-gig
    python3 audit.py path/to/out.svg --solution path/to/solution.json

Not a pixel comparison. The point of resetting type is that the font does
better than the generator did, so widths, letterforms and spacing are expected
to differ and are never scored. What is checked is the design's intent:

  alignment  a block whose lines share a left edge, centre or right edge in the
             original still shares it -- and in the same place
  size       each line's cap height against the original's
  weight     stroke weight (medial ridge width / cap), measured with a mask cut
             between the line's own ink and ground colours, so white type on a
             dark panel is measured exactly as black type on paper is
  case       capitals where the original set capitals
  leftovers  the original's ink still present in the shipped artwork (the SVG
             rendered with its text stripped), and type-like bands left in the
             artwork that nothing was reset over
  damage     artwork pixels -- neither the type's colour nor its ground -- that
             the blanking painted over
  overflow   text running off the page

Everything it prints is a lead, with the numbers that would explain it away.
Silence means nothing looked wrong.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile
from multiprocessing import Pool
import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402
from svgkit import CHROME, render_svg  # noqa: E402
from linemask import line_mask, hex_rgb  # noqa: E402
from typefeatures import weight as stroke_weight  # noqa: E402
from analyse import glyph_runs, line_metrics  # noqa: E402

# How far a line may move off its block's shared edge before it is reported,
# as a fraction of page width, with a floor in px -- the same tolerance the
# builder uses to decide that a block shares an edge.
from layout import blocks, EDGE_TOL, EDGE_MIN  # noqa: E402


# ----------------------------------------------------------------- browser
def text_boxes(svg_text, workdir):
    """[{text, x, y, w, h, fill, anchor, size}] for every <text>, from Chrome."""
    body = re.sub(r'^<\?xml[^>]*>\s*', '', svg_text)
    html = ('<!doctype html><meta charset="utf-8"><body style="margin:0">'
            + body + '''<pre id="out"></pre><script>
document.fonts.ready.then(() => {
  const out = [];
  document.querySelectorAll('svg text').forEach(t => {
    if (t.closest('[data-flaw], .flawed-label')) return;
    const b = t.getBBox();
    out.push({text: t.textContent, x: b.x, y: b.y, w: b.width, h: b.height,
              fill: t.getAttribute('fill') || '', anchor: t.getAttribute('text-anchor') || 'start',
              size: parseFloat(t.getAttribute('font-size') || '0'),
              base: parseFloat(t.getAttribute('y') || '0')});
  });
  document.getElementById('out').textContent = JSON.stringify(out);
});</script>''')
    p = os.path.join(workdir, '_boxes.html')
    open(p, 'w').write(html)
    dom = subprocess.run([CHROME, '--headless', '--disable-gpu', '--dump-dom',
                          '--virtual-time-budget=8000', p],
                         capture_output=True, text=True).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m:
        raise RuntimeError('Chrome returned no text boxes')
    return json.loads(m.group(1).replace('&quot;', '"').replace('&amp;', '&'))


def strip_text(svg_text):
    """The SVG as shipped, minus its type: what the artwork layer really is."""
    from flag_flawed import strip_label
    return re.sub(r'<text\b[^>]*>.*?</text>', '', strip_label(svg_text), flags=re.S)


def render(svg_text, workdir, name, w, h):
    p = os.path.join(workdir, name + '.svg')
    open(p, 'w').write(svg_text)
    png = os.path.join(workdir, name + '.png')
    render_svg(p, png, w, h, workdir=workdir)
    return np.array(Image.open(png).convert('RGB')).astype(int)


# ------------------------------------------------------------- measurement
def measure(arr, box, ink, ground=None, core=None, xcore=None):
    """Ink extents, cap, lower-case share and stroke weight of one line.

    `box` is (x0, y0, x1, y1) inclusive and may be padded; `core` is the
    (y0, y1) the line itself occupies. Only ink that reaches into the core rows
    is the line's: the padding catches the tops of this line's capitals, but
    also the feet of the line above, and a column holding both reads as one
    glyph a line and a half tall.
    """
    mask, ground = line_mask(arr, box, ink, ground)
    if mask is None or mask.sum() < 20:
        return None
    if core is not None or xcore is not None:
        # Keep components that reach the line's own rows and are centred
        # within its own columns: the padding otherwise admits the feet of
        # the line above and the edge of a banner or frame beside the type.
        lab, n = ndimage.label(mask, np.ones((3, 3), bool))
        keep = set(range(1, n + 1))
        if core is not None:
            r0, r1 = max(0, core[0] - box[1]), min(mask.shape[0], core[1] - box[1] + 1)
            keep &= set(np.unique(lab[r0:r1]).tolist())
        if xcore is not None:
            for i, sl in enumerate(ndimage.find_objects(lab), start=1):
                cx = box[0] + (sl[1].start + sl[1].stop - 1) / 2
                if not xcore[0] <= cx <= xcore[1]:
                    keep.discard(i)
        mask = np.isin(lab, sorted(keep))
        if mask.sum() < 20:
            return None
    runs = glyph_runs(mask, 0, mask.shape[0] - 1, 0, mask.shape[1] - 1, min_w=1)
    lm = line_metrics(runs)
    if not lm:
        return None
    cols = np.where(mask.any(0))[0]
    return dict(left=box[0] + int(cols.min()), right=box[0] + int(cols.max()),
                cap=cap_of(runs, lm), lower=lower_share(runs), ground=ground, mask=mask,
                weight=stroke_weight(mask, lm['cap']))


def cap_of(runs, lm):
    """Cap height from the capital line (see lower_share), not the highest ink:
    an accent over FETE or a comma's tail would otherwise add a fifth."""
    ext = [r[3] - r[2] + 1 for r in runs]
    real = [r for r, e in zip(runs, ext) if e >= 0.4 * max(ext)]
    if len(real) < 3:
        return lm['cap']
    tops = sorted(r[2] for r in real)
    # the capitals: tops within a whisker of the highest *typical* top
    top = float(np.percentile(tops, 15))
    return int(round(lm['base'] + 1 - top))


def lower_share(runs):
    """Share of glyph-sized runs whose tops sit well below the capital line.

    The capital line is a low percentile of the tops, not the highest: an
    accent (the circumflex of FETE) or a tall ampersand stands above the
    capitals, and taking it as the cap line makes every capital look lower
    case. ~0 in capitals, over 0.3 in mixed case.
    """
    if not runs:
        return 0.0
    ext = [r[3] - r[2] + 1 for r in runs]
    real = [r for r, e in zip(runs, ext) if e >= 0.4 * max(ext)]
    if len(real) < 3:
        return 0.0
    base = float(np.median([r[3] for r in real]))
    top = float(np.percentile([r[2] for r in real], 15))
    return sum(1 for r in real if r[2] - top > 0.15 * (base - top)) / len(real)


def orig_box(b, W, H):
    """The original line's box: its own rows, a little air either side."""
    hp = min(12, int(0.3 * b['cap']) + 2)
    return (max(0, b['left'] - hp), max(0, b['y0'] - 2),
            min(W - 1, b['right'] + hp), min(H - 1, b['y1'] + 2))


def edge(m, kind):
    return {'left': m['left'], 'right': m['right'],
            'centre': (m['left'] + m['right']) / 2}[kind]


# ------------------------------------------------------------------- audit
def audit(svg_path, sol, workdir):
    """-> list of findings (strings) for one poster."""
    svg = open(svg_path).read()
    if 'COULD NOT CONVERT' in svg:
        return []
    W, H = sol['size']
    src = sol['image']
    if not os.path.exists(src):
        src = os.path.join(poster_site.examples(), os.path.basename(src))
    orig = np.array(Image.open(src).convert('RGB')).astype(int)
    full = render(svg, workdir, 'full', W, H)
    art = render(strip_text(svg), workdir, 'art', W, H)
    boxes = text_boxes(svg, workdir)
    L = sol['lines']
    key_of = {b: a['key'] for a in sol['assigned'] for b in a['bands']}
    used = sorted(key_of)
    found = []

    # Match each assigned band to the <text> set over it: baseline inside the
    # band's rows, and overlapping it horizontally.
    placed = {}
    for bi in used:
        b = L[bi]
        best, score = None, 0
        for t in boxes:
            if not (b['y0'] - 0.3 * b['cap'] <= t['base'] <= b['y1'] + 0.6 * b['cap']):
                continue
            ov = min(b['right'], t['x'] + t['w']) - max(b['left'], t['x'])
            if ov > score:
                best, score = t, ov
        placed[bi] = best

    meas_o, meas_r = {}, {}
    boxes_o = {}
    for bi in used:
        b, t, key = L[bi], placed[bi], key_of[bi]
        bo = orig_box(b, W, H)
        boxes_o[bi] = bo
        mo = measure(orig, bo, b['rgb'], xcore=(b['left'], b['right']))
        if t is None:
            found.append(f"{key}: nothing is set over its band (y{b['y0']}..{b['y1']})")
            continue
        # The reset line sits on the measured baseline, so take the same rows
        # (shifted if the builder moved it) and the columns its own box spans.
        # Never pad vertically: in a tight list the next line is a few px away,
        # and its ink would be measured as this one's.
        dy = int(round(t['base'] - b['baseline']))
        hp = min(12, int(0.3 * b['cap']) + 2)
        br = (max(0, int(t['x']) - hp), max(0, b['y0'] + dy - int(0.15 * b['cap']) - 2),
              min(W - 1, int(t['x'] + t['w']) + hp), min(H - 1, b['y1'] + dy + 2))
        mr = measure(full, br, t['fill'] or b['rgb'], mo['ground'] if mo else None,
                     core=(b['y0'] + dy, b['y1'] + dy), xcore=(t['x'], t['x'] + t['w']))
        if not mo or not mr:
            continue
        meas_o[bi], meas_r[bi] = mo, mr
        if mr['right'] > W - 2 or mr['left'] < 1:
            found.append(f"{key}: runs off the page (ink x {mr['left']}..{mr['right']})")
        cr = mr['cap'] / max(1, mo['cap'])
        # A couple of px is measurement noise at any size; 12% is a real step.
        if abs(mr['cap'] - mo['cap']) > max(2.5, 0.12 * mo['cap']):
            found.append(f"{key}: cap {mr['cap']} vs original {mo['cap']} ({cr:.2f}x)"
                         f" -- shrunk to clear artwork, or a size misread?")
        if mo['weight'] and mr['weight']:
            wr = mr['weight'] / mo['weight']
            # On small type one pixel of stroke is a 50% change, so the step
            # must also be at least 1.5px of real stroke.
            dpx = abs(mr['weight'] * mr['cap'] - mo['weight'] * mo['cap'])
            if not 0.8 <= wr <= 1.25 and dpx >= 1.5:
                found.append(f"{key}: {'lighter' if wr < 1 else 'heavier'} than the "
                             f"original (stroke {mr['weight']:.3f} vs {mo['weight']:.3f}"
                             f" of cap, {wr:.2f}x)")
        if mo['lower'] < 0.12 and mr['lower'] > 0.3:
            found.append(f"{key}: capitals in the original, mixed case in the render")
        elif mo['lower'] > 0.3 and mr['lower'] < 0.12:
            found.append(f"{key}: mixed case in the original, capitals in the render")

    # Alignment, per block of the original.
    for g, kind in blocks([L[i] for i in used], W):
        if not kind:
            continue
        idx = [used[i] for i in g if used[i] in meas_r]
        if not idx:
            continue
        keys = sorted({key_of[i] for i in idx}, key=lambda k: min(
            i for i in idx if key_of[i] == k))
        orig_e = [edge(meas_o[i], kind) for i in idx]
        rend_e = [edge(meas_r[i], kind) for i in idx]
        tol = max(EDGE_MIN, EDGE_TOL * W)
        moved = max(abs(r - o) for r, o in zip(rend_e, orig_e))
        spread = max(rend_e) - min(rend_e)
        if (len(idx) > 1 and spread > tol) or moved > max(tol, 0.5 * min(
                meas_o[i]['cap'] for i in idx)):
            found.append(f"{'/'.join(keys)}: {kind}-aligned in the original "
                         f"(edge {np.median(orig_e):.0f}); rendered edges "
                         f"{', '.join(f'{v:.0f}' for v in rend_e)}")

    # Leftovers: the original's ink for each line still in the artwork layer.
    for bi in used:
        if bi not in meas_o:
            continue
        b, mo = L[bi], meas_o[bi]
        bo = boxes_o[bi]
        ma, _ = line_mask(art, bo, b['rgb'], mo['ground'])
        if ma is None:
            continue
        # Only where the original had ink: anything else of that colour is
        # artwork that was always there.
        left = (ma & mo['mask']).sum() / max(1, mo['mask'].sum())
        if left > 0.12:
            found.append(f"{key_of[bi]}: {left:.0%} of the original's ink is still "
                         f"in the artwork (a ghost under the reset line)")
        # Damage: pixels that were neither this line's ink nor its ground --
        # artwork -- and that the blanking repainted.
        sub_o = orig[bo[1]:bo[3] + 1, bo[0]:bo[2] + 1].astype(float)
        sub_a = art[bo[1]:bo[3] + 1, bo[0]:bo[2] + 1].astype(float)
        ink = hex_rgb(b['rgb'])
        third = ((np.linalg.norm(sub_o - ink, axis=2) > 70)
                 & (np.linalg.norm(sub_o - mo['ground'], axis=2) > 70))
        third &= ~ndimage.binary_dilation(mo['mask'], iterations=2)
        hit = third & (np.abs(sub_a - sub_o).max(2) > 80)
        if hit.sum() > max(60, 0.05 * b['cap'] ** 2):
            found.append(f"{key_of[bi]}: blanking repainted {hit.sum()} px of "
                         f"artwork round the line")

    # Type-like bands left in the artwork that nothing was reset over.
    try:
        from bands import find_lines
        k = sol.get('knobs') or {}
        ex = [tuple(int(v) for v in x.split(',')) if isinstance(x, str) else tuple(x)
              for x in (k.get('exclude') or [])]
        p = os.path.join(workdir, 'art.png')
        m = find_lines(p, min_glyphs=max(3, k.get('min_glyphs', 3)),
                       solid_radius=k.get('solid_radius', 24),
                       rule_length=k.get('rule_length', 160),
                       contrast=k.get('contrast', 40), exclude=ex)
        med = float(np.median([L[i]['cap'] for i in used])) if used else 10
        left_bands = [l for l in m['lines'] if l['n_runs'] >= 5 and l['cap'] >= 0.6 * med]
        for l in left_bands[:4]:
            found.append(f"type-like band left in the artwork at y{l['y0']}..{l['y1']} "
                         f"x{l['left']}..{l['right']} ({l['n_runs']} glyphs, cap "
                         f"{l['cap']}) -- unreset copy, or the artwork's own lettering?")
    except Exception as e:                     # never let the lead-finder crash the audit
        found.append(f'(could not scan the artwork for leftover type: {e})')
    return found


def audit_one(args):
    svg_path, soln_path = args
    name = os.path.basename(svg_path)[:-4]
    try:
        sol = json.load(open(soln_path))
        with tempfile.TemporaryDirectory() as wd:
            shutil.copy(svg_path, os.path.join(wd, 'in.svg'))
            return name, audit(svg_path, sol, wd)
    except Exception as e:
        return name, [f'AUDIT FAILED: {type(e).__name__}: {e}']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('targets', nargs='*',
                    help='style-event names, or one SVG path with --solution')
    ap.add_argument('--solution')
    ap.add_argument('-j', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()
    jobs = []
    if a.solution:
        jobs = [(a.targets[0], a.solution)]
    else:
        names = ([t if t.endswith('-v2') else t + '-v2' for t in a.targets] or
                 sorted(os.path.basename(p)[:-5] for p in
                        glob.glob(os.path.join(poster_site.solutions(), '*.json'))))
        for n in names:
            svg = os.path.join(poster_site.svgs(), n + '.svg')
            soln = os.path.join(poster_site.solutions(), n + '.json')
            if os.path.exists(svg) and os.path.exists(soln):
                jobs.append((svg, soln))
    with Pool(min(a.j, len(jobs) or 1)) as pool:
        results = pool.map(audit_one, jobs)
    n_found = 0
    for name, found in results:
        if not found:
            continue
        n_found += 1
        print(f'{name}: {len(found)}')
        for f in found:
            print(f'    {f}')
    print(f'\n{len(results) - n_found} quiet, {n_found} with leads, of {len(results)}')


if __name__ == '__main__':
    main()

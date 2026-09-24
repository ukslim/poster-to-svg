#!/usr/bin/env python3
"""Sense-check a finished poster SVG by reading it as text.

    python3 sense_check.py assets/poster-svg/swiss_international-fete-v2.svg

Nothing here is certainly a defect. Two halves of one phrase set at different
sizes is *suspicious*, not *wrong* -- a designer may really have stepped them,
and the solver may have had a good reason. So each finding is printed with the
measurements that would explain it away, and the reader decides. Silence means
nothing looked odd, which is what keeps this cheap: no images, no re-solving,
a few hundred bytes of output for a poster that is fine.

Run after building. `convert.py` runs it automatically.
"""
import base64, io, json, os, re, sys, html
from fontTools.ttLib import TTFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flag_flawed import strip_label  # noqa: E402
import sitepaths as poster_site  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)


def faces(svg):
    """family -> {weight: TTFont} for the embedded subsets."""
    out = {}
    for m in re.finditer(
            r'font-family:\s*"([^"]+)";\s*src:\s*url\(data:font/[a-z0-9-]+;base64,'
            r'([A-Za-z0-9+/=]+)\)[^}]*?font-weight:\s*(\d+)', svg, re.S):
        f = TTFont(io.BytesIO(base64.b64decode(m.group(2))))
        out.setdefault(m.group(1), {})[int(m.group(3))] = f
    return out


def texts(svg):
    """Every <text> as a dict, in document order."""
    out = []
    for m in re.finditer(r'<text([^>]*)>(.*?)</text>', svg, re.S):
        a, body = m.group(1), m.group(2)
        g = lambda k: (re.search(rf'{k}="([^"]*)"', a) or [None, None])[1]
        out.append(dict(cls=g('class') or '', x=float(g('x') or 0),
                        y=float(g('y') or 0), size=float(g('font-size') or 0),
                        text=html.unescape(re.sub(r'<[^>]+>', '', body))))
    return out


def width(font, s, size):
    """Advance width, ignoring kerning -- a slight over-estimate, which is the
    safe direction for an overflow check."""
    cmap, hmtx = font.getBestCmap(), font['hmtx']
    upem = font['head'].unitsPerEm
    total = 0
    for ch in s:
        gn = cmap.get(ord(ch))
        if gn:
            total += hmtx[gn][0]
    return total / upem * size


def check(path, soln=None, name=None):
    """Print what looks odd; return how many things. `soln` and `name` default
    to the site's stored solution for this file, for an SVG outside the site
    (a work directory) pass them."""
    svg = strip_label(open(path).read())
    if soln and not name:
        name = json.load(open(soln))['image'].rsplit('/', 1)[-1].rsplit('.', 1)[0]
    name = name or os.path.basename(path)[:-4]
    found = []

    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    page_w = float(vb.group(1)) if vb else None
    fs = faces(svg)
    cls_fam = dict(re.findall(
        r'\.([A-Za-z0-9_-]+)\s*\{[^}]*font-family:\s*"([^"]+)"', svg))
    T = texts(svg)

    # --- is every line of copy actually in the SVG? ---
    # The cheapest check there is, and the one that matters most: a poster
    # that reads correctly can still be missing a line, because the original
    # bitmap of it survived in the artwork and nothing was reset. Reversed
    # type on a small panel does exactly this.
    ev = 'fete' if '-fete' in name else 'gig' if '-gig' in name else None
    if ev and fs:   # an escape hatch embeds no fonts and carries no copy
        try:
            sys.path.insert(0, HERE)
            from measure import load_copy
            got = ' '.join(t['text'] for t in T).upper()
            for c in load_copy(ev):
                words = [w for w in re.findall(r"[A-Za-z0-9']{3,}", c['text'])][:3]
                if words and not all(w.upper() in got for w in words):
                    found.append(
                        f"{c['key']!r} is not in the SVG at all: "
                        f"{c['text'][:44]!r}.\n"
                        f"      might be fine: the poster may simply not print "
                        f"it. If it does, that line is still a bitmap in the "
                        f"artwork -- look for reversed type on a panel, which "
                        f"the ink masks do not pick up.")
        except Exception:
            pass

    # --- copy lines wrapped over several bands should be one size ---
    soln = soln or os.path.join(poster_site.solutions(), f'{name}.json')
    if os.path.exists(soln):
        s = json.load(open(soln))
        L = s['lines']
        # The <text> elements come out grouped by face, not in copy order, so
        # match each one to the copy line that contains it rather than by
        # position.
        norm = lambda t: re.sub(r'\s+', ' ', t).strip()
        bykey = {}
        for t in T:
            frag = norm(t['text'])
            if not frag:
                continue
            for a in s['assigned']:
                if frag and frag in norm(a['text']):
                    bykey.setdefault(a['key'], []).append(t)
                    break
        for a in s['assigned']:
            n = len(a['bands'])
            part = bykey.get(a['key'], [])
            if n < 2 or len(part) < n:
                continue
            sizes = [p['size'] for p in part]
            if max(sizes) - min(sizes) <= 0.02 * max(sizes):
                continue
            caps = [L[b]['cap'] for b in a['bands']]
            found.append(
                f"{a['key']!r} is one phrase but its {n} lines are set at "
                f"{', '.join('%.0f' % v for v in sizes)}.\n"
                f"      might be fine: the bands measure caps "
                f"{', '.join(str(c) for c in caps)}"
                + (" -- genuinely different sizes in the original"
                   if max(caps) > 1.12 * min(caps)
                   else " -- which agree, so the difference came from fitting, "
                        "not from the poster") + ".\n"
                f"      text: {' / '.join(repr(p['text'][:24]) for p in part)}")

        # --- a list item set differently from its siblings ---
        keys = [a['key'] for a in s['assigned']]
        fam = {}
        for k in keys:
            stem = re.sub(r'\d+$', '', k)
            if stem != k:
                fam.setdefault(stem, []).append(k)
        capof = {a['key']: s['lines'][a['bands'][0]]['cap'] for a in s['assigned']}
        for stem, ks in fam.items():
            if len(ks) < 3:
                continue
            sz = {k: bykey[k][0]['size'] for k in ks if bykey.get(k)}
            if not sz:
                continue
            med = sorted(sz.values())[len(sz) // 2]
            for k, v in sz.items():
                if 0.88 * med <= v <= 1.14 * med:
                    continue
                found.append(
                        f"{k!r} is set at {v:.0f} among {stem}* siblings at "
                        f"~{med:.0f}.\n"
                        f"      might be fine: its measured cap is {capof[k]} "
                        f"against a sibling median of "
                        f"{sorted(capof[x] for x in ks)[len(ks)//2]}. Sibling "
                        f"items in one list are usually one size, so a cap that "
                        f"disagrees is more likely a mis-measurement than a "
                        f"design.\n"
                        f"      text: {bykey[k][0]['text'][:40]!r}")

    # --- a line running off the page ---
    if page_w:
        for t in T:
            f = fs.get(cls_fam.get(t['cls'], ''), {})
            if not f:
                continue
            w = width(list(f.values())[0], t['text'], t['size'])
            if t['x'] + w > page_w + 2:
                found.append(
                    f"{t['text'][:28]!r} reaches x={t['x'] + w:.0f} on a "
                    f"{page_w:.0f}px page.\n"
                    f"      might be fine: this ignores kerning, so it "
                    f"over-estimates by a little; only worry past ~1%.")

    if found:
        print(f'{name}: {len(found)} thing(s) worth a look')
        for f in found:
            print(f'  - {f}')
    return len(found)


if __name__ == '__main__':
    n = sum(check(p) for p in sys.argv[1:])
    sys.exit(0)

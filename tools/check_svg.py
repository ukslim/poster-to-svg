#!/usr/bin/env python3
"""Check finished poster SVGs for the font-matching defect.

    python3 check_svg.py                    # everything in assets/poster-svg/
    python3 check_svg.py path/to/one.svg

For each <text>, work out the family and weight the browser will compute, run
CSS font matching against the embedded @font-face rules, and check the face it
lands on actually carries every character in that text. If it does not, the
browser substitutes a system font per missing character and a word comes out
half one face and half another.
"""
import re, base64, io, glob, os, sys, html
from fontTools.ttLib import TTFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flag_flawed import strip_label  # noqa: E402


def match(faces, want):
    """CSS font-weight matching: which face does the browser pick?"""
    if want in faces:
        return faces[want]
    ws = sorted(faces)
    if want == 400 and 500 in faces:
        return faces[500]
    if want <= 500:
        below = [w for w in ws if w < want]
        return faces[max(below)] if below else faces[min(ws)]
    above = [w for w in ws if w > want]
    return faces[min(above)] if above else faces[max(ws)]


HERE = os.path.dirname(os.path.abspath(__file__))
import sitepaths as poster_site  # noqa: E402

bad = []
targets = sys.argv[1:] or sorted(glob.glob(
    os.path.join(poster_site.svgs(), '*.svg')))
for p in targets:
    s = strip_label(open(p).read())

    faces = {}          # family -> {weight: (cmap, nglyphs)}
    for m in re.finditer(
            r'font-family:\s*"([^"]+)";\s*src:\s*url\(data:font/[a-z0-9-]+;base64,'
            r'([A-Za-z0-9+/=]+)\)[^}]*?font-weight:\s*(\d+)', s, re.S):
        f = TTFont(io.BytesIO(base64.b64decode(m.group(2))))
        faces.setdefault(m.group(1), {})[int(m.group(3))] = set(f.getBestCmap())
    # An SVG is XML, so a reader without a declaration must assume UTF-8 --
    # but macOS QuickLook reads Latin-1 and renders the two bytes of a `£` as
    # `Â£`. Declare the encoding, and keep the bytes ASCII so it cannot matter.
    enc = []
    if not s.startswith('<?xml'):
        enc.append('no XML declaration: a Latin-1 reader will mojibake any '
                   'non-ASCII character')
    raw = sorted({c for c in s if ord(c) > 127})
    if raw:
        enc.append(f'{len(raw)} non-ASCII character(s) {"".join(raw)!r} left '
                   f'raw; write them as numeric references')

    if not faces:
        if enc:
            bad.append(p)
            print(f'{os.path.basename(p):46} BROKEN (escape hatch)')
            for x in enc:
                print(f'      {x}')
        else:
            print(f'{os.path.basename(p):46} -- no embedded fonts (escape hatch)')
        continue

    # rules: selector -> (family or None, weight or None)
    rules = {}
    for m in re.finditer(r'(?:^|\s)(text|\.[A-Za-z0-9_-]+)\s*\{([^}]*)\}', s):
        fam = re.search(r'font-family:\s*"([^"]+)"', m.group(2))
        wt = re.search(r'font-weight:\s*(\d+)', m.group(2))
        prev = rules.get(m.group(1), (None, None))
        rules[m.group(1)] = (fam.group(1) if fam else prev[0],
                             int(wt.group(1)) if wt else prev[1])
    base_fam, base_wt = rules.get('text', (None, None))

    probs, seen = list(enc), {}
    for m in re.finditer(r'<text([^>]*)>(.*?)</text>', s, re.S):
        attrs, body = m.group(1), m.group(2)
        cm = re.search(r'class="([A-Za-z0-9_ -]+)"', attrs)
        fam, wt = base_fam, base_wt
        for c in (cm.group(1).split() if cm else []):
            f2, w2 = rules.get('.' + c, (None, None))
            fam, wt = f2 or fam, w2 if w2 is not None else wt
        aw = re.search(r'font-weight="(\d+)"', attrs)
        if aw:
            wt = int(aw.group(1))
        chars = set(html.unescape(re.sub(r'<[^>]+>', '', body)))
        key = (fam, wt if wt is not None else 400)
        seen.setdefault(key, set()).update(chars)

    for (fam, wt), chars in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if fam not in faces:
            probs.append(f'family {fam!r} @ {wt} has no @font-face')
            continue
        cmap = match(faces[fam], wt)
        miss = sorted({c for c in chars if ord(c) not in cmap})
        if miss:
            probs.append(f'{fam} @ {wt} resolves to a subset missing '
                         f'{"".join(miss)!r}')

    if probs:
        bad.append(p)
        print(f'{os.path.basename(p):46} BROKEN')
        for x in probs:
            print(f'      {x}')
    else:
        print(f'{os.path.basename(p):46} ok  ({sum(len(v) for v in faces.values())} face(s))')

print(f'\naffected: {len(bad)}')
for b in bad:
    print(' ', b)
sys.exit(1 if bad else 0)

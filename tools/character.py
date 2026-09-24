#!/usr/bin/env python3
"""Describe what a typeface is LIKE, in Google Fonts' own words, and rank by it.

    python3 character.py vocab                       # the words there are
    python3 character.py blocks mid_century_modern_graphic gig   # cut out each line
    python3 character.py rank "Sans/Grotesque 90, Loud 80, Playful 5"   # try a brief

Measurement can say a face is condensed, heavy and upright. It cannot say it
is a no-nonsense 1950s grotesque rather than a comic-book face that happens to
share those proportions -- which is the difference between Anton and Bangers.
Saying so is one look for Claude, so Claude says it, as a brief:

    --character headliner="Sans/Grotesque 90, Loud 80, Vintage 60, Playful 5"

using the vocabulary Google Fonts tags its families with: a classification
(a broad class alone -- Sans, Serif, Slab, Script, Monospace -- matches any of
its sub-classes, and is the safer word unless the sub-class is plain)
(/Sans/Grotesque, /Serif/Didone, /Slab/Clarendon ...), a theme (/Theme/Art
Deco, /Theme/Pixel ...) and expressive scores 0-100 (/Expressive/Playful,
Loud, Vintage, Sophisticated ...). Tags may be shortened to any unambiguous
part ("Grotesque", "Playful"). A brief is keyed by a copy line (headliner,
date, title ...) or a face group (display, body ...) and applies to that
line's whole face group.

Scoring: for each tag in the brief, the gap between the brief's number and the
family's, over 100, averaged. A classification or theme the family is not
tagged with scores 0 -- it is not that -- unless the family carries no
classification at all (a fifth do not), when classification is skipped.
Almost every family has expressive scores, so a missing one scores 0.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalogue  # noqa: E402

CLASSES = ('Sans', 'Serif', 'Slab', 'Script', 'Monospace')


def vocabulary():
    tags = set()
    for r in catalogue.load():
        tags.update(r.get('tags', {}))
    return sorted(tags)


def parse(spec, vocab=None):
    """'Grotesque 90, Loud 80' -> {'/Sans/Grotesque': 90, '/Expressive/Loud': 80}."""
    vocab = vocab or vocabulary()
    # A broad class on its own -- "Sans 90" -- means any of its sub-classes.
    vocab = list(vocab) + [f'/{c}/*' for c in CLASSES]
    out = {}
    for part in re.split(r'[;,]', spec):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'^(.*?)\s*[:=]?\s*(\d+)$', part)
        name, val = (m.group(1), int(m.group(2))) if m else (part, 80)
        name = name.strip().strip('/').lower()
        exact = [t for t in vocab if t.lower().strip('/') == name
                 or t.lower() == f'/{name}/*']
        hits = exact or [t for t in vocab if t.lower().rstrip('/').endswith('/' + name)] \
            or [t for t in vocab if name in t.lower()]
        if len(hits) != 1:
            raise SystemExit(f'--character: {part!r} matches '
                             f'{", ".join(hits) if hits else "no tag"}; '
                             'see `character.py vocab`')
        out[hits[0]] = max(0, min(100, val))
    return out


def mismatch(brief, tags):
    """0 (the family is what the brief says) .. 1 (it is something else).

    Root-mean-square, not a mean: one decisive gap -- Bangers is 86 points
    more Playful than a brief that says 5 -- must not be averaged away by the
    tags it happens to share with the right face. A family that carries no
    classification counts as half wrong on the brief's classification, not
    as a free pass. And a theme the family has but the brief did not ask for
    counts against it: a plain-grotesque brief is not met by an inline, a
    stencil or a blackletter face, however grotesque its bones.
    """
    if not brief:
        return 0.0
    classified = any(t.split('/')[1] in CLASSES for t in tags)
    gaps = []
    for t, want in brief.items():
        kind = t.split('/')[1]
        if kind in CLASSES and not classified:
            gaps.append(0.5)
            continue
        if t.endswith('/*'):
            # any sub-class of the broad class
            have = max((v for k, v in tags.items() if k.startswith(t[:-1])), default=0)
        elif kind in CLASSES:
            # A sibling sub-class earns most of the credit: Google tags Anton
            # Neo Grotesque 70 and Grotesque 30, and a brief written from one
            # look cannot be held to that line.
            sib = max((v for k, v in tags.items()
                       if k.startswith(f'/{kind}/') and k != t), default=0)
            have = max(tags.get(t, 0), 0.7 * sib)
        else:
            have = tags.get(t, 0)
        gaps.append(abs(want - have) / 100)
    unwanted = [v for t, v in tags.items()
                if t.startswith('/Theme/') and t not in brief]
    if unwanted:
        gaps.append(max(unwanted) / 100)
    return (sum(g * g for g in gaps) / len(gaps)) ** 0.5


def blocks(style, event, width=900):
    """Cut every assigned copy line out of the poster, labelled with its key:
    the image a character brief is written from. Uses the stored knobs."""
    from PIL import Image, ImageDraw, ImageFont
    from overlay import current
    name = f'{style}-{event}-v2'
    src, L, A, _ = current(name)
    im = Image.open(src).convert('RGB')
    f = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 14) \
        if os.path.exists('/System/Library/Fonts/Helvetica.ttc') else ImageFont.load_default()
    tiles = []
    for a in A:
        bs = [L[i] for i in a['bands']]
        x0, x1 = min(b['left'] for b in bs), max(b['right'] for b in bs)
        y0, y1 = min(b['y0'] for b in bs), max(b['y1'] for b in bs)
        pad = int(0.3 * max(b['cap'] for b in bs))
        crop = im.crop((max(0, x0 - pad), max(0, y0 - pad),
                        min(im.width, x1 + pad), min(im.height, y1 + pad)))
        h = min(110, max(36, crop.height))
        s = h / crop.height
        crop = crop.resize((max(1, int(crop.width * s)), h))
        if crop.width > width:
            crop = crop.crop((0, 0, width, h))
        t = Image.new('RGB', (width, h + 18), 'white')
        t.paste(crop, (0, 0))
        ImageDraw.Draw(t).text((3, h + 1), a['key'], fill='#B00000', font=f)
        tiles.append(t)
    sheet = Image.new('RGB', (width, sum(t.height + 3 for t in tiles)), '#CCCCCC')
    y = 0
    for t in tiles:
        sheet.paste(t, (0, y))
        y += t.height + 3
    out = f'/tmp/p2svg-{style}-{event}/blocks.png'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('vocab')
    b = sub.add_parser('blocks')
    b.add_argument('style')
    b.add_argument('event', choices=['gig', 'fete'])
    r = sub.add_parser('rank')
    r.add_argument('brief')
    r.add_argument('--n', type=int, default=15)
    a = ap.parse_args()
    if a.cmd == 'vocab':
        groups = {}
        for t in vocabulary():
            groups.setdefault(t.split('/')[1], []).append(t.split('/', 2)[2])
        for g, ts in groups.items():
            print(f'{g}: {", ".join(ts)}')
    elif a.cmd == 'blocks':
        print(blocks(a.style, a.event))
    else:
        brief = parse(a.brief)
        print('brief:', brief)
        seen = {}
        for rec in catalogue.load():
            if rec['embed'] and rec['family'] not in seen:
                seen[rec['family']] = mismatch(brief, rec.get('tags', {}))
        for fam, m in sorted(seen.items(), key=lambda kv: kv[1])[:a.n]:
            print(f'  {m:.3f}  {fam}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Index every candidate face into fonts/index.json.

    python3 build_index.py            # incremental; only does what's missing
    python3 build_index.py --rebuild  # from scratch

Two jobs:

1. Flatten variable fonts to static .ttf files on disk (wght in steps of 100,
   wdth at min/mid/max, opsz at its extremes), so that at solve time every
   candidate is a plain file that loads in milliseconds. Instancing costs
   seconds per call and must not happen inside the search loop.

2. Precompute the metrics Stage 1 scores on -- cap ratio, x-height ratio,
   stem/cap, counter ratio, coverage -- so ranking hundreds of faces is a
   table scan rather than hundreds of font parses.

System fonts are indexed with `embed: false`. They are often what the image
generator imitated, so they are valuable for IDENTIFYING a face, but they are
licensed and must never be embedded in a published SVG.
"""
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Face, stem_ratio, counter_ratio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.normpath(os.path.join(HERE, '..', 'fonts'))
INSTANCES = os.path.join(FONTS, '_instances')
INDEX = os.path.join(FONTS, 'index.json')

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

# The sample a coverage test has to satisfy: everything the poster copy uses.
REQUIRED = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789 .,:;'\"!?&/-£–’")


def axis_samples(axis):
    lo, hi, tag = axis.minValue, axis.maxValue, axis.axisTag
    if tag == 'wght':
        vals = [v for v in range(100, 1001, 100) if lo <= v <= hi]
    elif tag == 'wdth':
        vals = sorted({lo, (lo + hi) / 2, hi})
    elif tag == 'opsz':
        vals = sorted({lo, hi})
    else:
        vals = [axis.defaultValue]
    return [v for v in vals if lo <= v <= hi] or [axis.defaultValue]


def flatten_variable(path):
    """Instance one variable font to static files. Returns the paths made."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    import itertools
    base = TTFont(path)
    if 'fvar' not in base:
        return []
    axes = base['fvar'].axes
    stem = os.path.splitext(os.path.basename(path))[0].replace('%5B', '[').replace('%5D', ']')
    stem = stem.split('[')[0]
    made = []
    combos = list(itertools.product(*[axis_samples(a) for a in axes]))
    for combo in combos:
        loc = {a.axisTag: v for a, v in zip(axes, combo)}
        tag = '-'.join(f'{k}{int(v)}' for k, v in sorted(loc.items()))
        out = os.path.join(INSTANCES, f'{stem}-{tag}.ttf')
        if os.path.exists(out):
            made.append(out)
            continue
        try:
            inst = instantiateVariableFont(TTFont(path), loc, inplace=False,
                                           updateFontNames=False)
            inst.save(out)
            made.append(out)
        except Exception as e:
            print(f'    instance {tag} failed: {e}', file=sys.stderr)
    return made


def describe(path, index=None, embed=True):
    f = Face(path, index=index)
    name = f.f['name']
    family = name.getDebugName(16) or name.getDebugName(1) or os.path.basename(path)
    sub = name.getDebugName(17) or name.getDebugName(2) or ''
    try:
        weight = int(f.f['OS/2'].usWeightClass)
    except Exception:
        weight = 400
    try:
        width_class = int(f.f['OS/2'].usWidthClass)
    except Exception:
        width_class = 5
    missing = f.missing(REQUIRED)
    if len(missing) > 6:            # not a usable text face for this copy
        return None
    xh = None
    if f.has('x'):
        xh = f.bounds('x')[3] / f.upem
    return {
        'path': path, 'index': index, 'embed': embed,
        'family': str(family), 'subfamily': str(sub),
        'weight': weight, 'width_class': width_class,
        'upem': f.upem,
        'cap_ratio': round(f.cap_ratio(), 5),
        'x_ratio': round(xh, 5) if xh else None,
        'stem_ratio': round(stem_ratio(f) or 0, 5) or None,
        'counter_ratio': round(counter_ratio(f) or 0, 5) or None,
        'asc_ratio': round(f.bounds('d')[3] / f.upem, 5) if f.has('d') else None,
        'missing': missing,
        'kern': 'GPOS' in f.f,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true')
    args = ap.parse_args()

    os.makedirs(INSTANCES, exist_ok=True)
    existing = {}
    if os.path.exists(INDEX) and not args.rebuild:
        existing = {(r['path'], r['index']): r for r in json.load(open(INDEX))['faces']}

    statics = sorted(glob.glob(os.path.join(FONTS, '*.ttf')) +
                     glob.glob(os.path.join(FONTS, '*.otf')))

    # 1. flatten variable fonts
    print('flattening variable fonts...')
    variable, flat = [], []
    for p in statics:
        try:
            from fontTools.ttLib import TTFont
            if 'fvar' in TTFont(p):
                variable.append(p)
                flat += flatten_variable(p)
        except Exception:
            pass
    print(f'  {len(variable)} variable -> {len(flat)} instances')

    pool = [(p, None, True) for p in statics if p not in variable]
    pool += [(p, None, True) for p in sorted(set(flat))]
    for path, idxs in SYSTEM:
        if os.path.exists(path):
            pool += [(path, i, False) for i in idxs]

    # 2. describe everything
    print(f'indexing {len(pool)} faces...')
    faces, skipped = [], 0
    for path, index, embed in pool:
        key = (path, index)
        if key in existing:
            faces.append(existing[key])
            continue
        try:
            row = describe(path, index, embed)
        except Exception as e:
            print(f'  skip {os.path.basename(path)}[{index}]: {e}', file=sys.stderr)
            row = None
        if row is None:
            skipped += 1
        else:
            faces.append(row)

    json.dump({'required': REQUIRED, 'faces': faces}, open(INDEX, 'w'), indent=1)
    embeddable = sum(1 for f in faces if f['embed'])
    print(f'\n{len(faces)} faces indexed ({embeddable} embeddable, '
          f'{len(faces) - embeddable} identification-only), {skipped} skipped')
    print(f'-> {INDEX}')


if __name__ == '__main__':
    main()

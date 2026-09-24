#!/usr/bin/env python3
"""Turn a poster bitmap into measured text lines, and attach the known copy.

Used by solve_type.py. Split out because band-finding and copy-assignment are
the parts most likely to need tuning per poster, and are worth testing alone:

    python3 measure.py poster.webp --event gig
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The measurement code lives in its own modules now; these names are kept here
# so older callers and notes that say `from measure import ...` still work.
from copytext import load_copy, _prompt_labels, _sentences  # noqa: E402,F401
from masks import ink_masks, strip_solids, strip_rules  # noqa: E402,F401
from analyse import tracking  # noqa: E402,F401
from bands import (find_lines, reading_order, dedupe, eff_width, strip_marker,  # noqa: E402,F401
                   looks_like_text, measure_stem, baseline_fit, background_texture,
                   knockout_boxes, in_excluded)
from align import assign_copy, copy_orderings  # noqa: E402,F401
from blank import text_ink_mask  # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--event', default='gig', choices=['gig', 'fete'])
    ap.add_argument('--solid-radius', type=int, default=24,
                    help='0 disables; raise if heavy display type is eaten')
    ap.add_argument('--rule-length', type=int, default=160,
                    help='0 disables rule stripping')
    ap.add_argument('--rule-thick', type=int, default=5)
    ap.add_argument('--min-glyphs', type=int, default=2)
    ap.add_argument('--contrast', type=int, default=40,
                    help='local contrast an ink pixel must clear; raise on noisy art')
    ap.add_argument('--window', type=int, default=61)
    ap.add_argument('--wrap-cap-ratio', type=float, default=1.3,
                    help='how different two bands may be in size and still be '
                         'one wrapped line')
    ap.add_argument('--exclude', action='append', default=[],
                    metavar='X0,Y0,X1,Y1',
                    help='ignore this box when looking for type; repeatable')
    a = ap.parse_args()
    m = find_lines(a.image, a.min_glyphs, a.solid_radius, a.rule_length,
                   a.rule_thick, a.contrast, a.window, a.wrap_cap_ratio,
                   [tuple(int(v) for v in x.split(',')) for x in a.exclude])
    copy = load_copy(a.event)
    assigned, note, cost = assign_copy(m['lines'], copy,
                                       wrap_cap_ratio=a.wrap_cap_ratio)
    print(f"{a.image}  {m['size'][0]}x{m['size'][1]}  paper {m['paper']}")
    print(f"{len(m['lines'])} text bands, {len(m['rejected'])} rejected, "
          f"{len(copy)} copy lines -> {note}")
    for x in assigned:
        b = [m['lines'][i] for i in x['bands']]
        print(f"  {x['key']:14} cap {b[0]['cap']:3} base {b[0]['baseline']:4} "
              f"x {b[0]['left']:4}..{b[-1]['right']:4} {b[0]['rgb']} "
              f"stem {b[0]['stem_ratio'] or 0:.3f}"
              f"{'  (+%d bands)' % (len(b) - 1) if len(b) > 1 else ''}  {x['text'][:44]}")
    unused = set(range(len(m['lines']))) - {i for x in assigned for i in x['bands']}
    for i in sorted(unused):
        l = m['lines'][i]
        print(f"  {'UNASSIGNED':14} cap {l['cap']:3} base {l['baseline']:4} "
              f"x {l['left']:4}..{l['right']:4} {l['rgb']}")



if __name__ == '__main__':
    main()

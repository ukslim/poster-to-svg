#!/usr/bin/env python3
"""Solve, build and verify one poster in a single command.

    python3 convert.py swiss_international gig
    python3 convert.py bauhaus_modernist gig --artwork mask
    python3 convert.py factory_records gig --rule-length 0 --face display=Cinzel

Everything is built in a work directory first. Only when the alignment is
sound and the SVG passes check_svg is it published to the site as
assets/poster-svg/{style}-{event}-v2.svg, with the solution kept in
assets/poster-svg/solutions/ so it can be rebuilt without re-deriving.

Re-running a poster starts from the knobs its stored solution was built with
(--exclude boxes, --face, --min-glyphs, ...); anything given on the command
line overrides them, and --fresh ignores them. So a re-run after a tool change
reproduces the last attempt instead of depending on anyone remembering it.

Prints what needs a decision rather than guessing. A weak alignment is not
built at all unless --force: copy on the wrong lines is never worth shipping.
"""
import argparse, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402

DEFAULTS = dict(artwork='mask', face=[], solid_radius=24, rule_length=160,
                rule_thick=5, min_glyphs=3, contrast=40, window=61,
                wrap_cap_ratio=1.3, exclude=[], knockout=[])
# Ambiguities that mean the copy is on the wrong lines. Building then only
# produces a confident-looking wrong poster.
BLOCKING = ('weak_alignment', 'no_alignment')


def knobs(a, stored):
    """Defaults, then the stored solution's knobs, then the command line."""
    k = dict(DEFAULTS)
    if stored and not a.fresh:
        k.update({n: v for n, v in stored.items() if n in DEFAULTS})
    for n in DEFAULTS:
        v = getattr(a, n)
        if v is not None and v != []:
            k[n] = v
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('style')
    ap.add_argument('event', choices=['gig', 'fete'])
    ap.add_argument('--artwork',
                    help='auto | mask[:pad] | crop:x0,y0,x1,y1 | shapes:... | none')
    ap.add_argument('--face', action='append', default=[])
    ap.add_argument('--solid-radius', type=int)
    ap.add_argument('--rule-length', type=int)
    ap.add_argument('--rule-thick', type=int)
    ap.add_argument('--min-glyphs', type=int)
    ap.add_argument('--contrast', type=int,
                    help='local contrast for ink; raise on photographic art')
    ap.add_argument('--window', type=int)
    ap.add_argument('--wrap-cap-ratio', type=float,
                    help='size tolerance for two bands being one wrapped line')
    ap.add_argument('--exclude', action='append', default=[],
                    metavar='X0,Y0,X1,Y1',
                    help='ignore this box when looking for type; repeatable. '
                         'Use it for a drawing whose fine detail behaves like '
                         'small glyphs')
    ap.add_argument('--knockout', action='append', default=[],
                    metavar='X0,Y0,X1,Y1',
                    help='a paper-coloured panel knocked out of a shape, which '
                         'masking cannot reconstruct; repaint it exactly')
    ap.add_argument('--keep', type=int, default=24)
    ap.add_argument('--fresh', action='store_true',
                    help='ignore the knobs stored with an earlier solution')
    ap.add_argument('--force', action='store_true',
                    help='build even when the alignment is weak')
    ap.add_argument('--resolve-only', action='store_true',
                    help='solve and report, but do not build')
    a = ap.parse_args()

    name = f'{a.style}-{a.event}-v2'
    src = os.path.join(poster_site.examples(), name + '.webp')
    if not os.path.exists(src):
        raise SystemExit(f'no such poster: {src}')
    site_svg = os.path.join(poster_site.svgs(), name + '.svg')
    site_soln = os.path.join(poster_site.solutions(), name + '.json')
    work = f'/tmp/p2svg-{a.style}-{a.event}'
    os.makedirs(work, exist_ok=True)
    soln, out = os.path.join(work, 'solution.json'), os.path.join(work, 'out.svg')

    stored = None
    if os.path.exists(site_soln):
        stored = json.load(open(site_soln)).get('knobs')
    k = knobs(a, stored)
    if stored and not a.fresh:
        changed = {n: v for n, v in k.items() if v != DEFAULTS[n]}
        print(f'knobs (stored solution + command line; --fresh to ignore): '
              f'{json.dumps(changed) if changed else "defaults"}')

    from solve_type import solve, summarise
    s = solve(src, a.event, a.keep, min_glyphs=k['min_glyphs'],
              solid_radius=k['solid_radius'], rule_length=k['rule_length'],
              rule_thick=k['rule_thick'], contrast=k['contrast'], window=k['window'],
              wrap_cap_ratio=k['wrap_cap_ratio'],
              exclude=[tuple(int(v) for v in x.split(',')) for x in k['exclude']])
    s['knobs'] = k
    summarise(s)
    json.dump(s, open(soln, 'w'), indent=1)
    if a.resolve_only:
        print(f'\nsolution -> {soln}  (not published: --resolve-only)')
        return

    blocking = [x for x in s['ambiguities'] if x['kind'] in BLOCKING]
    if blocking and not a.force:
        print(f'\nNOT BUILT: {blocking[0]["kind"]}. The copy is probably on the '
              f'wrong lines; check it with overlay.py and fix the measurement '
              f'(--exclude, --min-glyphs, ...), or --force to build anyway.')
        sys.exit(2)

    print()
    from build_svg import build_file
    build_file(s, out, k['face'], k['artwork'], work,
               title=f'{a.style.replace("_", " ")} {a.event} poster')

    from svgkit import render_svg
    render_svg(out, os.path.join(work, 'render.png'), *s['size'], workdir=work)
    # Read the finished SVG back and say what looks odd. Deliberately the last
    # word rather than a gate: everything it raises is suspicious rather than
    # certainly wrong, and it prints the measurement that would explain each
    # one away, so the reader can dismiss it without reopening the poster.
    from sense_check import check
    print()
    if not check(out, soln):
        print('sense check: nothing looked odd')

    # The one hard gate: a face that lacks a character the browser needs
    # renders half the word in a system font. Never publish that.
    from check_svg import check_file
    status, probs = check_file(out)
    if probs:
        print(f'\nNOT PUBLISHED: check_svg says {status}')
        for p in probs:
            print(f'    {p}')
        sys.exit(1)

    os.makedirs(poster_site.solutions(), exist_ok=True)
    shutil.copy(out, site_svg)
    s['image'] = src
    json.dump(s, open(site_soln, 'w'), indent=1)
    print(f'\n-> {site_svg}\n   solution: {site_soln}\n   render: {work}/render.png')


if __name__ == '__main__':
    main()

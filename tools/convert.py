#!/usr/bin/env python3
"""Solve, build and verify one poster in a single command.

    python3 convert.py swiss_international gig
    python3 convert.py bauhaus_modernist gig --artwork mask
    python3 convert.py factory_records gig --rule-length 0 --face display=Cinzel

Writes assets/poster-svg/{style}-{event}-v2.svg and keeps the solution in
assets/poster-svg/solutions/ so the poster can be rebuilt later without re-deriving anything.

Prints what needs a decision rather than guessing. If the solver is unsure
about a face, look at the poster and re-run with --face; if a measurement is
wrong, adjust a knob (--solid-radius, --rule-length, --min-glyphs) rather than
editing the tools. Every poster is allowed to be its own special case.
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('style')
    ap.add_argument('event', choices=['gig', 'fete'])
    ap.add_argument('--artwork', default='mask',
                    help='auto | mask[:pad] | crop:x0,y0,x1,y1 | shapes:file | none')
    ap.add_argument('--face', action='append', default=[])
    ap.add_argument('--solid-radius', type=int, default=24)
    ap.add_argument('--rule-length', type=int, default=160)
    ap.add_argument('--rule-thick', type=int, default=5)
    ap.add_argument('--min-glyphs', type=int, default=2)
    ap.add_argument('--contrast', type=int, default=40,
                    help='local contrast for ink; raise on photographic art')
    ap.add_argument('--window', type=int, default=61)
    ap.add_argument('--wrap-cap-ratio', type=float, default=1.3,
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
    ap.add_argument('--resolve-only', action='store_true',
                    help='solve and report, but do not build')
    a = ap.parse_args()

    src = os.path.join(poster_site.examples(), f'{a.style}-{a.event}-v2.webp')
    if not os.path.exists(src):
        raise SystemExit(f'no such poster: {src}')
    out = os.path.join(poster_site.svgs(), f'{a.style}-{a.event}-v2.svg')
    SOLUTIONS = poster_site.solutions()
    os.makedirs(SOLUTIONS, exist_ok=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    soln = os.path.join(SOLUTIONS, f'{a.style}-{a.event}-v2.json')
    work = f'/tmp/p2svg-{a.style}-{a.event}'
    os.makedirs(work, exist_ok=True)

    from solve_type import solve, summarise
    s = solve(src, a.event, a.keep, min_glyphs=a.min_glyphs,
              solid_radius=a.solid_radius, rule_length=a.rule_length,
              rule_thick=a.rule_thick, contrast=a.contrast, window=a.window,
              wrap_cap_ratio=a.wrap_cap_ratio,
              exclude=[tuple(int(v) for v in x.split(',')) for x in a.exclude])
    s['knobs'] = dict(solid_radius=a.solid_radius, rule_length=a.rule_length,
                      rule_thick=a.rule_thick, min_glyphs=a.min_glyphs,
                      contrast=a.contrast, window=a.window,
                      wrap_cap_ratio=a.wrap_cap_ratio, artwork=a.artwork,
                      face=a.face, exclude=a.exclude, knockout=a.knockout)
    summarise(s)
    json.dump(s, open(soln, 'w'), indent=1)
    print(f'\nsolution -> {soln}')
    if a.resolve_only:
        return

    cmd = [sys.executable, os.path.join(HERE, 'build_svg.py'), soln, '-o', out,
           '--artwork', a.artwork, '--workdir', work,
           '--title', f'{a.style.replace("_", " ")} {a.event} poster']
    for f in a.face:
        cmd += ['--face', f]
    print()
    if subprocess.run(cmd).returncode:
        raise SystemExit('build failed')

    # verify: render and compare, region by region
    from svgkit import render_svg, diff
    import shutil
    shutil.copy(out, os.path.join(work, 'out.svg'))
    render_svg(os.path.join(work, 'out.svg'), os.path.join(work, 'render.png'),
               *s['size'], workdir=work)
    h = s['size'][1]
    top = min((l['y0'] for l in s['lines']), default=h // 2)
    print('\nregion diff vs the original (registration check, not a score):')
    print(' ', diff(os.path.join(work, 'render.png'), src,
                    {'above the type': (0, max(1, top)),
                     'the type': (max(1, top), h)}))
    # Read the finished SVG back and say what looks odd. Deliberately the last
    # word rather than a gate: everything it raises is suspicious rather than
    # certainly wrong, and it prints the measurement that would explain each
    # one away, so the reader can dismiss it without reopening the poster.
    from sense_check import check
    print()
    if not check(out):
        print('sense check: nothing looked odd')

    print(f'\n-> {out}')
    print(f'   render: {work}/render.png')


if __name__ == '__main__':
    main()

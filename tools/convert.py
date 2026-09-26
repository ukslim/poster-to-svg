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
                wrap_cap_ratio=1.3, exclude=[], knockout=[], assign=[], character=[],
                case=[], tilted=[], art=[], align=[], track=[], fill=[], shadow=[],
                fit=[])
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
    # A knob given on the command line adds to the stored ones, replacing
    # only the entries for the lines (or boxes) it names. Replacing the whole
    # list silently dropped the rest: --assign for one line sent every other
    # line back to the aligner (screenprint_pop), and --art for the presenter
    # un-kept a headline, which was then set tiny over its own pixels
    # (y2k_chrome). --fresh still drops everything stored.
    if stored and not a.fresh:
        for n, key in MERGE.items():
            given = getattr(a, n)
            if not given:
                continue
            said = {key(x) for x in given}
            k[n] = [x for x in stored.get(n) or [] if key(x) not in said] + list(given)
    return k


def _line(x):
    return x.partition('=')[0].strip()


def _lines_at(x):
    """'date,venue@X0,Y0,X1,Y1[@...]' -> the lines, as one key."""
    return frozenset(k.strip() for k in x.partition('@')[0].split(','))


# How each list knob's entries are told apart when merging (see knobs()).
MERGE = dict(
    assign=_line, face=_line, character=_line, case=_line, align=_line,
    track=_line, shadow=_line, fit=lambda x: x.strip(),
    # a line's own colour, and each run of words in it, are separate entries
    fill=lambda x: (_line(x), x.partition('=')[2].partition('@')[2]),
    art=_lines_at, tilted=_lines_at,
    exclude=lambda x: x, knockout=lambda x: x)


def face_by_name(spec, s):
    """'display=#2' -> 'display=Anton 400', naming the candidate it meant."""
    key, _, name = spec.partition('=')
    if not name.startswith('#'):
        return spec
    g = s['groups'].get(key) or next(
        (v for v in s['groups'].values() if key in v['lines']), None)
    if g is None or not name[1:].isdigit() or int(name[1:]) > len(g['candidates']):
        return spec
    c = g['candidates'][int(name[1:]) - 1]
    return f"{key}={c['family']} {c['sub']}"


def composite(s, work, width=420):
    """One image for the one look a build needs: the poster with every band
    numbered and labelled with its copy line, the rebuilt SVG beside it, and
    the face contact sheet below. -> path."""
    from PIL import Image
    from overlay import panel
    from sheet import group_sheet
    ov = panel(s['image'], s['lines'], s['assigned'], width, 'original, as assigned')
    rd = Image.open(os.path.join(work, 'render.png')).convert('RGB')
    rd = rd.resize((width, int(rd.height * width / rd.width)))
    top = Image.new('RGB', (2 * width + 8, max(ov.height, rd.height)), 'white')
    top.paste(ov, (0, 0))
    top.paste(rd, (width + 8, 0))
    parts = [top]
    try:
        # contact sheets, two to a row at half width: the top three faces of
        # each group beside the original line
        sheets = [sh for sh in (group_sheet(s, g, n=3) for g in s['groups']) if sh is not None]
        half = width + 4
        sheets = [sh.resize((half, int(sh.height * half / sh.width))) for sh in sheets]
        for i in range(0, len(sheets), 2):
            row = sheets[i:i + 2]
            r = Image.new('RGB', (2 * width + 8, max(x.height for x in row)), 'white')
            for j, x in enumerate(row):
                r.paste(x, (j * half, 0))
            parts.append(r)
    except Exception:
        pass
    img = Image.new('RGB', (2 * width + 8, sum(p.height + 6 for p in parts)), '#DDDDDD')
    y = 0
    for p in parts:
        img.paste(p, (0, y))
        y += p.height + 6
    path = os.path.join(work, 'look.png')
    img.save(path)
    return path


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
    ap.add_argument('--assign', action='append', default=[], metavar='KEY=#N[+#M]',
                    help='put copy line KEY on band #N (numbered by overlay.py), '
                         'or KEY=none; overrides the aligner; repeatable')
    ap.add_argument('--character', action='append', default=[], metavar='KEY=BRIEF',
                    help='what a line\'s face is like, in Google Fonts tags: '
                         'headliner="Grotesque 90, Loud 80, Playful 5" '
                         '(see character.py vocab); KEY is a copy line or face group')
    ap.add_argument('--case', action='append', default=[], metavar='KEY=upper|mixed',
                    help='say whether a copy line is set in capitals, where the '
                         'lettering defeats the automatic test; repeatable')
    ap.add_argument('--tilted', action='append', default=[], metavar='KEYS@X0,Y0,X1,Y1[@ANGLE]',
                    help='copy lines set on a slant, the box they sit in, and the slope by eye '
                         '(degrees rising to the right): date,venue@120,900,980,1200@8')
    ap.add_argument('--art', action='append', default=[], metavar='KEYS@X0,Y0,X1,Y1[@WHY]',
                    help='lettering that cannot be reset (drawn, painted, 3D, on a photo): '
                         'those copy lines stay the original pixels, and WHY says what makes '
                         'it so, e.g. "headliner@0,80,1024,560@hand-painted brush lettering"')
    ap.add_argument('--align', action='append', default=[], metavar='KEY=left|centre|right',
                    help='the edge a copy line is ranged on, where the block detection '
                         'got it wrong (a centred headline built off centre)')
    ap.add_argument('--track', action='append', default=[], metavar='KEY=fit|EM',
                    help='letter-spacing the measurement missed: fit spans the '
                         "original's width, or an amount in em, e.g. headliner=0.1")
    ap.add_argument('--fill', action='append', default=[], metavar='KEY=#RRGGBB',
                    help="a copy line's colour, where the measured ink is wrong "
                         '(colour fringes, a glow, a texture)')
    ap.add_argument('--shadow', action='append', default=[], metavar='KEY=DX,DY,#RGB',
                    help='a hard drop shadow behind a line, in px: headliner=6,6,#333333')
    ap.add_argument('--fit', action='append', default=[], metavar='KEY',
                    help="set this line no wider than the original's, where it must stay "
                         'inside a frame and no narrower cut of its face exists')
    ap.add_argument('--keep', type=int, default=80)
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
              wrap_cap_ratio=k['wrap_cap_ratio'], assign=k['assign'],
              character=dict(c.split('=', 1) for c in k['character']),
              case=dict(c.split('=', 1) for c in k['case']), tilted=k['tilted'], art=k['art'],
              exclude=[tuple(int(v) for v in x.split(',')) for x in k['exclude']])
    # Stored as points, not band numbers: re-measurement may renumber bands.
    k['assign'] = s['assign_points']
    # And a face chosen as #N by name: a re-solve may reorder the candidates.
    k['face'] = [face_by_name(f, s) for f in k['face']]
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
    # What is known about the conversion goes into the SVG itself (meta.py),
    # for the manifest and the site's index page.
    import meta
    svg = meta.stamp(open(out).read(), s)
    open(out, 'w').write(svg)

    from svgkit import render_svg
    render_svg(out, os.path.join(work, 'render.png'), *s['size'], workdir=work)
    look = composite(s, work)
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
    print(f'\n-> {site_svg}\n   solution: {site_soln}\n   look at: {look}')


if __name__ == '__main__':
    main()

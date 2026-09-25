#!/usr/bin/env python3
"""What is known about a conversion, written into the SVG itself.

    python3 meta.py assets/poster-svg/pop_art-fete-v2.svg       # print it
    python3 meta.py assets/poster-svg/*.svg --refresh           # rewrite it
    python3 meta.py assets/poster-svg/*.svg --stale             # not built by this version
    python3 meta.py assets/poster-svg/X.svg --reviewed          # looked at, and fixed

Every SVG the tools write carries one <metadata id="p2svg-meta"> block holding
a JSON record, so the file says for itself how it was made and what is wrong
with it, and `manifest.py` collects the records for the site's index page:

    status          converted | skipped (the escape hatch)
    faults          null, or the flawed mark's sentence (flag_flawed.py)
    fault_verdict   fixable | dead-end, with fault_outlook, when marked
    skip_reason     why the escape hatch was taken
    text_as_bitmap  lettering kept as the original's pixels (--art):
                    [{lines, text, box, why}]; why is null where unrecorded
    not_reset       copy lines neither reset nor kept as art: [{line, text}].
                    Either the poster never printed them, or their pixels are
                    still in the artwork -- look before calling it a fault
    tilted          lines set on a slant: [{lines, angle}]
    faces           [{family, style, weight, text}]: each embedded font and
                    the lines set in it, so it names what actually ships
    artwork         the treatment: mask | shapes | crop | none
    raster_bytes    bytes of embedded bitmap (0: all vector)
    version         the poster-to-svg that last built it: {commit, date (the
                    commit's), tag: "dirty" when built with uncommitted changes}
    built           the date of that build
    reviewed        the date someone looked at this build's render and fixed
                    what they could (SKILL.md step 6), or null: a build clears
                    it, so a batch rebuild shows up as unreviewed

Derived from the finished SVG and its stored solution, never supplied by hand
(except `why`, which comes from the --art knob), so it cannot drift: every
tool that changes an SVG rewrites the block. `--refresh` rewrites it for SVGs
made before the block existed; they keep version/built as null.
"""
import argparse, base64, datetime, html, io, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ID = 'p2svg-meta'
BLOCK = re.compile(r'[ \t]*<metadata id="' + ID + r'">.*?</metadata>\n?', re.S)


def read(svg):
    """The record in an SVG, or None."""
    m = re.search(r'<metadata id="' + ID + r'"><!\[CDATA\[(.*?)\]\]></metadata>', svg, re.S)
    return json.loads(m.group(1)) if m else None


def write(svg, record):
    """The SVG with `record` as its metadata block, replacing any old one.
    Placed after <desc> (or <title>), where a reader looks for it."""
    # '<' and '>' escaped so nothing that scans the SVG for tags finds any
    # inside the JSON; ASCII only, as everywhere else in these files.
    body = json.dumps(record, indent=1, ensure_ascii=True)
    body = body.replace('<', '\\u003c').replace('>', '\\u003e')
    block = f'  <metadata id="{ID}"><![CDATA[\n{body}\n  ]]></metadata>\n'
    svg = BLOCK.sub('', svg)
    for tag in ('</desc>', '</title>'):
        i = svg.find(tag)
        if i >= 0:
            j = svg.index('\n', i) + 1
            return svg[:j] + block + svg[j:]
    i = svg.index('>', svg.index('<svg')) + 1
    return svg[:i] + '\n' + block + svg[i:]


def tool_version():
    """{commit, date, tag} for the poster-to-svg checkout these tools run
    from: the short hash, its commit date, and tag "dirty" when the working
    tree has uncommitted changes (so the hash alone does not say what ran)."""
    try:
        run = lambda *a: subprocess.run(['git', '-C', HERE, *a], capture_output=True,
                                        text=True, check=True).stdout.strip()
        commit, date = run('log', '-1', '--format=%h %cs').split()
        return dict(commit=commit, date=date,
                    tag='dirty' if run('status', '--porcelain') else None)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def current(rec):
    """Was this record built by the checkout that is running now, clean?"""
    v, now = (rec or {}).get('version'), tool_version()
    return bool(v and now and v['commit'] == now['commit'] and not v.get('tag')
                and not now.get('tag'))


def _squeeze(t):
    return re.sub(r'[^A-Za-z0-9]', '', t)


def _known(sol):
    """[(squeezed name, family, style)] for every face the build could have
    used: the solution's candidates, then the families its --face knobs name.
    The builder names each @font-face as family + style squeezed together."""
    out = [(_squeeze(c['family'] + c['sub']), c['family'], c['sub'])
           for g in ((sol or {}).get('groups') or {}).values()
           for c in g.get('candidates') or []]
    for f in ((sol or {}).get('knobs') or {}).get('face') or []:
        fam = f.split('=', 1)[-1].split(':')[0].strip()
        if not fam.startswith('#'):
            out.append((_squeeze(fam), fam, None))
    return out


def _faces(svg, sol=None):
    """[{family, style, weight, text}] for each embedded face, with the lines
    the SVG sets in it. Named from the solution where it can be; failing that
    (a hand-built SVG) from the font's own name table."""
    from fontTools.ttLib import TTFont
    known = _known(sol)
    out = []
    for m in re.finditer(r'@font-face\s*\{\s*font-family:\s*"([^"]+)";\s*src:\s*url\(data:[^;]+;'
                         r'base64,([A-Za-z0-9+/=]+)\)[^}]*?font-weight:\s*(\d+)', svg):
        css, b64, weight = m.groups()
        # exact first; then a family named by a --face knob, whose style is
        # whatever the builder appended; a trailing digit may be the
        # builder's own suffix for a second subset of the same face
        hit = (next(((f, st) for sq, f, st in known if st is not None and sq == css), None)
               or next(((f, st) for sq, f, st in known if st is not None
                        and css.startswith(sq) and css[len(sq):].isdigit()), None)
               or next(((f, css[len(sq):] or None) for sq, f, st in known
                        if st is None and css.startswith(sq)), None))
        if hit is None:
            try:
                names = TTFont(io.BytesIO(base64.b64decode(b64)))['name']
                get = lambda *ids: next((str(names.getDebugName(i)) for i in ids
                                         if names.getDebugName(i)), None)
                hit = (get(16, 1) or css, get(17, 2))
            except Exception:
                hit = (css, None)
        texts = [html.unescape(re.sub(r'<[^>]+>', '', t)) for t in re.findall(
            r'<text class="' + re.escape(css.lower()) + r'"[^>]*>(.*?)</text>', svg, re.S)]
        out.append(dict(family=hit[0], style=hit[1], weight=int(weight), text=texts))
    return out


def _copy(sol):
    if sol.get('copy'):
        return {c['key']: c['text'] for c in sol['copy']}
    try:
        from copytext import load_copy
        return {c['key']: c['text'] for c in load_copy(sol['event'])}
    except Exception:
        return {}


def _art(sol):
    """[(keys, box, why)] from the solution, or from its --art knobs."""
    if sol.get('art') is not None:
        return [(a['keys'], a['box'], a.get('why')) for a in sol['art']]
    from solve_type import parse_art
    return list(parse_art(x) for x in (sol.get('knobs') or {}).get('art') or [])


def derive(svg, sol=None, old=None):
    """The record for a finished SVG. `sol` is its solution (None for a
    hand-built SVG); `old` a previous record, whose build stamp is kept."""
    from flag_flawed import reason, outlook
    old = old or {}
    if 'COULD NOT CONVERT' in svg:
        m = re.search(r'aria-label="Not converted to SVG\. (.*?)"', svg, re.S)
        return dict(status='skipped', faults=None, fault_verdict=None, fault_outlook=None,
                    skip_reason=html.unescape(m.group(1)).strip() if m else None,
                    version=old.get('version'), built=old.get('built'),
                    reviewed=old.get('reviewed'))
    verdict, note = outlook(svg)
    rec = dict(status='converted', faults=reason(svg), fault_verdict=verdict,
               fault_outlook=note, skip_reason=None,
               text_as_bitmap=[], not_reset=[], tilted=[], faces=_faces(svg, sol),
               artwork=None, raster_bytes=sum(
                   len(base64.b64decode(b)) for b in
                   re.findall(r'data:image/[a-z+]+;base64,([A-Za-z0-9+/=]+)', svg)),
               version=old.get('version'), built=old.get('built'),
                    reviewed=old.get('reviewed'))
    if sol:
        copy = _copy(sol)
        art = _art(sol)
        rec['text_as_bitmap'] = [
            dict(lines=keys, text=' / '.join(copy.get(k, '') for k in keys),
                 box=list(box), why=why) for keys, box, why in art]
        done = {a['key'] for a in sol.get('assigned') or []} | {k for keys, _, _ in art
                                                                  for k in keys}
        rec['not_reset'] = [dict(line=k, text=t) for k, t in copy.items() if k not in done]
        rec['tilted'] = [dict(lines=f['keys'], angle=round(f['angle'], 1))
                         for f in sol.get('frames') or []]
        spec = (sol.get('knobs') or {}).get('artwork')
        rec['artwork'] = spec.split(':', 1)[0] if spec else None
    return rec


def stamp(svg, sol=None):
    """The SVG with a fresh record, stamped as built now by this tool."""
    rec = derive(svg, sol)
    rec['version'], rec['built'] = tool_version(), datetime.date.today().isoformat()
    rec['reviewed'] = None
    return write(svg, rec)


def reviewed(svg, sol=None):
    """The SVG with its record re-derived and marked as looked at today."""
    rec = derive(svg, sol, read(svg))
    rec['reviewed'] = datetime.date.today().isoformat()
    return write(svg, rec)


def refresh(svg, sol=None):
    """The SVG with its record re-derived, keeping the old build stamp."""
    return write(svg, derive(svg, sol, read(svg)))


def solution_for(svg_path):
    """The stored solution beside a site SVG, or None."""
    p = os.path.join(os.path.dirname(os.path.abspath(svg_path)), 'solutions',
                     os.path.basename(svg_path)[:-4] + '.json')
    return json.load(open(p)) if os.path.exists(p) else None


def refresh_file(path):
    s = open(path, encoding='utf-8').read()
    new = refresh(s, solution_for(path))
    if new != s:
        open(path, 'w', encoding='utf-8').write(new)
    return new != s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('svg', nargs='+')
    ap.add_argument('--refresh', action='store_true',
                    help='re-derive the record from the SVG and its solution')
    ap.add_argument('--stale', action='store_true',
                    help='list the SVGs not built by this checkout, clean')
    ap.add_argument('--reviewed', action='store_true',
                    help="record that this build's render was looked at and fixed (step 6)")
    a = ap.parse_args()
    for p in a.svg:
        if a.stale:
            if not current(read(open(p, encoding='utf-8').read())):
                print(p)
        elif a.reviewed:
            s, sol = open(p, encoding='utf-8').read(), solution_for(p)
            # The review is where faces are chosen: one the ranking picked and
            # nobody confirmed has not been reviewed, however the render looks.
            from review import unchosen
            left = unchosen(sol) if sol else []
            if left:
                raise SystemExit(
                    f"{p}: not reviewed -- no face chosen for {', '.join(left)}. "
                    f"Look at review.py, then --face GROUP=\"Family sub\" for each, "
                    f"even to confirm the ranking's first")
            open(p, 'w', encoding='utf-8').write(reviewed(s, sol))
            print(f'{p}: reviewed')
        elif a.refresh:
            print(f"{p}: {'refreshed' if refresh_file(p) else 'unchanged'}")
        else:
            print(json.dumps(read(open(p, encoding='utf-8').read()), indent=1))


if __name__ == '__main__':
    main()

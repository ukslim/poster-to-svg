#!/usr/bin/env python3
"""Mark a conversion that is known to be flawed, or clear the mark.

    python3 flag_flawed.py assets/poster-svg/act_up_aids-gig-v2.svg \
        --flaw "Wedges of the triangle show through CARVER." \
        --outlook "Fixable: blanking misses ink the reset glyphs do not cover." \
        --verdict fixable
    python3 flag_flawed.py assets/poster-svg/act_up_aids-gig-v2.svg --clear
    python3 flag_flawed.py assets/poster-svg/*.svg --refresh   # redraw marks

A poster ends up in one of three states. No SVG, or the escape hatch: given up
on early. Marked flawed: converted, examined, and still wrong after three
attempts at fixing it. Unmarked: a success.

The mark is a small alert icon in the top-left corner. Hovering it opens a
panel saying "I know this conversion is flawed", one sentence on the flaw, and
one on the outlook -- a dead end, or a bug or feature that would fix it. The
hover is pure CSS, so it works where the SVG is opened directly or inlined in
a page; through an <img> tag a browser shows the SVG as a static picture and
only the icon appears, which is still an unmistakable flag.

Everything is recorded in the file's own attributes (data-flaw, data-outlook,
data-verdict), so `manifest.py --flawed` lists them without a second record
that could drift; the SVG's metadata block (meta.py) is re-derived from them
whenever the mark changes. The block is delimited and painted last; the
checkers strip it before reading the poster (`strip_label`), because its text
is set in a system font on purpose and is not part of the conversion.
"""
import argparse, html, os, re, sys, textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meta  # noqa: E402

START, END = '<!-- flawed-label -->', '<!-- /flawed-label -->'
TEXT = 'I know this conversion is flawed'
VERDICTS = ('fixable', 'dead-end')

# Panel geometry, in poster pixels (the posters are 1024 wide).
PAD, ICON, WRAP, LINE = 16, 44, 54, 27


def esc(t):
    for a, b in (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('"', '&quot;')):
        t = t.replace(a, b)
    return ''.join(c if ord(c) < 128 else f'&#{ord(c)};' for c in t)


def strip_label(svg):
    """The SVG without its flawed mark, if it has one."""
    return re.sub(re.escape(START) + r'.*?' + re.escape(END) + r'\n?', '', svg,
                  flags=re.S)


def _attr(svg, name):
    m = re.search(rf'{name}="([^"]*)"', svg)
    return html.unescape(m.group(1)) if m else ''


def reason(svg):
    """The recorded flaw if the SVG is marked flawed, else None."""
    if START not in svg:
        return None
    return _attr(svg, 'data-flaw') or _attr(svg, 'data-reason')


def outlook(svg):
    """(verdict, outlook sentence) for a marked SVG, else (None, None)."""
    if START not in svg:
        return None, None
    return _attr(svg, 'data-verdict') or None, _attr(svg, 'data-outlook') or None


def label(svg, flaw, outlook_text, verdict):
    lines = [(TEXT, 'head')]
    lines += [(t, 'body') for t in textwrap.wrap(flaw, WRAP, break_on_hyphens=False)]
    lines += [('', 'gap')]
    lines += [(t, 'note') for t in textwrap.wrap(outlook_text, WRAP, break_on_hyphens=False)]
    x = PAD + ICON + 14
    y, rows = PAD + 30, []
    for t, cls in lines:
        if cls == 'gap':
            y += 8
            continue
        rows.append(f'      <text class="{cls}" x="{x}" y="{y}">{esc(t)}</text>')
        y += LINE
    width = x - PAD + WRAP * 9.4 + 24
    height = y - PAD - LINE + 20
    cx = cy = PAD + ICON / 2
    block = f'''{START}
  <g id="flawed-label" class="p2svg-flawed" data-flaw="{esc(flaw)}"
     data-outlook="{esc(outlook_text)}" data-verdict="{verdict}">
    <title>{esc(TEXT)}. {esc(flaw)} {esc(outlook_text)}</title>
    <style type="text/css"><![CDATA[
      .p2svg-flawed text {{ font-family: ui-sans-serif, system-ui, -apple-system,
          "Helvetica Neue", Helvetica, Arial, sans-serif; fill: #111; white-space: pre; }}
      .p2svg-flawed .head {{ font-size: 21px; font-weight: 700; }}
      .p2svg-flawed .body {{ font-size: 19px; }}
      .p2svg-flawed .note {{ font-size: 19px; font-style: italic; fill: #444; }}
      .p2svg-flawed .panel {{ opacity: 0; visibility: hidden;
          transition: opacity .15s ease, visibility .15s; }}
      .p2svg-flawed:hover .panel {{ opacity: 1; visibility: visible; }}
      .p2svg-flawed .icon {{ cursor: help; }}
    ]]></style>
    <g class="panel">
      <rect x="{PAD}" y="{PAD}" width="{width}" height="{height}" rx="10"
            fill="#FFF8D6" stroke="#111" stroke-width="2"/>
{chr(10).join(rows)}
    </g>
    <g class="icon">
      <circle cx="{cx:g}" cy="{cy:g}" r="{ICON / 2:g}" fill="#FFD400" stroke="#111" stroke-width="3"/>
      <rect x="{cx - 3:g}" y="{cy - 13:g}" width="6" height="17" rx="3" fill="#111"/>
      <circle cx="{cx:g}" cy="{cy + 10:g}" r="3.5" fill="#111"/>
    </g>
  </g>
{END}
'''
    svg = strip_label(svg)
    i = svg.rstrip().rfind('</svg>')
    return svg[:i] + block + svg[i:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('svg', nargs='+')
    ap.add_argument('--flaw', help='one sentence: what is still wrong')
    ap.add_argument('--outlook', help='one sentence: a dead end, or what would fix it')
    ap.add_argument('--verdict', choices=VERDICTS)
    ap.add_argument('--clear', action='store_true')
    ap.add_argument('--refresh', action='store_true',
                    help='redraw existing marks from their stored text, e.g. '
                         'after changing the design here')
    a = ap.parse_args()
    if not (a.clear or a.refresh) and not (a.flaw and a.outlook and a.verdict):
        ap.error('--flaw, --outlook and --verdict are required '
                 '(or --clear / --refresh)')
    for p in a.svg:
        s = open(p, encoding='utf-8').read()
        if 'COULD NOT CONVERT' in s:
            if not a.refresh:
                print(f'{p}: escape-hatch placeholder, not marking')
            continue
        if a.refresh:
            if reason(s) is None:
                continue
            verdict, note = outlook(s)
            s = label(s, reason(s), note or '', verdict or 'fixable')
            open(p, 'w', encoding='utf-8').write(meta.refresh(s, meta.solution_for(p)))
            print(f'{p}: mark redrawn')
            continue
        s = strip_label(s) if a.clear else label(s, a.flaw, a.outlook, a.verdict)
        open(p, 'w', encoding='utf-8').write(meta.refresh(s, meta.solution_for(p)))
        print(f"{p}: {'mark cleared' if a.clear else 'marked flawed'}")


if __name__ == '__main__':
    main()

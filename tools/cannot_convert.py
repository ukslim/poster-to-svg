#!/usr/bin/env python3
"""Write the "not converted" placeholder SVG.

    python3 cannot_convert.py OUT.svg \
        --style "Grunge Typography" --event gig \
        --reason "The type is hand-torn and overprinted onto the texture; there is no font-like lettering to reset and no way to separate the words from the background."

Deliberately plain: it must not read as a poster. No embedded fonts, so it
stays under 2KB and renders anywhere.
"""
import argparse, os, sys, textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meta  # noqa: E402

W, H = 1024, 1536
BG, FG, MUTED, RULE = '#EFEEEC', '#2B2B2B', '#6B6B6B', '#C9C7C3'


def esc(t):
    """XML-escape, and write anything above ASCII as a numeric reference, so a
    reader that guesses Latin-1 (macOS QuickLook) cannot mojibake an em dash."""
    for a, b in (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;')):
        t = t.replace(a, b)
    return ''.join(c if ord(c) < 128 else f'&#{ord(c)};' for c in t)


def build(style, event, reason, width=W, height=H):
    wrapped = textwrap.wrap(reason, 52) or ['(no reason given)']
    lines = '\n'.join(
        f'    <tspan x="112" dy="{0 if i == 0 else 40}">{esc(l)}</tspan>'
        for i, l in enumerate(wrapped))
    subject = esc(f'{style} — {event} poster') if style else esc(f'{event} poster')
    cy = height // 2
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img"
     aria-label="Not converted to SVG. {esc(reason)}">
  <title>Not converted to SVG &#8212; {subject}</title>
  <style type="text/css"><![CDATA[
    text {{ font-family: ui-sans-serif, system-ui, -apple-system, "Helvetica Neue",
            Arial, sans-serif; fill: {FG}; }}
    .label  {{ font-size: 22px; letter-spacing: 3px; fill: {MUTED}; }}
    .head   {{ font-size: 54px; font-weight: 700; }}
    .subject{{ font-size: 28px; fill: {MUTED}; }}
    .reason {{ font-size: 28px; line-height: 40px; }}
  ]]></style>

  <rect width="{width}" height="{height}" fill="{BG}"/>
  <rect x="56" y="56" width="{width - 112}" height="{height - 112}"
        fill="none" stroke="{RULE}" stroke-width="2" stroke-dasharray="10 8"/>

  <text class="label"   x="112" y="{cy - 180}">COULD NOT CONVERT</text>
  <line x1="112" y1="{cy - 150}" x2="{width - 112}" y2="{cy - 150}"
        stroke="{RULE}" stroke-width="2"/>
  <text class="head"    x="112" y="{cy - 86}">No SVG version</text>
  <text class="subject" x="112" y="{cy - 40}">{subject}</text>
  <text class="reason"  x="112" y="{cy + 30}">
{lines}
  </text>
</svg>
'''


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('out')
    p.add_argument('--style', default='')
    p.add_argument('--event', default='')
    p.add_argument('--reason', required=True)
    p.add_argument('--width', type=int, default=W)
    p.add_argument('--height', type=int, default=H)
    a = p.parse_args()
    svg = meta.stamp(build(a.style, a.event, a.reason, a.width, a.height))
    open(a.out, 'w').write(svg)
    print(f'wrote {a.out} ({len(svg)} bytes)')

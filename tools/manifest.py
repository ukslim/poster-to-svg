#!/usr/bin/env python3
"""What is converted, what is skipped, what is left -- across all 200 posters.

    python3 manifest.py            # summary
    python3 manifest.py --todo     # just the outstanding ones
    python3 manifest.py --flawed   # converted, but labelled as known to be flawed

A converted SVG with no solution beside it is 'hand-built': it came from an
exploratory session, not from the tool.
    python3 manifest.py --json     # write manifest.json

Derived by scanning the directories, so it cannot drift from reality.
"""
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402
from flag_flawed import reason as flawed_reason, outlook as flawed_outlook  # noqa: E402


def scan():
    rows = []
    REPO, SVGS = poster_site.root(), poster_site.svgs()
    for src in sorted(glob.glob(os.path.join(poster_site.examples(), '*-v2.webp'))):
        base = os.path.basename(src)[:-len('.webp')]
        style, event = base.rsplit('-', 2)[0], base.rsplit('-', 2)[1]
        svg = os.path.join(SVGS, base + '.svg')
        row = dict(style=style, event=event, status='outstanding',
                   svg=None, bytes=None, faces=[], reason=None)
        if os.path.exists(svg):
            text = open(svg, encoding='utf-8').read()
            row['svg'] = os.path.relpath(svg, REPO)
            row['bytes'] = os.path.getsize(svg)
            if 'COULD NOT CONVERT' in text:
                row['status'] = 'skipped'
                m = re.search(r'aria-label="Not converted to SVG\. (.*?)"', text, re.S)
                row['reason'] = m.group(1).strip() if m else None
            else:
                # Converted, examined, and still wrong after three attempts.
                why = flawed_reason(text)
                row['status'] = 'done' if why is None else 'flawed'
                # No solution beside it: built by hand in an exploratory
                # session, not by the tool, so the tool cannot rebuild it and
                # regress.py and audit.py do not cover it.
                if not os.path.exists(os.path.join(poster_site.solutions(), base + '.json')):
                    row['status'] = 'hand-built'
                row['reason'] = why
                if why is not None:
                    row['verdict'], row['outlook'] = flawed_outlook(text)
                row['faces'] = sorted(set(re.findall(r'font-family:\s*"([^"]+)"', text)))
        soln = os.path.join(poster_site.solutions(), base + '.json')
        if os.path.exists(soln):
            try:
                s = json.load(open(soln))
                row['faces'] = row['faces'] or [
                    c['candidates'][0]['family'] for c in s['groups'].values()
                    if c.get('candidates')]
                row['knobs'] = s.get('knobs')
            except Exception:
                pass
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--todo', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--flawed', action='store_true')
    a = ap.parse_args()
    rows = scan()

    if a.json:
        out = os.path.join(poster_site.svgs(), 'manifest.json')
        json.dump(rows, open(out, 'w'), indent=1)
        print(f'-> {out}')

    done = [r for r in rows if r['status'] == 'done']
    skipped = [r for r in rows if r['status'] == 'skipped']
    flawed = [r for r in rows if r['status'] == 'flawed']
    todo = [r for r in rows if r['status'] == 'outstanding']
    hand = [r for r in rows if r['status'] == 'hand-built']

    if a.todo:
        for r in todo:
            print(f"{r['style']} {r['event']}")
        return
    if a.flawed:
        for r in flawed:
            print(f"{r['style']} {r['event']} [{r.get('verdict') or '?'}]: {r['reason']}")
            if r.get('outlook'):
                print(f"    {r['outlook']}")
        return

    print(f'{len(rows)} posters: {len(done)} done, {len(hand)} hand-built, '
          f'{len(flawed)} flawed, {len(skipped)} skipped, {len(todo)} outstanding')
    if done:
        print('\ndone:')
        for r in done:
            print(f"  {r['style']:34} {r['event']:5} {r['bytes']/1024:6.0f}KB  "
                  f"{', '.join(r['faces'][:3])}")
    if hand:
        print('\nhand-built (no solution; the tool has never converted these):')
        for r in hand:
            print(f"  {r['style']:34} {r['event']:5} {r['bytes']/1024:6.0f}KB  "
                  f"{', '.join(r['faces'][:3])}")
    if flawed:
        print('\nflawed (labelled; candidates for fixing):')
        for r in flawed:
            print(f"  {r['style']:34} {r['event']:5} {(r.get('verdict') or '?'):9} "
                  f"{(r['reason'] or '')[:80]}")
    if skipped:
        print('\nskipped:')
        for r in skipped:
            print(f"  {r['style']:34} {r['event']:5} {(r['reason'] or '')[:90]}")
    if todo:
        print(f'\noutstanding: {len(todo)}  (run with --todo to list)')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Pin the accepted copy assignment of each judged poster into its solution.

    python3 pin.py --dry-run      # say what would be pinned and what cannot
    python3 pin.py                # write knobs.assign into the site's solutions

The stored solutions were made with in-progress and ad-hoc code, so the tool
cannot be relied on to reproduce them -- replayed through today's aligner, 9
of the judged posters put copy on the wrong lines. Where tests/judged.json
says the stored assignment is right, this writes it into the solution's knobs
as `--assign` points (the centre of each band the line sat on; `key=none` for
copy the accepted solution left out), so convert.py rebuilds what was
accepted whatever the aligner would now choose.

Points, not band numbers, so re-measurement cannot misdirect them. A point
that no longer lands on any band the current code finds is reported and left
out: that line's detection is broken, and pinning cannot fix detection.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402

JUDGED = os.path.join(os.path.dirname(HERE), 'tests', 'judged.json')


def pins_for(sol, copy):
    """-> ['key=x,y;x,y', ..., 'key=none'] from a stored solution."""
    L = sol['lines']
    out, got = [], set()
    for a in sol['assigned']:
        pts = ';'.join(f"{(L[b]['left'] + L[b]['right']) // 2},"
                       f"{(L[b]['y0'] + L[b]['y1']) // 2}" for b in a['bands'])
        out.append(f"{a['key']}={pts}")
        got.add(a['key'])
    out += [f"{c['key']}=none" for c in copy if c['key'] not in got]
    return out


def check(name, sol, pins):
    """Apply the pins to today's measurement. -> (kept pins, problems)."""
    from regress import knobs_of
    from bands import find_lines
    from copytext import load_copy
    from align import assign_copy, apply_assign
    k = knobs_of(sol)
    m = find_lines(os.path.join(poster_site.examples(), name + '.webp'),
                   min_glyphs=k['min_glyphs'], solid_radius=k['solid_radius'],
                   rule_length=k['rule_length'], rule_thick=k['rule_thick'],
                   contrast=k['contrast'], window=k['window'],
                   wrap_cap_ratio=k['wrap_cap_ratio'], exclude=k['exclude'])
    copy = load_copy(name.rsplit('-', 2)[1])
    assigned, _, _ = assign_copy(m['lines'], copy, wrap_cap_ratio=k['wrap_cap_ratio'])
    kept, problems, owner = [], [], {}
    for p in pins:
        try:
            _, pts = apply_assign(m['lines'], copy, [dict(a) for a in assigned], [p])
        except SystemExit as e:
            problems.append(f'{p.split("=")[0]}: {e}')
            continue
        key = p.split('=')[0]
        if not p.endswith('=none'):
            # pts are normalised to band centres, so two keys on one band
            # produce the same point: today's code merged their lines.
            shared = [owner[pt] for pt in pts[0].split('=', 1)[1].split(';') if pt in owner]
            if shared:
                problems.append(f'{key}: its line is merged into {shared[0]}\'s band now')
                continue
            for pt in pts[0].split('=', 1)[1].split(';'):
                owner[pt] = key
        kept.append(p)
    return kept, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    from copytext import load_copy
    judged = {k: v for k, v in json.load(open(JUDGED)).items() if not k.startswith('_')}
    for short, v in sorted(judged.items()):
        if v.get('golden') != 'stored':
            continue
        name = short + '-v2'
        path = os.path.join(poster_site.solutions(), name + '.json')
        sol = json.load(open(path))
        pins = pins_for(sol, load_copy(short.rsplit('-', 1)[1]))
        kept, problems = check(name, sol, pins)
        flag = f'  {len(problems)} not pinnable' if problems else ''
        print(f'{short:40} {len(kept)}/{len(pins)} pinned{flag}')
        for p in problems:
            print(f'    {p}')
        if not a.dry_run:
            sol.setdefault('knobs', {})['assign'] = kept
            json.dump(sol, open(path, 'w'), indent=1)


if __name__ == '__main__':
    main()

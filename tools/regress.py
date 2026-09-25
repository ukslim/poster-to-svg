#!/usr/bin/env python3
"""Re-measure every solved poster and say what changed.

    python3 regress.py                 # compare against the baseline snapshot
    python3 regress.py --snapshot      # record the current behaviour as the baseline
    python3 regress.py --against-solutions   # compare with the stored solutions
    python3 regress.py swiss_international-fete   # just these

Runs band-finding and copy assignment -- the parts every later number depends
on -- on each poster that has a stored solution, with that solution's own
knobs, and compares where each copy line landed. Prints only what changed, and
a one-line summary, so a clean run costs a few dozen bytes to read.

The baseline lives in tests/regress_baseline.json. It records what the code
did, not what is right: the stored conversions are not known-good. A change is
a prompt to look (render the poster's overlay) and judge whether the new
assignment is better or worse -- never a failure by itself.
"""
import argparse, glob, json, os, sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitepaths as poster_site  # noqa: E402

BASELINE = os.path.join(os.path.dirname(HERE), 'tests', 'regress_baseline.json')
TOL = 3      # px a band edge may move before it counts as a change

DEFAULT_KNOBS = dict(solid_radius=24, rule_length=160, rule_thick=5, min_glyphs=2,
                     contrast=40, window=61, wrap_cap_ratio=1.3, exclude=[], assign=[])


def knobs_of(sol):
    k = dict(DEFAULT_KNOBS)
    k.update({n: v for n, v in (sol.get('knobs') or {}).items() if n in DEFAULT_KNOBS})
    k['exclude'] = [tuple(int(v) for v in x.split(',')) if isinstance(x, str) else tuple(x)
                    for x in (k.get('exclude') or [])]
    # As solve() does: lettering kept as art is not looked for, and its box
    # is excluded; lines set on a slant are measured in their own frame by
    # --tilted, so the level measurement here says nothing about them.
    from solve_type import parse_art, parse_tilt
    raw = sol.get('knobs') or {}
    arts = [parse_art(a) for a in raw.get('art') or []]
    k['exclude'] += [tuple(box) for _, box, _ in arts]
    k['skip_keys'] = ({key for keys, _, _ in arts for key in keys}
                      | {key for t in raw.get('tilted') or [] for key in parse_tilt(t)[0]})
    return k


def summary(lines, assigned, cost):
    """{key: [[y0, y1, left, right], ...]} plus the alignment cost."""
    out = {}
    for a in assigned:
        out[a['key']] = [[lines[b][f] for f in ('y0', 'y1', 'left', 'right')]
                         for b in a['bands']]
    return {'assigned': out, 'cost': round(float(cost), 3)}


def measure_one(name):
    """Re-run measurement + assignment for one poster. Top level for Pool."""
    import logging
    logging.getLogger('fontTools').setLevel(logging.ERROR)
    from measure import find_lines, load_copy, assign_copy
    sol = json.load(open(os.path.join(poster_site.solutions(), name + '.json')))
    k = knobs_of(sol)
    src = os.path.join(poster_site.examples(), name + '.webp')
    try:
        m = find_lines(src, min_glyphs=k['min_glyphs'], solid_radius=k['solid_radius'],
                       rule_length=k['rule_length'], rule_thick=k['rule_thick'],
                       contrast=k['contrast'], window=k['window'],
                       wrap_cap_ratio=k['wrap_cap_ratio'], exclude=k['exclude'])
        event = name.rsplit('-', 2)[1]
        copy = [c for c in load_copy(event) if c['key'] not in k['skip_keys']]
        assigned, _, cost = assign_copy(m['lines'], copy,
                                        wrap_cap_ratio=k['wrap_cap_ratio'])
        out = summary(m['lines'], assigned, cost)
        # The aligner's own answer is what is scored; the pinned answer (the
        # stored --assign points applied on top) is what convert.py would
        # build, and should reproduce what was accepted.
        if k['assign']:
            from align import apply_assign
            try:
                pinned, _ = apply_assign(m['lines'], copy, [dict(a) for a in assigned],
                                         [p for p in k['assign']
                                          if p.split('=')[0] not in k['skip_keys']])
                out['pinned'] = summary(m['lines'], pinned, cost)['assigned']
            except SystemExit as e:
                out['pinned_error'] = str(e)
        return name, out
    except Exception as e:                      # a crash is a result too
        return name, {'error': f'{type(e).__name__}: {e}'}


def from_solution(name):
    sol = json.load(open(os.path.join(poster_site.solutions(), name + '.json')))
    return summary(sol['lines'], sol['assigned'], sol.get('alignment_cost', 0.0))


def same_boxes(a, b):
    return len(a) == len(b) and all(
        all(abs(x - y) <= TOL for x, y in zip(p, q)) for p, q in zip(a, b))


def same_place(gold, got, slack=4):
    if not got or len(gold) != len(got):
        return False
    for (y0, y1, l, r), (g0, g1, gl, gr) in zip(gold, got):
        cx, cy = (l + r) / 2, (y0 + y1) / 2
        if not (gl - slack <= cx <= gr + slack and g0 - slack <= cy <= g1 + slack):
            return False
    return True


def compare(name, old, new):
    """-> list of human-readable differences (empty if none)."""
    if 'error' in new:
        return [f'CRASH {new["error"]}']
    if 'error' in old:
        return ['was a crash, now runs']
    out = []
    for key in sorted(set(old['assigned']) | set(new['assigned'])):
        o, n = old['assigned'].get(key), new['assigned'].get(key)
        if o is None:
            out.append(f'{key}: now assigned {n}')
        elif n is None:
            out.append(f'{key}: no longer assigned (was {o})')
        elif not same_boxes(o, n):
            out.append(f'{key}: {o} -> {n}')
    if abs(old['cost'] - new['cost']) > 0.02:
        out.append(f"alignment cost {old['cost']} -> {new['cost']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('only', nargs='*', help='poster names like style-event (no -v2)')
    ap.add_argument('--snapshot', action='store_true')
    ap.add_argument('--against-solutions', action='store_true')
    ap.add_argument('-j', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()

    names = sorted(os.path.basename(p)[:-5]
                   for p in glob.glob(os.path.join(poster_site.solutions(), '*.json')))
    if a.only:
        want = {o if o.endswith('-v2') else o + '-v2' for o in a.only}
        names = [n for n in names if n in want]
    with Pool(a.j) as pool:
        now = dict(pool.map(measure_one, names))

    if a.snapshot:
        base = json.load(open(BASELINE)) if os.path.exists(BASELINE) and a.only else {}
        base.update(now)
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        json.dump(base, open(BASELINE, 'w'), indent=0, sort_keys=True)
        print(f'baseline: {len(now)} posters -> {BASELINE}')
        return

    if a.against_solutions:
        base = {n: from_solution(n) for n in names}
    else:
        if not os.path.exists(BASELINE):
            raise SystemExit('no baseline yet: run with --snapshot first')
        base = json.load(open(BASELINE))

    changed, better, worse = 0, 0, 0
    for n in names:
        if n not in base:
            print(f'{n}: not in baseline')
            continue
        diffs = compare(n, base[n], now[n])
        if not diffs:
            continue
        changed += 1
        oc, nc = base[n].get('cost'), now[n].get('cost')
        if oc is not None and nc is not None:
            better += nc < oc - 0.02
            worse += nc > oc + 0.02
        print(f'{n}:')
        for d in diffs:
            print(f'    {d}')
    print(f'\n{len(names) - changed} unchanged, {changed} changed '
          f'({better} lower alignment cost, {worse} higher) -- look before judging')
    judged_score(names, now, base)
    sys.exit(1 if changed else 0)


def judged_score(names, now, base):
    """How many posters match the assignment a person judged right.

    tests/judged.json names, per poster, which assignment was right when
    someone looked: the stored solution, the baseline snapshot, or neither.
    Unlike the baseline diff, this is a quality number -- it should only go up.
    """
    path = os.path.join(os.path.dirname(BASELINE), 'judged.json')
    if not os.path.exists(path):
        return
    judged = {k + '-v2': v for k, v in json.load(open(path)).items() if not k.startswith('_')}
    # Pinned posters: does what convert.py would build match what was accepted?
    # Lines pin.py could not pin (their bands are gone) are left out.
    pinned_ok, pinned_bad = 0, []
    for n in names:
        if n not in now or not judged.get(n, {}).get('golden') == 'stored':
            continue
        if 'pinned_error' in now[n]:
            pinned_bad.append(n[:-3] + ' (error)')
            continue
        if 'pinned' not in now[n]:
            continue
        gold = from_solution(n)['assigned']
        got = now[n]['pinned']
        pinned_keys = {p.split('=')[0] for p in knobs_of(json.load(open(os.path.join(
            poster_site.solutions(), n + '.json'))))['assign']}
        # Same assignment = each accepted band's centre lies in the band now
        # given that line. Edges may move (re-segmentation refines them);
        # lines pin.py could not pin are known detection gaps, not failures.
        bad = [k for k in gold if k in pinned_keys and not same_place(gold[k], got.get(k))]
        if bad:
            pinned_bad.append(f"{n[:-3]} ({', '.join(bad)})")
        else:
            pinned_ok += 1
    if pinned_ok or pinned_bad:
        print(f'pinned: {pinned_ok}/{pinned_ok + len(pinned_bad)} reproduce the accepted '
              f'assignment' + (f'; not: {"; ".join(pinned_bad)}' if pinned_bad else ''))
    hit, miss, open_ = 0, [], []
    for n in names:
        j = judged.get(n)
        if not j:
            continue
        if j['golden'] is None:
            open_.append(n[:-3])
            continue
        gold = from_solution(n) if j['golden'] == 'stored' else base.get(n)
        if gold and not [d for d in compare(n, gold, now[n])
                         if not d.startswith('alignment cost')]:
            hit += 1
        else:
            miss.append(n[:-3])
    print(f'aligner alone: {hit}/{hit + len(miss)} match the assignment judged right'
          + (f'; not yet: {", ".join(miss)}' if miss else '')
          + (f'; no right answer yet: {", ".join(open_)}' if open_ else ''))


if __name__ == '__main__':
    main()

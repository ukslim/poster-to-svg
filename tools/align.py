"""Assign the known copy to measured bands."""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bands import eff_width  # noqa: E402


def copy_orderings(copy):
    """Plausible orders for the same copy.

    The aligner walks copy and bands together and cannot reorder, but posters
    do move one line about -- a fete commonly sets the beneficiary among the
    footer at the foot of the page rather than under the venue. Offering the
    few orderings a designer would actually use costs one more pass each and
    stops a line being dropped altogether for being in the wrong place.
    """
    yield copy
    # The presenter line is small print and posters put it almost anywhere:
    # above the headline, under it, or down among the ticket details.
    keys0 = [c['key'] for c in copy]
    if 'presenter' in keys0:
        p = keys0.index('presenter')
        rest = copy[:p] + copy[p + 1:]
        for j in range(1, len(rest) + 1):
            if j != p:
                yield rest[:j] + [copy[p]] + rest[j:]
    # A gig poster may run its small lines together at the top -- presenter,
    # then support -- and set the headliner lower down, above the date.
    keys = [c['key'] for c in copy]
    if 'headliner' in keys and 'support' in keys:
        h, s = keys.index('headliner'), keys.index('support')
        if s == h + 1:
            yield copy[:h] + [copy[s], copy[h]] + copy[s + 1:]
    movable = [i for i, c in enumerate(copy) if c['key'] == 'beneficiary']
    if not movable:
        return
    i = movable[0]
    rest = copy[:i] + copy[i + 1:]
    tail = [j for j, c in enumerate(rest) if c['key'].startswith('footer')]
    if tail:
        yield rest[:tail[0]] + [copy[i]] + rest[tail[0]:]      # before the footer
        if len(tail) > 1:
            yield rest[:tail[-1]] + [copy[i]] + rest[tail[-1]:]  # between footer lines
    yield rest + [copy[i]]                                      # last of all


def assign_copy(lines, copy, max_split=3, wrap_cap_ratio=1.3):
    """Align known copy to measured bands, allowing one copy line to wrap
    across consecutive bands (a headline usually does).

    Cost model needs no font: for a copy line of L characters set at cap height
    h, ink width should be about k*L*h, where k is width per character per cap
    height -- roughly constant for a face, whatever the size. Fit k, then pick
    the alignment minimising relative deviation.
    """
    if not lines or not copy:
        return [], 'no lines or no copy', 0.0
    n, m = len(copy), len(lines)

    def block(i, j):
        b = lines[i:j]
        return (sum(eff_width(x) for x in b), float(np.mean([x['cap'] for x in b])))

    # Display and body are usually different faces of different widths, so one
    # global k mis-costs the headline; keep one per size class. Three classes,
    # not two: a poster often sets a mid-sized line -- a date, a strapline --
    # between its headline and its small print, and lumping that in with the
    # headline fits k to display type, which is far tighter per character. The
    # mid-sized line's expected width then comes out short, the aligner decides
    # a wrapped line cannot be wrapped, and half of it is dropped.
    med_cap = float(np.median([l['cap'] for l in lines]))

    def big(idx):
        cap = lines[idx]['cap']
        return 2 if cap > 4.0 * med_cap else 1 if cap > 1.5 * med_cap else 0

    def run(k0):
        """One alignment, starting from k0 and re-fitting k three times."""
        k = {0: k0, 1: k0, 2: k0}
        out = []
        for _ in range(3):
            INF = float('inf')
            dp = [[INF] * (m + 1) for _ in range(n + 1)]
            back = [[None] * (m + 1) for _ in range(n + 1)]
            dp[0][0] = 0.0
            for ci in range(n + 1):
                for bi in range(m + 1):
                    # A band may belong to no copy line at all -- a logo, a
                    # catalogue number, a stray mark. Allow skipping one.
                    if bi and dp[ci][bi - 1] + 0.5 < dp[ci][bi]:
                        dp[ci][bi] = dp[ci][bi - 1] + 0.5
                        back[ci][bi] = ('skip', bi - 1)
                    if dp[ci][bi] == INF or ci == n:
                        continue
                    L = max(1, len(copy[ci]['text']))
                    for span in range(1, max_split + 1):
                        bj = bi + span
                        if bj > m:
                            break
                        # A line that wraps keeps its size, and resumes at the
                        # same margin. Without this a headline band and a
                        # small-print band get glued into one "line".
                        caps = [lines[i]['cap'] for i in range(bi, bj)]
                        if max(caps) > wrap_cap_ratio * min(caps):
                            break
                        # Consecutive lines of one wrapped block sit a leading
                        # apart. Without a limit, a footer will happily "wrap"
                        # onto a plate number 350px down the page because it
                        # happens to share the left margin and the cap height,
                        # stranding the last words of the sentence at the foot
                        # of the poster. Leading runs about 1.2-1.6 of cap.
                        gaps = [lines[i + 1]['baseline'] - lines[i]['baseline']
                                for i in range(bi, bj - 1)]
                        if gaps and max(gaps) > 3.0 * max(caps):
                            break
                        # A word set in an accent colour is a band of its own,
                        # on the same row as the rest of its line: VILLAGE in
                        # blue, then FETE in red, a word space apart. That is
                        # the line continuing, not wrapping, so it is exempt
                        # from the margin test below.
                        def runs_on(i):
                            p, q = lines[i - 1], lines[i]
                            return (min(p['y1'], q['y1']) - max(p['y0'], q['y0'])
                                    > 0.5 * min(p['cap'], q['cap'])
                                    and 0 < q['left'] - p['right']
                                    < 1.0 * max(p['cap'], q['cap']))
                        rows = [i for i in range(bi, bj)
                                if i == bi or not runs_on(i)]
                        # Resumes at the same margin: left, centre, or -- for a
                        # right-aligned title -- the right edge.
                        lefts = [lines[i]['left'] for i in rows]
                        mids = [(lines[i]['left'] + lines[i]['right']) / 2
                                for i in rows]
                        rights = [lines[i]['right'] for i in rows]
                        page = max(l['right'] for l in lines)
                        if (max(lefts) - min(lefts) > 0.06 * page
                                and max(mids) - min(mids) > 0.06 * page
                                and max(rights) - min(rights) > 0.03 * page):
                            break
                        W, h = block(bi, bj)
                        expect = k[big(bi)] * L * h
                        cost = abs(W - expect) / max(expect, 1.0)
                        cost += 0.05 * (span - 1)
                        if dp[ci][bi] + cost < dp[ci + 1][bj]:
                            dp[ci + 1][bj] = dp[ci][bi] + cost
                            back[ci + 1][bj] = ('use', bi, span)
                    if copy[ci].get('optional') and dp[ci][bi] + 0.6 < dp[ci + 1][bi]:
                        dp[ci + 1][bi] = dp[ci][bi] + 0.6
                        back[ci + 1][bi] = ('use', bi, 0)
            if dp[n][m] == float('inf'):
                return None, float('inf')
            out, ci, bi = [], n, m
            while ci > 0 or bi > 0:
                step = back[ci][bi]
                if step is None:
                    break
                if step[0] == 'skip':
                    bi = step[1]
                    continue
                _, prev, span = step
                out.append((ci - 1, prev, span))
                ci, bi = ci - 1, prev
            out.reverse()
            used = [(c, b, s) for c, b, s in out if s]
            for cls in (0, 1, 2):
                num = [sum(eff_width(lines[i]) for i in range(b, b + s))
                       for _, b, s in used if big(b) == cls]
                den = [len(copy[c]['text'])
                       * float(np.mean([lines[i]['cap'] for i in range(b, b + s)]))
                       for c, b, s in used if big(b) == cls]
                if num and float(np.sum(den)) > 0:
                    k[cls] = float(np.sum(num) / float(np.sum(den)))
        matched = max(1, len([1 for _, _, s in out if s]))
        return out, dp[n][m] / matched

    # Iteration only converges on the answer if it starts near it, and a single
    # greedy guess is easily wrong: one line that wraps but was given a single
    # band looks half as wide as it is, which drags the estimate between two
    # populations and mis-costs every line after it. Sweep plausible starting
    # values instead and keep whichever alignment ends up cheapest.
    # The largest type on the page is the display line: a gig poster's
    # headliner, a fête's title. Note this is *not* copy line 0 -- a gig leads
    # with "Pale Light Promotions presents:", which is some of the smallest
    # type on the poster -- so key it on the field, not the position. Without
    # this, a two-line title gives the DP two readings that cost exactly the
    # same (title over both bands, or title on one and the next copy line on
    # the other) and it breaks the tie arbitrarily, putting "Village Fête"
    # where the date should be. Prefer a solution that respects it, but do not
    # insist: a poster that really does set something else larger should still
    # align rather than fail outright.
    biggest = max(range(len(lines)), key=lambda i: lines[i]['cap'])

    big_cap = lines[biggest]['cap']

    def leads(out, variant):
        idx = next((i for i, c in enumerate(variant)
                    if c['key'] in ('headliner', 'title')), None)
        if idx is None:
            return True
        ok = False
        for c, b, s in out:
            if not s:
                continue
            if c == idx:
                ok = ok or b <= biggest < b + s
            # Display type is not merely the biggest on the page, it is
            # distinctly bigger. A band at very nearly the headline's cap is
            # the headline's second line, not the date set enormous -- which
            # is how "Village Fête" ends up labelled as the date on a poster
            # whose title runs to two lines.
            elif any(lines[i]['cap'] > 0.8 * big_cap for i in range(b, b + s)):
                return False
        return ok

    best, best_cost, best_copy = None, float('inf'), copy
    fallback, fallback_cost, fallback_copy = None, float('inf'), copy
    for variant in copy_orderings(copy):
        copy, n = variant, len(variant)
        for k0 in np.arange(0.25, 1.15, 0.05):
            out, cost = run(float(k0))
            if out is None:
                continue
            if cost < fallback_cost:
                fallback, fallback_cost, fallback_copy = out, cost, variant
            if leads(out, variant) and cost < best_cost:
                best, best_cost, best_copy = out, cost, variant
    if best is None:
        best, best_cost, best_copy = fallback, fallback_cost, fallback_copy
    copy, n = best_copy, len(best_copy)
    if best is None:
        return [], f'cannot align {len(best_copy)} copy lines to {m} bands', 0.0

    assigned = []
    for ci, bi, span in best:
        if not span:
            continue
        assigned.append(dict(key=copy[ci]['key'], text=copy[ci]['text'],
                             bands=list(range(bi, bi + span))))
    note = ('ok' if best_cost < 0.25
            else f'weak alignment (mean cost {best_cost:.2f})')
    return assigned, note, best_cost



def apply_assign(lines, copy, assigned, specs):
    """Override the aligner with assignments someone read off the overlay.

    Reading which band holds which copy line is one look at overlay.py for
    Claude, and a heuristic arms race for code. So the aligner proposes and a
    spec disposes: each `key=SPEC` is

        key=#3          band 3 as numbered on the overlay
        key=#1+#2       a line wrapped over bands 1 and 2
        key=512,1250    the band containing that point (how specs are stored,
                        so they survive re-measurement renumbering the bands;
                        several points are separated by ';')
        key=none        this copy line is not on the poster, or not to reset

    A band given to a key is taken from whatever the aligner gave it to.
    Returns (assigned, points) where points is the specs rewritten as
    coordinates, for storing with the solution.
    """
    if not specs:
        return assigned, []
    texts = {c['key']: c['text'] for c in copy}
    by_key = {a['key']: a for a in assigned}
    points = []
    for spec in specs:
        key, _, val = spec.partition('=')
        key, val = key.strip(), val.strip()
        if key not in texts:
            raise SystemExit(f'--assign {spec!r}: no copy line {key!r}; have '
                             + ', '.join(texts))
        if val == 'none':
            by_key.pop(key, None)
            points.append(f'{key}=none')
            continue
        bands = []
        for part in (val.split('+') if '#' in val else val.split(';')):
            part = part.strip()
            if part.startswith('#'):
                i = int(part[1:])
                if not 0 <= i < len(lines):
                    raise SystemExit(f'--assign {spec!r}: there is no band #{i}')
            else:
                x, y = (float(v) for v in part.split(','))
                hits = [h for h, l in enumerate(lines)
                        if l['left'] - 4 <= x <= l['right'] + 4
                        and l['y0'] - 4 <= y <= l['y1'] + 4]
                if not hits:
                    raise SystemExit(f'--assign {spec!r}: no band at {x:.0f},{y:.0f} '
                                     'now; look at the overlay again')
                i = min(hits, key=lambda h: abs((lines[h]['y0'] + lines[h]['y1']) / 2 - y))
            bands.append(i)
        # Keep the order given: it is the reading order. Sorting by top edge
        # read mid_century's "Village Fete" row as Fete-then-Village, because
        # the F stands 4px higher, and split the words across them backwards.
        bands = list(dict.fromkeys(bands))
        for a in list(by_key.values()):
            if a['key'] != key:
                a['bands'] = [b for b in a['bands'] if b not in bands]
                if not a['bands']:
                    del by_key[a['key']]
        by_key[key] = dict(key=key, text=texts[key], bands=bands, assigned_by='hand')
        points.append(f'{key}=' + ';'.join(
            f"{(lines[i]['left'] + lines[i]['right']) // 2},{(lines[i]['y0'] + lines[i]['y1']) // 2}"
            for i in bands))
    order = [c['key'] for c in copy]
    out = sorted(by_key.values(),
                 key=lambda a: (lines[a['bands'][0]]['y0'], order.index(a['key'])))
    return out, points

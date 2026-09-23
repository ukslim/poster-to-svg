#!/usr/bin/env python3
"""Unit tests for band ordering and line splitting.

    python3 test_measure.py

Synthetic bands and components only — no posters, negligible cost.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure import reading_order  # noqa: E402
from components import group_lines  # noqa: E402

fails = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'   {detail}' if detail else ''))
    if not ok:
        fails.append(name)


def band(name, left, right, base, cap=20):
    return dict(name=name, left=left, right=right, baseline=base,
                y0=base - cap, y1=base, cap=cap)


def comp(x0, x1, y0=100, y1=120):
    return {'box': (x0, y0, x1, y1), 'area': 1, 'n_runs': 1}


def names(lines):
    return [l['name'] for l in reading_order(lines)]


# --- one column: plain baseline order ----------------------------------------
col = [band('c', 50, 400, 200), band('a', 50, 600, 100), band('b', 50, 300, 150)]
check('single column reads top to bottom', names(col) == ['a', 'b', 'c'], names(col))

# --- beneficiary beside the attractions list, baselines interleaved -----------
page = [band('title', 50, 950, 100, cap=120),
        band('label', 520, 650, 1216),
        band('benef1', 70, 360, 1224),
        band('item0', 520, 700, 1246),
        band('benef2', 70, 300, 1250),
        band('item1', 520, 880, 1270),
        band('item2', 520, 730, 1294),
        band('footer', 70, 600, 1475)]
want = ['title', 'benef1', 'benef2', 'label', 'item0', 'item1', 'item2', 'footer']
check('left column is read before the right', names(page) == want, names(page))

# --- a list set in two halves on shared baselines, under a centred label ------
halves = [band('label', 400, 620, 1240),
          band('i0', 100, 330, 1282), band('i4', 600, 930, 1282),
          band('i1', 100, 470, 1314), band('i5', 600, 700, 1314),
          band('i2', 100, 330, 1348), band('i6', 600, 950, 1348),
          band('i3', 100, 330, 1383), band('i7', 600, 850, 1383),
          band('footer', 240, 790, 1500)]
want = ['label', 'i0', 'i1', 'i2', 'i3', 'i4', 'i5', 'i6', 'i7', 'footer']
check('two halves of a list read in order', names(halves) == want, names(halves))

# --- a footer under the short left column is not read as part of it ----------
short = [band('b1', 70, 360, 1224), band('label', 520, 650, 1216),
         band('b2', 70, 256, 1255), band('i0', 520, 660, 1244),
         band('i1', 520, 880, 1269), band('i2', 520, 730, 1294),
         band('i3', 520, 730, 1319), band('i4', 520, 850, 1344),
         band('i5', 520, 640, 1369), band('i6', 520, 880, 1394),
         band('i7', 520, 770, 1418), band('free', 72, 347, 1475),
         band('org', 72, 605, 1504)]
want = ['b1', 'b2', 'label', 'i0', 'i1', 'i2', 'i3', 'i4', 'i5', 'i6', 'i7',
        'free', 'org']
check('footer under a short column follows both', names(short) == want, names(short))

# --- columns ending level: the footer follows both ----------------------------
level = [band('l0', 70, 250, 1313), band('r0', 530, 910, 1313),
         band('l1', 70, 470, 1346), band('r1', 530, 680, 1346),
         band('l2', 70, 320, 1380), band('r2', 530, 940, 1380),
         band('l3', 70, 450, 1413), band('r3', 530, 830, 1412),
         band('free', 70, 450, 1478, cap=26), band('org', 70, 660, 1511)]
want = ['l0', 'l1', 'l2', 'l3', 'r0', 'r1', 'r2', 'r3', 'free', 'org']
check('footer under level columns follows both', names(level) == want, names(level))

# --- an accent-coloured word is the same line, not a column -------------------
accent = [band('brind', 98, 775, 171, cap=63), band('village', 54, 611, 418, cap=212),
          band('fete', 659, 978, 419, cap=217), band('date', 118, 579, 547, cap=30),
          band('venue', 612, 957, 547, cap=28)]
check('an accent word follows its line',
      names(accent)[:3] == ['brind', 'village', 'fete'], names(accent))

# --- a tall line beside two short ones: split, then regrouped -----------------
tall = [comp(50, 90, 1081, 1117), comp(98, 140, 1081, 1117),
        comp(560, 600, 1079, 1097), comp(606, 700, 1079, 1097),
        comp(560, 620, 1110, 1127), comp(626, 700, 1110, 1127)]
check('stacked lines beside a tall one come apart',
      len(group_lines(tall)) == 3, [len(g) for g in group_lines(tall)])

# --- a block far above the columns is not pulled into them --------------------
far = [band('top', 50, 200, 100), band('l', 50, 200, 900), band('r', 600, 800, 900)]
check('distant block stays out of the zone', names(far) == ['top', 'l', 'r'], names(far))


# --- group_lines splits at a gutter, not at a word space ----------------------
words = [comp(0, 40), comp(48, 90), comp(97, 140)]           # gaps ~0.35 h
split = [comp(0, 40), comp(48, 90), comp(200, 240), comp(248, 290)]
check('word spaces keep one line', len(group_lines(words)) == 1)
check('a gutter splits the row', len(group_lines(split)) == 2,
      [len(g) for g in group_lines(split)])

# --- list markers stay in the artwork -----------------------------------------
from measure import strip_marker  # noqa: E402
bullet = [(57, 64, 1300, 1307), (78, 90, 1296, 1313), (93, 104, 1300, 1313),
          (107, 118, 1300, 1313)]
check('a bullet is not part of its line', strip_marker(bullet)[0][0][0] == 78)
word = [(78, 90, 1296, 1313), (93, 104, 1300, 1313), (107, 118, 1300, 1313)]
check('a line without a marker is untouched', strip_marker(word) == (word, None))

# --- copy is the text the prompt gave, labels included ------------------------
# Needs the site's events.yaml and prompt template, so it only runs where a
# site can be found (from inside one, or with POSTER_SITE set).
from measure import load_copy  # noqa: E402
try:
    gig = {c['key']: c['text'] for c in load_copy('gig')}
except SystemExit:
    print('SKIP  copy labels (no poster site found)')
else:
    check('ticket lines carry the prompt\'s labels',
          gig['tickets'].startswith('Tickets: ')
          and gig['ticket_source'].startswith('Available from '),
          (gig['tickets'], gig['ticket_source']))
    check('no template syntax leaks into the copy',
          not any('{' in t for t in gig.values()))

# --- tracking: wide AND even gaps only ---------------------------------------
from measure import tracking  # noqa: E402
def row(gaps, w=12, cap=20):
    x, out = 0, []
    for g in [0] + gaps:
        x += g
        out.append((x, x + w - 1, 100, 100 + cap)); x += w
    return out
check('even wide gaps are tracking', tracking(row([7] * 9), 20) > 0)
check('tight gaps are not', tracking(row([2] * 9), 20) == 0)
check('wide but uneven gaps are not',
      tracking(row([1, 14, 2, 12, 1, 15, 3, 11, 2]), 20) == 0)
check('word spaces do not spoil an even line',
      tracking(row([7, 7, 7, 20, 7, 7, 7, 7, 7]), 20) > 0)

print(f'\n{len(fails)} failed' if fails else '\nall passed')
sys.exit(1 if fails else 0)

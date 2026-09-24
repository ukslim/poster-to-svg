"""How the poster's type is ranged: blocks, and the edge each block shares.

Shared by the builder, which anchors each line on the edge its block keeps
(text-anchor start / middle / end), and by audit.py, which checks the rebuilt
poster kept it. Both must agree on what a block is.

The point of anchoring: the reset face is never exactly as wide as the
generator's lettering. A centred line anchored at its left ink edge drifts off
centre by half the difference; anchored at its centre (text-anchor="middle"),
the browser keeps it centred whatever the face measures.
"""
EDGE_TOL, EDGE_MIN = 0.012, 6


def blocks(bands, page_w):
    """Group bands into blocks of type that read as one aligned column, and say
    how each is aligned: 'left', 'centre', 'right' or None.

    -> [(indices into `bands`, kind)]. Neighbours in a block overlap
    horizontally and sit within 2.5 caps of each other vertically. The
    alignment is whichever edge the lines share most tightly, provided they
    share it within EDGE_TOL of the page. A single line counts as centred when
    it sits on the page's centre line.
    """
    order = sorted(range(len(bands)), key=lambda i: bands[i]['y0'])
    groups, cur = [], []
    for i in order:
        b = bands[i]
        if cur:
            p = bands[cur[-1]]
            gap = b['y0'] - p['y1']
            overlap = min(b['right'], p['right']) - max(b['left'], p['left'])
            if gap > 2.5 * max(b['cap'], p['cap']) or overlap <= 0:
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        B = [bands[i] for i in g]
        edges = {'left': [b['left'] for b in B],
                 'centre': [(b['left'] + b['right']) / 2 for b in B],
                 'right': [b['right'] for b in B]}
        if len(B) == 1:
            c = edges['centre'][0]
            kind = 'centre' if abs(c - page_w / 2) < EDGE_TOL * page_w else None
        else:
            spread = {k: max(v) - min(v) for k, v in edges.items()}
            kind = min(spread, key=spread.get)
            if spread[kind] > max(EDGE_MIN, EDGE_TOL * page_w):
                kind = None
        out.append((g, kind))
    return out


def anchors(lines, band_ids, page_w):
    """-> ({band id: 'left'|'centre'|'right'}, {band id: block number}) for
    the given bands. A block with no shared edge is ranged left."""
    ids = list(band_ids)
    kinds, block_of = {}, {}
    for n, (g, kind) in enumerate(blocks([lines[i] for i in ids], page_w)):
        for k in g:
            kinds[ids[k]] = kind or 'left'
            block_of[ids[k]] = n
    return kinds, block_of

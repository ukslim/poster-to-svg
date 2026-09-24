"""Fetch Google Fonts styles as tiny subsets holding just the characters needed.

The CSS API's `text=` parameter returns a font containing only the requested
characters -- a couple of KB, kerning (GPOS) kept. So no font corpus is kept
locally: a style is fetched when it is on a shortlist or chosen for a poster,
and cached on disk by (family, style, text) in fonts/cache/.

An old Android user agent makes the API serve WOFF1 (zlib, readable without
brotli) static instances, one per requested style; fontTools re-saves each as
a plain .ttf that both it and PIL read.
"""
import hashlib, io, os, re, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.normpath(os.path.join(HERE, '..', 'fonts', 'cache'))
UA = ('Mozilla/5.0 (Linux; Android 4.0.4; Galaxy Nexus Build/IMM76B) '
      'AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.133 Mobile Safari/535.19')


def http(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def style_key(weight, italic=False, wdth=None):
    return (1 if italic else 0, int(wdth) if wdth else None, int(weight))


def css_urls(family, styles, text):
    """-> {(ital, wdth, wght): url} for the requested styles of one family.

    `styles` is a list of style_key tuples; all must agree on whether wdth is
    given. One request covers them all.
    """
    styles = sorted(set(styles))
    has_w = any(s[1] for s in styles)
    if has_w and len(styles) > 1:
        # The API serves the right width instance but does not label it
        # (no font-stretch for this user agent), so ask one style at a time
        # and key each answer by what was asked for.
        out = {}
        for st in styles:
            out.update(css_urls(family, [st], text))
        return out
    axes = 'ital,wdth,wght' if has_w else 'ital,wght'
    tuples = ';'.join(f'{s[0]},{s[1]},{s[2]}' if has_w else f'{s[0]},{s[2]}'
                      for s in styles)
    q = (f'https://fonts.googleapis.com/css2?family={urllib.parse.quote_plus(family)}'
         f':{axes}@{tuples}' + (f'&text={urllib.parse.quote(text)}' if text else ''))
    css = http(q).decode()
    out = {}
    blocks = css.split('@font-face')[1:]
    if has_w and len(blocks) == 1:
        u = re.search(r'url\((https://[^)]+)\)', blocks[0])
        return {styles[0]: u.group(1)} if u else {}
    for block in blocks:
        st = re.search(r'font-style:\s*(\w+)', block)
        wt = re.search(r'font-weight:\s*(\d+)', block)
        sw = re.search(r'font-stretch:\s*([\d.]+)%', block)
        u = re.search(r'url\((https://[^)]+)\)', block)
        if wt and u:
            key = (1 if st and st.group(1) == 'italic' else 0,
                   int(float(sw.group(1))) if (sw and has_w) else None,
                   int(wt.group(1)))
            out[key] = u.group(1)
    return out


def to_ttf(raw, path):
    from fontTools.ttLib import TTFont
    f = TTFont(io.BytesIO(raw))
    f.flavor = None
    f.save(path)


def cache_path(family, key, text):
    h = hashlib.sha1(f'{family}|{key}|{text}'.encode()).hexdigest()[:16]
    slug = re.sub(r'[^A-Za-z0-9]', '', family)
    return os.path.join(CACHE, f'{slug}-{key[0]}-{key[1] or 0}-{key[2]}-{h}.ttf')


def fetch(family, weight=400, italic=False, wdth=None, text=''):
    """-> path of a .ttf holding `text`'s characters in that style (cached).
    With no text, the whole font (for Latin, as the API serves it)."""
    key = style_key(weight, italic, wdth)
    path = cache_path(family, key, text)
    if os.path.exists(path):
        return path
    os.makedirs(CACHE, exist_ok=True)
    urls = css_urls(family, [key], text)
    if key not in urls:
        # The API may snap a weight to the nearest it has; take what it served.
        if len(urls) != 1:
            raise LookupError(f'{family} {key}: not served ({sorted(urls)})')
        key = next(iter(urls))
    to_ttf(http(urls[key]), path + '.part')
    os.replace(path + '.part', path)
    return path

#!/usr/bin/env python3
"""Fill the candidate-font cache.

    python3 fetch_fonts.py              # fetch anything missing
    python3 fetch_fonts.py --list       # just show what the corpus would be

Google Fonts are pulled through the CSS API with an old Android user-agent,
which serves WOFF1 (zlib, so no brotli needed) rather than WOFF2, and serves a
STATIC instance per requested weight rather than a variable font. fontTools
re-saves each one as a plain .ttf so both it and PIL can read it. That means no
variable-axis instancing step: ask for the weights you want and you get them.

Faces land in ../fonts/ as `Family-weight.ttf`. The directory is gitignored.
"""
import argparse, os, re, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.normpath(os.path.join(HERE, '..', 'fonts'))
UA = ('Mozilla/5.0 (Linux; Android 4.0.4; Galaxy Nexus Build/IMM76B) '
      'AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.133 Mobile Safari/535.19')

# family: weights. Chosen against the typographic families the 100 poster
# styles actually draw on. Weight lists are deliberately sparse -- a poster
# needs a display weight and a text weight, not all nine.
GOOGLE = {
    # --- grotesque / neo-grotesque -----------------------------------------
    'Archivo': [400, 600, 700, 900], 'Archivo Black': [400],
    'Inter': [400, 600, 700, 900], 'Public Sans': [400, 700, 900],
    'Roboto': [400, 700, 900], 'Arimo': [400, 700],
    'Work Sans': [400, 700, 900], 'Manrope': [400, 700, 800],
    'Figtree': [400, 700, 900], 'Instrument Sans': [400, 700],
    'Schibsted Grotesk': [400, 700, 900], 'Space Grotesk': [400, 700],
    'Familjen Grotesk': [400, 700], 'Anybody': [400, 700, 900],
    # --- condensed / compressed display ------------------------------------
    'Anton': [400], 'Oswald': [300, 400, 500, 600, 700],
    'Bebas Neue': [400], 'Fjalla One': [400], 'Antonio': [400, 600, 700],
    'Archivo Narrow': [400, 600, 700], 'Barlow Condensed': [400, 600, 700, 800, 900],
    'Barlow Semi Condensed': [400, 600, 700], 'Saira Condensed': [400, 700, 900],
    'Saira Extra Condensed': [400, 700, 900], 'Fira Sans Condensed': [400, 700],
    'Fira Sans Extra Condensed': [400, 700, 800], 'Encode Sans Condensed': [400, 700, 800],
    'Big Shoulders Display': [400, 700, 900], 'Teko': [400, 600, 700],
    'Khand': [400, 600, 700], 'Pathway Gothic One': [400], 'Six Caps': [400],
    'Abel': [400], 'News Cycle': [400, 700],
    # --- geometric ----------------------------------------------------------
    'Poppins': [400, 600, 700, 900], 'Jost': [400, 600, 700],
    'Montserrat': [400, 700, 900], 'Raleway': [400, 700, 900],
    'League Spartan': [400, 700, 900], 'Outfit': [400, 700, 900],
    'Sora': [400, 700, 800], 'Urbanist': [400, 700, 900],
    'Lexend': [400, 700, 900], 'Questrial': [400], 'Josefin Sans': [400, 700],
    # --- heavy display ------------------------------------------------------
    'Alfa Slab One': [400], 'Bungee': [400], 'Titan One': [400],
    'Passion One': [400, 700, 900], 'Ultra': [400], 'Rubik Mono One': [400],
    'Black Ops One': [400], 'Bowlby One': [400], 'Lilita One': [400],
    'Shrikhand': [400], 'Righteous': [400], 'Bangers': [400],
    'Luckiest Guy': [400], 'Fredoka': [400, 600, 700],
    # --- slab ---------------------------------------------------------------
    'Roboto Slab': [400, 700, 900], 'Bitter': [400, 700, 900],
    'Zilla Slab': [400, 700], 'Arvo': [400, 700], 'Rokkitt': [400, 700, 900],
    'Crete Round': [400], 'Josefin Slab': [400, 700],
    # --- serif / didone / transitional --------------------------------------
    'Playfair Display': [400, 700, 900], 'Libre Bodoni': [400, 700],
    'Bodoni Moda': [400, 700, 900], 'Prata': [400], 'Cormorant': [400, 600, 700],
    'EB Garamond': [400, 600, 700], 'Libre Baskerville': [400, 700],
    'Lora': [400, 700], 'Source Serif 4': [400, 700, 900],
    'Crimson Pro': [400, 700], 'Spectral': [400, 700, 800],
    'Cardo': [400, 700], 'Gelasio': [400, 700], 'Tinos': [400, 700],
    'Noto Serif Display': [400, 700, 900],
    # --- display serif / period ---------------------------------------------
    'Abril Fatface': [400], 'Cinzel': [400, 700, 900], 'Italiana': [400],
    'Gilda Display': [400], 'Antic Didone': [400], 'Yeseva One': [400],
    'Rozha One': [400], 'Marcellus': [400], 'Sorts Mill Goudy': [400],
    'IM Fell English': [400], 'Playfair Display SC': [400, 700],
    # --- art nouveau / deco / period revival ---------------------------------
    'Limelight': [400], 'Monoton': [400], 'Poiret One': [400], 'Megrim': [400],
    'Della Respira': [400], 'Gruppo': [400], 'Federo': [400],
    'Julius Sans One': [400], 'Forum': [400], 'Cormorant Garamond': [400, 700],
    # --- blackletter ---------------------------------------------------------
    'UnifrakturMaguntia': [400], 'Pirata One': [400], 'Grenze Gotisch': [400, 700],
    'Eagle Lake': [400],
    # --- script / hand --------------------------------------------------------
    'Caveat': [400, 700], 'Permanent Marker': [400], 'Rock Salt': [400],
    'Shadows Into Light': [400], 'Amatic SC': [400, 700],
    'Special Elite': [400], 'Covered By Your Grace': [400],
    # --- mono / technical ------------------------------------------------------
    'JetBrains Mono': [400, 700], 'Space Mono': [400, 700],
    'IBM Plex Mono': [400, 600, 700], 'Courier Prime': [400, 700],
    'Share Tech Mono': [400], 'Major Mono Display': [400],
    # --- stencil / pixel --------------------------------------------------------
    'Stardos Stencil': [400, 700], 'Saira Stencil One': [400],
    'Press Start 2P': [400], 'Silkscreen': [400, 700], 'VT323': [400],
}

# Non-Google faces fetched straight from their source.
DIRECT = {
    'texgyreheros-regular.otf':   'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreheros-regular.otf',
    'texgyreheros-bold.otf':      'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreheros-bold.otf',
    'texgyreheroscn-regular.otf': 'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreheroscn-regular.otf',
    'texgyreheroscn-bold.otf':    'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreheroscn-bold.otf',
    'texgyretermes-regular.otf':  'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyretermes-regular.otf',
    'texgyretermes-bold.otf':     'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyretermes-bold.otf',
    'texgyrebonum-regular.otf':   'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyrebonum-regular.otf',
    'texgyrebonum-bold.otf':      'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyrebonum-bold.otf',
    'texgyrepagella-regular.otf': 'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyrepagella-regular.otf',
    'texgyrepagella-bold.otf':    'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyrepagella-bold.otf',
    'texgyreadventor-regular.otf': 'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreadventor-regular.otf',
    'texgyreadventor-bold.otf':   'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreadventor-bold.otf',
    'texgyreschola-regular.otf':  'https://mirrors.ctan.org/fonts/tex-gyre/opentype/texgyreschola-regular.otf',
    'NimbusSansNarrow-Bold.otf':  'https://raw.githubusercontent.com/ArtifexSoftware/urw-base35-fonts/master/fonts/NimbusSansNarrow-Bold.otf',
    'NimbusSans-Regular.otf':     'https://raw.githubusercontent.com/ArtifexSoftware/urw-base35-fonts/master/fonts/NimbusSans-Regular.otf',
    'NimbusSans-Bold.otf':        'https://raw.githubusercontent.com/ArtifexSoftware/urw-base35-fonts/master/fonts/NimbusSans-Bold.otf',
}


def slug(family):
    return family.replace(' ', '')


def fetch(url, ua=None):
    req = urllib.request.Request(url, headers={'User-Agent': ua or 'curl/8'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def google_family(family, weights):
    """-> [(weight, url)] of static WOFF files."""
    q = f"{urllib.parse.quote_plus(family)}:wght@{';'.join(str(w) for w in weights)}"
    css = fetch(f'https://fonts.googleapis.com/css2?family={q}', UA).decode()
    blocks = css.split('@font-face')
    out = []
    for b in blocks[1:]:
        w = re.search(r'font-weight:\s*(\d+)', b)
        u = re.search(r'url\((https://[^)]+)\)', b)
        if w and u:
            out.append((int(w.group(1)), u.group(1)))
    return out


def save_as_ttf(raw, path):
    """WOFF/OTF/TTF bytes -> a plain .ttf both fontTools and PIL can read."""
    import io
    from fontTools.ttLib import TTFont
    f = TTFont(io.BytesIO(raw))
    f.flavor = None
    f.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    total = sum(len(v) for v in GOOGLE.values()) + len(DIRECT)
    if args.list:
        print(f'{len(GOOGLE)} Google families, {total} faces incl. direct downloads')
        return

    os.makedirs(FONTS, exist_ok=True)
    have = set(os.listdir(FONTS))
    got = skipped = failed = 0

    for name, url in DIRECT.items():
        if name in have:
            skipped += 1
            continue
        try:
            open(os.path.join(FONTS, name), 'wb').write(fetch(url))
            got += 1
            print(f'  {name}')
        except Exception as e:
            failed += 1
            print(f'  FAILED {name}: {e}', file=sys.stderr)

    for family, weights in sorted(GOOGLE.items()):
        wanted = {w: f'{slug(family)}-{w}.ttf' for w in weights}
        if all(n in have for n in wanted.values()):
            skipped += len(wanted)
            continue
        try:
            served = google_family(family, weights)
        except Exception as e:
            failed += len(wanted)
            print(f'  FAILED {family}: {e}', file=sys.stderr)
            continue
        for weight, url in served:
            name = wanted.get(weight, f'{slug(family)}-{weight}.ttf')
            if name in have:
                skipped += 1
                continue
            try:
                save_as_ttf(fetch(url, UA), os.path.join(FONTS, name))
                got += 1
                print(f'  {name}')
            except Exception as e:
                failed += 1
                print(f'  FAILED {name}: {e}', file=sys.stderr)

    print(f'\n{got} fetched, {skipped} already present, {failed} failed')
    print(f'cache: {FONTS} ({len(os.listdir(FONTS))} files)')


if __name__ == '__main__':
    main()

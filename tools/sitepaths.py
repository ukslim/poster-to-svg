"""Where the poster site is, and where everything in it lives.

The tools work on a Jekyll site that holds the posters -- not on this repo. The
site is `$POSTER_SITE` if set; otherwise the nearest directory at or above the
current working directory that has `_data/events.yaml`. Run the tools from
anywhere inside the site, or point `POSTER_SITE` at it.

Paths that belong to the tool itself (the font cache, its index) are resolved
from the tool's own location instead, in the modules that use them.
"""
import os

_root = None


def root():
    global _root
    if _root:
        return _root
    env = os.environ.get('POSTER_SITE')
    if env:
        if not os.path.exists(os.path.join(env, '_data', 'events.yaml')):
            raise SystemExit(f'POSTER_SITE={env} has no _data/events.yaml')
        _root = os.path.abspath(env)
        return _root
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, '_data', 'events.yaml')):
            _root = d
            return d
        up = os.path.dirname(d)
        if up == d:
            raise SystemExit(
                "can't find the poster site: run from inside it (a directory "
                "with _data/events.yaml above the cwd) or set POSTER_SITE")
        d = up


def examples():
    return os.path.join(root(), 'assets', 'poster-examples')


def svgs():
    return os.path.join(root(), 'assets', 'poster-svg')


def solutions():
    return os.path.join(svgs(), 'solutions')


def events_yaml(repo=None):
    return os.path.join(repo or root(), '_data', 'events.yaml')


def prompt_template(repo=None):
    return os.path.join(repo or root(), '_includes', 'poster_prompts.html')

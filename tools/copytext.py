"""The poster's copy: events.yaml as the prompt template set it, in reading order."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sitepaths as poster_site  # noqa: E402


def _prompt_labels(event, repo=None):
    """{field: (before, after)} -- the words the prompt wraps round each field.

    events.yaml holds bare values; the prompt template adds labels as it
    builds the text the image model was given ("Tickets: " before the ticket
    prices, "Available from " before the ticket source). The poster prints
    what the prompt said, so the copy must too. Read from the template itself
    rather than repeated here, so the two cannot drift.
    """
    import re
    path = poster_site.prompt_template(repo)
    try:
        tpl = open(path, encoding='utf-8').read()
    except OSError:
        return {}
    out = {}
    pat = r'^(.*?)\{\{\s*site\.data\.events\.' + event + r'\.(\w+)\s*\}\}(.*?)$'
    for m in re.finditer(pat, tpl, re.M):
        before, after = (re.sub(r'\{%.*?%\}', '', g) for g in (m.group(1), m.group(3)))
        out.setdefault(m.group(2), (before, after))
    return out


def load_copy(event, repo=None):
    """The poster's text, in reading order: events.yaml as the prompt set it."""
    import yaml
    data = yaml.safe_load(open(poster_site.events_yaml(repo)))[event]
    labels = _prompt_labels(event, repo)

    def text(k):
        before, after = labels.get(k, ('', ''))
        return f'{before}{data[k]}{after}'

    if event == 'gig':
        keys = ['presenter', 'headliner', 'support', 'date', 'venue',
                'tickets', 'ticket_source', 'footer']
        return [{'key': k, 'text': text(k)} for k in keys if data.get(k)]
    # fete: fixed fields, then a variable-length attractions list.
    #
    # A fete poster does not simply print these strings. It heads the list with
    # a label of its own ("Attractions:"), and it breaks the footer into its
    # separate sentences, often with the beneficiary sitting between them. Both
    # are offered here as optional lines so the aligner can use them when the
    # poster does and skip them when it does not.
    out = [{'key': k, 'text': str(data[k])}
           for k in ('title', 'date', 'venue') if data.get(k)]
    if data.get('beneficiary'):
        out.append({'key': 'beneficiary', 'text': str(data['beneficiary']),
                    'optional': True})
    if data.get('attractions'):
        out.append({'key': 'attractions_label', 'text': 'Attractions:',
                    'optional': True})
    out += [{'key': f'attraction{i}', 'text': str(a), 'optional': True}
            for i, a in enumerate(data['attractions'])]
    for i, sentence in enumerate(_sentences(str(data.get('footer', '')))):
        out.append({'key': f'footer{i}', 'text': sentence, 'optional': True})
    return out


def _sentences(text):
    """Split a run-on footer into the lines a poster would actually set."""
    import re
    parts = [p.strip() for p in re.split(r'(?<=\.)\s+', text) if p.strip()]
    # "Free entry. All welcome." belongs together; a new clause starting with a
    # verb like "Organised by" is a separate line.
    out = []
    for p in parts:
        if out and len(p) < 26 and not re.match(r'^(Organised|Run|Hosted|In aid)', p):
            out[-1] = out[-1] + ' ' + p
        else:
            out.append(p)
    return out


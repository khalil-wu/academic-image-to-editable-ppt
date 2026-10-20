"""Check draw.io structure and optionally compare an editor roundtrip with its source."""

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes
import zlib

from lxml import etree as E


def inspect(path):
    pages = []
    for diagram in E.parse(str(path)).getroot().findall('diagram'):
        model = diagram.find('mxGraphModel')
        if model is None:
            model = E.fromstring(unquote(zlib.decompress(base64.b64decode(diagram.text), -15).decode()).encode())
        cells = model.findall('root/mxCell')
        ids = [c.get('id') for c in cells]
        assert len(ids) == len(set(ids)), f'Duplicate IDs in {diagram.get("name")}'
        for cell in cells:
            for key in ('parent', 'source', 'target'):
                assert cell.get(key) is None or cell.get(key) in ids, f'Dangling {key}: {cell.get("id")}'
        pictures = {}
        for cell in cells:
            style = cell.get('style', '')
            if 'image=' not in style:
                continue
            uri = style.split('image=', 1)[1].split(';', 1)[0]
            header, payload = uri.split(',', 1)
            raw = unquote_to_bytes(payload) if 'svg+xml' in header else base64.b64decode(payload)
            if 'svg+xml' in header:
                E.fromstring(raw)
            pictures[cell.get('id')] = sha256(raw).hexdigest()
        fonts = {}
        for entry in filter(None, model.get('extFonts', '').split('|')):
            name, uri = entry.split('^', 1)
            fonts[name] = sha256(base64.b64decode(uri.split(',', 1)[1])).hexdigest() if uri.startswith('data:') else uri
        labels = {c.get('id'): c.get('value') for c in cells if c.get('value')}
        references = {c.get('id'): [c.get(k) for k in ('parent', 'source', 'target')] for c in cells}
        edges = [c for c in cells if c.get('edge') == '1']
        pages.append({'name': diagram.get('name'), 'ids': sorted(ids), 'labels': labels,
                      'pictures': pictures, 'fonts': fonts, 'references': references, 'edges': len(edges),
                      'bound_edges': sum(bool(c.get('source') and c.get('target')) for c in edges)})
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    pages = inspect(args.source)
    if args.compare:
        other = inspect(args.compare)
        assert len(pages) == len(other), 'Page count changed during roundtrip'
        for original, saved in zip(pages, other):
            for key in ('name', 'ids', 'labels', 'pictures', 'fonts', 'references', 'edges', 'bound_edges'):
                assert original[key] == saved[key], f'Roundtrip changed {key} in {original["name"]}'
    report = {'source': args.source.name, 'pages': [{
        'name': p['name'], 'cells': len(p['ids']), 'text_objects': len(p['labels']),
        'images': len(p['pictures']), 'fonts': list(p['fonts']), 'edges': p['edges'],
        'bound_edges': p['bound_edges']} for p in pages],
        'unique_ids_and_valid_references': True,
        'roundtrip_preserved': bool(args.compare)}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(content, encoding='utf-8')
    print(content)


if __name__ == '__main__':
    main()

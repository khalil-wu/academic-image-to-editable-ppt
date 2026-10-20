"""Build editable draw.io from the same native scene and lossless assets used for PPTX."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--assets', type=Path)
    parser.add_argument('--embed-font', type=Path, action='append', default=[])
    parser.add_argument('--bind-nearby', action='store_true')
    args = parser.parse_args()
    scripts = Path(__file__).resolve().parent
    scene = json.loads(args.scene.read_text(encoding='utf-8'))

    def run(script, *items):
        subprocess.run([sys.executable, str(scripts / script), *map(str, items)], check=True)

    with TemporaryDirectory(prefix='editable-diagram-') as work:
        work = Path(work)
        prepared = work / 'layout.pptx'
        run('build_native_layout.py', args.scene.resolve(), prepared)
        if args.assets:
            run('insert_assets_pptx.py', prepared, args.assets.resolve(), work / 'assets.pptx')
            prepared = work / 'assets.pptx'
        if scene.get('module_groups'):
            run('group_and_connect.py', prepared, args.scene.resolve(), work / 'grouped.pptx')
            prepared = work / 'grouped.pptx'
        options = ['--page-names', scene.get('title', args.scene.stem)]
        for font in args.embed_font:
            options.extend(['--embed-font', font.resolve()])
        if args.bind_nearby:
            options.append('--bind-nearby')
        run('pptx_to_drawio.py', prepared, args.output.resolve(), *options)

    report_path = args.output.with_suffix('.conversion.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['intermediate_pptx_sha256'] = report.pop('source_sha256')
    report['source'] = args.scene.name
    report['source_format'] = 'scene-and-lossless-assets'
    report['source_sha256'] = sha256(args.scene.read_bytes()).hexdigest()
    if args.assets:
        report['asset_manifest'] = args.assets.name
        report['asset_manifest_sha256'] = sha256(args.assets.read_bytes()).hexdigest()
    report_path.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))


if __name__ == '__main__':
    main()

"""无损裁出素材；不描摹、不重绘、不量化、不放大。清除矩形仅用于去掉待原生重建的文字/屏幕内容。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--crop', nargs=4, type=int, required=True, metavar=('X', 'Y', 'W', 'H'))
    parser.add_argument('--clear-rect', nargs=4, type=int, action='append', default=[], metavar=('X', 'Y', 'W', 'H'), help='裁图内部的相对坐标，将对应矩形设透明')
    args = parser.parse_args()
    original = Image.open(args.source)
    x, y, w, h = args.crop
    image = original.crop((x, y, x + w, y + h)).convert('RGBA')
    for left, top, width, height in args.clear_rect:
        image.paste((255, 255, 255, 0), (left, top, left + width, top + height))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format='PNG')
    record = {'operation': 'lossless-crop', 'source_file': str(args.source.resolve()),
              'source_sha256': sha256(args.source.read_bytes()).hexdigest(), 'source_size_px': list(original.size),
              'crop_xywh': args.crop, 'clear_rects_relative_xywh': args.clear_rect,
              'output_size_px': list(image.size), 'output_sha256': sha256(args.output.read_bytes()).hexdigest(),
              'resampled': False, 'traced': False, 'palette_quantized': False}
    args.output.with_suffix('.source.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'asset': str(args.output), 'pixels': list(image.size), 'operation': record['operation']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

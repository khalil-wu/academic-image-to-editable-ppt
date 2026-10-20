"""比较位图文字与本地字体候选，提供最近视觉匹配而非确定的原字体识别。"""

import argparse
import json
from pathlib import Path

import cv2
from fontTools.ttLib import TTFont
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def ink_bounds(mask):
    yy, xx = np.nonzero(mask)
    return mask[yy.min():yy.max() + 1, xx.min():xx.max() + 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--crop', nargs=4, type=int, required=True, metavar=('X', 'Y', 'W', 'H'))
    parser.add_argument('--text', required=True)
    parser.add_argument('--fonts', nargs='+', required=True, type=Path)
    parser.add_argument('--threshold', type=int, default=190)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    x, y, w, h = args.crop
    image = Image.open(args.image).convert('RGBA')
    white = Image.new('RGBA', image.size, 'white'); white.alpha_composite(image)
    crop = white.convert('RGB').crop((x, y, x + w, y + h))
    gray = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2GRAY)
    mask = ink_bounds(gray < args.threshold)
    height, width = mask.shape
    candidates = []
    for filename in args.fonts:
        best = None
        for size in range(max(6, round(height * .95)), round(height * 1.9) + 1):
            font = ImageFont.truetype(str(filename), size)
            bbox = font.getbbox(args.text)
            sample = Image.new('L', (bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 8), 0)
            ImageDraw.Draw(sample).text((4 - bbox[0], 4 - bbox[1]), args.text, font=font, fill=255)
            rendered = ink_bounds(np.array(sample) > 95)
            rh, rw = rendered.shape
            resized = cv2.resize(rendered.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
            dice = 2 * np.count_nonzero(mask & resized) / (mask.sum() + resized.sum())
            natural_width = max(1, round(rw * height / rh))
            natural = cv2.resize(rendered.astype(np.uint8), (natural_width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
            aligned = np.zeros_like(mask)
            if natural_width <= width:
                left = (width - natural_width) // 2
                aligned[:, left:left + natural_width] = natural
            else:
                left = (natural_width - width) // 2
                aligned[:] = natural[:, left:left + width]
            natural_dice = 2 * np.count_nonzero(mask & aligned) / (mask.sum() + aligned.sum())
            aspect = min((rw / rh) / (width / height), (width / height) / (rw / rh))
            score = .55 * natural_dice + .35 * dice + .1 * aspect
            item = {'font_file': str(filename.resolve()), 'font_px': round(size * height / rh, 2),
                    'sample_font_px': size, 'rendered_ink_px': [natural_width, height],
                    'shape_similarity': round(float(dice), 4), 'natural_shape_similarity': round(float(natural_dice), 4),
                    'aspect_similarity': round(aspect, 4), 'score': round(float(score), 4),
                    'width_ratio_for_reference': round(width / natural_width, 4)}
            if best is None or item['score'] > best['score']:
                best = item
        font_data = TTFont(filename, fontNumber=0, lazy=True)
        names = font_data['name']
        best['family'] = names.getDebugName(1)
        best['style'] = names.getDebugName(2)
        best['full_name'] = names.getDebugName(4)
        font_data.close()
        candidates.append(best)
    candidates.sort(key=lambda item: item['score'], reverse=True)
    report = {'image': str(args.image.resolve()), 'crop_xywh': args.crop, 'text': args.text,
              'reference_ink_px': [width, height], 'method': 'visual-nearest-match-not-source-metadata',
              'candidates': candidates}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'text': args.text, 'best': candidates[:3], 'report': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

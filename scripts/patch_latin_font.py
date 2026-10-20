"""仅替换整个 PPTX 的西文字体槽，并验证其他节点未改变。"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree as ET

DRAWINGML = 'http://schemas.openxmlformats.org/drawingml/2006/main'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--font', default='Times New Roman')
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('输出另存，不覆盖最新源稿。')
    source_hash = sha256(args.source.read_bytes()).hexdigest()
    # 支持标准 a 前缀；字体名由 XML 编码，避免将引号拼进属性。
    escaped_font = args.font.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').encode('utf-8')
    pattern = re.compile(rb'(<a:latin\b[^>]*\btypeface=")[^"]*(")')
    changed = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.source) as source, ZipFile(args.output, 'w') as target:
        for part in source.infolist():
            data = source.read(part.filename)
            updated = data
            if part.filename.startswith('ppt/') and part.filename.endswith('.xml'):
                updated = pattern.sub(lambda match: match.group(1) + escaped_font + match.group(2), data)
                if updated != data:
                    before = ET.fromstring(data)
                    after = ET.fromstring(updated)
                    old_fonts = list(before.iter(f'{{{DRAWINGML}}}latin'))
                    new_fonts = list(after.iter(f'{{{DRAWINGML}}}latin'))
                    changed[part.filename] = dict(Counter(font.get('typeface') for font in old_fonts if font.get('typeface') != args.font))
                    for old, new in zip(old_fonts, new_fonts):
                        assert new.get('typeface') == args.font
                        new.set('typeface', old.get('typeface'))
                    assert ET.tostring(before) == ET.tostring(after)
            target.writestr(part, updated)
    assert sha256(args.source.read_bytes()).hexdigest() == source_hash
    with ZipFile(args.output) as check:
        actual = Counter()
        for name in check.namelist():
            if name.startswith('ppt/') and name.endswith('.xml'):
                actual.update(font.get('typeface') for font in ET.fromstring(check.read(name)).iter(f'{{{DRAWINGML}}}latin'))
        if set(actual) - {args.font}:
            raise ValueError(f'仍有未替换的字体：{dict(actual)}；此文件可能使用非标准命名空间前缀，需按命名空间处理。')
    report = {'source': str(args.source.resolve()), 'source_sha256': source_hash,
              'output': str(args.output.resolve()), 'font': args.font,
              'changed_fields': changed, 'font_declarations': dict(actual),
              'changed_only': 'a:latin/@typeface', 'source_unchanged': True}
    args.output.with_suffix('.font-change.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pptx': str(args.output), 'font': args.font,
                      'changed_fields': sum(sum(count.values()) for count in changed.values())}, ensure_ascii=False))


if __name__ == '__main__':
    main()

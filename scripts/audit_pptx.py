"""核对原生文字、独立高清素材、禁止描摹的来源记录及真实连接器引用。"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import posixpath
import re
from zipfile import ZipFile

from lxml import etree as ET

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def normalized(text):
    return re.sub(r'\s+', '', text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pptx', type=Path)
    parser.add_argument('--expect', type=Path)
    parser.add_argument('--assets-report', type=Path)
    parser.add_argument('--profile', choices=['components', 'full', 'hybrid'], default='components')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    expect = json.loads(args.expect.read_text(encoding='utf-8')) if args.expect else {}
    assets = json.loads(args.assets_report.read_text(encoding='utf-8')) if args.assets_report else None
    issues, slides, all_texts, all_groups, all_connections = [], [], Counter(), {}, {}
    if expect.get('asset_mode') == 'generated-original' and assets is None:
        issues.append('任务要求API原创，需提供 --assets-report 核对实际生成素材。')
    all_pictures = {}
    totals = Counter()
    with ZipFile(args.pptx) as z:
        presentation = ET.fromstring(z.read('ppt/presentation.xml'))
        relations = {rel.get('Id'): rel.get('Target') for rel in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))}
        targets = [relations[node.get(f'{{{NS["r"]}}}id')] for node in presentation.findall('p:sldIdLst/p:sldId', NS)]
        order = [target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target)) for target in targets]
        used_media = set()
        for index, part in enumerate(order, start=1):
            root = ET.fromstring(z.read(part))
            properties = root.xpath('//p:cNvPr', namespaces=NS)
            ids = [node.get('id') for node in properties]
            names = {node.get('id'): node.get('name') for node in properties}
            duplicates = [key for key, count in Counter(ids).items() if count > 1]
            if duplicates:
                issues.append(f'第 {index} 页重复形状 ID：{duplicates}')
            slide_texts = []
            for shape in root.xpath('//p:sp[p:txBody]', namespaces=NS):
                paragraphs = shape.findall('p:txBody/a:p', NS)
                text = '\n'.join(''.join(paragraph.xpath('.//a:t/text()', namespaces=NS)) for paragraph in paragraphs)
                if text.strip():
                    slide_texts.append(text)
                    all_texts[normalized(text)] += 1
            groups = {}
            for group in root.xpath('//p:grpSp', namespaces=NS):
                name = group.find('p:nvGrpSpPr/p:cNvPr', NS).get('name')
                groups[name] = {'native_paths': len(group.xpath('.//p:sp/p:spPr/a:custGeom', namespaces=NS)),
                                'pictures': len(group.xpath('.//p:pic', namespaces=NS))}
            all_groups.update({(index, name): info for name, info in groups.items()})
            connections = []
            for connector in root.xpath('//p:cxnSp', namespaces=NS):
                nv = connector.find('p:nvCxnSpPr', NS)
                name = nv.find('p:cNvPr', NS).get('name')
                start = nv.find('p:cNvCxnSpPr/a:stCxn', NS)
                end = nv.find('p:cNvCxnSpPr/a:endCxn', NS)
                for endpoint in (start, end):
                    if endpoint is not None and endpoint.get('id') not in names:
                        issues.append(f'第 {index} 页 {name} 引用了不存在的形状 {endpoint.get("id")}')
                record = {'name': name, 'from': names.get(start.get('id')) if start is not None else None,
                          'to': names.get(end.get('id')) if end is not None else None}
                connections.append(record)
                all_connections[(index, name)] = record

            rel_name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
            local_rels = {rel.get('Id'): {'target': rel.get('Target'), 'mode': rel.get('TargetMode')}
                          for rel in ET.fromstring(z.read(rel_name))} if rel_name in z.namelist() else {}
            images = []
            for blip in root.xpath('//a:blip', namespaces=NS):
                rid = blip.get(f'{{{NS["r"]}}}embed') or blip.get(f'{{{NS["r"]}}}link')
                relation = local_rels.get(rid, {})
                target = relation.get('target', '')
                path = target if relation.get('mode') == 'External' else (target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target)))
                images.append({'relationship': rid, 'target': path, 'external': relation.get('mode') == 'External'})
                used_media.add(path)
            for picture in root.xpath('//p:pic', namespaces=NS):
                name = picture.find('p:nvPicPr/p:cNvPr', NS).get('name')
                rid = picture.find('p:blipFill/a:blip', NS).get(f'{{{NS["r"]}}}embed')
                target = local_rels[rid]['target']
                media = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                all_pictures[(index, name)] = media
            counts = {'native_shapes': len(root.xpath('//p:sp', namespaces=NS)),
                      'text_boxes': len(slide_texts),
                      'native_freeforms': len(root.xpath('//p:sp/p:spPr/a:custGeom', namespaces=NS)),
                      'groups': len(groups), 'connectors': len(connections),
                      'unbound_connectors': sum(not c['from'] or not c['to'] for c in connections),
                      'pictures': len(root.xpath('//p:pic', namespaces=NS)), 'image_uses': len(images),
                      'ole_objects': len(root.xpath('//*[local-name()="oleObj"]')),
                      'content_parts': len(root.xpath('//p:contentPart', namespaces=NS))}
            totals.update(counts)
            if args.profile == 'full' and any(counts[key] for key in ('pictures', 'image_uses', 'ole_objects', 'content_parts')):
                issues.append(f'第 {index} 页仍含图片、图片填充或非原生外部对象，不能声明全部可编辑。')
            slides.append({'slide': index, 'part': part, 'counts': counts, 'texts': slide_texts,
                           'groups': groups, 'connections': connections, 'images': images,
                           'latin_font_declarations': dict(Counter(root.xpath('//a:latin/@typeface', namespaces=NS))),
                           'east_asian_font_declarations': dict(Counter(root.xpath('//a:ea/@typeface', namespaces=NS)))})
        for text, count in Counter(normalized(text) for text in expect.get('expected_texts', [])).items():
            if all_texts[text] < count:
                issues.append(f'原生文本缺失或出现次数不足：{text}')
        for name in expect.get('expected_groups', []):
            if not any(group_name == name for _, group_name in all_groups):
                issues.append(f'缺少原生组：{name}')
        if args.profile == 'components' and all_pictures and assets is None:
            issues.append('组件编辑模式需要 --assets-report，不能把整图贴底当成分元素重建。')
        if assets:
            described = set()
            for asset in assets['assets']:
                key = (assets['slide'], asset['name']);described.add(key)
                media = all_pictures.get(key)
                if media is None or sha256(z.read(media)).hexdigest() != asset['embedded_sha256']:
                    issues.append(f'素材未按原始文件嵌入：{asset["name"]}')
                if expect.get('asset_policy') == 'no-tracing':
                    if asset.get('source_type') not in ('source-crop', 'api-generated-original', 'licensed-original'):
                        issues.append(f'素材来源不符合禁止描摹要求：{asset["name"]}')
                    if asset.get('crop_record'):
                        record = json.loads(Path(asset['crop_record']).read_text(encoding='utf-8'))
                        if record['traced'] or record['resampled'] or record['palette_quantized'] or record['output_sha256'] != asset['embedded_sha256']:
                            issues.append(f'素材被描摹、重采样或降质处理：{asset["name"]}')
                minimum_ppi = expect.get('quality', {}).get('min_ppi')
                if minimum_ppi and asset['effective_ppi'] < minimum_ppi:
                    issues.append(f'素材有效分辨率低于场景要求：{asset["name"]}')
                if asset.get('source_type') == 'api-generated-original':
                    record_path = Path(asset['generation_record'])
                    record = json.loads(record_path.read_text(encoding='utf-8'))
                    if not record.get('api_call_completed') or record.get('model') != expect.get('image_model', record.get('model')):
                        issues.append(f'缺少真实生成记录或模型不符：{asset["name"]}')
                    for output in record['outputs']:
                        for file_key, hash_key in [('file', 'sha256'), ('prompt_file', 'prompt_sha256')]:
                            if sha256((record_path.parent / output[file_key]).read_bytes()).hexdigest() != output[hash_key]:
                                issues.append(f'生成原图或提示词不匹配：{asset["name"]}')
            if set(all_pictures) != described:
                issues.append('独立图片对象与素材清单不完整对应。')
        for connection in expect.get('expected_connections', []):
            actual = all_connections.get((connection.get('slide', 1), connection['name']))
            if actual is None or actual['from'] != connection['from'] or actual['to'] != connection['to']:
                issues.append(f'连接器关系不匹配：{connection["name"]}')
        unused_media = sorted(set(name for name in z.namelist() if name.startswith('ppt/media/')) - used_media)
    report = {'pptx': str(args.pptx.resolve()), 'profile': args.profile,
              'passed': not issues, 'issues': issues, 'totals': dict(totals),
              'unused_media_not_a_failure': unused_media, 'slides': slides,
              'manual_checks_still_required': ['科研逻辑与逐字校对', '实际字体与渲染', '文字溢出与走线', '原生对象编辑及连接器移动行为']}
    output = args.output or args.pptx.with_suffix('.audit.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'totals': report['totals'], 'issues': issues, 'report': str(output)}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

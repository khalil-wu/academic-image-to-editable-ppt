"""将高清素材作为独立图片对象加入 PPT；保留原始像素与透明度，不进行路径描摹。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import posixpath
from zipfile import ZipFile

from lxml import etree as E
from PIL import Image

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def add(parent, tag, **attrs):
    prefix, local = tag.split(':')
    return E.SubElement(parent, f'{{{NS[prefix]}}}{local}', **{key: str(value) for key, value in attrs.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.manifest.read_text(encoding='utf-8'))
    report = {'slide': plan.get('slide', 1), 'asset_policy': 'no-tracing', 'assets': []}
    with ZipFile(args.source) as source:
        presentation = E.fromstring(source.read('ppt/presentation.xml'))
        selected = presentation.findall('p:sldIdLst/p:sldId', NS)[plan.get('slide', 1) - 1]
        relations = {rel.get('Id'): rel.get('Target') for rel in E.fromstring(source.read('ppt/_rels/presentation.xml.rels'))}
        target = relations[selected.get(f'{{{NS["r"]}}}id')]
        part = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target))
        root = E.fromstring(source.read(part));tree = root.find('p:cSld/p:spTree', NS)
        relpart = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
        rels = E.fromstring(source.read(relpart))
        content_types = E.fromstring(source.read('[Content_Types].xml'))
        extensions = {node.get('Extension') for node in content_types}
        next_id = max(map(int, root.xpath('//p:cNvPr/@id', namespaces=NS))) + 1
        names = set(root.xpath('//p:cNvPr/@name', namespaces=NS))
        next_rel = max([int(rel.get('Id')[3:]) for rel in rels if rel.get('Id').startswith('rId')] + [0]) + 1
        media, media_by_hash = {}, {}
        for asset in plan['assets']:
            if asset['name'] in names:
                raise ValueError(f'对象已存在，请另存并定位替换：{asset["name"]}')
            file = args.manifest.parent / asset['file']
            raw = file.read_bytes();digest = sha256(raw).hexdigest()
            with Image.open(file) as image:
                iw, ih = image.size;image_format = image.format
            suffix, mime = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg')}[image_format]
            if suffix not in extensions:
                E.SubElement(content_types, f'{{{CT}}}Default', Extension=suffix, ContentType=mime);extensions.add(suffix)
            if digest not in media_by_hash:
                path = 'ppt/media/asset_' + digest[:24] + '.' + suffix
                media[path] = raw;media_by_hash[digest] = path
            path = media_by_hash[digest]
            rid = 'rId' + str(next_rel);next_rel += 1
            E.SubElement(rels, f'{{{REL}}}Relationship', Id=rid, Type=NS['r'] + '/image', Target=posixpath.relpath(path, posixpath.dirname(part)))
            pic = E.Element(f'{{{NS["p"]}}}pic')
            nv = add(pic, 'p:nvPicPr');add(nv, 'p:cNvPr', id=next_id, name=asset['name'], descr=asset.get('source_type', 'asset'))
            next_id += 1
            add(add(nv, 'p:cNvPicPr'), 'a:picLocks', noChangeAspect=1);add(nv, 'p:nvPr')
            fill = add(pic, 'p:blipFill');blip = add(fill, 'a:blip');blip.set(f'{{{NS["r"]}}}embed', rid)
            add(add(fill, 'a:stretch'), 'a:fillRect')
            x, y, w, h = asset['box_pt'];ratio = min(w / iw, h / ih)
            rw, rh = iw * ratio, ih * ratio;x += (w - rw) / 2;y += (h - rh) / 2
            props = add(pic, 'p:spPr');xf = add(props, 'a:xfrm')
            add(xf, 'a:off', x=round(x * 12700), y=round(y * 12700));add(xf, 'a:ext', cx=round(rw * 12700), cy=round(rh * 12700))
            add(add(props, 'a:prstGeom', prst='rect'), 'a:avLst')
            first_text = next((node for node in tree if node.find('p:txBody', NS) is not None), None) if plan.get('before_text', True) else None
            ext = tree.find('p:extLst', NS)
            index = tree.index(first_text) if first_text is not None else tree.index(ext) if ext is not None else len(tree)
            tree.insert(index, pic);names.add(asset['name'])
            record = dict(asset, file=str(file.resolve()), media=path, embedded_sha256=digest,
                          pixels=[iw, ih], actual_box_pt=[x, y, rw, rh], effective_ppi=round(72 / ratio, 1), editability='independent-image-object')
            for field in ['crop_record', 'generation_record']:
                if asset.get(field):
                    record[field] = str((args.manifest.parent / asset[field]).resolve())
            report['assets'].append(record)
        xml = lambda node: E.tostring(node, encoding='UTF-8', xml_declaration=True, standalone=True)
        replacements = {part: xml(root), relpart: xml(rels), '[Content_Types].xml': xml(content_types)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.output, 'w') as target:
            for member in source.infolist():
                target.writestr(member, replacements.get(member.filename, source.read(member.filename)))
            for name, raw in media.items():
                target.writestr(name, raw)
    args.output.with_suffix('.assets.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pptx': str(args.output), 'independent_images': len(report['assets']), 'pixels_preserved': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()

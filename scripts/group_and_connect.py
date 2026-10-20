"""按场景分组模块，并把直线连接器绑定到随模块移动的真实锚点。"""

import argparse
import json
from pathlib import Path
import posixpath
from zipfile import ZipFile

from lxml import etree as E

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def add(parent, tag, **attrs):
    prefix, local = tag.split(':')
    return E.SubElement(parent, f'{{{NS[prefix]}}}{local}', **{key: str(value) for key, value in attrs.items()})


def geometry(element):
    transform = element.find('p:grpSpPr/a:xfrm', NS) if element.tag == f'{{{NS["p"]}}}grpSp' else element.find('p:spPr/a:xfrm', NS)
    if transform is None:
        return None
    off, ext = transform.find('a:off', NS), transform.find('a:ext', NS)
    return [int(off.get('x')), int(off.get('y')), int(ext.get('cx')), int(ext.get('cy'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('分组稿请另存，保留输入原稿。')
    scene = json.loads(args.scene.read_text(encoding='utf-8'))
    canvas = scene['canvas']
    unit = (canvas.get('width_pt', canvas['width']) / canvas['width'] if canvas.get('unit') == 'px' else 1) * 12700
    report = {'groups': [], 'connections': []}
    with ZipFile(args.source) as source:
        presentation = E.fromstring(source.read('ppt/presentation.xml'))
        selected = presentation.findall('p:sldIdLst/p:sldId', NS)[scene.get('slide', 1) - 1]
        rels = {rel.get('Id'): rel.get('Target') for rel in E.fromstring(source.read('ppt/_rels/presentation.xml.rels'))}
        target = rels[selected.get(f'{{{NS["r"]}}}id')]
        part = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target))
        root = E.fromstring(source.read(part))
        tree = root.find('p:cSld/p:spTree', NS)
        next_id = max(map(int, root.xpath('//p:cNvPr/@id', namespaces=NS))) + 1
        groups = {}
        existing_names = set(root.xpath('//p:cNvPr/@name', namespaces=NS))
        for spec in scene.get('module_groups', []):
            if spec['name'] in existing_names:
                raise ValueError(f'模块组已存在：{spec["name"]}')
            x, y, w, h = [round(value * unit) for value in spec['box']]
            pad = spec.get('tolerance', 5) * unit
            children = []
            for child in list(tree)[2:]:
                box = geometry(child)
                if box is None:
                    continue
                cx, cy, cw, ch = box
                if cx >= x - pad and cy >= y - pad and cx + cw <= x + w + pad and cy + ch <= y + h + pad:
                    children.append(child)
            if not children:
                raise ValueError(f'模块区域没有可分组对象：{spec["name"]}')
            insert_at = min(tree.index(child) for child in children)
            group = E.Element(f'{{{NS["p"]}}}grpSp', nsmap={'p': NS['p'], 'a': NS['a']})
            nv = add(group, 'p:nvGrpSpPr')
            add(nv, 'p:cNvPr', id=next_id, name=spec['name']);next_id += 1
            add(nv, 'p:cNvGrpSpPr');add(nv, 'p:nvPr')
            xf = add(add(group, 'p:grpSpPr'), 'a:xfrm')
            add(xf, 'a:off', x=x, y=y);add(xf, 'a:ext', cx=w, cy=h)
            add(xf, 'a:chOff', x=x, y=y);add(xf, 'a:chExt', cx=w, cy=h)
            for child in children:
                tree.remove(child);group.append(child)
            tree.insert(insert_at, group)
            groups[spec['name']] = group
            existing_names.add(spec['name'])
            report['groups'].append({'name': spec['name'], 'children': len(children)})

        def port(group_name, name, x, y):
            nonlocal next_id
            shape_id = next_id;next_id += 1
            shape = add(groups[group_name], 'p:sp')
            nv = add(shape, 'p:nvSpPr');add(nv, 'p:cNvPr', id=shape_id, name=name)
            add(nv, 'p:cNvSpPr');add(nv, 'p:nvPr')
            props = add(shape, 'p:spPr');xf = add(props, 'a:xfrm')
            add(xf, 'a:off', x=x - 64, y=y);add(xf, 'a:ext', cx=128, cy=128)
            add(add(props, 'a:prstGeom', prst='rect'), 'a:avLst')
            add(props, 'a:noFill');add(add(props, 'a:ln'), 'a:noFill');add(props, 'a:effectLst')
            return shape_id

        for spec in scene.get('bind_connections', []):
            found = root.xpath('//p:cxnSp[p:nvCxnSpPr/p:cNvPr[@name=$name]]', namespaces=NS, name=spec['name'])
            connector = found[0]
            geom = connector.find('p:spPr/a:prstGeom', NS)
            if geom is None or geom.get('prst') != 'line':
                raise ValueError('该工具绑定直线；复杂曲线须用目标Office应用的原生连接器验证。')
            xf = connector.find('p:spPr/a:xfrm', NS)
            if xf.get('rot', '0') != '0':
                raise ValueError('旋转连接器请先转换为实际端点坐标。')
            x, y, w, h = geometry(connector)
            sx, ex = (x + w, x) if xf.get('flipH') in ('1', 'true') else (x, x + w)
            sy, ey = (y + h, y) if xf.get('flipV') in ('1', 'true') else (y, y + h)
            props = connector.find('p:nvCxnSpPr/p:cNvCxnSpPr', NS)
            for old in list(props):
                if E.QName(old).localname in ('stCxn', 'endCxn'):
                    props.remove(old)
            from_name = 'PORT_' + spec['name'] + '_from';to_name = 'PORT_' + spec['name'] + '_to'
            start = port(spec['from'], from_name, sx, sy)
            end = port(spec['to'], to_name, ex, ey)
            add(props, 'a:stCxn', id=start, idx=0);add(props, 'a:endCxn', id=end, idx=0)
            report['connections'].append({'name': spec['name'], 'from': from_name, 'to': to_name})
        data = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.output, 'w') as output:
            for member in source.infolist():
                output.writestr(member, data if member.filename == part else source.read(member.filename))
    args.output.with_suffix('.bindings.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pptx': str(args.output), 'module_groups': len(report['groups']), 'bound_connections': len(report['connections'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""从像素或点坐标场景生成单页原生底稿；不用于覆盖用户已手调的幻灯片。"""

import argparse
import json
from pathlib import Path
import posixpath
from zipfile import ZipFile

from lxml import etree as E

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def add(parent, tag, **attributes):
    prefix, name = tag.split(':')
    return E.SubElement(parent, f'{{{NS[prefix]}}}{name}', **{key: str(value) for key, value in attributes.items()})


def color(parent, value, alpha=1):
    item = add(parent, 'a:srgbClr', val=value.lstrip('#'))
    if alpha != 1:
        add(item, 'a:alpha', val=round(alpha * 100000))


def fill(parent, style):
    if style.get('gradient'):
        gradient = add(parent, 'a:gradFill', rotWithShape=1)
        stops = add(gradient, 'a:gsLst')
        colors = style['gradient']
        for index, value in enumerate(colors):
            color(add(stops, 'a:gs', pos=round(index * 100000 / (len(colors) - 1))), value, style.get('opacity', 1))
        add(gradient, 'a:lin', ang=round(style.get('gradient_angle', 90) * 60000), scaled=1)
    elif style.get('fill'):
        color(add(parent, 'a:solidFill'), style['fill'], style.get('opacity', 1))
    else:
        add(parent, 'a:noFill')


def run_style(parent, style, scale, tag='a:rPr'):
    properties = add(parent, tag, sz=round(style.get('font_size', 16) * scale * 100),
                     b=1 if style.get('bold') else 0, i=1 if style.get('italic') else 0,
                     spc=round(style.get('char_spacing', 0) * scale * 100), lang=style.get('lang', 'en-US'))
    color(add(properties, 'a:solidFill'), style.get('color', '#10102E'))
    add(properties, 'a:latin', typeface=style.get('font', 'Arial'))
    add(properties, 'a:ea', typeface=style.get('font_ea', '微软雅黑'))
    return properties


def bounds(item):
    if 'box' in item:
        return item['box']
    points = [command[index:index + 2] for command in item['commands'] for index in range(1, len(command), 2)]
    xs, ys = zip(*points)
    return [min(xs), min(ys), max(max(xs) - min(xs), .001), max(max(ys) - min(ys), .001)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--template', type=Path, default=Path(__file__).parent.parent / 'assets' / 'blank.pptx')
    args = parser.parse_args()
    plan = json.loads(args.scene.read_text(encoding='utf-8'))
    canvas = plan['canvas']
    scale = canvas.get('width_pt', canvas['width']) / canvas['width'] if canvas.get('unit', 'pt') == 'px' else 1
    elements = plan['elements']
    ids = {item['id']: index + 2 for index, item in enumerate(elements)}
    if len(ids) != len(elements):
        raise ValueError('场景元素 ID 必须唯一。')
    if args.template.resolve() == args.output.resolve():
        raise ValueError('输出须另存，不能覆盖模板。')
    with ZipFile(args.template) as source:
        presentation = E.fromstring(source.read('ppt/presentation.xml'))
        slides = presentation.findall('p:sldIdLst/p:sldId', NS)
        if len(slides) != 1:
            raise ValueError('该布局生成器使用单页模板，多页任务请先导出单页底稿。')
        relations = {rel.get('Id'): rel.get('Target') for rel in E.fromstring(source.read('ppt/_rels/presentation.xml.rels'))}
        target = relations[slides[0].get(f'{{{NS["r"]}}}id')]
        part_name = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target))
        root = E.fromstring(source.read(part_name))
        tree = root.find('p:cSld/p:spTree', NS)
        for child in list(tree)[2:]:
            tree.remove(child)
        root.find('p:cSld', NS).set('name', plan.get('title', 'Image reconstruction'))
        size = presentation.find('p:sldSz', NS)
        size.set('cx', str(round(canvas['width'] * scale * 12700)))
        size.set('cy', str(round(canvas['height'] * scale * 12700)))
        size.set('type', 'custom')
        for _, item in sorted(enumerate(elements), key=lambda pair: (pair[1].get('z', pair[0]), pair[0])):
            kind = item['type']
            style = item.get('style', {})
            if kind not in ('rect', 'ellipse', 'text', 'path', 'connector'):
                raise ValueError(f'不支持的布局对象：{kind}')
            native_connector = kind == 'connector' and len(item['commands']) == 2 and item['commands'][1][0] == 'L'
            if kind == 'connector' and not native_connector and (item.get('from') or item.get('to')):
                raise ValueError('自定义多段／曲线箭头应作为原生自由曲线，或由 Office 连接器工具绑定；不能伪造实时连接。')
            shape = add(tree, 'p:cxnSp' if native_connector else 'p:sp')
            nv = add(shape, 'p:nvCxnSpPr' if native_connector else 'p:nvSpPr')
            add(nv, 'p:cNvPr', id=ids[item['id']], name=item['id'])
            spec = add(nv, 'p:cNvCxnSpPr' if native_connector else 'p:cNvSpPr', **({'txBox': 1} if kind == 'text' else {}))
            if native_connector:
                for key, tag, site in [('from', 'a:stCxn', 3), ('to', 'a:endCxn', 1)]:
                    if item.get(key):
                        add(spec, tag, id=ids[item[key]], idx=item.get(key + '_site', site))
            add(nv, 'p:nvPr')
            props = add(shape, 'p:spPr')
            x, y, w, h = bounds(item)
            xf = add(props, 'a:xfrm')
            if style.get('rotation'):
                xf.set('rot', str(round(style['rotation'] * 60000)))
            add(xf, 'a:off', x=round(x * scale * 12700), y=round(y * scale * 12700))
            add(xf, 'a:ext', cx=max(1, round(w * scale * 12700)), cy=max(1, round(h * scale * 12700)))
            if native_connector:
                start, end = item['commands'][0][1:], item['commands'][1][1:]
                if start[0] > end[0]:
                    xf.set('flipH', '1')
                if start[1] > end[1]:
                    xf.set('flipV', '1')
                add(add(props, 'a:prstGeom', prst='line'), 'a:avLst')
            elif kind in ('path', 'connector'):
                geom = add(props, 'a:custGeom')
                for tag in ('avLst', 'gdLst', 'ahLst', 'cxnLst'):
                    add(geom, 'a:' + tag)
                add(geom, 'a:rect', l='0', t='0', r='r', b='b')
                path = add(add(geom, 'a:pathLst'), 'a:path', w=max(1, round(w * 1000)), h=max(1, round(h * 1000)),
                           fill='norm' if style.get('fill') or style.get('gradient') else 'none', stroke='1' if style.get('stroke') else '0')
                for command in item['commands']:
                    if command[0] == 'Z':
                        add(path, 'a:close')
                    else:
                        step = add(path, {'M': 'a:moveTo', 'L': 'a:lnTo', 'C': 'a:cubicBezTo'}[command[0]])
                        for px, py in zip(command[1::2], command[2::2]):
                            add(step, 'a:pt', x=round((px - x) * 1000), y=round((py - y) * 1000))
            else:
                radius = style.get('radius', 0)
                geom = add(props, 'a:prstGeom', prst='ellipse' if kind == 'ellipse' else ('roundRect' if radius else 'rect'))
                av = add(geom, 'a:avLst')
                if radius:
                    add(av, 'a:gd', name='adj', fmla=f'val {round(min(.5, radius / min(w, h)) * 100000)}')
            fill(props, {} if kind == 'text' else style)
            line = add(props, 'a:ln', w=round(style.get('stroke_width', 1) * scale * 12700), cap='rnd')
            if style.get('stroke'):
                color(add(line, 'a:solidFill'), style['stroke'], style.get('stroke_opacity', 1))
                if style.get('dash'):
                    add(line, 'a:prstDash', val='dash')
                add(line, 'a:round')
                if style.get('arrow'):
                    add(line, 'a:tailEnd', type='triangle', w=style.get('arrow_size', 'med'), len=style.get('arrow_size', 'med'))
                if style.get('start_arrow'):
                    add(line, 'a:headEnd', type='triangle', w='med', len='med')
            else:
                add(line, 'a:noFill')
            if kind == 'text':
                body = add(shape, 'p:txBody')
                body_props = add(body, 'a:bodyPr', wrap='square' if style.get('wrap') else 'none',
                                 lIns=0, tIns=0, rIns=0, bIns=0, anchor=style.get('anchor', 'ctr'))
                add(body_props, 'a:noAutofit')
                add(body, 'a:lstStyle')
                for text in item['text'].split('\n'):
                    para = add(body, 'a:p')
                    pp = add(para, 'a:pPr', algn={'left': 'l', 'center': 'ctr', 'right': 'r'}[style.get('align', 'left')])
                    if style.get('line_height'):
                        add(add(pp, 'a:lnSpc'), 'a:spcPts', val=round(style['line_height'] * scale * 100))
                    else:
                        add(add(pp, 'a:lnSpc'), 'a:spcPct', val=100000)
                    add(add(pp, 'a:spcBef'), 'a:spcPts', val=0)
                    add(add(pp, 'a:spcAft'), 'a:spcPts', val=0)
                    run_style(pp, style, scale, 'a:defRPr')
                    run = add(para, 'a:r')
                    run_style(run, style, scale)
                    add(run, 'a:t').text = text
                    run_style(para, style, scale, 'a:endParaRPr')
        replacements = {part_name: E.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True),
                        'ppt/presentation.xml': E.tostring(presentation, encoding='UTF-8', xml_declaration=True, standalone=True)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.output, 'w') as output:
            for member in source.infolist():
                output.writestr(member, replacements.get(member.filename, source.read(member.filename)))
    print(json.dumps({'pptx': str(args.output), 'native_elements': len(elements), 'scale_pt_per_input_unit': scale}, ensure_ascii=False))


if __name__ == '__main__':
    main()

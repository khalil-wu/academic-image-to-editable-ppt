"""Translate existing PPTX objects to editable draw.io pages without tracing raster artwork."""

import argparse
import base64
from collections import Counter
from hashlib import sha256
import html
from io import BytesIO
import json
import math
from pathlib import Path
import posixpath
import re
from urllib.parse import quote
from zipfile import ZipFile
import zlib

from lxml import etree as E
from PIL import Image
from fontTools.ttLib import TTFont

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
EMU = 12700


def number(value):
    return f'{value:.5f}'.rstrip('0').rstrip('.') if value else '0'


def node(container, tag, **attrs):
    return E.SubElement(container, tag, **{key: str(value) for key, value in attrs.items()})


def properties(element):
    return element.find('p:spPr', NS)


def identity(element):
    entry = element.find('.//p:cNvPr', NS)
    return entry.get('id'), entry.get('name', entry.get('id'))


def color(element, theme, default='none'):
    if element is None:
        return default, 1
    item = next((entry for entry in element if E.QName(entry).localname in ('srgbClr', 'schemeClr', 'sysClr', 'prstClr')), None)
    if item is None:
        return default, 1
    kind = E.QName(item).localname
    value = item.get('val') if kind == 'srgbClr' else theme.get(item.get('val'), item.get('lastClr', '000000'))
    alpha = item.find('a:alpha', NS)
    return '#' + value, int(alpha.get('val')) / 100000 if alpha is not None else 1


def paint(props, theme):
    if props is None:
        return {'fillColor': 'none', 'strokeColor': 'none'}
    fill, opacity = color(props.find('a:solidFill', NS), theme)
    style = {'fillColor': fill, 'fillOpacity': number(opacity * 100)}
    gradient = props.find('a:gradFill/a:gsLst', NS)
    if gradient is not None:
        style['fillColor'], _ = color(gradient[0], theme)
        style['gradientColor'], _ = color(gradient[-1], theme)
        linear = props.find('a:gradFill/a:lin', NS)
        angle = int(linear.get('ang', 0)) / 60000 if linear is not None else 90
        style['gradientDirection'] = ['east', 'south', 'west', 'north'][round(angle / 90) % 4]
    line = props.find('a:ln', NS)
    stroke, alpha = color(line.find('a:solidFill', NS), theme) if line is not None else ('none', 1)
    style.update(strokeColor=stroke, strokeOpacity=number(alpha * 100))
    return style


def transform(element):
    if E.QName(element).localname == 'grpSp':
        return element.find('p:grpSpPr/a:xfrm', NS)
    if E.QName(element).localname == 'graphicFrame':
        return element.find('p:xfrm', NS)
    return element.find('p:spPr/a:xfrm', NS)


def bounds(element, matrix):
    xf = transform(element)
    off, ext = xf.find('a:off', NS), xf.find('a:ext', NS)
    x, y, w, h = [int(off.get('x')), int(off.get('y')), int(ext.get('cx')), int(ext.get('cy'))]
    sx, sy, tx, ty = matrix
    return [x * sx + tx, y * sy + ty, w * abs(sx), h * abs(sy)]


def child_matrix(group, parent):
    xf = transform(group)
    off, ext, childoff, childext = [xf.find('a:' + tag, NS) for tag in ['off', 'ext', 'chOff', 'chExt']]
    sx = int(ext.get('cx')) / int(childext.get('cx'))
    sy = int(ext.get('cy')) / int(childext.get('cy'))
    return (parent[0] * sx, parent[1] * sy,
            parent[2] + parent[0] * (int(off.get('x')) - int(childoff.get('x')) * sx),
            parent[3] + parent[1] * (int(off.get('y')) - int(childoff.get('y')) * sy))


def path_commands(path, box):
    width, height = float(path.get('w', 1)), float(path.get('h', 1))
    result = []
    for command in path:
        kind = E.QName(command).localname
        if kind == 'close':
            result.append(('Z', []));continue
        letter = {'moveTo': 'M', 'lnTo': 'L', 'cubicBezTo': 'C', 'quadBezTo': 'Q'}[kind]
        points = [(int(p.get('x')) / width * box[2], int(p.get('y')) / height * box[3]) for p in command]
        result.append((letter, points))
    return result


def svg_path(commands):
    return ' '.join(letter + (' ' + ' '.join(f'{number(x)},{number(y)}' for x, y in points) if points else '') for letter, points in commands)


def svg_vector_group(group, parent_matrix, theme):
    outer = bounds(group, parent_matrix)
    svg = E.Element('svg', nsmap={None: 'http://www.w3.org/2000/svg'}, width=number(outer[2]), height=number(outer[3]), viewBox=f'0 0 {number(outer[2])} {number(outer[3])}')
    defs = node(svg, 'defs')
    paths = 0

    def visit(element, matrix):
        nonlocal paths
        if E.QName(element).localname == 'grpSp':
            for item in list(element)[2:]:
                visit(item, child_matrix(element, matrix))
            return
        props = properties(element)
        if props is None:
            raise ValueError('Only genuine vector groups can be embedded as source SVG.')
        box = bounds(element, matrix)
        style = paint(props, theme)
        attrs = {'fill': style['fillColor'], 'stroke': style['strokeColor'], 'fill-opacity': str(float(style['fillOpacity']) / 100), 'stroke-opacity': str(float(style['strokeOpacity']) / 100)}
        ln = props.find('a:ln', NS)
        attrs['stroke-width'] = number(int(ln.get('w', 12700)) * abs(matrix[0])) if ln is not None else '0'
        gradient = props.find('a:gradFill/a:gsLst', NS)
        if gradient is not None:
            gid = 'gradient' + str(len(defs))
            direction = style['gradientDirection']
            g = node(defs, 'linearGradient', id=gid, x1='0%', y1='0%', x2='100%' if direction == 'east' else '0%', y2='100%' if direction == 'south' else '0%')
            for stop in gradient:
                fill, alpha = color(stop, theme)
                node(g, 'stop', offset=f'{int(stop.get("pos"))/1000}%', **{'stop-color': fill, 'stop-opacity': str(alpha)})
            attrs['fill'] = f'url(#{gid})'
        x, y, w, h = box
        wrapper = node(svg, 'g', transform=f'translate({number(x - outer[0])} {number(y - outer[1])})')
        xf = transform(element)
        if xf.get('flipH') in ('1', 'true'):
            wrapper = node(wrapper, 'g', transform=f'translate({number(w)} 0) scale(-1 1)')
        if xf.get('flipV') in ('1', 'true'):
            wrapper = node(wrapper, 'g', transform=f'translate(0 {number(h)}) scale(1 -1)')
        if xf.get('rot'):
            wrapper = node(wrapper, 'g', transform=f'rotate({int(xf.get("rot"))/60000} {w/2} {h/2})')
        custom = props.find('a:custGeom/a:pathLst', NS)
        if custom is not None:
            for path in custom:
                local = dict(attrs)
                if path.get('fill') == 'none':local['fill'] = 'none'
                if path.get('stroke') == '0':local['stroke'] = 'none'
                node(wrapper, 'path', d=svg_path(path_commands(path, box)), **local);paths += 1
        else:
            preset = props.find('a:prstGeom', NS).get('prst')
            if preset == 'ellipse':node(wrapper, 'ellipse', cx=w/2, cy=h/2, rx=w/2, ry=h/2, **attrs)
            elif preset in ('rect', 'roundRect'):
                rect = node(wrapper, 'rect', x=0, y=0, width=w, height=h, **attrs)
                if preset == 'roundRect':
                    adj = props.find('a:prstGeom/a:avLst/a:gd', NS)
                    rect.set('rx', str(min(w, h) * (int(adj.get('fmla').split()[-1]) / 100000 if adj is not None else .16)))
            else:raise ValueError(f'Unsupported source SVG preset: {preset}')
    visit(group, parent_matrix)
    return E.tostring(svg, encoding='utf-8'), paths


def paragraph_alignment(body):
    first = body.find('a:p/a:pPr', NS)
    return {'l':'left', 'ctr':'center', 'r':'right', 'just':'left'}.get(first.get('algn') if first is not None else None, 'left')


def rich_text(body, matrix, theme, font_faces, used_faces):
    paragraphs = []
    plain = []
    first = None
    for para in body.findall('a:p', NS):
        pp = para.find('a:pPr', NS)
        default = pp.find('a:defRPr', NS) if pp is not None else None
        runs, paragraph_plain = [], []
        for run in para:
            if E.QName(run).localname == 'br':runs.append('<br>');paragraph_plain.append('\n');continue
            if E.QName(run).localname not in ('r', 'fld'):continue
            value = ''.join(run.xpath('.//a:t/text()', namespaces=NS));paragraph_plain.append(value)
            rp = run.find('a:rPr', NS)
            rp = rp if rp is not None else default
            def attr(key, fallback):
                return rp.get(key, default.get(key, fallback) if default is not None else fallback) if rp is not None else fallback
            latin = rp.find('a:latin', NS) if rp is not None else None
            east = rp.find('a:ea', NS) if rp is not None else None
            family = latin.get('typeface') if latin is not None else 'Arial'
            family = {'+mn-lt': 'Arial', '+mj-lt': 'Arial'}.get(family, family)
            ea = east.get('typeface') if east is not None else 'Microsoft YaHei'
            bold, italic = attr('b', '0') == '1', attr('i', '0') == '1'
            physical_face = font_faces.get((family.casefold(), bold, italic))
            if physical_face is not None:
                family = physical_face['name']
                used_faces.add(family)
            size = int(attr('sz', '1600')) / 100 * EMU * abs(matrix[1])
            fill, _ = color(rp.find('a:solidFill', NS), theme, '#000000') if rp is not None else ('#000000', 1)
            styles = [f'font-family:{html.escape(family)}, {html.escape(ea)}, sans-serif', f'font-size:{number(size)}px', f'color:{fill}']
            if physical_face is not None:styles.extend(['font-weight:400','font-style:normal','font-synthesis:none'])
            else:
                if bold:styles.append('font-weight:700')
                if italic:styles.append('font-style:italic')
            if attr('u', 'none') not in ('none', ''):styles.append('text-decoration:underline')
            if attr('spc', '0') != '0':styles.append(f'letter-spacing:{number(int(attr("spc","0"))/100*EMU*abs(matrix[0]))}px')
            if first is None:first = {'fontFamily': family, 'fontSize': number(size), 'fontColor': fill}
            runs.append('<span style="' + ';'.join(styles) + '">' + html.escape(value).replace('\n', '<br>') + '</span>')
        align = {'l': 'left', 'ctr': 'center', 'r': 'right', 'just': 'justify'}.get(pp.get('algn') if pp is not None else None, 'left')
        paragraphs.append('<div style="margin:0;line-height:1.05;text-align:' + align + ';white-space:nowrap">' + ''.join(runs) + '</div>')
        plain.append(''.join(paragraph_plain))
    return ''.join(paragraphs), '\n'.join(plain), first or {'fontFamily': 'Arial', 'fontSize': '16', 'fontColor': '#000000'}


class Converter:
    def __init__(self, archive, scale, prefixes, bind_nearby, embedded_fonts):
        self.archive, self.scale, self.prefixes, self.bind_nearby = archive, scale, tuple(prefixes), bind_nearby
        self.theme = {'dk1': '000000', 'lt1': 'FFFFFF', 'tx1': '000000', 'bg1': 'FFFFFF', 'tx2': '44546A', 'bg2': 'E7E6E6'}
        theme = E.fromstring(archive.read('ppt/theme/theme1.xml'))
        for entry in theme.findall('a:themeElements/a:clrScheme/*', NS):
            self.theme[E.QName(entry).localname] = entry[0].get('val', entry[0].get('lastClr', '000000'))
        self.reports = []
        self.embedded_fonts = embedded_fonts
        self.font_faces = {(entry['family'].casefold(), entry['bold'], entry['italic']):entry for entry in embedded_fonts}

    def style(self, props, matrix):
        style = paint(props, self.theme)
        line = props.find('a:ln', NS) if props is not None else None
        style['strokeWidth'] = number(int(line.get('w', 12700)) * abs(matrix[0])) if line is not None else '0'
        if line is not None and line.find('a:prstDash', NS) is not None:
            style.update(dashed='1', dashPattern='4 3')
        return style

    def vertex(self, identifier, name, box, parent, origin, style, value='', base_style=''):
        cell = node(self.root, 'mxCell', id=identifier, value=value, style=base_style+';'.join(f'{k}={v}' for k,v in style.items())+';', vertex=1, parent=parent, name=name)
        node(cell, 'mxGeometry', x=number(box[0]-origin[0]), y=number(box[1]-origin[1]), width=number(box[2]), height=number(box[3]), **{'as': 'geometry'})
        self.report['vertices'] += 1
        return cell

    def convert(self, part, page_index, page_name, width, height):
        self.page_index = page_index
        self.root = E.Element('root');node(self.root,'mxCell',id='0');node(self.root,'mxCell',id='1',parent='0')
        self.report = {'page': page_index, 'name': page_name, 'vertices': 0, 'text_boxes': 0, 'groups': 0, 'image_objects': 0, 'existing_vector_assets': 0, 'source_vector_paths': 0, 'tables': 0, 'table_cells': 0, 'edges': 0, 'bound_edges': 0, 'unbound_edges': 0, 'curve_edges_approximated': [], 'texts': [], 'images': [], 'connections': [], 'inferred_connections': []}
        self.idmap, self.edges, self.containers, self.boxes = {}, [], [], {}
        self.used_faces = set()
        relpart = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels')
        self.rels = {r.get('Id'): r.get('Target') for r in E.fromstring(self.archive.read(relpart))}
        self.part = part
        source = E.fromstring(self.archive.read(part))
        for element in list(source.find('p:cSld/p:spTree',NS))[2:]:
            self.visit(element,(self.scale/EMU,self.scale/EMU,0,0),'1',(0,0))
        for record in self.edges:self.edge(*record)
        model = E.Element('mxGraphModel',dx='1280',dy='900',grid='0',gridSize='10',guides='1',tooltips='1',connect='1',arrows='1',fold='1',page='1',pageScale='1',pageWidth=number(width*self.scale),pageHeight=number(height*self.scale),math='0',shadow='0',background='#FFFFFF')
        if self.used_faces:
            model.set('extFonts','|'.join(entry['name']+'^'+entry['url'] for entry in self.embedded_fonts if entry['name'] in self.used_faces))
        self.report['embedded_font_faces'] = sorted(self.used_faces)
        model.append(self.root);self.reports.append(self.report)
        return model

    def visit(self, element, matrix, parent, origin):
        kind = E.QName(element).localname
        if kind in ('extLst',):return
        source_id,name = identity(element)
        identifier = f'p{self.page_index}_s{source_id}'
        self.idmap[source_id] = identifier
        box = bounds(element,matrix);self.boxes[identifier] = box
        if kind == 'grpSp':
            if name.startswith(self.prefixes) and not element.xpath('.//a:t|.//p:pic',namespaces=NS):
                raw, paths = svg_vector_group(element,matrix,self.theme)
                image = 'data:image/svg+xml,' + quote(raw.decode('utf-8'),safe='')
                self.vertex(identifier,name,box,parent,origin,{'shape':'image','image':image,'imageAspect':'0','aspect':'fixed','strokeColor':'none','fillColor':'none'})
                self.report['image_objects'] += 1;self.report['existing_vector_assets'] += 1;self.report['source_vector_paths'] += paths
                self.report['images'].append({'name':name,'kind':'source-vector-svg','source_paths':paths,'embedded_sha256':sha256(raw).hexdigest()})
                return
            self.vertex(identifier,name,box,parent,origin,{'collapsible':'0','recursiveResize':'1','connectable':'0'},base_style='group;')
            self.report['groups'] += 1
            self.containers.append((identifier,box,name))
            for child in list(element)[2:]:self.visit(child,child_matrix(element,matrix),identifier,box[:2])
            return
        if kind == 'pic':
            rid = element.find('p:blipFill/a:blip',NS).get('{'+NS['r']+'}embed')
            target = self.rels[rid]
            media = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(self.part),target))
            raw = self.archive.read(media)
            suffix = media.rsplit('.',1)[-1].lower()
            mime = {'jpeg':'image/jpeg','jpg':'image/jpeg','png':'image/png','gif':'image/gif','bmp':'image/bmp','svg':'image/svg+xml'}[suffix]
            source_rect = element.find('p:blipFill/a:srcRect',NS)
            if source_rect is not None and any(int(source_rect.get(k,0)) for k in ['l','r','t','b']):
                image = Image.open(BytesIO(raw));w,h = image.size
                image = image.crop((round(w*int(source_rect.get('l',0))/100000),round(h*int(source_rect.get('t',0))/100000),round(w*(1-int(source_rect.get('r',0))/100000)),round(h*(1-int(source_rect.get('b',0))/100000))))
                output=BytesIO();image.save(output,format='PNG');raw=output.getvalue();mime='image/png'
            image_uri = 'data:image/svg+xml,' + quote(raw.decode('utf-8'),safe='') if suffix == 'svg' else mime.join(['data:',','])+base64.b64encode(raw).decode()
            style={'shape':'image','image':image_uri,'imageAspect':'0','aspect':'fixed','strokeColor':'none','fillColor':'none'}
            xf=transform(element)
            if xf.get('rot'):style['rotation']=number(int(xf.get('rot'))/60000)
            if xf.get('flipH') in ('1','true'):style['flipH']='1'
            if xf.get('flipV') in ('1','true'):style['flipV']='1'
            self.vertex(identifier,name,box,parent,origin,style)
            self.report['image_objects'] += 1;self.report['images'].append({'name':name,'kind':'source-raster','media':media,'embedded_sha256':sha256(raw).hexdigest(),'cropped':source_rect is not None})
            return
        if kind == 'graphicFrame':
            table=element.find('a:graphic/a:graphicData/a:tbl',NS)
            if table is None:raise ValueError(f'Unsupported graphic frame: {name}')
            self.table(identifier,name,box,parent,origin,table,matrix);return
        props=properties(element);custom=props.find('a:custGeom/a:pathLst',NS)
        preset=props.find('a:prstGeom',NS)
        line=props.find('a:ln',NS)
        if kind=='cxnSp' or (custom is not None and all(path.get('fill')=='none' for path in custom) and line is not None):
            marker = node(self.root, 'edge_placeholder')
            self.edges.append((identifier,name,box,parent,origin,element,matrix,marker));return
        style=self.style(props,matrix)
        if custom is not None:
            shape=E.Element('shape',name=name,w=number(box[2]),h=number(box[3]),aspect='variable',strokewidth='inherit')
            foreground=node(shape,'foreground')
            for path in custom:
                p=node(foreground,'path')
                for command,points in path_commands(path,box):
                    if command=='Z':node(p,'close')
                    elif command=='M':node(p,'move',x=number(points[0][0]),y=number(points[0][1]))
                    elif command=='L':node(p,'line',x=number(points[0][0]),y=number(points[0][1]))
                    elif command=='C':node(p,'curve',x1=number(points[0][0]),y1=number(points[0][1]),x2=number(points[1][0]),y2=number(points[1][1]),x3=number(points[2][0]),y3=number(points[2][1]))
                    elif command=='Q':node(p,'quad',x1=number(points[0][0]),y1=number(points[0][1]),x2=number(points[1][0]),y2=number(points[1][1]))
                node(foreground,'fillstroke')
            encoded=base64.b64encode(zlib.compress(quote(E.tostring(shape,encoding='unicode'),safe='').encode())[2:-4]).decode()
            style['shape']='stencil('+encoded+')'
        else:
            prst=preset.get('prst') if preset is not None else 'rect'
            if prst=='ellipse':style['shape']='ellipse'
            elif prst in ('rect','roundRect'):
                style['shape']='rectangle'
                if prst=='roundRect':
                    adj=preset.find('a:avLst/a:gd',NS);factor=int(adj.get('fmla').split()[-1])/100000 if adj is not None else .16
                    style.update(rounded='1',absoluteArcSize='1',arcSize=number(2*min(box[2:])*factor))
            else:raise ValueError(f'Unsupported preset {prst}: {name}')
        xf=transform(element)
        if xf.get('rot'):style['rotation']=number(int(xf.get('rot'))/60000)
        if xf.get('flipH') in ('1','true'):style['flipH']='1'
        if xf.get('flipV') in ('1','true'):style['flipV']='1'
        body=element.find('p:txBody',NS);value=''
        if body is not None:
            value,plain,font=rich_text(body,matrix,self.theme,self.font_faces,self.used_faces)
            if plain:
                self.report['text_boxes']+=1;self.report['texts'].append(plain)
                style.update(font)
                bp=body.find('a:bodyPr',NS)
                style.update(html='1',whiteSpace='nowrap',overflow='visible',verticalAlign={'ctr':'middle','b':'bottom','t':'top'}.get(bp.get('anchor'),'top'),spacing='0',spacingLeft=number(int(bp.get('lIns',91440))*abs(matrix[0])),spacingRight=number(int(bp.get('rIns',91440))*abs(matrix[0])),spacingTop=number(int(bp.get('tIns',45720))*abs(matrix[1])),spacingBottom=number(int(bp.get('bIns',45720))*abs(matrix[1])),align=paragraph_alignment(body))
            else:value=''
        self.vertex(identifier,name,box,parent,origin,style,value)

    def table(self,identifier,name,box,parent,origin,table,matrix):
        self.vertex(identifier,name,box,parent,origin,{'collapsible':'0','recursiveResize':'1','connectable':'0'},base_style='group;')
        self.report['tables']+=1;self.report['groups']+=1
        widths=[int(x.get('w')) for x in table.find('a:tblGrid',NS)];rows=table.findall('a:tr',NS)
        sx=box[2]/sum(widths);sy=box[3]/sum(int(r.get('h')) for r in rows)
        y=0
        for ri,row in enumerate(rows):
            x=0;h=int(row.get('h'))*sy
            for ci,cell in enumerate(row.findall('a:tc',NS)):
                w=widths[ci]*sx;props=cell.find('a:tcPr',NS)
                style=self.style(props,matrix);style.update(shape='rectangle',html='1',whiteSpace='nowrap',overflow='visible',spacing='0',align='left')
                borders=props.find('a:lnL',NS)
                if borders is not None:
                    style['strokeColor'],_=color(borders.find('a:solidFill',NS),self.theme)
                    style['strokeWidth']=number(int(borders.get('w',12700))*abs(matrix[0]))
                body=cell.find('a:txBody',NS);value,plain,font=rich_text(body,matrix,self.theme,self.font_faces,self.used_faces);style.update(font)
                style['align'] = paragraph_alignment(body)
                bp=body.find('a:bodyPr',NS)
                style['verticalAlign']={'ctr':'middle','b':'bottom','t':'top'}.get(props.get('anchor',bp.get('anchor')),'middle')
                for attr,key in [('marL','spacingLeft'),('marR','spacingRight'),('marT','spacingTop'),('marB','spacingBottom')]:style[key]=number(int(props.get(attr,'0'))*abs(matrix[0]))
                self.vertex(identifier+f'_r{ri}_c{ci}',f'{name} [{ri+1},{ci+1}]',[box[0]+x,box[1]+y,w,h],identifier,box[:2],style,value)
                self.report['table_cells']+=1;self.report['texts'].append(plain);x+=w
            y+=h

    def edge(self,identifier,name,box,parent,origin,element,matrix,marker):
        props=properties(element);style=self.style(props,matrix);style.update(edgeStyle='none',noEdgeStyle='1',html='1',rounded='0',endArrow='none',startArrow='none')
        line=props.find('a:ln',NS)
        for tag,key in [('tailEnd','endArrow'),('headEnd','startArrow')]:
            arrow=line.find('a:'+tag,NS) if line is not None else None
            if arrow is not None:style[key]={'triangle':'block','stealth':'classic','diamond':'diamond','oval':'oval','arrow':'open','none':'none'}[arrow.get('type')]
        custom=props.find('a:custGeom/a:pathLst',NS)
        points=[];curved=False
        if custom is None:points=[(0,0),(box[2],box[3])]
        else:
            for command,values in path_commands(custom[0],box):
                if command in ('M','L'):points.append(values[0])
                elif command in ('C','Q'):
                    curved=True;p0=points[-1]
                    for k in range(1,9):
                        t=k/8;u=1-t
                        if command=='C':
                            p1,p2,p3=values;point=tuple(u**3*p0[j]+3*u*u*t*p1[j]+3*u*t*t*p2[j]+t**3*p3[j] for j in [0,1])
                        else:
                            p1,p2=values;point=tuple(u*u*p0[j]+2*u*t*p1[j]+t*t*p2[j] for j in [0,1])
                        points.append(point)
                else:raise ValueError(f'Closed path cannot be converted to flow edge: {name}')
        xf=transform(element)
        points=[(box[2]-x if xf.get('flipH') in ('1','true') else x,box[3]-y if xf.get('flipV') in ('1','true') else y) for x,y in points]
        points=[(box[0]+x-origin[0],box[1]+y-origin[1]) for x,y in points]
        if curved:style['curved']='1';self.report['curve_edges_approximated'].append(name)
        cn=element.find('p:nvCxnSpPr/p:cNvCxnSpPr',NS)
        refs=[]
        for key,tag in [('source','stCxn'),('target','endCxn')]:
            endpoint=cn.find('a:'+tag,NS) if cn is not None else None
            refs.append((key,self.idmap.get(endpoint.get('id')) if endpoint is not None else None,endpoint))
        if self.bind_nearby and (style['endArrow'] != 'none' or style['startArrow'] != 'none'):
            for i,(key,terminal,endpoint) in enumerate(refs):
                if terminal:continue
                px,py=points[0 if i==0 else -1];px+=origin[0];py+=origin[1]
                best=None
                for cid,cb,container_name in self.containers:
                    if not container_name.startswith('MODULE_'):continue
                    x,y,w,h=cb
                    distance=min(abs(px-x)+max(y-py,0,py-y-h),abs(px-x-w)+max(y-py,0,py-y-h),abs(py-y)+max(x-px,0,px-x-w),abs(py-y-h)+max(x-px,0,px-x-w))
                    if distance<18 and (best is None or distance<best[0]):best=(distance,cid,cb,container_name)
                if best:
                    _,cid,cb,container_name=best
                    terminal_id=identifier+'_'+key+'_port'
                    self.vertex(terminal_id,name+' '+key+' port',[px,py,.01,.01],cid,cb[:2],{'shape':'rectangle','fillColor':'none','strokeColor':'none','opacity':'0','connectable':'1'})
                    refs[i]=(key,terminal_id,None)
                    self.report['inferred_connections'].append({'edge':name,'end':key,'module':container_name,'distance_px':round(best[0],3)})
        attrs={'id':identifier,'edge':'1','parent':parent,'value':'','name':name}
        for key,terminal,endpoint in refs:
            if terminal:
                attrs[key]=terminal
                index=int(endpoint.get('idx',0)) if endpoint is not None else 0
                rx,ry={0:(.5,0),1:(0,.5),2:(.5,1),3:(1,.5)}.get(index,(.5,.5))
                prefix='exit' if key=='source' else 'entry'
                style.update({prefix+'X':str(rx),prefix+'Y':str(ry),prefix+'Perimeter':'0'})
        attrs['style']=';'.join(f'{k}={v}' for k,v in style.items())+';'
        cell=node(self.root,'mxCell',**attrs);geom=node(cell,'mxGeometry',relative='1',**{'as':'geometry'})
        for point,role in [(points[0],'sourcePoint'),(points[-1],'targetPoint')]:node(geom,'mxPoint',x=number(point[0]),y=number(point[1]),**{'as':role})
        if len(points)>2:
            array=node(geom,'Array',**{'as':'points'})
            for x,y in points[1:-1]:node(array,'mxPoint',x=number(x),y=number(y))
        self.root.remove(cell)
        self.root.replace(marker,cell)
        self.report['edges']+=1
        bound=bool(attrs.get('source') and attrs.get('target'))
        self.report['bound_edges' if bound else 'unbound_edges']+=1
        self.report['connections'].append({'name':name,'source':attrs.get('source'),'target':attrs.get('target')})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--pages',nargs='+',type=int)
    parser.add_argument('--page-names',nargs='+')
    parser.add_argument('--scale',type=float,default=4/3,help='Draw.io px per PowerPoint point')
    parser.add_argument('--asset-group-prefixes',nargs='+',default=['ICON_','CHART_'])
    parser.add_argument('--bind-nearby',action='store_true',help='Infer only nearby MODULE_ terminal ports; review the generated report')
    parser.add_argument('--embed-font',type=Path,action='append',default=[],help='Embed an explicitly permitted TTF/OTF/WOFF font file; repeat for physical regular/bold faces')
    args=parser.parse_args()
    embedded_fonts=[]
    for font_path in args.embed_font:
        font=TTFont(font_path,fontNumber=0);names=font['name'];family=names.getDebugName(16) or names.getDebugName(1)
        weight=font['OS/2'].usWeightClass;italic=bool(font['OS/2'].fsSelection & 1)
        name=family if weight<600 and not italic else names.getDebugName(4)
        raw=font_path.read_bytes();extension=font_path.suffix.lower().lstrip('.');mime={'ttf':'font/ttf','otf':'font/otf','woff':'font/woff','woff2':'font/woff2'}[extension]
        embedded_fonts.append({'family':family,'name':name,'bold':weight>=600,'italic':italic,'weight':weight,'source':font_path.name,'license':names.getDebugName(13),'sha256':sha256(raw).hexdigest(),'url':'data:'+mime+';base64,'+base64.b64encode(raw).decode()})
        font.close()
    with ZipFile(args.source) as archive:
        presentation=E.fromstring(archive.read('ppt/presentation.xml'));size=presentation.find('p:sldSz',NS)
        width,height=int(size.get('cx'))/EMU,int(size.get('cy'))/EMU
        relations={r.get('Id'):r.get('Target') for r in E.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))}
        slides=[]
        for entry in presentation.findall('p:sldIdLst/p:sldId',NS):
            target=relations[entry.get('{'+NS['r']+'}id')]
            slides.append(target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt',target)))
        pages=args.pages or list(range(1,len(slides)+1))
        converter=Converter(archive,args.scale,args.asset_group_prefixes,args.bind_nearby,embedded_fonts)
        mxfile=E.Element('mxfile',host='app.diagrams.net',type='device',compressed='false')
        for i,page in enumerate(pages):
            name=args.page_names[i] if args.page_names else f'Page {page}'
            diagram=node(mxfile,'diagram',id='page-'+str(page),name=name)
            diagram.append(converter.convert(slides[page-1],page,name,width,height))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(E.tostring(mxfile,encoding='utf-8',xml_declaration=True))
    report={'source':args.source.name,'source_sha256':sha256(args.source.read_bytes()).hexdigest(),'output':args.output.name,'format':'editable-mxGraphModel','raster_tracing':False,'source_vector_conversion':'existing DrawingML paths converted directly to SVG assets','embedded_fonts':[{k:v for k,v in entry.items() if k!='url'} for entry in embedded_fonts],'notes':['Native table cells are editable rectangles grouped as a table, not a raster table.','Existing cubic edges are approximated by editable curved-edge waypoints; compare actual draw.io rendering.','Source/target references are preserved. Optional --bind-nearby terminal inference is listed explicitly.'],'pages':converter.reports}
    args.output.with_suffix('.conversion.json').write_bytes(json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
    print(json.dumps({'drawio':str(args.output),'pages':len(pages),'summary':[{k:v for k,v in p.items() if k not in ['texts','images','connections','inferred_connections','curve_edges_approximated']} for p in converter.reports]},ensure_ascii=False))


if __name__=='__main__':main()

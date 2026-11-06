"""Generate a data-free HTML form definition from the supplied original workbook."""
import json
from pathlib import Path
from datetime import datetime
import warnings
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries, get_column_letter
from lxml import etree
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'assets/QualityPerformance_Template.xlsx'
warnings.filterwarnings('ignore',category=UserWarning,module='openpyxl')
book=load_workbook(SOURCE,data_only=False)
with ZipFile(SOURCE) as package:
    theme=etree.fromstring(package.read('xl/theme/theme1.xml'))
    palette=[]
    for entry in theme.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme'):
        child=entry[0]
        palette.append(child.get('lastClr') or child.get('val'))

def color(c,default='#000000'):
    if c is not None and c.type=='rgb' and isinstance(c.rgb,str):return '#'+c.rgb[-6:]
    if c is not None and c.type=='theme' and c.theme<len(palette):
        value=palette[c.theme]
        if value and len(value)==6:
            channels=[int(value[i:i+2],16) for i in (0,2,4)]
            tint=c.tint or 0
            channels=[round(v*(1+tint) if tint<0 else v*(1-tint)+255*tint) for v in channels]
            return '#'+''.join(f'{max(0,min(255,v)):02x}' for v in channels)
    return default

def border(side):
    if not side or not side.style:return 'none'
    width=2 if side.style in ('medium','thick','double') else 1
    kind='dashed' if 'dash' in side.style.lower() else 'solid'
    return f'{width}px {kind} {color(side.color)}'

def marker(m,x,y):return (x[min(m.col,len(x)-1)]+m.colOff/9525,y[min(m.row,len(y)-1)]+m.rowOff/9525)

sheets=[]
for sheet in book:
    area=list(sheet.print_area.ranges)[0] if hasattr(sheet.print_area,'ranges') else str(sheet.print_area).split('!')[-1].replace("'",'')
    c1,r1,c2,r2=range_boundaries(str(area))
    x=[0];y=[0]
    for col in range(1,c2+1):
        dim=next((d for d in sheet.column_dimensions.values() if d.min and d.min<=col<=(d.max or d.min)),None)
        width=(dim.width if dim else sheet.sheet_format.defaultColWidth) or 8.43
        x.append(x[-1]+(0 if dim and dim.hidden else int(width*7+5)))
    for row in range(1,r2+1):
        dim=sheet.row_dimensions[row]
        y.append(y[-1]+(0 if dim.hidden else (dim.height or sheet.sheet_format.defaultRowHeight or 15)*4/3))
    merged={};covered=set()
    for region in sheet.merged_cells.ranges:
        if region.min_col>c2 or region.min_row>r2:continue
        merged[(region.min_row,region.min_col)]=(min(region.max_row,r2),min(region.max_col,c2))
        covered.update((r,c) for r in range(region.min_row,min(region.max_row,r2)+1) for c in range(region.min_col,min(region.max_col,c2)+1) if (r,c)!=(region.min_row,region.min_col))
    cells=[]
    for row in range(r1,r2+1):
        for col in range(c1,c2+1):
            if (row,col) in covered:continue
            cell=sheet.cell(row,col);endrow,endcol=merged.get((row,col),(row,col))
            value=cell.value
            # All source observations, calculations and report-specific notes are blank.
            if cell.data_type=='f' or isinstance(value,(int,float,datetime)):value=''
            if sheet.title=='요약' and col==1 and 8<=row<=13:value=str(row-7)
            if sheet.title in ('1','2','3','4') and cell.coordinate in ('A25','J25'):value=''
            if sheet.title=='5' and cell.coordinate in ('A26','J26'):value=''
            if sheet.title=='표지' and cell.coordinate=='AT28':value='작성일자 :'
            if sheet.title=='표지' and cell.coordinate=='AT30':value='작 성 자 :'
            if sheet.title=='6' and cell.coordinate=='N1':value=''
            font=cell.font;align=cell.alignment
            style={'left':x[col-1],'top':y[row-1],'width':x[endcol]-x[col-1],'height':y[endrow]-y[row-1],
                   'fontFamily':font.name or '맑은 고딕','fontSize':(font.sz or 11)*4/3,'fontWeight':'700' if font.b else '400',
                   'fontStyle':'italic' if font.i else 'normal','color':color(font.color),
                   'background':color(cell.fill.fgColor,'transparent') if cell.fill.patternType=='solid' else 'transparent',
                   'textAlign':align.horizontal if align.horizontal in ('left','right','center') else 'left',
                   'justifyContent':{'center':'center','bottom':'end'}.get(align.vertical,'start'),
                   'borderTop':border(cell.border.top),'borderRight':border(cell.border.right),
                   'borderBottom':border(cell.border.bottom),'borderLeft':border(cell.border.left)}
            if value or cell.has_style:cells.append({'address':cell.coordinate,'text':str(value or ''),'style':style})
    charts=[]
    drawings=[]
    with ZipFile(SOURCE) as z:
        # The original cover and contents headings are drawing text, not cell values.
        index=book.sheetnames.index(sheet.title)+1
        relpath=f'xl/worksheets/_rels/sheet{index}.xml.rels'
        if relpath in z.namelist():
            rel=etree.fromstring(z.read(relpath))
            for entry in rel:
                if not entry.get('Type','').endswith('/drawing'):continue
                import posixpath
                target=entry.get('Target')
                path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/worksheets/'+target)
                drawing=etree.fromstring(z.read(path))
                ns={'x':'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                draw_rels=posixpath.join(posixpath.dirname(path),'_rels',posixpath.basename(path)+'.rels')
                if draw_rels in z.namelist():
                    relationships=etree.fromstring(z.read(draw_rels))
                    targets={e.get('Id'):e.get('Target') for e in relationships}
                    def point(n):
                        c=int(n.find('x:col',ns).text);r=int(n.find('x:row',ns).text)
                        return x[min(c,len(x)-1)]+int(n.find('x:colOff',ns).text)/9525,y[min(r,len(y)-1)]+int(n.find('x:rowOff',ns).text)/9525
                    chart_ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships','a':ns['a']}
                    for anchor in drawing:
                        references=anchor.xpath('.//c:chart/@r:id',namespaces=chart_ns)
                        if not references:continue
                        left,top=point(anchor.find('x:from',ns));right,bottom=point(anchor.find('x:to',ns))
                        for rid in references:
                            target=targets[rid]
                            part=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.dirname(path)+'/'+target)
                            root=etree.fromstring(z.read(part))
                            title=''.join(root.xpath('.//c:title//a:t/text()',namespaces=chart_ns))
                            types=root.xpath('.//c:plotArea/*',namespaces=chart_ns)
                            kind=next((etree.QName(n).localname for n in types if etree.QName(n).localname.endswith('Chart')),'barChart')
                            charts.append({'left':left,'top':top,'width':right-left,'height':bottom-top,'title':title,'type':kind,'part':part})
                for anchor in drawing:
                    text=anchor.xpath('.//x:sp/x:txBody//a:t/text()',namespaces=ns)
                    start=anchor.find('x:from',ns);end=anchor.find('x:to',ns)
                    if not text or start is None or end is None:continue
                    def point(n):
                        c=int(n.find('x:col',ns).text);r=int(n.find('x:row',ns).text)
                        return x[min(c,len(x)-1)]+int(n.find('x:colOff',ns).text)/9525,y[min(r,len(y)-1)]+int(n.find('x:rowOff',ns).text)/9525
                    left,top=point(start);right,bottom=point(end)
                    text=[('____' if t=='2026' else '__' if t=='8' else t) for t in text]
                    sizes=anchor.xpath('.//x:sp/x:txBody//a:rPr/@sz',namespaces=ns)
                    drawings.append({'text':''.join(text),'left':left,'top':top,'width':right-left,'height':bottom-top,'fontSize':int(sizes[0])/100*4/3 if sizes else 32})
    sheets.append({'name':sheet.title,'width':x[-1],'height':y[-1],'cells':cells,'charts':charts,'drawings':drawings})
(ROOT/'static/quality_performance_form.json').write_text(json.dumps({'sheets':sheets},ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print('Generated',len(sheets),'sheets;',sum(len(s['charts']) for s in sheets),'chart frames')

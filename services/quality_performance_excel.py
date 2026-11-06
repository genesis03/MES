"""Edit original OOXML cells/caches only; preserve all 17 sheets and package parts."""
import ast
import calendar
import math
import re
import zipfile
from datetime import date,datetime,timedelta
from io import BytesIO
from pathlib import Path
from lxml import etree as ET
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import get_column_letter,column_index_from_string
from services.quality_performance_service import GOALS,FINAL_GROUPS,CLAIM_GROUPS

TEMPLATE=Path(__file__).resolve().parent.parent/'assets/QualityPerformance_Template.xlsx'
S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
C='http://schemas.openxmlformats.org/drawingml/2006/chart'
NS={'s':S,'c':C}
REF=re.compile(r'(?:(?:\'([^\']+)\'|([A-Za-z가-힣_][\w가-힣]*))!)?(\$?[A-Z]{1,3}\$?\d+)(?::(\$?[A-Z]{1,3}\$?\d+))?')
TOKEN=re.compile(r'"(?:[^"]|"")*"|(?:(?:\'[^\']+\'|[A-Za-z가-힣_][\w가-힣]*)!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?')


class Workbook:
    def __init__(self,path):
        self.zip=zipfile.ZipFile(path);self.shared=[]
        if 'xl/sharedStrings.xml' in self.zip.namelist():
            self.shared=[''.join(n.xpath('.//s:t/text()',namespaces=NS)) for n in ET.fromstring(self.zip.read('xl/sharedStrings.xml'))]
        self.sheets={};self.paths={};self.cells={};self.cache={};self.stack=set()
        book=ET.fromstring(self.zip.read('xl/workbook.xml'))
        rels=ET.fromstring(self.zip.read('xl/_rels/workbook.xml.rels'))
        targets={r.get('Id'):r.get('Target') for r in rels}
        for sheet in book.find(f'{{{S}}}sheets'):
            name=sheet.get('name');target=targets[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            root=ET.fromstring(self.zip.read(path));self.sheets[name]=root;self.paths[name]=path
            self.cells[name]={c.get('r'):c for c in root.findall('.//s:sheetData/s:row/s:c',NS)}
            masters={}
            for coord,cell in self.cells[name].items():
                f=cell.find('s:f',NS)
                if f is not None and f.get('t')=='shared' and f.text:masters[f.get('si')]=(coord,f.text)
            for coord,cell in self.cells[name].items():
                f=cell.find('s:f',NS)
                if f is not None and f.get('t')=='shared':
                    origin,text=masters[f.get('si')]
                    f.text=Translator('='+text,origin=origin).translate_formula(coord).lstrip('=')
                    f.attrib.clear()

        self.book=book

    def cell(self,sheet,coord):
        if coord in self.cells[sheet]:return self.cells[sheet][coord]
        root=self.sheets[sheet];data=root.find('s:sheetData',NS);number=int(re.search(r'\d+',coord)[0]);row=data.find(f's:row[@r="{number}"]',NS)
        if row is None:
            row=ET.Element(f'{{{S}}}row',r=str(number));data.append(row)
        cell=ET.Element(f'{{{S}}}c',r=coord)
        column=column_index_from_string(re.match('[A-Z]+',coord)[0]);position=0
        for position,other in enumerate(row):
            if column_index_from_string(re.match('[A-Z]+',other.get('r'))[0])>column:row.insert(position,cell);break
        else:row.append(cell)
        self.cells[sheet][coord]=cell;return cell

    def put(self,sheet,coord,value,formula=False):
        cell=self.cell(sheet,coord)
        for child in list(cell):
            if child.tag in (f'{{{S}}}f',f'{{{S}}}v',f'{{{S}}}is'):cell.remove(child)
        cell.attrib.pop('t',None);cell.attrib.pop('cm',None)
        if formula:ET.SubElement(cell,f'{{{S}}}f').text=value.lstrip('=')
        elif value is None:pass
        elif isinstance(value,str):
            cell.set('t','inlineStr');text=ET.SubElement(ET.SubElement(cell,f'{{{S}}}is'),f'{{{S}}}t');text.text=value
            if value.strip()!=value:text.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        else:ET.SubElement(cell,f'{{{S}}}v').text=str(value)

    def value(self,sheet,coord):
        key=sheet,coord.replace('$','')
        if key in self.cache:return self.cache[key]
        if key in self.stack:raise ValueError('순환 수식: '+str(key))
        cell=self.cells.get(sheet,{}).get(key[1])
        if cell is None:return ''
        formula=cell.find('s:f',NS)
        if formula is not None:
            self.stack.add(key)
            try:value=self.formula(sheet,formula.text or '')
            finally:self.stack.remove(key)
        elif cell.get('t')=='inlineStr':value=''.join(cell.xpath('.//s:t/text()',namespaces=NS))
        else:
            v=cell.findtext('s:v',default='',namespaces=NS)
            if cell.get('t')=='s':value=self.shared[int(v)] if v else ''
            elif cell.get('t') in ('str','e'):value=v
            else:value=float(v) if v else ''
        self.cache[key]=value;return value

    def references(self,current,token):
        match=REF.fullmatch(token)
        if not match:raise ValueError('수식 참조: '+token)
        sheet=match[1] or match[2] or current;first=match[3].replace('$','');last=(match[4] or '').replace('$','')
        if not last:return self.value(sheet,first)
        def split(value):return column_index_from_string(re.match('[A-Z]+',value)[0]),int(re.search(r'\d+',value)[0])
        a,b=split(first),split(last)
        return [[self.value(sheet,f'{get_column_letter(col)}{row}') for col in range(a[0],b[0]+1)] for row in range(a[1],b[1]+1)]

    def formula(self,sheet,formula):
        output=[];cursor=0
        for match in TOKEN.finditer(formula):
            piece=formula[cursor:match.start()].replace('<>','!=').replace('&','|')
            piece=re.sub(r'(?<![<>!=])=(?!=)','==',piece);output.append(piece)
            token=match[0]
            output.append(repr(token[1:-1].replace('""','"')) if token.startswith('"') else f'REF({token!r})')
            cursor=match.end()
        output.append(formula[cursor:].replace('&','|'));expression=''.join(output)
        def number(x):return 0 if x in ('',None) else float(x)
        def flat(value):
            if isinstance(value,list):return [x for part in value for x in flat(part)]
            return [value]
        def ev(node):
            if isinstance(node,ast.Expression):return ev(node.body)
            if isinstance(node,ast.Constant):return node.value
            if isinstance(node,ast.UnaryOp):return -number(ev(node.operand)) if isinstance(node.op,ast.USub) else number(ev(node.operand))
            if isinstance(node,ast.BinOp):
                a,b=ev(node.left),ev(node.right)
                if isinstance(node.op,ast.BitOr):return str(int(a) if isinstance(a,float) and a.is_integer() else a)+str(b)
                a,b=number(a),number(b)
                if isinstance(node.op,ast.Add):return a+b
                if isinstance(node.op,ast.Sub):return a-b
                if isinstance(node.op,ast.Mult):return a*b
                if isinstance(node.op,ast.Div):return a/b
                if isinstance(node.op,ast.Pow):return a**b
            if isinstance(node,ast.Compare):
                a,b=ev(node.left),ev(node.comparators[0]);op=node.ops[0]
                if isinstance(op,ast.Eq):return a==b
                if isinstance(op,ast.NotEq):return a!=b
                if isinstance(op,ast.Gt):return number(a)>number(b)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                name=node.func.id
                if name=='IFERROR':
                    try:return ev(node.args[0])
                    except (ZeroDivisionError,ValueError,IndexError,TypeError):return ev(node.args[1])
                if name=='IF':return ev(node.args[1] if ev(node.args[0]) else node.args[2])
                args=[ev(a) for a in node.args]
                if name=='REF':return self.references(sheet,args[0])
                if name=='OR':return any(args)
                if name=='SUM':return sum(number(v) for v in flat(args) if isinstance(v,(int,float)))
                if name=='MONTH':return (datetime(1899,12,30)+timedelta(days=number(args[0]))).month
                if name=='EDATE':
                    d=datetime(1899,12,30)+timedelta(days=number(args[0]));index=d.year*12+d.month-1+int(args[1]);year,month=divmod(index,12);month+=1
                    return (datetime(year,month,min(d.day,calendar.monthrange(year,month)[1]))-datetime(1899,12,30)).days
                if name=='MATCH':return flat(args[1]).index(args[0])+1
                if name=='INDEX':
                    rows=args[0];r=int(args[1]);c=int(args[2]) if len(args)>2 else 1
                    if not isinstance(rows,list):rows=[[rows]]
                    if r==0:return [row[c-1] for row in rows]
                    return rows[r-1][c-1]
            raise ValueError('지원하지 않는 보고서 수식: '+formula)
        return ev(ast.parse(expression,mode='eval'))

    def finish(self):
        self.cache.clear()
        for sheet,cells in self.cells.items():
            for coord,cell in cells.items():
                if cell.find('s:f',NS) is None:continue
                value=self.value(sheet,coord)
                if isinstance(value,list):value=value[0] if value else ''
                old=cell.find('s:v',NS)
                if old is not None:cell.remove(old)
                cell.attrib.pop('t',None)
                if isinstance(value,str):cell.set('t','str')
                ET.SubElement(cell,f'{{{S}}}v').text=str(value) if value is not None else ''
        changed={self.paths[name]:ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True) for name,root in self.sheets.items()}
        calc=self.book.find('s:calcPr',NS)
        if calc is None:calc=ET.SubElement(self.book,f'{{{S}}}calcPr')
        calc.set('calcMode','auto');calc.set('fullCalcOnLoad','1');calc.set('forceFullCalc','1')
        changed['xl/workbook.xml']=ET.tostring(self.book,xml_declaration=True,encoding='UTF-8',standalone=True)
        if 'xl/calcChain.xml' in self.zip.namelist():
            chain=ET.fromstring(self.zip.read('xl/calcChain.xml'));sheet_ids={s.get('sheetId'):s.get('name') for s in self.book.find('s:sheets',NS)};current=None
            for entry in list(chain):
                current=entry.get('i',current);cell=self.cells.get(sheet_ids.get(current,''),{}).get(entry.get('r'))
                if cell is None or cell.find('s:f',NS) is None:chain.remove(entry)
                else:entry.set('i',current)
            changed['xl/calcChain.xml']=ET.tostring(chain,xml_declaration=True,encoding='UTF-8',standalone=True)

        # Keep chart styles, geometry and anchors; update existing reference caches only.
        for name in self.zip.namelist():
            if not (name.startswith('xl/charts/chart') and name.endswith('.xml')):continue
            root=ET.fromstring(self.zip.read(name));edited=False
            # The summary's original literal category caches were swapped/stale:
            # the percent chart has four metrics, the PPM chart only customers.
            summary_categories={
                'xl/charts/chart1.xml':"'요약'!$B$8:$B$11",
                'xl/charts/chart2.xml':"'요약'!$B$12",
            }
            if name in summary_categories:
                for category in root.xpath('.//c:ser/c:cat',namespaces=NS):
                    for child in list(category):category.remove(child)
                    ET.SubElement(ET.SubElement(category,f'{{{C}}}strRef'),f'{{{C}}}f').text=summary_categories[name]
                    edited=True
            # These original charts used fixed literal values, so their series did
            # not follow worksheet updates. Bind them to the same report cells.
            literal_sources={
                'xl/charts/chart7.xml':("'1-2'!$N$20:$N$25", "'1-2'!$A$20:$A$25"),
                'xl/charts/chart8.xml':("'1-2'!$R$20:$R$25", "'1-2'!$A$20:$A$25"),
                'xl/charts/chart11.xml':("'2-1'!$C$30:$N$30", None),
            }
            if name in literal_sources:
                values,categories=literal_sources[name]
                for series in root.xpath('.//c:ser[c:val/c:numLit]',namespaces=NS):
                    value=series.find('c:val',NS)
                    for child in list(value):value.remove(child)
                    ET.SubElement(ET.SubElement(value,f'{{{C}}}numRef'),f'{{{C}}}f').text=values
                    if categories:
                        category=series.find('c:cat',NS)
                        for child in list(category):category.remove(child)
                        ET.SubElement(ET.SubElement(category,f'{{{C}}}strRef'),f'{{{C}}}f').text=categories
                    edited=True
            for ref in root.xpath('.//c:numRef|.//c:strRef',namespaces=NS):
                text=ref.findtext('c:f',namespaces=NS)
                if not text:continue
                try:values=self.references('요약',text)
                except ValueError:continue
                def flatten(value):return [x for row in value for x in (row if isinstance(row,list) else [row])] if isinstance(value,list) else [value]
                values=flatten(values)
                if ref.getparent().tag==f'{{{C}}}tx':values=values[:1]
                numeric=ref.tag==f'{{{C}}}numRef';tag='numCache' if numeric else 'strCache';cache=ref.find(f'c:{tag}',NS)
                if cache is None:cache=ET.SubElement(ref,f'{{{C}}}{tag}')
                for child in list(cache):
                    if child.tag in (f'{{{C}}}ptCount',f'{{{C}}}pt'):cache.remove(child)
                ET.SubElement(cache,f'{{{C}}}ptCount',val=str(len(values)))
                for i,value in enumerate(values):
                    if numeric and not isinstance(value,(int,float)):continue
                    point=ET.SubElement(cache,f'{{{C}}}pt',idx=str(i));ET.SubElement(point,f'{{{C}}}v').text=str(value)
                edited=True
            if edited:changed[name]=ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
        if hasattr(self,'cover_year'):
            cover=ET.fromstring(self.zip.read('xl/drawings/drawing1.xml'))
            texts=cover.xpath('.//a:t',namespaces={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'})
            # Dynamic cover text is inside the original shape, not a worksheet cell.
            for node in texts:
                if node.text=='2026':node.text=str(self.cover_year)
                elif node.text=='8':node.text=str(self.cover_month)
            changed['xl/drawings/drawing1.xml']=ET.tostring(cover,xml_declaration=True,encoding='UTF-8',standalone=True)
        result=BytesIO()
        with zipfile.ZipFile(result,'w',compression=zipfile.ZIP_DEFLATED) as output:
            for info in self.zip.infolist():output.writestr(info,changed.get(info.filename,self.zip.read(info.filename)))
        result.seek(0);return result


def build_excel(report,writing_date,author):
    book=Workbook(TEMPLATE);year,month=map(int,report['month'].split('-'))
    book.cover_year=year;book.cover_month=month
    def put(s,c,v):book.put(s,c,v)
    def formula(s,c,v):book.put(s,c,v,True)
    serial=(writing_date-date(1899,12,30)).days
    put('표지','U22',serial);put('표지','AT28',f'작성일자 : {writing_date:%Y. %m. %d.}');put('표지','AT30','작 성 자 : '+author)
    put('요약','A1',f'{year}년 종합 품질실적 보고')
    put('요약','D36',f'{month}월');put('2','S2',f'{month}월');put('6','N1',writing_date.isoformat())
    for index,m in enumerate(report['months']):
        active=index<month;c=get_column_letter(index+3);b=get_column_letter(index+2);d=get_column_letter(index+4)
        for group,qtyrow,badrow in [('COPPER',16,17),('CAP',20,21),('SEAL',24,25)]:
            put('1-1',f'{c}{qtyrow}',m['raw'][group] if active else None);put('1-1',f'{c}{badrow}',0 if active else None)
        for kind,qr,br in [('LATHE',16,17),('TAP',21,22)]:
            put('2-1',f'{c}{qr}',m['process'][kind]['qty'] if active else None);put('2-1',f'{c}{br}',m['process'][kind]['bad'] if active else None)
        put('2-1',f'{c}26',0 if active else None);put('2-1',f'{c}27',0 if active else None)
        for i,name in enumerate(('찍힘','긁힘','치수','칩','소재','오조립','조도','기타'),20):put('2-2',f'{b}{i}',m['process_types'].get(name,0) if active else None)
        for name,qr,br in [('메인텍',15,16),('혜성금속',18,19)]:
            put('3-1',f'{d}{qr}',m['subcontract'][name]['qty'] if active else None);put('3-1',f'{d}{br}',m['subcontract'][name]['bad'] if active else None)
        for i,name in enumerate(('찍힘','긁힘','박리','얼룩','치수','기타'),20):put('3-2',f'{b}{i}',m['subcontract_types'].get(name,0) if active else None)
        for group,qr,br in [('BASE',15,16),('310140',18,19),('SV1',21,22),('AX1-EV',24,25)]:
            put('4-1',f'{c}{qr}',m['final'][group]['qty'] if active else None);put('4-1',f'{c}{br}',m['final'][group]['bad'] if active else None)
        for i,name in enumerate(('찍힘','긁힘','치수','칩','소재','조도','기타'),20):put('4-2',f'{b}{i}',m['final_types'].get(name,0) if active else None)
        for field,row in [('delivery_qty',19),('customer_bad',20),('customer_cases',21)]:put('5',f'{d}{row}',m['manual'][field] if active else None)
        formula('5',f'{d}22',f'IF(OR({d}19="",{d}20=""),"",IFERROR({d}20/{d}19*1000000,""))')
        formula('5',f'{d}23',f'IF({d}22="","",2-({d}22/{d}18))')
        put('6',f'{c}15',m['manual']['revenue'] if active else None)
        for group,rows in [('BASE',(16,17,18)),('SV1',(20,21,22)),('AX1-EV',(24,25,26))]:
            for key,row in zip(('occurred','agreed','deducted'),rows):put('6',f'{c}{row}',m['manual']['claims'][group][key] if active else None)
        for row,parts in [(29,(16,20,24)),(30,(17,21,25)),(31,(18,22,26)),(19,(16,17,18)),(23,(20,21,22)),(27,(24,25,26))]:
            missing=','.join(f'{c}{r}=""' for r in parts)
            formula('6',f'{c}{row}',f'IF(OR({missing}),"",'+ '+'.join(f'{c}{r}' for r in parts)+')')
        formula('6',f'{c}32',f'IF(OR({c}15="",{c}16="",{c}20="",{c}24=""),"",IFERROR({c}29/{c}15,""))')
        for row in (16,17,18,20,21,22,24,25,26):formula('6',f'O{row}',f'SUM(C{row}:N{row})')
    # Remove stale private working values outside the printed report areas.
    for name in ('요약','1','1-1','1-2','2','2-1','2-2','3','3-1','3-2','4','4-1','4-2','5','6'):
        sheet=book.cells[name]
        for coord,cell in list(sheet.items()):
            column=column_index_from_string(re.match('[A-Z]+',coord)[0])
            if column>16 and cell.find('s:f',NS) is None and cell.get('t') not in ('s','inlineStr'):
                put(name,coord,None)
    for row in range(39,43):put('1-1',f'C{row}',0)  # overwritten with verified vendor totals below
    vendors=report.get('raw_vendors',{})
    for row,name in zip(range(39,43),('한국고무','성민전자','성재금속','황해정밀')):put('1-1',f'C{row}',sum(q for vendor,q in vendors.items() if name in vendor.replace(' ','')))
    # Correct arithmetic/date references without moving or restyling any cell.
    for section,key in [('1','inbound'),('2','process'),('3','subcontract'),('4','final'),('5','customer')]:
        target=GOALS[key]
        for coord in ('P4','P6','P16','P18'):put(section,coord,target)
        metric=22 if section=='5' else 21
        formula(section,'P10',f'INDEX($D${metric}:$O${metric},1,{month})')
        formula(section,'P8',f'INDEX($D${metric}:$O${metric},1,{month-1})' if month>1 else f'C{metric}')
        formula(section,'P12','IF(P10="","",2-P10/P6)');formula(section,'P14','IF(OR(P10="",P8=""),"",P10-P8)')
        for i in range(12):put(section,f'{get_column_letter(i+4)}18',target)
        if year!=2026:
            for col,y in [('B',year-2),('C',year-1)]:
                put(section,col+'17',f'{y}년')
                for row in range(19,24):put(section,f'{col}{row}',None)
    for s,last in [('1-2',25),('2-2',27),('3-2',25),('4-2',26)]:
        put(s,'R19',f'{month}월')
        for row in range(20,last+1):formula(s,f'R{row}',f'{get_column_letter(month+1)}{row}')
    for row,target in [(15,.003),(19,.003),(23,.003),(27,.003)]:
        for col in range(3,16):put('1-1',f'{get_column_letter(col)}{row}',target)
    for col in range(3,16):put('2-1',f'{get_column_letter(col)}30',GOALS['process']);put('4-1',f'{get_column_letter(col)}27',GOALS['final'])
    for col in range(4,17):put('3-1',f'{get_column_letter(col)}21',GOALS['subcontract'])
    for col in range(3,16):put('6',f'{get_column_letter(col)}28',GOALS['claim'])
    formula('요약','F13',f"'6'!{get_column_letter(month+2)}32")
    formula('요약','E13',f"'6'!{get_column_letter(month+1)}32" if month>1 else '""')
    for row in range(8,14):formula('요약',f'H{row}',f'IF(F{row}="","",2-F{row}/D{row})')
    # Keep these as live formulas so users can also complete sheets 5/6 in Excel.
    for field,row in [('delivery_qty',19),('customer_bad',20),('customer_cases',21)]:
        missing=','.join(f'{get_column_letter(i+4)}{row}=""' for i in range(month))
        formula('5',f'P{row}',f'IF(OR({missing}),"",SUM(D{row}:{get_column_letter(month+3)}{row}))')
    formula('5','P22','IF(OR(P19="",P20=""),"",IFERROR(P20/P19*1000000,""))')
    formula('5','P23','IF(P22="","",2-P22/P18)')
    for row in (15,16,17,18,19,20,21,22,23,24,25,26,27,29,30,31):
        missing=','.join(f'{get_column_letter(i+3)}{row}=""' for i in range(month))
        formula('6',f'O{row}',f'IF(OR({missing}),"",SUM(C{row}:{get_column_letter(month+2)}{row}))')
    formula('6','O32','IF(OR(O29="",O15=""),"",IFERROR(O29/O15,""))')
    for row in range(8,14):formula('요약',f'I{row}',f'IF(OR(F{row}="",E{row}=""),"",F{row}-E{row})')
    notes=report['manual']['notes']
    for section,key in [('1','inbound'),('2','process'),('3','subcontract'),('4','final'),('5','customer')]:
        header=24 if section in ('1','2','3','4') else 25
        prefixes={'inbound':('원자재','원본 업체'),'process':('공정','보고 기간의'),'subcontract':('외주',),'final':('조립','최종검사'),'customer':()}
        related=[issue for issue in report.get('issues',[]) if any(issue.startswith(prefix) for prefix in prefixes[key]) and any(word in issue for word in ('미연결','미분류','없습니다','맞지','없는'))]
        cause=notes[key]['cause']+ ('\n확인: '+' / '.join(related) if related else '')
        if len(cause)>32000:cause=cause[:31900]+'\n추가 확인사항은 품질실적 화면에서 확인하세요.'
        put(section,'A'+str(header+1),cause);put(section,'J'+str(header+1),notes[key]['action'])
    # Original unguarded chart ratios can otherwise produce #DIV/0! on zero months.
    for name,cells in book.cells.items():
        for coord,cell in list(cells.items()):
            f=cell.find('s:f',NS)
            if f is not None and '/' in (f.text or '') and 'IFERROR(' not in f.text and 'IF(' not in f.text:formula(name,coord,f'IFERROR({f.text},"")')
    return book.finish()

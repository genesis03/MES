"""Remove a report worksheet without rewriting styles or the remaining drawings."""
from collections import defaultdict
from io import BytesIO
import posixpath
import re
from zipfile import ZipFile
from lxml import etree as ET

S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
EP='http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'
VT='http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'


def _relationships(part):
    return posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')


def _target(source,target):
    return target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(source),target))


def remove_sheet(stream,name):
    """Preserve cached summary results when their source sheet is retired.

    Remove only private descendant parts; shared images/styles remain untouched.
    The uploaded template is never modified.
    """
    original=ZipFile(stream)
    parts={entry:original.read(entry) for entry in original.namelist()}
    workbook=ET.fromstring(parts['xl/workbook.xml'])
    sheets=workbook.find(f'{{{S}}}sheets')
    sheet=next((n for n in sheets if n.get('name')==name),None)
    if sheet is None:
        original.close()
        stream.seek(0)
        return stream
    index=list(sheets).index(sheet)
    count=len(sheets)
    sheet_id=sheet.get('sheetId')
    rid=sheet.get(f'{{{R}}}id')
    bookrels=ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    relation=next(n for n in bookrels if n.get('Id')==rid)
    root_part=_target('xl/workbook.xml',relation.get('Target'))
    bookrels.remove(relation)
    sheets.remove(sheet)
    names=workbook.find(f'{{{S}}}definedNames')
    removed_names=0
    if names is not None:
        for entry in list(names):
            local=entry.get('localSheetId')
            if local is not None and int(local)==index:
                names.remove(entry);removed_names+=1
            elif local is not None and int(local)>index:
                entry.set('localSheetId',str(int(local)-1))
    for view in workbook.findall(f'.//{{{S}}}workbookView'):
        if int(view.get('activeTab','0'))>=len(sheets):view.set('activeTab','0')
    def xml(part,node):
        parts[part]=ET.tostring(node,xml_declaration=True,encoding='UTF-8',standalone=True)
    xml('xl/workbook.xml',workbook)
    xml('xl/_rels/workbook.xml.rels',bookrels)

    # Finished workbook formulas already have evaluated caches. Freeze only
    # cross-sheet references to the removed sheet, keeping summary values valid.
    reference=re.compile(r"(?:'"+re.escape(name)+r"'|(?<![\w'])"+re.escape(name)+r")!")
    for part in list(parts):
        if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml',part) or part==root_part:continue
        root=ET.fromstring(parts[part]);changed=False
        for cell in root.findall(f'.//{{{S}}}c'):
            formula=cell.find(f'{{{S}}}f')
            if formula is not None and reference.search(formula.text or ''):
                cell.remove(formula);changed=True
        if changed:xml(part,root)
    if 'xl/calcChain.xml' in parts:
        chain=ET.fromstring(parts['xl/calcChain.xml']);current=None
        for entry in list(chain):
            current=entry.get('i',current)
            if current==sheet_id:chain.remove(entry)
            else:entry.set('i',current)
        # Remove frozen summary cells from the calculation chain as well.
        sheet_parts={n.get('sheetId'):_target('xl/workbook.xml',next(r.get('Target') for r in bookrels if r.get('Id')==n.get(f'{{{R}}}id'))) for n in sheets}
        formulas={sid:{cell.get('r') for cell in ET.fromstring(parts[part]).findall(f'.//{{{S}}}c') if cell.find(f'{{{S}}}f') is not None} for sid,part in sheet_parts.items()}
        for entry in list(chain):
            if entry.get('r') not in formulas.get(entry.get('i'),set()):chain.remove(entry)
        xml('xl/calcChain.xml',chain)

    outgoing=defaultdict(set);incoming=defaultdict(set)
    for part,content in parts.items():
        if not part.endswith('.rels'):continue
        if part=='_rels/.rels':source=''
        else:
            folder,filename=part.rsplit('/_rels/',1)
            source=folder+'/'+filename[:-5]
        for entry in ET.fromstring(content):
            if entry.get('TargetMode')=='External':continue
            target=_target(source,entry.get('Target'))
            outgoing[source].add(target);incoming[target].add(source)
    removed={root_part};queue=[root_part]
    while queue:
        for target in outgoing[queue.pop()]:
            if target in parts and target not in removed:removed.add(target);queue.append(target)
    while True:
        shared={part for part in removed if incoming[part]-removed}
        if not shared:break
        removed-=shared
    removed|={_relationships(part) for part in list(removed) if _relationships(part) in parts}
    types=ET.fromstring(parts['[Content_Types].xml'])
    for entry in list(types):
        if entry.get('PartName','').lstrip('/') in removed:types.remove(entry)
    xml('[Content_Types].xml',types)
    if 'docProps/app.xml' in parts:
        properties=ET.fromstring(parts['docProps/app.xml'])
        titles=properties.find(f'{{{EP}}}TitlesOfParts/{{{VT}}}vector')
        if titles is not None:
            for item in list(titles):
                if item.text in (name,f"'{name}'!Print_Area",f'{name}!Print_Area'):titles.remove(item)
            titles.set('size',str(len(titles)))
        headings=properties.find(f'{{{EP}}}HeadingPairs/{{{VT}}}vector')
        if headings is not None:
            numbers=headings.findall(f'{{{VT}}}variant/{{{VT}}}i4')
            if numbers:numbers[0].text=str(count-1)
            if len(numbers)>1:numbers[1].text=str(max(0,int(numbers[1].text)-removed_names))
        xml('docProps/app.xml',properties)
    output=BytesIO()
    with ZipFile(output,'w') as result:
        for info in original.infolist():
            if info.filename not in removed:result.writestr(info,parts[info.filename])
    original.close()
    output.seek(0)
    return output

from datetime import datetime
from decimal import Decimal
from itertools import groupby
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.pagebreak import Break
from services.material_usage_service import daily_review


def safe(value):
    return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value


def qty(value):
    return Decimal(value) if value is not None else None


def build_workbook(records, author):
    book=Workbook();sheet=book.active;sheet.title='일일 조립 누락 검토'
    widths=[22,22,12,18,18,18,18,18,40,60]
    for i,width in enumerate(widths,1):sheet.column_dimensions[chr(64+i)].width=width
    thin=Side(style='thin',color='94A3B8')
    groups=daily_review(records)
    for day, items in groupby(groups,key=lambda r:r['work_date']):
        if sheet.max_row>1:sheet.append([]);sheet.row_breaks.append(Break(id=sheet.max_row))
        title=sheet.max_row+1 if sheet.max_row>1 or sheet.cell(1,1).value else 1
        sheet.cell(title,1,f'■ {day} 조립공정 누락방지 검토');sheet.merge_cells(start_row=title,start_column=1,end_row=title,end_column=10)
        sheet.cell(title,1).font=Font(size=16,bold=True);sheet.row_dimensions[title].height=30
        sheet.append([f'작성일: {datetime.now():%Y-%m-%d}   작성자: {author}']);sheet.merge_cells(start_row=title+1,start_column=1,end_row=title+1,end_column=10)
        sheet.append(['생산품번','자재품번','단위','총 생산수량','투입 합계','사용 합계','사용−생산','소모율(%)','비고(판정)','수불 불일치 내역'])
        header=title+2
        for item in items:
            sheet.append([safe(item['part_no']),safe(item['material_part']),safe(item['unit']),item['production_qty'],item['issued_qty'],item['used_qty'],item['difference'],item['usage_rate'],item['verdict'],safe('\n'.join(item['balance_details']) or '일치 · 차이 0')])
        for row in sheet.iter_rows(min_row=header,max_row=sheet.max_row,max_col=10):
            for cell in row:
                cell.border=Border(left=thin,right=thin,top=thin,bottom=thin);cell.alignment=Alignment(vertical='center',wrap_text=True)
                if cell.row==header:cell.fill=PatternFill('solid',fgColor='BDDEE5');cell.font=Font(bold=True)
                elif 4<=cell.column<=8:cell.number_format='#,##0.######'
            if row[0].row>header:sheet.row_dimensions[row[0].row].height=48
        for text in ['검토방법: 총 생산수량은 양품·불량·셋업 포함. 같은 자재의 여러 투입/사용 행을 합산합니다.',
            '소모율 = 자재 사용 합계 ÷ 총 생산수량 × 100. 100% 미만: 조립 누락 확인 / 105% 이상: 자재 LOSS 증가 확인.',
            '수불차이 = 투입−사용−폐기−잔여. 양수: 사용처 미기록 / 음수: 투입 초과 기록. LOT·행별 차이는 상단 내역 참조.',
            '생산 1개당 각 자재 1개 기준의 수기 검토입니다. 미입력은 0으로 간주하지 않습니다. 실제 조립 누락을 확정하지 않습니다.']:
            sheet.append([text]);sheet.merge_cells(start_row=sheet.max_row,start_column=1,end_row=sheet.max_row,end_column=10);sheet.row_dimensions[sheet.max_row].height=24
    if not groups:sheet.append(['조회 조건에 해당하는 유효 자재 기록이 없습니다.'])
    sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.print_options.horizontalCentered=True;sheet.print_area=sheet.dimensions
    detail=book.create_sheet('자재 사용 상세')
    detail.append(['일자','작업자','생산품번','생산 LOT','총 생산수량','자재품번','자재 LOT','단위','투입','사용','폐기','잔여','수불차이','수불 불일치 내역','생산 대비 자재 사용 차이','비고','상태'])
    for record in records:
        summaries={(g['part_no'].casefold(),g['unit'].casefold()):g for g in record['summary']}
        for material in record['materials'] or [None]:
            values=[record['work_date'],record['worker'],record['part_no'],record['production_lot'],qty(record['production_qty'])]
            if material:
                group=summaries[(material['part_no'].casefold(),material['unit'].casefold())]
                values += [material['part_no'],material['lot_no'],material['unit']]+[qty(material[k]) for k in ['issued_qty','used_qty','scrap_qty','remaining_qty','balance_difference']]+[material['balance_description'],group['usage_description']]
            else:values += ['']*10
            detail.append([safe(v) for v in values+[record['note']+(' / '+material['note'] if material and material['note'] else ''),'취소' if record['cancelled'] else '유효']])
    detail.freeze_panes='F2';detail.auto_filter.ref=detail.dimensions
    for column in detail.columns:detail.column_dimensions[column[0].column_letter].width=20
    return book

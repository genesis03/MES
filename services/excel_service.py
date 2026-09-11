import io
import re
from datetime import datetime
from typing import List, Dict, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def parse_delivery_date(row: dict) -> datetime:
    """원장 데이터 9열(인덱스 8)의 납품일자를 직접 추출하여 datetime 객체로 변환"""
    cols = list(row.values())
    raw_date = str(cols[8]).strip() if len(cols) > 8 else ""

    if raw_date:
        clean_date = re.sub(r"[.\s/]+", "-", raw_date)
        for fmt in ("%Y-%m-%d", "%y-%m-%d", "%Y%m%d", "%y%m%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(clean_date, fmt)
            except ValueError:
                pass
                
        digits_only = re.sub(r"\D", "", raw_date)
        try:
            if len(digits_only) == 6:
                return datetime.strptime(digits_only, "%y%m%d")
            elif len(digits_only) == 8:
                return datetime.strptime(digits_only, "%Y%m%d")
        except ValueError:
            pass
            
    return datetime.now()

def create_manual_history_excel(rows: List[Dict[str, Any]]) -> io.BytesIO:
    """수기 발행 이력 엑셀(.xlsx) 생성"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "수기발행이력"

    headers = ["순번", "등록일시", "고객사", "납품일자", "품번", "품명", "수량", "시리얼", "바코드"]
    ws.append(headers)

    header_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    header_font = Font(name="맑은 고딕", bold=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    for idx, row in enumerate(rows, start=1):
        ws.append([
            idx,
            row["created_at"],
            row["customer"],
            row["delivery_date"],
            row["part_no"],
            row["part_name"],
            row["qty"],
            row["serial"],
            row["barcode"]
        ])

    for r_idx, row_cells in enumerate(ws.iter_rows(min_row=1, max_row=len(rows) + 1, min_col=1, max_col=9), start=1):
        for cell in row_cells:
            cell.border = thin_border
            if r_idx == 1:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                if cell.column in [1, 2, 3, 4, 5, 8, 9]:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    if cell.column in [4, 5, 8, 9]:
                        cell.number_format = "@"
                elif cell.column == 6:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif cell.column == 7:
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(horizontal="right", vertical="center")

    for col in ws.columns:
        max_len = 0
        has_val = False
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value is not None:
                has_val = True
                val_str = str(cell.value)
                korean_chars = len(re.findall(r'[\uac00-\ud7a3]', val_str))
                calc_len = len(val_str) + korean_chars
                if calc_len > max_len:
                    max_len = calc_len
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12) if has_val else 4

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def create_shipping_analysis_excel(current_data: List[Dict[str, Any]]) -> io.BytesIO:
    """출고 LOT 계산 분석 엑셀 파일 생성"""
    wb = openpyxl.Workbook()
    part_groups = {}
    lot_summary = {}

    first_record = current_data[0] if current_data else {}
    actual_delivery_date = parse_delivery_date(first_record)
    display_date_str = actual_delivery_date.strftime("%Y-%m-%d")
    delivery_date_obj = actual_delivery_date.date()
    
    # 1. 데이터 파싱 및 그룹화
    for row in current_data:
        cols = list(row.values())
        raw_barcode = str(cols[2] if len(cols) > 2 else "").strip()
        match = re.search(r"P(.*?)Q(.*?)S(.*)", raw_barcode)
        if match:
            p_no, q_val, s_val = match.group(1), match.group(2), match.group(3)
            qty = float(q_val) if q_val.isdigit() else 0.0
            clean_serial = s_val[:-6] if s_val.endswith("110657") else s_val.replace("110657", "")
            lot_date = clean_serial[:6]
            
            if p_no not in part_groups:
                part_groups[p_no] = []
                lot_summary[p_no] = {}
                
            part_groups[p_no].append({"serial": clean_serial, "qty": qty})
            lot_summary[p_no][lot_date] = lot_summary[p_no].get(lot_date, 0.0) + qty

    sorted_p_nos = sorted(part_groups.keys())

    header_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    header_font = Font(name="맑은 고딕", bold=True)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))

    # [1] 개요 시트
    ws_overview = wb.active
    ws_overview.title = "개요"
    ws_overview.append(["품번", "총 수량", "건수(Box)"])
    
    for p_no in sorted_p_nos:
        items = part_groups[p_no]
        total_qty = sum(item["qty"] for item in items)
        box_count = len(items)
        ws_overview.append([p_no, total_qty, box_count])
        
    for row in ws_overview.iter_rows(min_row=1, max_row=len(sorted_p_nos) + 1, min_col=1, max_col=3):
        for cell in row:
            cell.border = thin_border
            if cell.row == 1:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")
            else:
                if cell.column == 1:
                    cell.alignment = Alignment(horizontal="center")
                else:
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(horizontal="right")

    # [2] 품번별 상세 시트
    created_sheet_names = {"개요"}
    for p_no in sorted_p_nos:
        items = part_groups[p_no]
        items.sort(key=lambda x: x["serial"])
        
        base_sheet_name = re.sub(r'[\\/*?:\[\]]', '', p_no)[:28]
        safe_name = base_sheet_name
        dup_count = 1
        while safe_name in created_sheet_names:
            safe_name = f"{base_sheet_name}_{dup_count}"
            dup_count += 1
        created_sheet_names.add(safe_name)
        
        ws_part = wb.create_sheet(title=safe_name)
        
        for idx, item in enumerate(items, start=1):
            ws_part.cell(row=idx, column=1, value=item["serial"]).number_format = "@"
            ws_part.cell(row=idx, column=2, value=item["qty"]).number_format = "#,##0"
            ws_part.cell(row=idx, column=3, value=item["qty"]).number_format = "#,##0"
            ws_part.cell(row=idx, column=4, value=f'=IF(A{idx}="","",LEFT(A{idx},6))').number_format = "@"
            
        ws_part.cell(row=1, column=8, value="로트").font = header_font
        ws_part.cell(row=1, column=8).fill = gray_fill
        ws_part.cell(row=1, column=8).border = thin_border
        
        ws_part.cell(row=1, column=9, value="수량").font = header_font
        ws_part.cell(row=1, column=9).fill = gray_fill
        ws_part.cell(row=1, column=9).border = thin_border
        
        sorted_lot_keys = sorted(lot_summary[p_no].keys())
        lot_row = 2
        for lot_key in sorted_lot_keys:
            ws_part.cell(row=lot_row, column=8, value=lot_key).number_format = "@"
            ws_part.cell(row=lot_row, column=9, value=f"=SUMIFS($C:$C,$D:$D,H{lot_row})").number_format = "#,##0"
            ws_part.cell(row=lot_row, column=8).border = thin_border
            ws_part.cell(row=lot_row, column=9).border = thin_border
            ws_part.cell(row=lot_row, column=8).alignment = Alignment(horizontal="center")
            ws_part.cell(row=lot_row, column=9).alignment = Alignment(horizontal="right")
            lot_row += 1
            
        ws_part.cell(row=1, column=11, value="로트수").font = header_font
        ws_part.cell(row=1, column=11).fill = yellow_fill
        ws_part.cell(row=1, column=12, value="=COUNTA(A:A)").number_format = "#,##0"
        
        ws_part.cell(row=2, column=11, value="총수량").font = header_font
        ws_part.cell(row=2, column=11).fill = yellow_fill
        ws_part.cell(row=2, column=12, value="=SUM(C:C)").number_format = "#,##0"
        
        for r in range(1, 3):
            for c in range(11, 13):
                ws_part.cell(row=r, column=c).border = thin_border
                ws_part.cell(row=r, column=c).font = header_font
                if c == 11:
                    ws_part.cell(row=r, column=c).alignment = Alignment(horizontal="center")
                else:
                    ws_part.cell(row=r, column=c).alignment = Alignment(horizontal="right")

        insp_headers = ["검사", "은도금", "소재"]
        insp_vals = ["-", "-", "-"]
        for col_i, (h_val, d_val) in enumerate(zip(insp_headers, insp_vals), start=11):
            c5 = ws_part.cell(row=5, column=col_i, value=h_val)
            c6 = ws_part.cell(row=6, column=col_i, value=d_val)
            c5.font = header_font
            c5.fill = gray_fill
            c5.border = thin_border
            c5.alignment = Alignment(horizontal="center", vertical="center")
            c6.border = thin_border
            c6.alignment = Alignment(horizontal="center", vertical="center")
            
            if col_i in [12, 13]:
                c6.number_format = 'General'

    # [3] 발행대장 시트
    ws_final = wb.create_sheet(title="발행대장")
    headers = ["순번", "부품 번호", "LOT NO", "발주서", "발송일", "납품수량", "총수량", "발행부수", "캡", "씰", "고객", "부품명"]
    ws_final.append(headers)
    
    f_row = 1
    for p_no in sorted_p_nos:
        sorted_lots = sorted(lot_summary[p_no].keys())
        for lot_key in sorted_lots:
            total_l_qty = lot_summary[p_no][lot_key]
            ws_final.append([
                f_row, p_no, lot_key, "", delivery_date_obj, "", total_l_qty, "", "", "", "", ""
            ])
            f_row += 1
            
    for row in ws_final.iter_rows(min_row=1, max_row=f_row, min_col=1, max_col=12):
        for cell in row:
            cell.border = thin_border
            if cell.row == 1:
                cell.fill = gray_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")
            else:
                if cell.column in [1, 2, 3, 4, 8, 9, 10, 11, 12]:
                    cell.alignment = Alignment(horizontal="center")
                elif cell.column == 5:
                    cell.number_format = 'yyyy-mm-dd'
                    cell.alignment = Alignment(horizontal="center")
                else:
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(horizontal="right")
                    
    # 열 너비 정밀 자동 조정
    for ws in wb.worksheets:
        for col in ws.columns:
            max_len = 0
            has_val = False
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.value is not None and str(cell.value).strip() != "":
                    has_val = True
                    val_str = str(cell.value)
                    if val_str.startswith("="):
                        calc_len = 8
                    else:
                        korean_chars = len(re.findall(r'[\uac00-\ud7a3]', val_str))
                        calc_len = len(val_str) + korean_chars
                        
                    if calc_len > max_len:
                        max_len = calc_len
                        
            if has_val:
                ws.column_dimensions[col_letter].width = max(max_len + 4, 10)
            else:
                ws.column_dimensions[col_letter].width = 4

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

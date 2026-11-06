"""Monthly quality performance: explicit source selection, no operational writes."""
import json
import re
from collections import defaultdict
from models.models import ItemMasterModel,ItemBomModel,ProcessModel,PurchaseInboundMaster,PurchaseInboundItem
from models.quality_performance import QualityPerformanceMonth
from models.inbound_sync import ExternalInboundRecord
from models.production_sync import ExternalProductionRecord
from models.production import ProductionPerformance,ProductionWorkOrder
from models.production_run import ProductionRun,ProductionRunDefect
from services.production_sync_mapping import ItemConnections,stage_suffix
from services.production_sync_view import external_record_view
from services.subcontract_fault_view import external_rows
from services.subcontract_defect_status import subcontract_defect_status
from models.quality_migration import DEFECT_TYPE_SEED
from services.defect_report_format import report_types

GOALS={'inbound':.003,'process':.003,'subcontract':.007,'final':.008,'customer':10.,'claim':.01}
CAP_PARTS={'280098','YR280554','YR280555','YR280556','YR280589'}
SEAL_PARTS={'480187','480207'}
FINAL_GROUPS=('BASE','310140','SV1','AX1-EV')
CLAIM_GROUPS=('BASE','SV1','AX1-EV')
TYPE_NAMES=dict(DEFECT_TYPE_SEED)|{'13':'기타'}


def blank_manual():
    return {'delivery_qty':None,'customer_bad':None,'customer_cases':None,'revenue':None,
        'claims':{key:{k:None for k in ('occurred','agreed','deducted')} for key in CLAIM_GROUPS},
        'author':'','notes':{key:{'cause':'','action':''} for key in GOALS}}


def group_material(part,material_type):
    part=part.strip().upper()
    if part in CAP_PARTS:return 'CAP'
    if part in SEAL_PARTS:return 'SEAL'
    if material_type.strip()=='동소재':return 'COPPER'
    return None


def stage(process):
    suffix=stage_suffix(process)
    return 'LATHE' if suffix=='-A' else 'TAP' if suffix=='-B' else 'ASSEMBLY' if suffix=='-C' else None


def final_group(item,part,connections):
    # SV1 / AX1-EV classification is explicitly based on the internal name.
    name=(item.part_name if item else '').upper()
    if 'SV1' in name:return 'SV1'
    if 'AX1-EV' in name:return 'AX1-EV'
    candidates=[part]+([item.part_no] if item else [])
    seen=set(p.casefold() for p in candidates);frontier=list(seen)
    while frontier:
        for parent in connections.parents.get(frontier.pop(),()):
            if parent not in seen:seen.add(parent);frontier.append(parent)
    matches=set()
    for p in seen:
        base=re.sub(r'-(?:ag|a|b|c|d)$','',p,flags=re.I)
        family=re.match(r'^(?:yr)?(310061|310067|310070|310140)(?:-|$)',base)
        if family:
            matches.add('310140' if family[1]=='310140' else 'BASE')
    return next(iter(matches)) if len(matches)==1 else None


def type_name(detail):
    code=str(detail.get('code') or '')
    match=re.fullmatch(r'(?:EXTERNAL:)?F(1[0-3]|[1-9])',code,re.I)
    if match:code=match[1]
    return TYPE_NAMES.get(code,str(detail.get('name') or '기타'))


def add_types(target,details,bad,allowed,issues,label):
    total=sum(max(float(d.get('qty') or 0),0) for d in details)
    if total>bad+1e-6:
        target['기타']+=bad
        issues.add(label+' 불량 유형 합계가 불량수량과 맞지 않아 해당 불량은 기타로 표시했습니다.')
        return
    for detail in details:
        qty=float(detail.get('qty') or 0)
        if qty<=0:continue
        name=type_name(detail);name=name if name in allowed else '기타'
        target[name]+=qty
    if bad-total>1e-6:
        target['기타']+=bad-total
        issues.add(label+' 유형 미분류 불량은 기타에 포함했습니다.')


def new_month():
    return {'raw':{k:0. for k in ('COPPER','CAP','SEAL')},'process':{k:{'qty':0.,'bad':0.} for k in ('LATHE','TAP')},
        'assembly':{'qty':0.,'bad':0.},
        'process_types':defaultdict(float),'final':{k:{'qty':0.,'bad':0.} for k in FINAL_GROUPS},
        'final_types':defaultdict(float),'subcontract':{k:{'qty':0.,'bad':0.} for k in ('메인텍','혜성금속')},
        'subcontract_types':defaultdict(float),'manual':blank_manual()}


def build_report(db,month,source='EXTERNAL'):
    year,selected=map(int,month.split('-'));start=f'{year}-01-01';end=f'{year}-{selected:02d}-31'
    months=[new_month() for _ in range(12)];issues=set();connections=ItemConnections(db);vendors=defaultdict(float)
    by_part=defaultdict(list)
    for item in db.query(ItemMasterModel):by_part[item.part_no.casefold()].append(item)
    def item_for(part):
        choices=by_part.get(part.casefold(),[])
        return choices[0] if len(choices)==1 else None
    def m_for(day):return months[int(day[5:7])-1]
    def note(message):issues.add(message)
    if source=='EXTERNAL':
        receipts=db.query(ExternalInboundRecord).filter(ExternalInboundRecord.present.is_(True),ExternalInboundRecord.inbound_date.between(start,end))
        count=0
        for record in receipts:
            if record.product_type!='원자재':continue
            group=group_material(record.part_no,record.material_type)
            if group:
                m_for(record.inbound_date)['raw'][group]+=float(record.confirmed_qty);vendors[record.partner_name]+=float(record.confirmed_qty);count+=1
            else:note('원자재 분류 미연결: '+record.part_no)
        if not count:note('보고 기간의 외부 원자재 입고 자료가 없습니다.')
    else:
        receipts=(db.query(PurchaseInboundMaster,PurchaseInboundItem,ItemMasterModel)
            .join(PurchaseInboundItem,PurchaseInboundItem.inbound_id==PurchaseInboundMaster.id)
            .join(ItemMasterModel,ItemMasterModel.id==PurchaseInboundItem.item_id)
            .filter(PurchaseInboundMaster.status=='CONFIRMED',PurchaseInboundMaster.inbound_date.between(start,end)))
        for master,line,item in receipts:
            group=group_material(line.part_no,item.material_type)
            if group:
                m_for(master.inbound_date)['raw'][group]+=float(line.inbound_qty);vendors[master.partner_name]+=float(line.inbound_qty)
            else:note('원자재 분류 확인: '+line.part_no)
    # One source is used for a whole report, never blindly add native and imported copies.
    productions=[]
    if source=='EXTERNAL':
        for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date.between(start,end)):
            view=external_record_view(record,connections)
            if view['lot_conflict']:note('LOT 충돌 실적 제외: '+record.lot_no);continue
            if any('전체수량과' in n for n in view['notes']):note('생산 합계 확인: '+record.lot_no)
            item=item_for(view['mes_part_no'] or record.part_no)
            proc=view['process_name'] or record.process_name
            if not stage(proc):proc=record.process_name
            qty=float(view['total_qty']) if view['total_qty'] is not None else None
            if qty is None:note('전체 생산수량 미확인: '+record.lot_no);continue
            productions.append({'date':record.work_date,'part':record.part_no,'item':item,'stage':stage(proc),'qty':qty,
                'bad':float(record.fault_qty),'details':view['defects'],
                'shared':view['assembly_group_defects'],'shared_key':(record.work_date,record.process_name,view['equipment_name'],view['shift_type'],tuple(sorted(view['assembly_group_lots'])))})
    else:
        names={p.process_code:p.process_name for p in db.query(ProcessModel)}
        runs=db.query(ProductionRun).filter(ProductionRun.status=='COMPLETED',ProductionRun.performance_date.between(start,end)).all()
        used={r.performance_id for r in runs if r.performance_id}
        for record in runs:
            order=db.get(ProductionWorkOrder,record.work_order_id);item=db.get(ItemMasterModel,order.item_id) if order else None
            details=[{'code':d.defect_type_code,'name':d.defect_type_name,'qty':d.defect_qty} for d in db.query(ProductionRunDefect).filter_by(run_id=record.id)]
            productions.append({'date':record.performance_date,'part':item.part_no if item else order.part_no if order else '', 'item':item,'stage':stage(names.get(record.process_code,record.process_code)),
                'qty':float(record.good_qty+record.defect_qty+record.setup_qty),'bad':float(record.defect_qty),'details':details,'shared':[]})
        for record in db.query(ProductionPerformance).filter(ProductionPerformance.performance_date.between(start,end)):
            if record.id in used:continue
            order=db.get(ProductionWorkOrder,record.work_order_id);item=db.get(ItemMasterModel,order.item_id) if order else None
            productions.append({'date':record.performance_date,'part':item.part_no if item else order.part_no if order else '', 'item':item,'stage':stage(names.get(record.process_code,record.process_code)),
                'qty':float(record.good_qty+record.defect_qty+record.setup_qty),'bad':float(record.defect_qty),'details':[],'shared':[]})
    shared=defaultdict(list)
    for row in productions:
        m=m_for(row['date']);kind=row['stage']
        if kind in ('LATHE','TAP'):
            m['process'][kind]['qty']+=row['qty'];m['process'][kind]['bad']+=row['bad']
            add_types(m['process_types'],row['details'],row['bad'],{'찍힘','긁힘','치수','칩','소재','오조립','조도'},issues,'공정')
        elif kind=='ASSEMBLY':
            # Reference row in the process sheet must show actual assembly work,
            # including work that still needs a final-inspection product group.
            m['assembly']['qty']+=row['qty'];m['assembly']['bad']+=row['bad']
            group=final_group(row['item'],row['part'],connections)
            if not group:note('최종검사 품목군 미연결: '+row['part']);continue
            m['final'][group]['qty']+=row['qty'];m['final'][group]['bad']+=row['bad']
            if row['shared']:shared[row['shared_key']].append(row)
            else:add_types(m['final_types'],row['details'],row['bad'],{'찍힘','긁힘','치수','칩','소재','조도'},issues,'조립')
        elif row['qty']:note('보고서 대상 외 공정 제외: '+row['part'])
    for rows in shared.values():
        add_types(m_for(rows[0]['date'])['final_types'],rows[0]['shared'],sum(r['bad'] for r in rows),{'찍힘','긁힘','치수','칩','소재','조도'},issues,'조립 공유 LOT')
    if not productions:note('보고 기간의 '+('외부' if source=='EXTERNAL' else 'MES')+' 생산실적이 없습니다.')
    if source=='EXTERNAL':outs=external_rows(db,start,end)
    else:outs=[r for r in subcontract_defect_status(db,start,end,limit=10**9)['items'] if r.get('record_source')=='MES']
    cols={c['code']:c['name'] for c in report_types(db)}
    for row in outs:
        if row.get('unallocated') and row.get('inbound_qty') is None and not row.get('defect_qty'):continue
        partner=row['partner_name'] or ''
        if source=='EXTERNAL' and not partner:
            choices={v for v in [row['raw'].get('APPLY_COMP_NM',''),row['raw'].get('ORDER_COMP_NM','')] if any(n in v.replace(' ','') for n in ('메인텍','혜성금속'))}
            if len(choices)==1:partner=choices.pop()
        groups=[name for name in ('메인텍','혜성금속') if name in partner.replace(' ','')]
        if len(groups)!=1:note('외주 업체 분류 미연결: '+(partner or row['part_no']));continue
        if row['inbound_qty'] is None:note('외주 입고수량 미연결: '+row['part_no']);continue
        m=m_for(row['date']);bucket=m['subcontract'][groups[0]]
        bucket['qty']+=float(row['inbound_qty']);bucket['bad']+=float(row['defect_qty'])
        details=[{'name':cols.get(code,'기타'),'qty':qty} for code,qty in row['by_type'].items() if qty is not None]
        add_types(m['subcontract_types'],details,float(row['defect_qty']),{'찍힘','긁힘','박리','얼룩','치수'},issues,'외주')
    for saved in db.query(QualityPerformanceMonth).filter(QualityPerformanceMonth.month.between(f'{year}-01',month)):
        months[int(saved.month[5:7])-1]['manual']=blank_manual()|json.loads(saved.data_json)
    for index,m in enumerate(months[:selected],1):
        if m['manual']['delivery_qty'] is None or m['manual']['customer_bad'] is None or m['manual']['customer_cases'] is None:note(f'{index}월 고객불량 수기 자료 미입력')
    issues.add('원자재 불량수량은 사용자 지정에 따라 0으로 집계합니다.')
    issues.add('생산수량은 양품·불량·셋업 합계이며, 조립실적은 공정불량에서 제외하고 최종검사에만 반영합니다.')
    for vendor in vendors:
        if not any(name in vendor.replace(' ','') for name in ('한국고무','성민전자','성재금속','황해정밀')):note('원본 업체 차트에 없는 입고 거래처: '+vendor)
    if year!=2026:issues.add('이전 연도 비교 실적은 해당 연도 원장이 확인되지 않아 공란입니다.')
    row=db.query(QualityPerformanceMonth).filter_by(month=month).one_or_none()
    def rate(bad,qty,scale=1):return bad/qty*scale if bad is not None and qty is not None and qty else None
    overview=[]
    for index,m in enumerate(months[:selected],1):
        pqty=sum(r['qty'] for r in m['process'].values());pbad=sum(r['bad'] for r in m['process'].values())
        fqty=sum(r['qty'] for r in m['final'].values());fbad=sum(r['bad'] for r in m['final'].values())
        sqty=sum(r['qty'] for r in m['subcontract'].values());sbad=sum(r['bad'] for r in m['subcontract'].values())
        claims=[g['occurred'] for g in m['manual']['claims'].values()];claim=sum(claims) if all(v is not None for v in claims) else None
        overview.append({'month':f'{year}-{index:02d}','raw_qty':m['raw'],'process_qty':pqty,'process_bad':pbad,'process_rate':rate(pbad,pqty),
            'subcontract_qty':sqty,'subcontract_bad':sbad,'subcontract_rate':rate(sbad,sqty),
            'final_qty':fqty,'final_bad':fbad,'final_rate':rate(fbad,fqty),'customer_ppm':rate(m['manual']['customer_bad'],m['manual']['delivery_qty'],1000000),
            'claim_rate':rate(claim,m['manual']['revenue'])})
    return {'month':month,'source':source,'version':row.version if row else 0,'goals':GOALS,'months':months,'overview':overview,'issues':sorted(issues),'manual':months[selected-1]['manual'],'raw_vendors':dict(vendors)}

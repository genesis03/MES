"""Monthly numbered plans with audited edits and an applied-plan projection."""
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace as Object
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from fastapi import HTTPException
from sqlalchemy import update, func
from sqlalchemy.exc import IntegrityError
from models.production_month_plan import ProductionMonthPlanLine, ProductionMonthPlanDay, ProductionPlanWorkTime
from models.production_plan_revision import ProductionPlanRevisionState as State, ProductionPlanRevision as Revision, ProductionPlanRevisionReference as Reference, ProductionPlanRevisionApplication as Application, ProductionPlanRevisionEdit as Edit, ProductionPlanRevisionMigration as Migration


def label(number):return f'Rev.{number:02d}'
def actor(user):return str(getattr(user,'username','') or 'unknown')
def timestamp(value):return value.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') if value else ''


def capture(db,month):
    from models.equipment import EquipmentMaster
    from services.oee_schedule import month_days,default_excluded_minutes
    lines=db.query(ProductionMonthPlanLine).filter_by(month=month).order_by(ProductionMonthPlanLine.equipment_id,ProductionMonthPlanLine.item_id).all()
    times={(r.equipment_id,r.work_date):{'equipment_id':r.equipment_id,'work_date':r.work_date,'start_time':r.start_time,
        'end_time':r.end_time,'next_day':r.next_day,'excluded_minutes':r.excluded_minutes}
        for r in db.query(ProductionPlanWorkTime).filter(ProductionPlanWorkTime.work_date.like(month+'-%'))}
    equipment={e.id:e for e in db.query(EquipmentMaster)}
    for line in lines:
        for day in month_days(month):
            key=(line.equipment_id,day.isoformat())
            times.setdefault(key,{'equipment_id':key[0],'work_date':key[1],'start_time':'08:00','end_time':'08:00',
                'next_day':True,'excluded_minutes':default_excluded_minutes(equipment[key[0]].process_code)})
    return {'month':month,'lines':[{'equipment_id':r.equipment_id,'item_id':r.item_id,'cycle_seconds':r.cycle_seconds,
        'expected_oee':r.expected_oee,'days':[{'work_date':d.work_date,'quantity':d.quantity,'cycle_seconds':d.cycle_seconds,
        'expected_oee':d.expected_oee} for d in sorted(r.days,key=lambda d:d.work_date)]} for r in lines],
        'times':[times[key] for key in sorted(times)]}


def encoded(data):return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def add_revision(db,month,number,snapshot,reason,username):
    revision=Revision(month=month,number=number,snapshot_json=encoded(snapshot),reason=reason,created_by=username)
    db.add(revision);db.flush()
    db.add_all([Reference(revision_id=revision.id,item_id=r['item_id'],equipment_id=r['equipment_id']) for r in snapshot['lines']])
    return revision


def ensure(db,month):
    state=db.get(State,month)
    if state is None:
        state=State(month=month,applied_revision=0,generation=0)
        db.add(state)
        try:
            db.flush()
            revision=add_revision(db,month,0,capture(db,month),'기존 생산계획 최초 보존','system')
            db.add(Application(month=month,revision_id=revision.id,reason='기존 적용 계획 보존',applied_by='system'))
            db.add(Migration(month=month))
            db.commit()
        except IntegrityError:
            db.rollback()
        state=db.get(State,month)
        if state is None:raise HTTPException(409,'계획 버전을 초기화하지 못했습니다. 다시 불러오세요.')
    return state


def lock(db,month,generation=None):
    migrate_legacy_draft(db,month)
    state=ensure(db,month)
    expected=state.generation if generation is None else generation
    result=db.execute(update(State).where(State.month==month,State.generation==expected).values(generation=State.generation+1))
    if result.rowcount!=1:
        db.rollback();raise HTTPException(409,'다른 사용자가 해당 월 계획을 변경했습니다. 다시 불러온 뒤 저장하세요.')
    db.refresh(state)
    return state


def snapshot(db,month,number):
    revision=db.query(Revision).filter_by(month=month,number=number).first()
    if revision is None:raise HTTPException(404,'해당 월의 Revision을 찾을 수 없습니다.')
    return json.loads(revision.snapshot_json)|{'_revision_number':number}


def migrate_legacy_draft(db,month):
    state=ensure(db,month)
    if db.get(Migration,month) is not None:return
    expected=state.generation
    result=db.execute(update(State).where(State.month==month,State.generation==expected).values(generation=State.generation+1))
    if result.rowcount!=1:
        db.rollback();raise HTTPException(409,'계획 전환 중입니다. 다시 조회하세요.')
    data=capture(db,month)
    existing=db.query(Revision).filter_by(month=month).all()
    if not any(r.snapshot_json==encoded(data) for r in existing):
        add_revision(db,month,max(r.number for r in existing)+1,data,'기존 작업본 보존 (Rev 전용 전환)','system')
    active=next(r for r in existing if r.number==state.applied_revision)
    sync_applied(db,month,json.loads(active.snapshot_json))
    db.add(Migration(month=month));db.commit()


def details(db,month):
    migrate_legacy_draft(db,month)
    state=ensure(db,month)
    revisions=db.query(Revision).filter_by(month=month).order_by(Revision.number.desc()).all()
    return {'month':month,'generation':state.generation,'applied_revision':state.applied_revision,
        'applied_label':label(state.applied_revision),
        'saved_by':state.saved_by or '', 'saved_at':timestamp(state.saved_at),
        'revisions':[{'number':r.number,'label':label(r.number),'reason':r.reason,'created_by':r.created_by,
                      'created_at':timestamp(r.created_at)} for r in revisions],
        'edits':[{'id':e.id,'number':number,'process_code':e.process_code,'edited_by':e.edited_by,
                  'edited_at':timestamp(e.edited_at),'reason':e.reason}
                 for e,number in db.query(Edit,Revision.number).join(Revision,Revision.id==Edit.revision_id)
                 .filter(Revision.month==month).order_by(Edit.id.desc()).limit(100)],
        'applications':[{'revision':db.get(Revision,a.revision_id).number,'reason':a.reason,'applied_by':a.applied_by,
                         'applied_at':timestamp(a.applied_at)} for a in db.query(Application).filter_by(month=month).order_by(Application.id.desc()).limit(100)]}


def copy_revision(db,month,number,reason,user,generation):
    state=lock(db,month,generation)
    data=snapshot(db,month,state.applied_revision if number is None else number)
    data.pop('_revision_number',None)
    latest=db.query(Revision).filter_by(month=month).order_by(Revision.number.desc()).first()
    revision=add_revision(db,month,latest.number+1,data,reason,actor(user))
    created=revision.number
    db.commit()
    return details(db,month)|{'created_revision':created}


def sync_applied(db,month,data):
    """Maintain the legacy tables as the applied-plan projection, never a separate draft."""
    lines={(r.equipment_id,r.item_id):r for r in db.query(ProductionMonthPlanLine).filter_by(month=month)}
    keep={(r['equipment_id'],r['item_id']) for r in data['lines']}
    for key,row in lines.items():
        if key not in keep:db.delete(row)
    for value in data['lines']:
        key=(value['equipment_id'],value['item_id']);row=lines.get(key)
        if row is None:
            row=ProductionMonthPlanLine(month=month,equipment_id=key[0],item_id=key[1],cycle_seconds=value['cycle_seconds'],expected_oee=value['expected_oee']);db.add(row);db.flush()
        row.cycle_seconds=value['cycle_seconds'];row.expected_oee=value['expected_oee']
        saved={d.work_date:d for d in row.days};wanted={d['work_date'] for d in value['days']}
        for day in row.days:
            if day.work_date not in wanted:db.delete(day)
        for day in value['days']:
            target=saved.get(day['work_date'])
            if target is None:target=ProductionMonthPlanDay(line_id=row.id,work_date=day['work_date']);db.add(target)
            target.quantity=day['quantity'];target.cycle_seconds=day['cycle_seconds'];target.expected_oee=day['expected_oee']
    times={(r.equipment_id,r.work_date):r for r in db.query(ProductionPlanWorkTime).filter(ProductionPlanWorkTime.work_date.like(month+'-%'))}
    keep={(r['equipment_id'],r['work_date']) for r in data['times']}
    for key,row in times.items():
        if key not in keep:db.delete(row)
    for value in data['times']:
        row=times.get((value['equipment_id'],value['work_date']))
        if row is None:row=ProductionPlanWorkTime(equipment_id=value['equipment_id'],work_date=value['work_date']);db.add(row)
        for key in ('start_time','end_time','next_day','excluded_minutes'):setattr(row,key,value[key])


def apply(db,month,number,reason,user,generation):
    state=lock(db,month,generation)
    revision=db.query(Revision).filter_by(month=month,number=number).first()
    if revision is None:db.rollback();raise HTTPException(404,'해당 월의 Revision이 없습니다.')
    state.applied_revision=number
    sync_applied(db,month,json.loads(revision.snapshot_json))
    db.add(Application(month=month,revision_id=revision.id,reason=reason,applied_by=actor(user)))
    db.commit()
    return details(db,month)


def edit_revision(db,month,number,data,process_code,user,generation,reason=''):
    state=lock(db,month,generation)
    revision=db.query(Revision).filter_by(month=month,number=number).first()
    if revision is None:db.rollback();raise HTTPException(404,'해당 월의 Revision이 없습니다.')
    after=encoded(data)
    if after==revision.snapshot_json:
        db.rollback();return details(db,month)
    db.add(Edit(revision_id=revision.id,process_code=process_code,before_json=revision.snapshot_json,
                after_json=after,reason=reason.strip() or label(number)+' 직접 수정',edited_by=actor(user)))
    revision.snapshot_json=after
    if state.applied_revision==number:
        sync_applied(db,month,data)
    state.saved_by=actor(user);state.saved_at=datetime.utcnow()
    # Keep old references too: before-images remain part of the audit history.
    refs={(r.item_id,r.equipment_id) for r in db.query(Reference).filter_by(revision_id=revision.id)}
    for line in data['lines']:
        key=(line['item_id'],line['equipment_id'])
        if key not in refs:
            db.add(Reference(revision_id=revision.id,item_id=key[0],equipment_id=key[1]));refs.add(key)
    db.commit()
    return details(db,month)


def applied_snapshots(db,first,last):
    return {s.month:json.loads(r.snapshot_json)|{'_revision_number':r.number,'_snapshot_hash':hashlib.sha256(r.snapshot_json.encode()).hexdigest()} for s,r in db.query(State,Revision).join(Revision,
        (Revision.month==State.month)&(Revision.number==State.applied_revision)).filter(
        State.month>=first.strftime('%Y-%m'),State.month<=last.strftime('%Y-%m'))}


@dataclass
class PlanData:
    rows:list
    times:dict
    revisions:list
    def __iter__(self):return iter((self.rows,self.times))
    def __getitem__(self,index):return (self.rows,self.times)[index]


def plan_data(db,first,last,applied=True,selected=None):
    """Return day/line pairs and saved hours using one consistent plan source."""
    snapshots=({selected['month']:selected} if selected is not None else applied_snapshots(db,first,last) if applied else {})
    query=db.query(ProductionMonthPlanDay,ProductionMonthPlanLine).join(ProductionMonthPlanLine,
        ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).filter(
        ProductionMonthPlanDay.work_date>=str(first),ProductionMonthPlanDay.work_date<=str(last))
    rows=[]
    if selected is None:
        if snapshots:query=query.filter(ProductionMonthPlanLine.month.notin_(list(snapshots)))
        rows=list(query)
    times={}
    if selected is None:
        query_times=db.query(ProductionPlanWorkTime).filter(ProductionPlanWorkTime.work_date>=str(first),ProductionPlanWorkTime.work_date<=str(last))
        if snapshots:query_times=query_times.filter(func.substr(ProductionPlanWorkTime.work_date,1,7).notin_(list(snapshots)))
        times={(r.equipment_id,r.work_date):r for r in query_times}
    for month,data in snapshots.items():
        for line in data['lines']:
            obj=Object(**{k:v for k,v in line.items() if k!='days'},month=month)
            rows.extend((Object(**day),obj) for day in line['days'] if str(first)<=day['work_date']<=str(last))
        for r in data['times']:
            if str(first)<=r['work_date']<=str(last):times[r['equipment_id'],r['work_date']]=Object(**r)
    if applied and selected is None:
        # A switch during initial data loading must never mix two applied plans.
        latest=applied_snapshots(db,first,last)
        if {m:(d['_revision_number'],d['_snapshot_hash']) for m,d in snapshots.items()}!={m:(d['_revision_number'],d['_snapshot_hash']) for m,d in latest.items()}:
            raise HTTPException(409,'적용 생산계획이 변경되었습니다. 다시 조회하세요.')
    months={line.month for _,line in rows}|set(snapshots)
    info=[{'month':m,'number':snapshots.get(m,{}).get('_revision_number',0),
           'label':label(snapshots.get(m,{}).get('_revision_number',0))} for m in sorted(months)]
    return PlanData(rows,times,info)


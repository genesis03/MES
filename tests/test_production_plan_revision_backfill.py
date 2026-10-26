from datetime import date,datetime
import json
from test_production_sync import setup
from test_production_month_plan import initialize
from test_equipment_oee import plan_ct
from models.production_month_plan import ProductionMonthPlanLine,ProductionMonthPlanDay,ProductionPlanWorkTime
from models.production_plan_revision import ProductionPlanRevision,ProductionPlanRevisionState
from services.production_plan_revision_backfill import ensure_existing_plan_revisions
from services.production_plan_revisions import ensure,add_revision,capture
from services.equipment_oee import calculate


def test_all_saved_months_get_rev_zero_without_opening_them(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    with setup.sessions() as db:
        for month in range(1,13):plan_ct(db,equipment,item,f'2026-{month:02d}-12',20+month)
        db.commit()
        before=calculate(db,date(2026,9,12),date(2026,9,12),now=datetime(2026,10,20))
    assert ensure_existing_plan_revisions(setup.engine)==12
    with setup.sessions() as db:
        states=db.query(ProductionPlanRevisionState).all()
        revisions=db.query(ProductionPlanRevision).all()
        assert {s.month for s in states}=={f'2026-{m:02d}' for m in range(1,13)}
        assert len(revisions)==12 and all(r.number==0 for r in revisions)
        assert all(s.applied_revision==0 and s.generation==0 for s in states)
        for revision in revisions:
            line=json.loads(revision.snapshot_json)['lines'][0]
            assert line['cycle_seconds']==20+int(revision.month[-2:])
            assert line['days'][0]['quantity']==1000
        # Initializing revisions freezes snapshots; existing plan rows/hours are preserved.
        assert db.query(ProductionMonthPlanLine).count()==12
        assert db.query(ProductionMonthPlanDay).count()==12
        assert db.query(ProductionPlanWorkTime).count()==0
        after=calculate(db,date(2026,9,12),date(2026,9,12),now=datetime(2026,10,20))
        assert before['summary']['planned_qty']==after['summary']['planned_qty']
        assert before['planned_seconds']==after['planned_seconds']
    assert ensure_existing_plan_revisions(setup.engine)==0
    with setup.sessions() as db:assert db.query(ProductionPlanRevision).count()==12


def test_existing_revisions_and_applied_selection_are_not_reinitialized(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    with setup.sessions() as db:
        for month in ('2026-01','2026-02','2026-10'):plan_ct(db,equipment,item,month+'-12',30)
        db.commit()
        state=ensure(db,'2026-10')
        data=capture(db,'2026-10');data['lines'][0]['cycle_seconds']=99
        add_revision(db,'2026-10',1,data,'기존 개정','tester')
        state.applied_revision=1;state.generation=7;db.commit()
        original=[(r.id,r.number,r.snapshot_json,r.created_at) for r in db.query(ProductionPlanRevision).order_by(ProductionPlanRevision.id)]
    assert ensure_existing_plan_revisions(setup.engine)==2
    with setup.sessions() as db:
        assert [(r.id,r.number,r.snapshot_json,r.created_at) for r in db.query(ProductionPlanRevision).filter_by(month='2026-10').order_by(ProductionPlanRevision.id)]==original
        state=db.get(ProductionPlanRevisionState,'2026-10')
        assert state.applied_revision==1 and state.generation==7
        assert db.query(ProductionPlanRevision).count()==4

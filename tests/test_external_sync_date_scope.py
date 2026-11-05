"""All sync targets honor the recorded date range, including automatic runs."""
import json
from datetime import timedelta
import pytest
from test_production_sync import setup, DAY, row as production_row
from test_packing_sync import row as packing_row
from test_subcontract_fault_sync import row as subcontract_row
from test_shipment_sync import record as shipment_record
from services import production_sync_service as sync


def historical_source(kind, day):
    if kind=='production':return production_row(day=day)
    if kind=='packing':return packing_row(day=day)
    if kind=='subcontract':return subcontract_row(CONFIRM_DATE=day.isoformat())
    record=shipment_record();record['header']['OUT_DATE']=day.isoformat()
    return record


@pytest.mark.parametrize('kind',['production','packing','subcontract','shipment'])
def test_auto_does_not_revisit_known_history_manual_can_refresh_it(setup,monkeypatch,kind):
    old=DAY-timedelta(days=100)
    source=historical_source(kind,old)
    State,Run,Record,Client=sync.backend(kind)
    token,_=sync.claim(old,old,'MANUAL',kind)
    sync.save_day([source],token,kind,queried_day=old)
    with setup.sessions() as db:
        state=db.get(State,1);state.enabled=True;state.lease_token=None;state.lease_until=None;state.next_run_at=None
        stored=db.query(Record).one();before=(stored.content_hash,stored.last_seen_at)
        db.commit()
    visited=[]
    def fetch_day(self,day):
        visited.append(day)
        self.query_errors=[]
        return ([source] if day==old else []),[]
    monkeypatch.setattr(Client,'__init__',lambda self,*a:None)
    monkeypatch.setattr(Client,'fetch_day',fetch_day)
    token,run_id=sync.claim(DAY,DAY+timedelta(days=1),'AUTO',kind)
    sync.execute(DAY,DAY+timedelta(days=1),token,run_id,kind)
    assert visited==[DAY,DAY+timedelta(days=1)]
    with setup.sessions() as db:
        run=db.get(Run,run_id);counts=json.loads(run.counts_json)
        assert run.status=='SUCCESS'
        assert counts['received']==0 and counts['unchanged']==0 and counts['completed_days']==2
        assert run.start_date==DAY.isoformat() and run.end_date==(DAY+timedelta(days=1)).isoformat()
        stored=db.query(Record).one()
        assert (stored.content_hash,stored.last_seen_at)==before
    token,run_id=sync.claim(old,old,'MANUAL',kind)
    sync.execute(old,old,token,run_id,kind)
    assert visited[-1]==old
    with setup.sessions() as db:
        run=db.get(Run,run_id);counts=json.loads(run.counts_json)
        assert run.status=='SUCCESS' and counts['received']==1 and counts['unchanged']==1
        assert db.query(Record).count()==1

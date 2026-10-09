from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from routers import subcontract_inbound as api
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundMaster


class Database:
    def __init__(self,status):
        self.rows={SubcontractInboundMaster:SimpleNamespace(id=1,outbound_id=2),SubcontractOutboundMaster:SimpleNamespace(id=2,status=status)}
    def get(self,model,key):
        row=self.rows.get(model)
        return row if row and row.id==key else None


def test_cancelled_outbound_allows_existing_receipt_but_not_new_receipt(monkeypatch):
    db=Database('CANCELLED')
    monkeypatch.setattr(api,'_serialize_outbound_source',lambda db,outbound:{'outbound_id':outbound.id,'status':outbound.status})
    assert api.get_existing_inbound_source(1,db,None)=={'outbound_id':2,'status':'CANCELLED'}
    with pytest.raises(HTTPException) as exc:api.get_inbound_source(2,db,None)
    assert exc.value.status_code==409


def test_existing_source_requires_matching_receipt_and_outbound(monkeypatch):
    db=Database('OUTBOUND')
    with pytest.raises(HTTPException) as exc:api.get_existing_inbound_source(3,db,None)
    assert exc.value.status_code==404
    db.rows.pop(SubcontractOutboundMaster)
    with pytest.raises(HTTPException) as exc:api.get_existing_inbound_source(1,db,None)
    assert exc.value.status_code==404

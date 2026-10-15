"""Cancelled receipts must not contribute to defect inquiries or totals."""
import pytest
from sqlalchemy import text

from test_production_sync import setup, add_item
from models.models import PurchaseInboundItem, PurchaseInboundMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundMaster
from models.quality import QualityInboundDefectDetail, QualityInboundResult
from routers.quality_defect_status import router


@pytest.mark.parametrize('source', ['GENERAL', 'SUBCONTRACT'])
def test_cancelled_receipt_defects_excluded_from_rows_and_totals(setup, source):
    setup.app.include_router(router)
    with setup.sessions() as db:
        # Unrelated order/warehouse references are outside this inquiry test.
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item_id = add_item(db, part='310062-Ag')
        masters = []
        for index, qty in enumerate([2, 4, 11], start=1):
            common = dict(id=index, inbound_no=f'IN-{index}', inbound_date='2026-09-23',
                          partner_id=1, partner_name='검증 거래처')
            if source == 'GENERAL':
                master = PurchaseInboundMaster(**common, status='CANCELLED' if index == 3 else 'CONFIRMED')
                item = PurchaseInboundItem(id=index, inbound_id=index, item_id=item_id,
                    part_no='310062-Ag', unit='EA', inbound_qty=660, supplier_lot_no=f'LOT-{index}',
                    storage_location='TEST', unit_price=0)
            else:
                master = SubcontractInboundMaster(**common, status='CANCELLED' if index == 3 else 'RECEIVED',
                    outbound_id=1, outbound_no='OUT-1', order_id=1, order_no='ORDER-1',
                    processing_type_code='PLATE', processing_type_name='은도금', storage_location='TEST')
                item = SubcontractInboundItem(id=index, inbound_id=index, outbound_item_id=1,
                    order_item_id=1, previous_item_id=item_id, item_id=item_id,
                    previous_part_no='310062-Ag', part_no='310062-Ag', part_name='검증 품목',
                    unit='EA', outbound_qty=660, good_qty=660, defect_qty=0)
            db.add(master); db.flush(); db.add(item); db.flush()
            result = QualityInboundResult(source_type=source, inbound_id=index, inbound_item_id=index,
                inspection_status='COMPLETED', defect_qty=qty)
            db.add(result); db.flush()
            db.add(QualityInboundDefectDetail(result_id=result.id, source_type=source,
                inbound_item_id=index, defect_type_code='TEST', defect_qty=qty))
            masters.append(master)
        db.commit()

        for filter_source in ['ALL', source]:
            response = setup.client.get('/api/quality/defect-status', params={
                'start_date': '2026-09-01', 'end_date': '2026-09-30', 'source': filter_source})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['total'] == 2
            assert data['total_defect_qty'] == 6
            assert sorted(row['defect_qty'] for row in data['items']) == [2, 4]

        for master in masters:
            master.status = 'CANCELLED'
        db.commit()
        data = setup.client.get('/api/quality/defect-status').json()
        assert data['items'] == []
        assert data['total'] == 0
        assert data['total_defect_qty'] == 0
        # Cancellation hides history without deleting quality records.
        assert db.query(QualityInboundResult).count() == 3
        assert db.query(QualityInboundDefectDetail).count() == 3

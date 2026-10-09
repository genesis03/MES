import io
from openpyxl import Workbook
from test_production_sync import setup, ADMIN
from test_packing_sync import add_finished
from core.security import require_admin_user
from models.partner import Partner
from models.sales import SalesOrderMaster, SalesOrderItem
from routers.sales_unsold_import import router
from services.sales_order_service import sync_order_status


def workbook(qty=120, customer='대성하이피오토㈜'):
    w=Workbook();s=w.active;s.append(['회사명']);s.append(['일자-No.','거래처명','품번','품목명','수주수량','미판매수량','단가','미판매공급가액','적요','품목별납기일자'])
    s.append(['2026/09/23 -1',customer,'SOURCE','Terminal',240,qty,1,qty,None,None]);s.append(['총합계',None,None,None,240,qty])
    b=io.BytesIO();w.save(b);return b.getvalue()


def initialize(setup):
    setup.app.include_router(router);setup.app.dependency_overrides[require_admin_user]=lambda:ADMIN
    with setup.sessions() as db:
        item=add_finished(db);customer=Partner(partner_code='C1',partner_name='대성하이피오토(주)',partner_type='CUSTOMER',is_active='Y');db.add(customer);db.flush()
        order=SalesOrderMaster(order_no='OLD',order_date='2026-01-01',customer_id=customer.id,customer_name=customer.partner_name,status='PARTIAL')
        order.items.append(SalesOrderItem(item_id=item.id,part_no=item.part_no,order_qty=300,shipped_qty=100,status='PARTIAL'));db.add(order);db.commit()


def post(setup, route, data, fingerprint=None):
    return setup.client.post('/api/sales/unsold/import/'+route,files={'file':('test.xlsx',data)},data={'fingerprint':fingerprint} if fingerprint else {})


def test_replace_preserves_history_and_blocks_repeat(setup):
    initialize(setup);data=workbook();preview=post(setup,'preview',data)
    assert preview.status_code==200,preview.text
    assert preview.json()['close_qty']==200
    assert post(setup,'apply',data,preview.json()['fingerprint']).status_code==200
    with setup.sessions() as db:
        old=db.query(SalesOrderMaster).filter_by(order_no='OLD').one()
        assert old.status=='CANCELLED' and old.items[0].order_qty==300 and old.items[0].shipped_qty==100
        sync_order_status(old);assert old.status=='CANCELLED'
        new=db.query(SalesOrderMaster).filter_by(status='ORDERED').one();assert new.items[0].order_qty==120 and new.items[0].shipped_qty==0
    assert post(setup,'preview',data).status_code==409


def test_invalid_and_stale_preview_do_not_close_orders(setup):
    initialize(setup)
    assert post(setup,'preview',workbook(-1)).status_code==422
    assert post(setup,'preview',workbook(customer='unknown')).status_code==409
    data=workbook();preview=post(setup,'preview',data).json()
    with setup.sessions() as db:
        row=db.query(SalesOrderItem).one();row.shipped_qty=101;db.commit()
    assert post(setup,'apply',data,preview['fingerprint']).status_code==409
    with setup.sessions() as db:assert db.query(SalesOrderMaster).one().status=='PARTIAL'

from test_production_sync import setup, add_item, ADMIN
from core import security
from models.models import ItemMasterModel, ManualLabelModel
from routers.manual import router


def test_manual_label_keeps_edited_name_and_item_identity(setup, monkeypatch):
    monkeypatch.setattr(security, 'get_current_user', lambda *args: ADMIN)
    setup.app.include_router(router)
    with setup.sessions() as db:
        item_id = add_item(db, part='MANUAL-A')
        db.get(ItemMasterModel, item_id).part_name = '마스터 품명'
        db.commit()
    assert setup.client.get('/api/manual/item', params={'part_no': ' MANUAL-A '}).json() == {'part_name': '마스터 품명'}
    assert setup.client.get('/api/manual/item', params={'part_no': 'UNKNOWN'}).json() == {'part_name': None}
    for part, name in [('MANUAL-A', '라벨용 수정 품명'), ('MANUAL-A', ''), ('UNKNOWN', '수기 품명')]:
        response = setup.client.post('/api/manual/add', json={
            'part_no': part, 'part_name': name, 'qty': '120', 'serial': 'TEST',
        })
        assert response.status_code == 200, response.text
    data = setup.client.get('/api/manual/data').json()['data']
    assert [r['part_name'] for r in data] == ['수기 품명', '마스터 품명', '라벨용 수정 품명']
    with setup.sessions() as db:
        records = db.query(ManualLabelModel).order_by(ManualLabelModel.id).all()
        assert [r.item_id for r in records] == [item_id, item_id, None]
        assert db.get(ItemMasterModel, item_id).part_name == '마스터 품명'

from email.message import Message
from urllib.parse import parse_qs,urlencode
import pytest
from test_production_sync import setup
from services import production_sync_client as source
from services.packing_sync_client import PackingClient
from services.subcontract_fault_client import SubcontractFaultClient


class Page:
    headers=Message()
    def __init__(self,raw=b'<input name="ENCRYPT.KEY" value="111">'):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def read(self,*_):return self.raw


@pytest.mark.parametrize('client_type',[source.ProductionClient,PackingClient,SubcontractFaultClient])
def test_initialization_key_supersedes_stale_page_and_invalid_saved_setting(monkeypatch,client_type):
    seen=[]
    class Opener:
        def open(self,*_,**kwargs):return Page()
    opener=Opener()
    monkeypatch.setattr(source,'build_opener',lambda *_:opener)
    monkeypatch.setattr(source.config,'PRODUCTION_SYNC_ENCRYPT_KEY','not-a-key')
    def post(same,payload):
        assert same is opener;seen.append(payload)
        if payload.get('SO.INIT.LOAD')=='Y':
            assert payload['ENCRYPT.KEY']=='' and payload['CHECK.ENCRYPT.KEY']==''
            assert not any('LoginShortSet' in key for key in payload)
            return {'ENCRYPT.KEY':['939'],'SO.NET.END':['SO.NET.END']}
        assert payload['ENCRYPT.KEY']=='939'
        return {'Common.Set.LoginUserSet.USER_ID':['test-user'],'SO.NET.END':['SO.NET.END']}
    monkeypatch.setattr(source,'post',post)
    client=client_type('test-user','test-password')
    assert client.encrypt_key=='939' and client.encrypt_key_source=='initialization'
    assert len(seen)==2 and source.client_common(client)['ENCRYPT.KEY']=='939'


@pytest.mark.parametrize('keys',[[''],['bad'],['123456789'],['939,940'],['939','940']])
def test_invalid_initialization_key_does_not_fall_back_or_send_credentials(monkeypatch,keys):
    class Opener:
        def open(self,*_,**kwargs):return Page()
    monkeypatch.setattr(source,'build_opener',lambda *_:Opener())
    seen=[]
    def post(_,payload):
        seen.append(payload)
        return {'ENCRYPT.KEY':keys,'ENCRYPT.ID':['private-id'],'ENCRYPT.IV':['private-iv'],'SO.NET.END':['SO.NET.END']}
    monkeypatch.setattr(source,'post',post)
    with pytest.raises(source.SyncError) as error:source.ProductionClient('test-user','test-password')
    assert len(seen)==1 and seen[0]['SO.INIT.LOAD']=='Y'
    assert error.value.diagnostic['stage']=='initialization'
    assert error.value.diagnostic['error_kind']=='protocol_key_invalid'
    assert 'private-id' not in str(error.value)+str(error.value.diagnostic)
    assert 'private-iv' not in str(error.value)+str(error.value.diagnostic)
    assert 'test-password' not in str(error.value)+str(error.value.diagnostic)


def test_initialization_exception_has_its_own_safe_diagnostic():
    class Opener:
        def open(self,request,**kwargs):
            payload=parse_qs(request.data.decode(),keep_blank_values=True)
            assert payload['SO.INIT.LOAD']==['Y'] and payload['ENCRYPT.KEY']==['']
            return Page(urlencode({'THROW.TYPE':'System.Exception','THROW.MESS':'private server SQL',
                'ENCRYPT.ID':'private-id','SO.NET.END':'SO.NET.END'}).encode())
    with pytest.raises(source.SyncError) as error:source.initialize_protocol_key(Opener())
    assert error.value.diagnostic['stage']=='initialization'
    assert error.value.diagnostic['error_kind']=='server_exception'
    assert 'private server SQL' not in str(error.value)+str(error.value.diagnostic)
    assert 'private-id' not in str(error.value)+str(error.value.diagnostic)


def test_login_check_reports_auto_key_without_exposing_credentials(setup,monkeypatch):
    from types import SimpleNamespace
    from routers import production_sync as api
    monkeypatch.setattr(api,'ProductionClient',lambda *_:SimpleNamespace(encrypt_key_source='initialization'))
    result=setup.client.post('/api/production/external-sync/credentials/test',json={'username':'test-user','password':'test-password'})
    assert result.status_code==200,result.text
    assert result.json()['protocol_key_source']=='initialization'
    assert '자동' in result.json()['message'] and 'test-password' not in result.text

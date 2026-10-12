from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from test_production_sync import setup
from core.security import hash_password
from models.models import UserModel
from models.auth_session import AuthSession, utc_now
from routers.auth import router
from services.auth_session_service import issue_token, verify_username, token_digest


def prepare(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        for name in ['operator','other']:
            db.add(UserModel(username=name,name=name,password_hash=hash_password('test-pass'),role='USER',created_at='2026-10-12'))
        db.commit()


def login(client,name='operator',password='test-pass'):
    return client.post('/api/login',json={'username':name,'password':password})


def test_other_browser_revokes_old_session_same_browser_keeps_token(setup):
    prepare(setup)
    with TestClient(setup.app) as first,TestClient(setup.app) as second:
        assert login(first).status_code==200
        original=first.cookies.get('session_token')
        with setup.sessions() as db:
            before=db.get(AuthSession,token_digest(original)).expires_at
        assert login(first).status_code==200
        assert first.cookies.get('session_token')==original
        with setup.sessions() as db:
            assert db.query(AuthSession).count()==1
            assert db.get(AuthSession,token_digest(original)).expires_at==before
        assert login(second).status_code==200
        assert second.cookies.get('session_token')!=original
        assert first.get('/api/session').status_code==401
        assert second.get('/api/session').status_code==200
        with setup.sessions() as db:
            assert verify_username(original,db) is None
            assert verify_username(second.cookies.get('session_token'),db)=='operator'


def test_bad_password_preserves_login_and_switching_accounts_revokes_old_cookie(setup):
    prepare(setup)
    with TestClient(setup.app) as first,TestClient(setup.app) as second:
        assert login(first).status_code==200
        original=first.cookies.get('session_token')
        assert login(second,password='wrong').status_code==400
        assert first.get('/api/session').status_code==200
        assert login(second,'other').status_code==200
        assert first.get('/api/session').status_code==200
        assert login(first,'other').status_code==200
        assert second.get('/api/session').status_code==401
        with setup.sessions() as db:
            assert verify_username(original,db) is None
            assert verify_username(first.cookies.get('session_token'),db)=='other'


def test_expired_same_browser_token_is_replaced(setup):
    prepare(setup)
    assert login(setup.client).status_code==200
    old=setup.client.cookies.get('session_token')
    with setup.sessions() as db:
        db.get(AuthSession,token_digest(old)).expires_at=utc_now()-timedelta(seconds=1)
        db.commit()
    assert login(setup.client).status_code==200
    assert setup.client.cookies.get('session_token')!=old
    assert setup.client.get('/api/session').status_code==200


def test_simultaneous_logins_leave_only_one_valid_token(setup):
    prepare(setup)
    def issue(_):
        with setup.sessions() as db:
            token=issue_token('operator',db)
            db.commit()
            return token
    with ThreadPoolExecutor(max_workers=4) as executor:
        tokens=list(executor.map(issue,range(4)))
    with setup.sessions() as db:
        assert sum(verify_username(token,db)=='operator' for token in tokens)==1
        assert db.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).count()==1

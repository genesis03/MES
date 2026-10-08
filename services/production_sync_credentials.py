"""Credentials encrypted at rest with a server-local Fernet key."""
import os
from cryptography.fernet import Fernet, InvalidToken
from core import config
from core.database import SessionLocal
from models.production_sync import ProductionSyncCredential


class CredentialError(Exception):
    pass


def cipher(create=False):
    path = config.PRODUCTION_SYNC_KEY_PATH
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as file:
                    file.write(Fernet.generate_key()); file.flush(); os.fsync(file.fileno())
        return Fernet(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CredentialError('이 서버의 연동 암호화 키를 읽을 수 없습니다. 키를 복원하거나 비밀번호를 다시 설정해 주세요.') from exc


def read_credentials(db=None):
    if db is None:
        with SessionLocal() as session:
            return read_credentials(session)
    row = db.get(ProductionSyncCredential, 1)
    if row is None:
        return config.PRODUCTION_SYNC_USER, config.PRODUCTION_SYNC_PASSWORD
    try:
        password = cipher().decrypt(row.encrypted_password.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError) as exc:
        raise CredentialError('저장된 연동 계정을 이 서버에서 읽을 수 없습니다. 비밀번호를 다시 설정해 주세요.') from exc
    return row.username, password


def credential_status(db):
    row = db.get(ProductionSyncCredential, 1)
    username = row.username if row else config.PRODUCTION_SYNC_USER
    try:
        user, password = read_credentials(db)
        return {'username': username, 'configured': bool(user and password),
                'source': 'screen' if row else 'environment' if user else '', 'error': ''}
    except CredentialError as exc:
        return {'username': username, 'configured': False, 'source': 'screen', 'error': str(exc)}


def save_credentials(db, username, password):
    row = db.get(ProductionSyncCredential, 1)
    if not password:
        old_user, password = read_credentials(db)
        if not password or old_user != username:
            raise CredentialError('처음 저장하거나 아이디를 변경할 때는 비밀번호를 입력해 주세요.')
    encrypted = cipher(create=True).encrypt(password.encode('utf-8')).decode('ascii')
    if row:
        row.username = username; row.encrypted_password = encrypted
    else:
        db.add(ProductionSyncCredential(id=1, username=username, encrypted_password=encrypted))
    db.commit()

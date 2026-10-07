"""도면 이력/원본 보존 및 서버 권한을 운영 DB와 분리하여 검증합니다."""
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

_bootstrap = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_bootstrap.name) / "bootstrap.db")
from core import config
from core.database import Base, get_db, engine as bootstrap_engine
from core.security import create_session_token
from models.document import DocumentFile, ItemDocument, ItemRevision
from models.document_migration import ensure_document_codes
from models.models import ItemMasterModel, UserModel
from models.audit_log import AuditLogModel
from routers import basic_info, documents
from services.audit_log import install_audit_logging, reset_audit_context, set_audit_context
from services.item_identity_service import item_usage_summary


@pytest.fixture(scope="session", autouse=True)
def cleanup_bootstrap():
    yield
    bootstrap_engine.dispose()
    _bootstrap.cleanup()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "test.db"), connect_args={"check_same_thread": False, "timeout": 30})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    ensure_document_codes(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(config, "DOCUMENT_STORAGE_ROOT", tmp_path / "documents")
    with factory() as db:
        for name, level in (("writer", "WRITE"), ("reader", "READ"), ("denied", "NONE")):
            db.add(UserModel(username=name, name=name, password_hash="test", role="USER", created_at="2026-10-01",
                permissions=json.dumps({"basic_info":{"READ":True,"WRITE":True}, "menu_access":{
                    "/basic-info/drawings":level, "/basic-info/items":"WRITE"}})))
        db.add_all([ItemMasterModel(id=i, part_no=p, part_name=p, revision="Rev.00", account_type="PROD",
            material_type="FINISHED", created_at="2026-10-01") for i, p in ((1,"PART-A"),(2,"PART-B"))])
        db.commit()
    install_audit_logging()
    app = FastAPI()
    app.include_router(documents.router)
    app.include_router(basic_info.router)
    def sessions():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    @app.middleware("http")
    async def audit(request, call_next):
        token = set_audit_context(user_id=1, username="writer", request_path=request.url.path, menu_path="/basic-info/drawings")
        try:
            return await call_next(request)
        finally:
            reset_audit_context(token)
    with TestClient(app) as client:
        login_as(client, factory, "writer")
        yield client, factory, tmp_path
    engine.dispose()


def login_as(client, factory, username):
    # The issued session and request dependencies must use the same isolated DB.
    with factory() as db:
        token = create_session_token(username, db=db)
        db.commit()
    client.cookies.set("session_token", token)


def revision(client, code="Rev.00", previous=None, item_id=1):
    return client.post(f"/api/documents/items/{item_id}/revisions", json={
        "revision_code":code, "previous_revision_id":previous, "change_reason":"설계 변경" if previous else ""})


def pdf_bytes():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(output)
    return output.getvalue()


def upload(client, revision_id, files=None, **changes):
    data = {"title":"테스트 도면", "document_no":"DRAW-001", "document_type":"DRAWING", "document_revision":"A"}
    data.update(changes)
    return client.post(f"/api/documents/revisions/{revision_id}/documents", data=data,
        files=files or [("files", ("도면.pdf", pdf_bytes(), "application/pdf"))])


def activate(client, revision_id):
    return client.post(f"/api/documents/revisions/{revision_id}/activate")


def test_preview_uses_native_viewer_without_pdf_sandbox(setup):
    client, _, _ = setup
    revision_id = revision(client).json()["id"]
    file = upload(client, revision_id).json()["files"][0]
    response = client.get(file["preview_url"])
    assert response.status_code == 200
    assert response.content == pdf_bytes()
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    policy = response.headers["content-security-policy"]
    assert "sandbox" not in policy
    assert "frame-ancestors 'none'" in policy
    download = client.get(file["download_url"])
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["content-security-policy"] == "sandbox"
    page = client.get("/basic-info/drawings")
    assert page.status_code == 200
    assert "drawingPreviewDialog" not in page.text
    assert "drawingPreviewFrame" not in page.text
    script = (config.BASE_DIR / "static" / "drawing_management.js").read_text(encoding="utf-8")
    assert 'target="_blank" rel="noopener noreferrer" title="도면 보기" aria-label="도면 보기">도면 보기</a>' in script
    assert 'title="다운로드" aria-label="다운로드">${downloadIcon}</a>' in script
    assert 'stroke="currentColor"' in script
    assert 'aria-hidden="true" focusable="false"' in script
    assert ">새 탭에서 보기</a>" not in script
    assert "data-preview-id" not in script


def test_revision_lifecycle_keeps_old_files_and_current_is_unique(setup):
    client, factory, root = setup
    first = revision(client).json()["id"]
    original = upload(client, first)
    assert original.status_code == 201, original.text
    file = original.json()["files"][0]
    assert activate(client, first).status_code == 200
    second = revision(client, "Rev.01", first).json()["id"]
    # 초안 생성은 기존 사용 도면을 바꾸지 않습니다.
    with factory() as db:
        assert db.get(ItemRevision, first).status == "CURRENT"
        assert db.get(ItemMasterModel, 1).revision == "Rev.00"
    assert upload(client, second).status_code == 201
    assert activate(client, second).status_code == 200
    with factory() as db:
        assert db.get(ItemRevision, first).status == "SUPERSEDED"
        assert db.get(ItemRevision, second).status == "CURRENT"
        # 도면 개정은 구형 품목 Revision 값을 갱신하지 않습니다.
        assert db.get(ItemMasterModel, 1).revision == "Rev.00"
        assert db.scalar(select(func.count()).select_from(ItemRevision).where(ItemRevision.status == "CURRENT")) == 1
        stored = db.get(DocumentFile, file["id"])
        assert (root / "documents" / stored.relative_path).read_bytes() == pdf_bytes()
        assert db.scalar(select(func.count()).select_from(AuditLogModel).where(AuditLogModel.table_name == "item_revisions")) >= 4
    assert client.get(file["download_url"]).content == pdf_bytes()
    assert upload(client, first).status_code == 409
    assert upload(client, second).status_code == 409
    assert client.post(f"/api/documents/revisions/{second}/retire", json={"reason":"제품 폐기"}).status_code == 200
    assert client.get(file["download_url"]).status_code == 200
    assert client.get("/api/documents/items?drawing_state=CURRENT").json() == []
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DocumentFile)) == 2


def test_db_rejects_two_current_revisions(setup):
    client, factory, _ = setup
    first = revision(client).json()["id"]
    second = revision(client, "Rev.01", first).json()["id"]
    with factory() as db:
        db.get(ItemRevision, first).status = "CURRENT"
        db.get(ItemRevision, second).status = "CURRENT"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_duplicate_and_wrong_item_previous_revision(setup):
    client, _, _ = setup
    first = revision(client).json()["id"]
    assert revision(client, "rev.00", first).status_code == 409
    assert revision(client, "Rev.01", first, item_id=2).status_code == 422
    assert revision(client, "Rev.01").status_code == 422
    assert client.post("/api/documents/items/1/revisions", json={"revision_code":"Rev.01", "previous_revision_id":first}).status_code == 422


def test_activation_requires_intact_drawing(setup):
    client, factory, root = setup
    first = revision(client).json()["id"]
    assert activate(client, first).status_code == 422
    file = upload(client, first).json()["files"][0]
    with factory() as db:
        path = root / "documents" / db.get(DocumentFile, file["id"]).relative_path
    path.write_bytes(b"changed")
    assert activate(client, first).status_code == 409
    assert client.get(file["download_url"]).status_code == 409
    with factory() as db:
        assert db.get(ItemRevision, first).status == "DRAFT"


def test_upload_failure_cleans_only_uncommitted_files(setup):
    client, factory, root = setup
    first = revision(client).json()["id"]
    good = upload(client, first).json()
    files = [("files", ("valid.pdf", pdf_bytes(), "application/pdf")), ("files", ("fake.pdf", b"not a pdf", "application/pdf"))]
    assert upload(client, first, files).status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ItemDocument)) == 1
        assert db.scalar(select(func.count()).select_from(DocumentFile)) == 1
    assert len(list((root / "documents").rglob("*.pdf"))) == 1
    assert client.get(good["files"][0]["download_url"]).status_code == 200


def test_limits_and_unsafe_formats(setup, monkeypatch):
    client, _, _ = setup
    first = revision(client).json()["id"]
    monkeypatch.setattr(config, "DOCUMENT_MAX_FILE_BYTES", 10)
    assert upload(client, first).status_code == 413
    monkeypatch.setattr(config, "DOCUMENT_MAX_FILE_BYTES", 100000)
    assert upload(client, first, [("files", ("a.svg", b"<svg></svg>", "image/svg+xml"))]).status_code == 422
    assert upload(client, first, [("files", ("a.pdf", b"", "application/pdf"))]).status_code == 422
    assert upload(client, first, document_type="INVENTED").status_code == 422


def test_image_and_cad_originals(setup):
    client, _, _ = setup
    first = revision(client).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (4,4)).save(image, format="PNG")
    response = upload(client, first, [("files", ("../image.png", image.getvalue(), "text/html")),
                                     ("files", ("original.step", b"ISO-10303-21;\nEND-ISO-10303-21;", "application/octet-stream"))])
    assert response.status_code == 201, response.text
    png, cad = response.json()["files"]
    assert png["original_name"] == "image.png" and png["media_type"] == "image/png"
    assert client.get(png["preview_url"]).status_code == 200
    assert client.get(cad["preview_url"]).status_code == 415
    assert client.get(cad["download_url"]).status_code == 200


@pytest.mark.parametrize("name,can_read", [("reader",True),("denied",False)])
def test_server_permissions_and_forged_source_header(setup, name, can_read):
    client, factory, _ = setup
    first = revision(client).json()["id"]
    file = upload(client, first).json()["files"][0]
    login_as(client, factory, name)
    expected = 200 if can_read else 403
    assert client.get("/api/documents/items").status_code == expected
    assert client.get(file["download_url"]).status_code == expected
    assert client.get(file["preview_url"]).status_code == expected
    assert client.get("/basic-info/drawings").status_code == expected
    assert client.post(f"/api/documents/revisions/{first}/activate", headers={"X-MES-Menu-Path":"/basic-info/items"}).status_code == 403
    assert revision(client, "Rev.01", first).status_code == 403
    assert upload(client, first).status_code == 403
    assert client.post(f"/api/documents/revisions/{first}/retire", json={"reason":"x"}).status_code == 403


def test_item_rename_ignores_legacy_revision_and_keeps_delete_protection(setup):
    client, factory, _ = setup
    first = revision(client).json()["id"]
    file = upload(client, first).json()["files"][0]
    assert activate(client, first).status_code == 200
    assert client.post("/api/basic-info/items/update", json={"id":1,"part_no":"RENAMED","revision":"Rev.01"}).status_code == 200
    with factory() as db:
        assert db.get(ItemMasterModel, 1).revision == "Rev.00"
    assert client.post("/api/basic-info/items/update", json={"id":1,"part_no":"RENAMED","revision":"Rev.00"}).status_code == 200
    assert client.get("/api/documents/items?keyword=PART-A").json()[0]["item_id"] == 1
    assert client.get(file["download_url"]).status_code == 200
    assert client.post("/api/basic-info/items/delete", json={"id":1}).status_code == 409
    with factory() as db:
        assert db.get(ItemRevision, first).item_id == 1
        assert any(row["table"] == "item_revisions" for row in item_usage_summary(db, 1))


def test_concurrent_activation_and_older_draft_cannot_replace_newer(setup):
    client, factory, _ = setup
    first = revision(client).json()["id"]
    second = revision(client, "Rev.01", first).json()["id"]
    assert upload(client, first).status_code == 201
    assert upload(client, second).status_code == 201
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: activate(client, second), range(2)))
    assert sorted(response.status_code for response in responses) == [200,409]
    assert activate(client, first).status_code == 409
    with factory() as db:
        assert db.get(ItemMasterModel, 1).revision == "Rev.00"
        assert db.scalar(select(func.count()).select_from(ItemRevision).where(ItemRevision.status == "CURRENT")) == 1


def test_retired_draft_cannot_be_applied_but_file_remains(setup):
    client, _, _ = setup
    first = revision(client).json()["id"]
    document = upload(client, first).json()
    assert client.post(f"/api/documents/{document['id']}/retire", json={"reason":"오등록"}).status_code == 200
    assert activate(client, first).status_code == 422
    assert client.get(document["files"][0]["download_url"]).status_code == 200


def test_missing_login_and_safe_path(setup):
    client, factory, _ = setup
    first = revision(client).json()["id"]
    file = upload(client, first).json()["files"][0]
    client.cookies.clear()
    assert client.get(file["download_url"]).status_code == 401
    assert client.get("/basic-info/drawings", follow_redirects=False).status_code == 303
    login_as(client, factory, "writer")
    with factory() as db:
        db.get(DocumentFile, file["id"]).relative_path = "../../outside.pdf"
        db.commit()
    assert client.get(file["download_url"]).status_code == 409


def test_backup_includes_db_and_all_document_history(setup):
    from deploy.oracle.backup_db import backup_snapshot
    import zipfile
    client, factory, root = setup
    first = revision(client).json()["id"]
    file = upload(client, first).json()["files"][0]
    assert activate(client, first).status_code == 200
    assert client.post(f"/api/documents/revisions/{first}/retire", json={"reason":"폐기"}).status_code == 200
    target = backup_snapshot(root / "test.db", root / "backup", documents_root=root / "documents")
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["sha256"] == file["sha256"]
        assert archive.read(manifest["files"][0]["path"]) == pdf_bytes()
        assert archive.read("manual_labels.db").startswith(b"SQLite format 3")
    with factory() as db:
        stored = db.get(DocumentFile, file["id"])
        (root / "documents" / stored.relative_path).write_bytes(b"broken")
    with pytest.raises(RuntimeError):
        backup_snapshot(root / "test.db", root / "backup", documents_root=root / "documents")
    assert list((root / "backup").glob("*.zip")) == [target]

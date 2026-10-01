#!/usr/bin/env python3
"""SQLite 운영 DB 안전 백업.

SQLite backup API를 사용해 실행 중인 DB도 일관된 스냅샷으로 백업합니다.
기본 경로는 Oracle 배포 구조(data/manual_labels.db -> backup/) 기준입니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(repo_root / "data" / "manual_labels.db"),
        help="원본 SQLite DB 경로",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(repo_root / "backup"),
        help="백업 저장 폴더",
    )
    parser.add_argument(
        "--documents-root",
        default=None,
        help="지정 시 DB+등록된 기술문서 원본+체크섬 목록을 하나의 ZIP으로 백업",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=14,
        help="보관 일수(기본 14일)",
    )
    return parser.parse_args()


def backup_snapshot(source: Path, backup_dir: Path, retention_days: int = 14, documents_root: Path | None = None) -> Path:
    source, backup_dir = source.resolve(), backup_dir.resolve()
    if not source.is_file():
        raise ValueError(f"DB 파일을 찾을 수 없습니다: {source}")
    if retention_days < 1:
        raise ValueError("retention-days는 1 이상이어야 합니다.")
    if source.parent == backup_dir:
        raise ValueError("백업 폴더는 원본 DB 폴더와 분리해 주세요.")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = ".zip" if documents_root is not None else ".db"
    target = backup_dir / f"manual_labels_{stamp}{suffix}"
    with tempfile.TemporaryDirectory(prefix="mes-snapshot-", dir=backup_dir) as scratch:
        snapshot = Path(scratch) / "manual_labels.db"
        # 백업된 DB에 기록된 파일만 복사합니다. 커밋된 파일은 불변/보존하므로
        # 백업 도중 새 업로드가 발생해도 이 스냅샷의 파일 집합은 바뀌지 않습니다.
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
                check = dst.execute("PRAGMA integrity_check").fetchone()
                if not check or str(check[0]).lower() != "ok":
                    raise RuntimeError(f"백업 무결성 검사 실패: {check}")
        if documents_root is None:
            snapshot.replace(target)
        else:
            root = documents_root.resolve()
            archive = Path(scratch) / "snapshot.zip"
            manifest = {"created_at": datetime.now().isoformat(), "database": "manual_labels.db", "files": []}
            with closing(sqlite3.connect(snapshot)) as db, zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(snapshot, "manual_labels.db")
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                rows = db.execute("SELECT id,relative_path,size_bytes,sha256 FROM document_files ORDER BY id").fetchall() if "document_files" in tables else []
                for file_id, relative, expected_size, expected_hash in rows:
                    if Path(relative).is_absolute() or ".." in Path(relative).parts:
                        raise RuntimeError(f"문서 파일 {file_id}: 상대경로 확인 실패")
                    path = (root / relative).resolve()
                    if path == root or not path.is_relative_to(root) or not path.is_file():
                        raise RuntimeError(f"문서 파일 {file_id}: 원본 파일 또는 경로 확인 실패")
                    digest, size = hashlib.sha256(), 0
                    archive_name = "documents/" + Path(relative).as_posix()
                    with path.open("rb") as original, bundle.open(archive_name, "w") as output:
                        while chunk := original.read(1024 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                            output.write(chunk)
                    if size != expected_size or digest.hexdigest() != expected_hash:
                        raise RuntimeError(f"문서 파일 {file_id}: 원본 크기/체크섬 검증 실패")
                    manifest["files"].append({"file_id": file_id, "path": archive_name, "size_bytes": size, "sha256": expected_hash})
                with snapshot.open("rb") as snapshot_file:
                    manifest["database_sha256"] = hashlib.file_digest(snapshot_file, "sha256").hexdigest()
                bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.replace(target)

    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for old in list(backup_dir.glob("manual_labels_*.db")) + list(backup_dir.glob("manual_labels_*.zip")):
        if old == target or not old.is_file() or old.is_symlink():
            continue
        if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
            old.unlink()
            removed += 1

    print(f"백업 완료: {target}")
    print(f"보관 정책: {retention_days}일 / 오래된 백업 삭제: {removed}개")
    return target


def main() -> int:
    args = parse_args()
    backup_snapshot(Path(args.source), Path(args.backup_dir), args.retention_days,
                    Path(args.documents_root) if args.documents_root else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

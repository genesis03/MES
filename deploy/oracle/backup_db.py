#!/usr/bin/env python3
"""SQLite 운영 DB 안전 백업.

SQLite backup API를 사용해 실행 중인 DB도 일관된 스냅샷으로 백업합니다.
기본 경로는 Oracle 배포 구조(data/manual_labels.db -> backup/) 기준입니다.
"""

from __future__ import annotations

import argparse
import sqlite3
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
        "--retention-days",
        type=int,
        default=14,
        help="보관 일수(기본 14일)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    backup_dir = Path(args.backup_dir).resolve()

    if not source.is_file():
        raise SystemExit(f"DB 파일을 찾을 수 없습니다: {source}")
    if args.retention_days < 1:
        raise SystemExit("retention-days는 1 이상이어야 합니다.")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"manual_labels_{stamp}.db"

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise RuntimeError(f"백업 무결성 검사 실패: {check}")

    cutoff = datetime.now() - timedelta(days=args.retention_days)
    removed = 0
    for old in backup_dir.glob("manual_labels_*.db"):
        if old == target:
            continue
        if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
            old.unlink()
            removed += 1

    print(f"백업 완료: {target}")
    print(f"보관 정책: {args.retention_days}일 / 오래된 백업 삭제: {removed}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

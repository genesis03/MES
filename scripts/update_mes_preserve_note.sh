#!/usr/bin/env bash
# Update this checkout while preserving the PC-specific note. Never stash the DB.
set -euo pipefail
mes_repo_root="$(git rev-parse --show-toplevel)"
cd -- "$mes_repo_root"
mes_note='새 텍스트 문서.txt'
if ! git diff --cached --quiet -- "$mes_note"; then
    echo '텍스트 문서가 커밋 대기 중입니다. 해당 파일의 스테이징을 먼저 정리하세요.' >&2
    exit 2
fi
if [[ ! -f "$mes_note" ]]; then
    echo '보존할 로컬 텍스트 문서가 없어 일반 업데이트를 실행합니다.'
    exec git pull --ff-only origin main
fi
umask 077
mes_backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/mes-local-note.XXXXXX")"
cp -p -- "$mes_note" "$mes_backup_dir/note.txt"
restore_local_note() {
    local mes_result=$?
    trap - EXIT
    if ! cp -p -- "$mes_backup_dir/note.txt" "$mes_note"; then
        echo "로컬 문서 복원 실패. 백업 위치: $mes_backup_dir/note.txt" >&2
        exit 1
    fi
    if git ls-files --error-unmatch -- "$mes_note" >/dev/null 2>&1; then
        if ! git update-index --skip-worktree -- "$mes_note"; then
            echo "문서는 복원했지만 로컬 예외 설정에 실패했습니다. 백업: $mes_backup_dir/note.txt" >&2
            exit 1
        fi
    elif ! git check-ignore -q -- "$mes_note"; then
        local mes_exclude
        mes_exclude="$(git rev-parse --git-path info/exclude)"
        mkdir -p -- "$(dirname -- "$mes_exclude")"
        printf '\n/%s\n' "$mes_note" >> "$mes_exclude"
    fi
    echo "PC 텍스트 문서는 원래 내용으로 보존했습니다. 백업: $mes_backup_dir/note.txt"
    exit "$mes_result"
}
trap restore_local_note EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if git ls-files --error-unmatch -- "$mes_note" >/dev/null 2>&1; then
    git update-index --no-skip-worktree -- "$mes_note"
    git update-index --no-assume-unchanged -- "$mes_note"
    git restore --worktree -- "$mes_note"
else
    # The original has already been copied to a private backup above.
    rm -- "$mes_note"
fi
git pull --ff-only origin main

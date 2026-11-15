# PC 전용 텍스트 문서는 Git 업데이트에서 제외

`새 텍스트 문서.txt`는 최신 main의 추적 대상에서 제거하고 `.gitignore`에 등록했다. PC 파일은 로컬에서 자유롭게 수정할 수 있으며, 앞으로는 기존 명령으로 MES를 업데이트한다.

```bash
git pull origin main
```

별도 업데이트 스크립트와 skip-worktree 설정은 더 이상 사용하지 않는다. 이 파일은 일반 `git add`로도 다시 올라가지 않는다. `git add -f`로 강제 등록하면 다시 추적되므로 사용하지 않는다. GitHub 최신 main에서는 파일이 빠지지만 과거 커밋의 이력까지 삭제하는 변경은 아니다.

## 이미 이 파일을 추적하고 있는 PC — 최초 한 번

원격의 추적 해제 변경은 해당 PC에서 파일 삭제로 적용될 수 있다. 따라서 최초 업데이트 한 번만 파일을 백업한 채 적용하고 원래 내용을 복원한다. 이후에는 위의 일반 pull만 사용한다.

MES 폴더에서 실행한다.

```bash
(
    set -e
    mes_note_backup="$(mktemp)"
    cp -p -- "새 텍스트 문서.txt" "$mes_note_backup"
    printf '문서 백업: %s\n' "$mes_note_backup"
    trap 'cp -p -- "$mes_note_backup" "새 텍스트 문서.txt"' EXIT
    git update-index --no-skip-worktree -- "새 텍스트 문서.txt"
    git update-index --no-assume-unchanged -- "새 텍스트 문서.txt"
    git restore --worktree -- "새 텍스트 문서.txt"
    git pull origin main
)
```

성공·실패 모두 백업한 문서 내용은 돌려놓는다. 다른 파일의 로컬 변경이 겹치면 일반 Git 규칙대로 업데이트가 중단되며, 다른 파일이나 DB를 임시 보관·덮어쓰지 않는다. 백업 파일은 출력한 임시 경로에 남긴다.

이번 변경 이후 텍스트 파일을 지우지 말고 로컬 전용으로 사용하면 된다. 새 PC에서 clone하면 이 파일은 내려오지 않는다.

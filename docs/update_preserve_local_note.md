# PC용 텍스트 문서를 보존하면서 업데이트

`새 텍스트 문서.txt`는 GitHub에도 남겨 수동으로 갱신할 수 있다. 추적 중인 파일은 `.gitignore`에 적는 것만으로 pull에서 제외되지 않는다.

PC 문서를 별도로 유지하려면 MES 폴더에서 다음 명령으로 업데이트한다.

```bash
bash scripts/update_mes_preserve_note.sh
```

이 명령은 PC 문서를 임시 비공개 폴더에 백업하고, 그 파일만 Git 기준 내용으로 잠시 복원해 fast-forward 업데이트한 뒤 PC 내용을 돌려놓는다. 업데이트가 실패해도 문서를 복원하고 백업은 남긴다. DB나 다른 파일을 stash하거나 교체하지 않는다. 다른 파일의 로컬 변경이 원격 변경과 겹치면 정상적으로 중단한다. 문서를 이미 스테이징했다면 먼저 중단해 커밋 대기 내용을 보호한다.

복원 후에는 해당 PC의 index에 skip-worktree를 설정한다. 이것만으로 이후의 모든 원격 변경 충돌이 해결되는 것은 아니므로, 이 PC에서는 위 보존용 명령을 계속 사용한다. GitHub의 텍스트 문서를 삭제하거나 다른 사람의 파일을 변경하지 않는다. 기존의 임시 worktree를 통한 파일 단독 업로드 방법은 계속 사용할 수 있다.

## 아직 보존용 스크립트를 받지 못한 PC

현재 문서 때문에 pull이 막혔다면, 작업 폴더를 변경하지 않는 fetch로 스크립트만 임시 파일에 받아 실행한다.

```bash
git fetch origin main &&
mes_update_script="$(mktemp)" &&
git show origin/main:scripts/update_mes_preserve_note.sh > "$mes_update_script" &&
bash "$mes_update_script" &&
rm -- "$mes_update_script"
```

`fast-forward` 불가 오류는 로컬 커밋과 원격 커밋이 갈라졌다는 뜻이며 강제 덮어쓰지 않는다. 오류를 확인하고 로컬 작업을 보존한 채 별도로 정리해야 한다.

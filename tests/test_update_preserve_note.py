from pathlib import Path
import subprocess

import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/update_mes_preserve_note.sh'
NOTE='새 텍스트 문서.txt'


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True).stdout.strip()


def commit(root,message):
    git(root,'add','.')
    git(root,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m',message)


@pytest.fixture
def repositories(tmp_path):
    origin=tmp_path/'remote.git';git(tmp_path,'init','--bare',str(origin))
    remote=tmp_path/'source';git(tmp_path,'clone',str(origin),str(remote));git(remote,'checkout','-b','main')
    (remote/NOTE).write_text('GitHub note v1')
    (remote/'code.py').write_text('version1')
    (remote/'manual_labels.db').write_bytes(b'old database')
    commit(remote,'initial');git(remote,'push','origin','main')
    local=tmp_path/'PC';git(tmp_path,'clone','--branch','main',str(origin),str(local))
    return remote,local


def update(root):return subprocess.run(['bash',str(SCRIPT)],cwd=root,capture_output=True,text=True)


def test_remote_note_change_does_not_replace_pc_note_or_dirty_db(repositories):
    remote,local=repositories
    (local/NOTE).write_text('private PC content')
    (local/'manual_labels.db').write_bytes(b'live local database')
    (remote/NOTE).write_text('GitHub note v2');(remote/'code.py').write_text('version2')
    commit(remote,'update');git(remote,'push','origin','main')
    result=update(local)
    assert result.returncode==0,result.stdout+result.stderr
    assert (local/NOTE).read_text()=='private PC content'
    assert (local/'code.py').read_text()=='version2'
    assert (local/'manual_labels.db').read_bytes()==b'live local database'
    assert git(local,'ls-files','-v','--',NOTE).startswith('S ')
    # A later remote edit must also work, even with skip-worktree already enabled.
    (remote/NOTE).write_text('GitHub note v3');commit(remote,'second');git(remote,'push','origin','main')
    assert update(local).returncode==0
    assert (local/NOTE).read_text()=='private PC content'
    assert git(remote,'show','HEAD:'+NOTE)=='GitHub note v3'


def test_failed_pull_restores_note_and_leaves_other_changes(repositories):
    remote,local=repositories
    (local/NOTE).write_text('private PC content');(local/'code.py').write_text('local code')
    (remote/'code.py').write_text('remote code');commit(remote,'overlap');git(remote,'push','origin','main')
    result=update(local)
    assert result.returncode!=0
    assert (local/NOTE).read_text()=='private PC content'
    assert (local/'code.py').read_text()=='local code'


def test_staged_note_is_not_overwritten_or_silently_committed(repositories):
    _,local=repositories
    (local/NOTE).write_text('staged content');git(local,'add','--',NOTE)
    result=update(local)
    assert result.returncode==2
    assert git(local,'show',':'+NOTE)=='staged content'
    assert (local/NOTE).read_text()=='staged content'

"""Validate the actual installer-generated units without touching /etc or certificates."""
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize('directory',['MES','MES with spaces','MES%instance'])
def test_installer_units_are_accepted_by_systemd(tmp_path,directory):
    verifier=shutil.which('systemd-analyze')
    if not verifier:pytest.skip('systemd-analyze is not installed')
    script=(Path(__file__).parents[1]/'scripts/oracle_https.sh').read_text()
    generator=re.search(r'''python3 - "\$mes_repo_root" <<'PY'\n(.*?)\nPY''',script,re.S).group(1)
    root=tmp_path/directory
    root.mkdir()
    (root/'scripts').mkdir()
    (root/'scripts/oracle_https.sh').write_text('#!/bin/bash\nexit 0\n')
    captured={}
    def capture(path,text,*args,**kwargs):captured[path.name]=text
    with patch.object(sys,'argv',['-',str(root)]),patch.object(Path,'write_text',capture):
        exec(compile(generator,'oracle_https.sh','exec'),{})
    assert 'WorkingDirectory="' not in captured['mes-https-renew.service']
    for name,text in captured.items():(tmp_path/name).write_text(text)
    result=subprocess.run([verifier,'verify',str(tmp_path/'mes-https-renew.service'),str(tmp_path/'mes-https-renew.timer')],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr

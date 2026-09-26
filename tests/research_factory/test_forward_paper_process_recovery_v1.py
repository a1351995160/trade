"""前瞻Paper提交边界的真实进程退出与恢复，全部输入显式合成。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from test_forward_paper_v1 import paper_source, paper_case, snapshot, ingest
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1


def _worker(mode,root,snapshot_root,snapshot_id,output):
    from chanlun_trader.research_factory import forward_paper_v1 as module
    value=SnapshotStoreV1(snapshot_root).load(snapshot_id)
    session=ForwardPaperSessionV1(root,clock=lambda:pd.Timestamp(value['received_at']))
    original=module._atomic_write
    if mode.startswith('crash-'):
        def interrupted(path,payload):
            if Path(path).name=='HEAD.json':
                if mode=='crash-after':original(path,payload)
                os._exit(17)
            return original(path,payload)
        module._atomic_write=interrupted
    status=session.ingest(snapshot_root,snapshot_id)
    Path(output).write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8')


@pytest.mark.parametrize('mode',['crash-before','crash-after'])
def test_process_restart_at_phase_commit_does_not_duplicate_fills(tmp_path,paper_source,mode):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    value=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    control_root=tmp_path/'continuous'
    shutil.copytree(session.root,control_root)
    continuous=ForwardPaperSessionV1(control_root,clock=lambda:clock[0])
    expected=ingest(continuous,store,value)
    assert expected['state']['economic']['trades']
    repo=Path(__file__).resolve().parents[2]
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':os.pathsep.join([
        str(repo/'src'),str(repo),str(repo/'tests/research_factory'),os.environ.get('PYTHONPATH','')])}
    source='from test_forward_paper_process_recovery_v1 import _worker; import sys; _worker(*sys.argv[1:])'
    output=tmp_path/'resumed.json'
    command=[sys.executable,'-c',source,mode,str(session.root),str(store.root),value['snapshot_id'],str(output)]
    first=subprocess.run(command,cwd=repo,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert first.returncode==17,first.stdout+first.stderr
    assert len(list(session.path('stages').glob('*.json')))==2
    command[3]='resume'
    second=subprocess.run(command,cwd=repo,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert second.returncode==0,second.stdout+second.stderr
    recovered=json.loads(output.read_text(encoding='utf-8'))
    assert stable_hash(recovered)==stable_hash(expected)
    assert recovered['completed_stages']==2 and recovered['real_observation_days']==0
    assert ingest(ForwardPaperSessionV1(session.root,clock=lambda:clock[0]),store,value)==expected

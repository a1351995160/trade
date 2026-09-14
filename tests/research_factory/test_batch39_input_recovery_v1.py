import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def test_exact_recovery_root_old_evidence_and_revocation(tmp_path,monkeypatch):
    import batch39_input_recovery_v1 as r
    monkeypatch.setattr(r,'ORIGINAL',tmp_path/'original')
    monkeypatch.setattr(r,'ROOT',tmp_path/'original/input-recovery-v1')
    monkeypatch.setattr(r,'active',lambda:None)
    monkeypatch.delenv('RESEARCH_BATCH39_INPUT_RECOVERY',raising=False)
    assert r.resolve(tmp_path/'other')==tmp_path/'other'
    monkeypatch.setenv('RESEARCH_BATCH39_INPUT_RECOVERY','1')
    with pytest.raises(PermissionError,match='ONLY_BATCH39'):r.resolve(tmp_path/'other')
    original=tmp_path/'failure.json';original.write_text('original failure')
    value={'original_evidence':{str(original):r.sha(original)}}
    value['identity']=r.stable_hash(value);r.save(r.ROOT/'RECOVERY.json',value)
    assert r.resolve(r.ORIGINAL)==r.ROOT
    original.write_text('changed')
    with pytest.raises(PermissionError,match='FAILURE_CHANGED'):r.resolve(r.ORIGINAL)
    original.write_text('original failure');(r.ROOT/'revocation.json').write_text('{}')
    with pytest.raises(PermissionError,match='REVOKED'):r.resolve(r.ORIGINAL)

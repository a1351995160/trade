"""先在合成绩效混合记录上检查投影和异常不泄露。"""
import json
from scripts.acquire_exact_research_evidence import project, read_entry
from scripts.acquire_exact_research_evidence import build_set
import pytest


def test_nested_performance_and_classification_never_returned(tmp_path):
    payload = {'objective_id':'OBJ', 'returns':123.456, 'p_value':0.12345,
               'nested':{'trial_id':'TRIAL', 'classification':'SECRET_BAD', 'curve':[998877], 'exposure_count':2}}
    path = tmp_path/'source.json'
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    result = read_entry({'path':str(path), 'identity':{'objective_id':'OBJ'}}, tmp_path)
    assert result['status']=='READ_PROJECTED'
    text = json.dumps(result)
    assert all(secret not in text for secret in ('123.456','0.12345','SECRET_BAD','998877','classification','p_value'))
    assert path.read_bytes()==before
    assert project(payload)[1]['fields']=={'trial_id':'TRIAL','exposure_count':2}


def test_missing_corrupt_and_conflict_are_separate(tmp_path):
    path = tmp_path/'source.json'
    entry = {'path':str(path), 'identity':{'objective_id':'EXPECTED'}}
    assert read_entry(entry,tmp_path)['status']=='MISSING'
    path.write_text('{"SECRET_PERFORMANCE":bad')
    result = read_entry(entry,tmp_path)
    assert result['status']=='CORRUPT_OR_INVALID_PATH' and 'SECRET' not in json.dumps(result)
    path.write_text('{"objective_id":"OTHER"}')
    assert read_entry(entry,tmp_path)['status']=='IDENTITY_CONFLICT'


def test_exact_paths_must_match_observed_identities(tmp_path):
    events = [{'trial_id':f'T_T{i:03}', 'candidate_id':f'C{i}'} for i in range(1,17)]
    history = {'files':[{'path':'batches/B/search_budget_registry.json','projection':{'objective_id':'O'}},
                        {'path':'batches/B/trial_registry.json','projection':{'events':events}}]}
    requests = []
    for i,e in enumerate(events,1):
        base=tmp_path/'reports/research_daemon/O/predictive'
        gate=base/'B'/e['candidate_id']
        if i>1: gate=gate/e['trial_id']
        requests.append(dict(objective_id='O',batch_id='B',**e,
                             gate_expected_path=str(gate/'performance_access_gate.json'),
                             contract_expected_path=str(base/'trial_contracts'/(e['trial_id']+'.json'))))
    assert len(build_set(history,{'requests':requests},tmp_path))==36
    requests[0]['gate_expected_path']=str(tmp_path/'reports/UNAPPROVED.json')
    with pytest.raises(ValueError,match='REQUEST_PATH_NOT_DERIVED'):
        build_set(history,{'requests':requests},tmp_path)


def test_access_denied_is_not_missing(tmp_path, monkeypatch):
    path=tmp_path/'source.json'
    path.write_text('{}')
    def denied(self):
        raise PermissionError('SECRET_CONTENT')
    monkeypatch.setattr(type(path),'read_bytes',denied)
    result=read_entry({'path':str(path),'identity':{}},tmp_path)
    assert result['status']=='ACCESS_DENIED' and 'SECRET_CONTENT' not in json.dumps(result)

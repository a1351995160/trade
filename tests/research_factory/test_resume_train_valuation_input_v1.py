import pytest

import resume_train_valuation_input_v1 as r
from test_train_valuation_input_v1 import payload


@pytest.fixture
def state(tmp_path,monkeypatch):
    root=tmp_path/'source';receipt=tmp_path/'governance/read.json'
    monkeypatch.setattr(r.source,'ROOT',root);monkeypatch.setattr(r.source,'RECEIPT',receipt)
    monkeypatch.setattr(r,'ROOT',root/'recovery-v1');monkeypatch.setattr(r,'RECEIPT',receipt.parent/'recovery-v1.json')
    monkeypatch.setenv('CODEX_THREAD_ID','test-thread')
    parent={'receipt_id':'original','plan':{'symbols':['000001.SZ','000002.SZ','000003.SZ'],'objective_id':'objective','expires_at':'2027-01-01T00:00:00+00:00'}}
    monkeypatch.setattr(r.source,'guard',lambda:parent);r.save(receipt,parent)
    r.save(root/'resources/fetch.started.json',{'pid':12})
    r.save(root/'resources/fetch.completed.json',{'returncode':-9,'timed_out':True,'elapsed_seconds':900})
    folder=root/'responses/000001.SZ';p=payload()
    r.save(folder/'started.json',{'query':r.query('000001.SZ')})
    r.save(folder/'response.json',p);r.save(folder/'access.json',{'sha256':r.sha(folder/'response.json')})
    r.save(folder/'quality.json',r.source.validate_response(p,'000001.SZ'))
    r.save(root/'responses/000002.SZ/started.json',{'query':r.query('000002.SZ')})
    return root


def test_no_live_worker_and_no_unproven_failure(state):
    done=state/'resources/fetch.completed.json';old=done.read_bytes();done.unlink()
    with pytest.raises(PermissionError,match='UNSETTLED'):r.reconcile()
    done.write_bytes(old)
    done.write_text('{"returncode":0,"elapsed_seconds":10}',encoding='utf-8')
    with pytest.raises(PermissionError,match='NO_FAILED'):r.reconcile()


def test_scope_preserves_old_evidence_and_budget(state):
    budget=r.RECEIPT.parent/'budget.json';r.save(budget,{'used':12});old=budget.read_bytes()
    before={str(p):p.read_bytes() for p in state.rglob('*.json')}
    first=r.confirm();assert r.confirm()==first
    assert first['state']['remaining']==['000002.SZ','000003.SZ']
    assert first['state']['interrupted']==['000002.SZ']
    assert first['performance_allowance']==0 and first['additional_data_seconds']==3600
    assert budget.read_bytes()==old
    assert all(r.Path(p).read_bytes()==b for p,b in before.items())
    r.save(r.ROOT/'revocation.json',{'reason':'test'})
    with pytest.raises(PermissionError,match='REVOKED'):r.guard()


def test_hash_conflicts_and_partial_response_rejected(state):
    path=state/'responses/000001.SZ/response.json';old=path.read_bytes();path.write_bytes(old+b' ')
    with pytest.raises(PermissionError,match='SOURCE_CONFLICT'):r.reconcile()
    path.write_bytes(old)
    r.save(state/'responses/000002.SZ/response.json',payload())
    with pytest.raises(PermissionError,match='PARTIAL_SAVED'):r.reconcile()


def test_bound_identity_and_resource_limit(state):
    r.confirm();assert 0<r.remaining_seconds()<=900
    r.save(r.ROOT/'resources/used.completed.json',{'elapsed_seconds':3600})
    assert r.remaining_seconds()<=0
    path=state/'resources/fetch.completed.json';path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(PermissionError,match='BOUND_IDENTITY'):r.guard()


def test_exact_interrupted_request_once_without_overwriting_old_started(state,monkeypatch):
    from contextlib import contextmanager
    import chanlun_trader.synthetic_batch_resources as resources
    import chanlun_trader.data.minute.baostock_provider as provider
    r.confirm();symbol='000002.SZ';folder=state/'responses'/symbol;old=(folder/'started.json').read_bytes()
    monkeypatch.setattr(resources,'worker_resource_handshake',lambda:{'execution':{'purpose':'VALUATION_INTERRUPTED_READ_ONCE','symbol':symbol}})
    class Response:
        error_code='0';error_msg='';fields=r.source.FIELDS.split(',')
        def __init__(self):self.i=0
        def next(self):self.i+=1;return self.i==1
        def get_row_data(self):return ['2022-08-01','sz.000002','-1','','0']
    class Provider:
        @contextmanager
        def session(self):yield self
        @property
        def bs(self):return self
        def query_history_k_data_plus(self,**kwargs):
            assert kwargs==r.query(symbol);return Response()
    monkeypatch.setattr(provider,'BaoStock5MinProvider',Provider)
    r.interrupted_worker(symbol)
    assert (folder/'started.json').read_bytes()==old
    assert r.read(folder/'quality.json')['row_count']==1
    assert r.read(folder/'access.json')['recovery_receipt_id']==r.read(r.RECEIPT)['receipt_id']
    r.guard()
    with pytest.raises(PermissionError,match='OVERWRITE'):r.interrupted_worker(symbol)


def test_new_worker_failure_is_settled_and_never_auto_replayed(state,monkeypatch):
    import chanlun_trader.synthetic_batch_resources as resources
    calls=[]
    def fail(*args,**kwargs):
        calls.append(kwargs['execution']);kwargs['on_started'](123)
        assert kwargs['wall_seconds']<=900 and kwargs['memory_mib']==2048
        return {'returncode':1,'stdout':b'','stderr':b'provider failure','timed_out':False}
    monkeypatch.setattr(resources,'run_bounded_worker',fail)
    with pytest.raises(RuntimeError,match='RECOVERY_WORKER_FAILED'):r.run()
    assert len(calls)==1
    done=r.ROOT/'resources/interrupted-000002.SZ.completed.json'
    assert r.read(done)['returncode']==1
    with pytest.raises(PermissionError,match='NO_AUTOMATIC_RECOVERY_REPLAY'):r.run()
    assert len(calls)==1


def test_materialization_uses_only_amended_remaining_data_seconds(state,monkeypatch):
    import materialize_train_turnover_v1 as m
    import chanlun_trader.synthetic_batch_resources as resources
    receipt=r.confirm()
    r.save(state/'FETCH_COMPLETED.json',{'recovery_receipt_id':receipt['receipt_id']})
    r.save(r.ROOT/'resources/used.completed.json',{'elapsed_seconds':3500})
    monkeypatch.setattr(m,'ROOT',state/'turnover-input-v1')
    def stop(*args,**kwargs):
        assert kwargs['wall_seconds']==100
        kwargs['on_started'](321)
        return {'returncode':1,'stdout':b'','stderr':b'synthetic stop','timed_out':False}
    monkeypatch.setattr(resources,'run_bounded_worker',stop)
    with pytest.raises(RuntimeError,match='TURNOVER_MATERIALIZATION_FAILED'):m.run()
    assert (r.ROOT/'resources/materialize-turnover.completed.json').exists()
    assert not (state/'resources/materialize-turnover.started.json').exists()

import pytest


def test_exact_window_failure_reconciliation(tmp_path,monkeypatch):
    import run_train_search_batch_v1 as b
    from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES
    monkeypatch.setattr(b,'BATCH',32)
    monkeypatch.setattr(b,'INPUT',tmp_path/'input')
    b.save(tmp_path/'train-search-batch-v31/RUNNER_COMPLETED.json',{'status':'SCREEN_PASS_REVIEWED_NOT_QUALIFIED'})
    for name in NAMES:
        window=tmp_path/'response-confirmation-window-v1'/name
        b.save(window/'FINAL_STATUS.json',dict(account_started=True,account_completed=True,report_completed=True))
        b.save(window/'ACCOUNT_SETTLEMENT.json',{'completed':True})
        metrics={'train_net_return':-.1}
        b.save(window/'ACCOUNT_RESULT.json',{'metrics':metrics})
        b.save(window/'RESULT_SUMMARY.json',{'original_metrics':metrics})
        b.save(window/'RESULTS_INDEX.json',{p:{'path':str(window/p),'sha256':b.sha(window/p)}
            for p in ('ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json')})
    b.reconcile_confirmation_window_failures()
    target=window/'RESULT_SUMMARY.json'
    original=target.read_bytes()
    target.write_bytes(original+b' ')
    with pytest.raises(PermissionError,match='EVIDENCE_CHANGED'):
        b.reconcile_confirmation_window_failures()
    target.write_bytes(original)
    # 合成试件的明确损坏，仅测试；真实原件不会被本测试触及。
    (window/'FINAL_STATUS.json').write_text('{"account_started":true,"account_completed":false,"report_completed":true}')
    with pytest.raises(PermissionError,match='INCOMPLETE'):
        b.reconcile_confirmation_window_failures()
    monkeypatch.setattr(b,'BATCH',33)
    with pytest.raises(PermissionError,match='ONLY'):
        b.reconcile_confirmation_window_failures()

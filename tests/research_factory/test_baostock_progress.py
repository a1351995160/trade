"""进度观察只读取访问元数据，不读取行情或产生任何文件。"""
import importlib.util
import json
from pathlib import Path


def test_progress_metadata_only_and_revised_quality(tmp_path):
    source = Path(__file__).resolve().parents[2]/'scripts/show_baostock_progress.py'
    spec = importlib.util.spec_from_file_location('progress_viewer', source)
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)

    def save(name, value):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    save('READ_PLAN.json', dict(symbols=['A', 'B'], batch_symbols=2, total_seconds=5400))
    for flag in ('1', '3'):
        save(f'responses/A/{flag}.access.json', dict(error_code='0'))
    save('responses/B/3.access.json', dict(error_code='0'))
    save('quality/batch-0.json', dict(passed=False))
    save('quality-ipo-v1/batch-0.json', dict(passed=True))
    save('resources/fetch-hfq1-0.json', dict(elapsed_seconds=10, returncode=0))
    save('resources/fetch-hfq1-1.started.json', dict(pid=123))
    # 不合法的行情正文也不应被观察器读取。
    (tmp_path/'responses/A/1.json').write_text('NOT PRICE JSON', encoding='utf-8')
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = viewer.snapshot(tmp_path)
    assert (result['complete'], result['partial'], result['passed']) == (1, 1, 1)
    assert result['settled_seconds'] == 10
    assert result['pending'] == [('fetch-hfq1-1.started.json', 123)]
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_watch_repeats_and_interrupt_only_exits_viewer(monkeypatch, capsys):
    source = Path(__file__).resolve().parents[2]/'scripts/show_baostock_progress.py'
    spec = importlib.util.spec_from_file_location('progress_watch', source)
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    calls = []
    monkeypatch.setattr(viewer, 'show_once', lambda: calls.append('check'))

    def sleep(seconds):
        assert seconds == 60
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(viewer.time, 'sleep', sleep)
    viewer.run(watch=True)
    assert calls == ['check', 'check']
    assert '未向取数进程发送停止指令' in capsys.readouterr().out

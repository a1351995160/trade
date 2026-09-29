"""CI生成文档与公共示例必须同源，预检不能成为执行授权。"""
from copy import deepcopy
from pathlib import Path
import pytest

from scripts.generate_research_capabilities_v1 import check_examples, main
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities, render_markdown


def test_checked_in_document_matches_source_and_examples_preview():
    snapshot = capabilities()
    path = Path(__file__).resolve().parents[2] / 'docs' / 'RESEARCH_CAPABILITIES.md'
    assert path.read_text(encoding='utf-8') == render_markdown(snapshot)
    results = check_examples(snapshot)
    assert set(results) == set(snapshot['examples'])
    assert all(row['status'] == 'PREVIEW_ONLY_CONTENT_AND_AUTHORIZATION_NOT_CHECKED' for row in results.values())
    assert snapshot['acceptance']['s1'] == 'EXTERNAL_PUBLICATION'
    assert all(row['evidence'] != 'VALIDATED' for row in snapshot['features'])


def test_check_rejects_stale_document_without_rewriting(tmp_path):
    path = tmp_path / 'capabilities.md'
    path.write_text('old documentation', encoding='utf-8')
    assert main(['--check', '--output', str(path)]) == 1
    assert path.read_text(encoding='utf-8') == 'old documentation'
    assert main(['--output', str(path)]) == 0
    assert main(['--check', '--output', str(path)]) == 0


def test_example_cannot_self_declare_qualification():
    snapshot = deepcopy(capabilities())
    snapshot['examples']['ma_cross']['qualified'] = True
    with pytest.raises(ValueError):
        check_examples(snapshot)

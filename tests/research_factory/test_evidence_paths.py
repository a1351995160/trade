import pytest

from chanlun_trader.research_factory.evidence_paths import within_root


def test_rejects_traversal_and_sibling_prefix(tmp_path):
    root = tmp_path/'approved'
    root.mkdir()
    assert within_root(root/'proof.json',root) == root/'proof.json'
    with pytest.raises(PermissionError):
        within_root(root/'..'/'secret.json',root)
    with pytest.raises(PermissionError):
        within_root(tmp_path/'approved-other'/'proof.json',root)

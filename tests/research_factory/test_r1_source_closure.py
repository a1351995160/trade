import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT


def test_backend_default_prompt_comes_from_deployment_not_empty_data_root(tmp_path):
    from chanlun_trader.research_factory.codex_backend import CodexInvocationAdapterV1, CodexResearchAgentBackendV1
    # 只构造真实 adapter；绝不调用配置的执行器。
    adapter = CodexInvocationAdapterV1(root=tmp_path, executable=sys.executable)
    backend = CodexResearchAgentBackendV1(adapter=adapter)
    assert Path(backend.prompt.document_path) == SOURCE_ROOT / "docs/CODEX_RESEARCH_PROMPT_V1.md"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("missing_resource", [False, True])
def test_clean_source_fresh_process_without_production_module_mocks(tmp_path, missing_resource):
    source, data = tmp_path / "source", tmp_path / "data"
    source.mkdir()
    data.mkdir()
    for directory in ("src", "docs"):
        shutil.copytree(SOURCE_ROOT / directory, source / directory, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(SOURCE_ROOT / "config.yaml", source / "config.yaml")
    if missing_resource:
        (source / "docs/CODEX_RESEARCH_PROMPT_V1.md").unlink()
    # 同名数据 root 脚本是拒绝路径的哨兵，不作为任何成功依赖。
    (data / "scripts").mkdir()
    (data / "scripts/run_engine_corrected_phase4_v3.py").write_text("raise AssertionError('DATA_ROOT_CODE_IMPORTED')", encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(source / "src")]), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("r1_cold_worker.py")), str(source), str(data)], cwd=data, env=environment, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PARTIAL"
    assert sum(row["status"] == "LOADED" for row in report["matrix"]) == 17
    assert sum(row["status"] == "BLOCKED_MISSING_SOURCE" for row in report["matrix"]) == 2
    assert any(row["status"] == "MISSING_DATA_POLICY" for row in report["matrix"])
    assert any(row["status"] == ("MISSING_RESOURCE" if missing_resource else "RESOURCE_LOADED") for row in report["matrix"])
    print("R1_COLD_EVIDENCE=" + json.dumps(report, ensure_ascii=False))

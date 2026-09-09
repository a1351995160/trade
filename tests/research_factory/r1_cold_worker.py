"""全新进程的加载证据。仅拦截副作用，不替换生产模块。"""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys


source, data = map(Path, sys.argv[1:3])
rows = []


def prohibit(event, args):
    if event in {"subprocess.Popen", "socket.connect", "os.mkdir", "os.remove", "os.rename", "os.rmdir"}:
        raise AssertionError("COLD_LOAD_SIDE_EFFECT:" + event)
    if event == "open":
        mode = args[1]
        flags = args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & 3):
            raise AssertionError("COLD_LOAD_WRITE")


def no_execution(frame, event, arg):
    if event == "call" and frame.f_code.co_name in {"execute", "build", "invoke", "start", "resume", "recover_all", "run"}:
        module = frame.f_globals.get("__name__", "")
        if module in {"chanlun_trader.research_factory.predictive_executor", "chanlun_trader.research_factory.real_sample_feasibility", "chanlun_trader.research_factory.predictive_trial_start", "chanlun_trader.research_factory.predictive_trial_reauthorization", "chanlun_trader.research_factory.codex_backend", "chanlun_trader.engine.engine"}:
            raise AssertionError("COLD_LOAD_EXECUTION_DISABLED")


sys.addaudithook(prohibit)
sys.setprofile(no_execution)
for name in (
    "research_factory.predictive_executor", "research_factory.real_runtime",
    "research_factory.real_sample_feasibility", "research_factory.durability",
    "research_factory.predictive_trial_start", "research_factory.predictive_trial_reauthorization",
    "research.strategy_semantic", "research.unified_factor", "engine.engine", "engine.corporate_action",
    "research.data_router", "research.guard", "research.pit_tradability",
    "data.minute.tdx_raw_hq_provider", "data.minute.manifest", "data.minute.validator", "data.minute.gap_resolution",
):
    module = importlib.import_module("chanlun_trader." + name)
    path = Path(module.__file__).resolve()
    assert path.is_relative_to(source / "src"), path
    rows.append(dict(entrypoint=name, status="LOADED", source=str(path.relative_to(source)), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1
from chanlun_trader.research_factory.predictive_trial_reauthorization import PredictiveTrialReauthorizationServiceV1
from chanlun_trader.research_factory.real_sample_feasibility import RealSampleFeasibilityProviderV1
from chanlun_trader.research.data_router import ResearchDataRouter
from chanlun_trader.research_factory.codex_backend import CodexInvocationAdapterV1, CodexResearchAgentBackendV1, _proposal_schema
from chanlun_trader.research_factory.source_dependencies import load_corrected_module
import jsonschema
import yaml

for constructor in (PredictiveTrialStartServiceV1, PredictiveTrialReauthorizationServiceV1):
    service = constructor(data, auto_run=False)
    assert service.auto_run is False
    rows.append(dict(entrypoint=constructor.__name__, status="SAFE_CONSTRUCTION"))
RealSampleFeasibilityProviderV1(data)
for label, loader in (("CanonicalPredictiveExecutorV1._corrected_module", CanonicalPredictiveExecutorV1(data, "R1")._corrected_module), ("real_runtime.corrected_loader", load_corrected_module)):
    try:
        loader()
    except ModuleNotFoundError as exc:
        rows.append(dict(entrypoint=label, status="BLOCKED_MISSING_SOURCE", error=str(exc)))
    else:
        raise AssertionError("Expected absent original runner; provenance must be reviewed if restored")
try:
    ResearchDataRouter(data)
except FileNotFoundError as exc:
    rows.append(dict(entrypoint="ResearchDataRouter(empty)", status="MISSING_DATA_POLICY", error=str(exc)))
jsonschema.Draft202012Validator.check_schema(_proposal_schema())
rows.append(dict(entrypoint="codex proposal schema", status="VALID_SCHEMA"))
try:
    prompt = CodexResearchAgentBackendV1(adapter=CodexInvocationAdapterV1(root=data, executable=sys.executable)).prompt
    rows.append(dict(entrypoint="CodexResearchPromptV1", status="RESOURCE_LOADED", hash=prompt.prompt_hash))
except FileNotFoundError as exc:
    rows.append(dict(entrypoint="CodexResearchPromptV1", status="MISSING_RESOURCE", error=str(exc)))
yaml.safe_load((source / "config.yaml").read_text(encoding="utf-8"))
for name, module in tuple(sys.modules.items()):
    if name.startswith("chanlun_trader") and getattr(module, "__file__", None):
        assert Path(module.__file__).resolve().is_relative_to(source / "src"), name
print(json.dumps({"status": "PARTIAL", "matrix": rows, "python": sys.version,
    "packages": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "pyarrow", "jsonschema", "pyyaml", "pytdx", "baostock")},
    "unverified": ["corrected runner transitive imports", "real data", "engine execution", "wheel deployment"]}, ensure_ascii=False))

"""长历史建模账户：复用公共入口与独立核账，不授予独立确认资格。"""
from pathlib import Path
import hashlib

from .formal_account_backend_v1 import (
    FormalAccountBackendV1, FormalWindowStrategyV1, window_input_identity,
)
from .strategy_interface_v1 import prepare, run


PROFILE = "HISTORICAL_MODELED"
LIMITATIONS = [
    "历史日线开盘价建模成交；不是当时采集的实时快照。",
    "历史证券状态的可见时间是建模假设，未证明当时可知。",
    "已暴露历史窗口仅供方法适用性研究，不是独立确认数据。",
    "逐日独立核账通过不代表统计方法适用或策略有效。",
]


def _require_historical_profile(bundle):
    if bundle.get("profile") != PROFILE:
        raise ValueError("HISTORICAL_PROFILE_REQUIRED")
    if bundle.get("source_hashes", {}).get("execution_profile") != PROFILE:
        raise ValueError("HISTORICAL_PROFILE_IDENTITY_REQUIRED")
    if bundle.get("open_snapshots") != [] or bundle.get("close_snapshots") != []:
        raise ValueError("HISTORICAL_OBSERVED_SNAPSHOTS_FORBIDDEN")


def historical_input_identity(bundle, window):
    """profile 标识写入 source_hashes，复用已有不可变输入身份算法。"""
    _require_historical_profile(bundle)
    return window_input_identity(bundle, window)


class HistoricalAccountBackendV1(FormalAccountBackendV1):
    def describe(self):
        value = super().describe()
        source = Path(__file__).resolve()
        value["source_hashes"][str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        value.update(backend="HISTORICAL_WINDOW_ACCOUNT_V1", profile=PROFILE,
                     fill_reference="NEXT_SESSION_OPEN_DAILY_BAR",
                     state_availability="MODELED_NOT_OBSERVED",
                     independent_confirmation_eligible=False, strategy_qualified=False,
                     limitations=list(LIMITATIONS))
        return value

    def _validate_bundle(self, bundle):
        _require_historical_profile(bundle)
        super()._validate_bundle(bundle)

    def _observed_sessions(self, bundle):
        _require_historical_profile(bundle)
        return None

    def run(self, strategy, bundle, actions, guard):
        value = super().run(strategy, bundle, actions, guard)
        # 最后再次检查 profile，避免在执行期间切换模式。
        _require_historical_profile(bundle)
        value.update(profile=PROFILE, research_only=True,
                     independent_confirmation_eligible=False,
                     strategy_qualified=False, limitations=list(LIMITATIONS))
        return value


def prepare_historical_account(proposal, *, strategy_id, window, costs="BASE"):
    return prepare(FormalWindowStrategyV1(proposal, strategy_id=strategy_id),
                   HistoricalAccountBackendV1(window, costs))


def run_historical_account(proposal, *, strategy_id, bundle, window, costs="BASE",
                           input_identity, active_check):
    _require_historical_profile(bundle)
    value = run(FormalWindowStrategyV1(proposal, strategy_id=strategy_id),
                HistoricalAccountBackendV1(window, costs), frame=bundle,
                actions=bundle["events"], input_identity=input_identity,
                active_check=active_check)
    value["report"].update(profile=PROFILE, research_only=True,
                           independent_confirmation_eligible=False,
                           strategy_qualified=False, limitations=list(LIMITATIONS))
    return value

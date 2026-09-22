"""BT_BEHAVIOR_DAILY_V1 CLI — 与公共 API 共用同一服务函数。

用法：
    python scripts/run_behavior_backtest_v1.py --request REQUEST.json [--out RESULT.json]

REQUEST.json 为 ``BehaviorRequestV1`` 的 JSON 映射；可包含 ``dataset_path``
指向 CSV/Parquet 合成行情（文件入口只在 CLI 提供）。

输出：stdout 为人类可读摘要，``--out`` 写出完整结果 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.engine.behavior_service_v1 import (  # noqa: E402
    BehaviorRequestV1,
    run_behavior_backtest_v1,
    write_result,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="BT_BEHAVIOR_DAILY_V1 行为验收回测")
    parser.add_argument("--request", required=True, help="请求 JSON 文件")
    parser.add_argument("--out", help="结果 JSON 输出路径")
    args = parser.parse_args()

    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    request = BehaviorRequestV1.from_mapping(payload)
    result = run_behavior_backtest_v1(request)

    print("模式：%s" % result.mode)
    print("引擎版本：%s" % result.engine_version)
    print("指标合同：%s（%s）" % (result.indicator_contract, result.indicator_versions))
    print("条件合同：%s" % result.condition_contract)
    print("退出合同：%s / %s" % (result.exit_contract, result.exit_execution_mode))
    print("价格模式：%s" % result.price_mode)
    print("信号数：%d，订单数：%d，成交数：%d" % (len(result.signals), len(result.orders), len(result.fills)))
    print("期末现金：%.4f，期末权益：%.4f" % (result.cash, result.final_equity))
    for fill in result.fills:
        print("  成交 %s %s %d @ %.4f 费用 %.4f" % (
            fill["fill_time"], fill["side"], fill["quantity"], fill["price"], fill["fee"]))
    for rejection in result.rejections:
        print("  拒因 %s %s @ %s" % (rejection["symbol"], rejection["reason"], rejection["at"]))
    if args.out:
        path = write_result(args.out, result)
        print("结果：%s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

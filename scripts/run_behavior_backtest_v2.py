"""BT_BEHAVIOR_DAILY_V2 CLI — 与公共 API 共用同一服务函数。

用法：
    python scripts/run_behavior_backtest_v2.py --request REQUEST.json [--out RESULT.json]
    python scripts/run_behavior_backtest_v2.py --list-indicators

REQUEST.json 为 ``BehaviorRequestV2`` 的 JSON 映射；可包含 ``dataset_path``
（CSV/Parquet 合成行情）与 ``dataset_root``（必须成对给出，路径受根约束）。

``--out`` 必须落在 ``--out-root`` 内（默认当前工作目录）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.engine.behavior_service_v1 import (  # noqa: E402
    BehaviorRequestError,
    resolve_within_root,
)
from chanlun_trader.engine.behavior_service_v2 import (  # noqa: E402
    run_behavior_backtest_v2,
    write_result_v2,
)
from chanlun_trader.engine.custom_indicators_v2 import (  # noqa: E402
    custom_condition_fixtures,
    custom_exit_condition_fixtures,
    register_custom_indicators,
)
from chanlun_trader.engine.indicator_registry_v2 import default_registry  # noqa: E402


def _print_indicators() -> None:
    registry = default_registry()
    register_custom_indicators(registry)
    print("注册表：%s（%d 项 / %d 家族）"
          % (registry.to_dict()["registry_version"], len(registry.specs()),
             len(registry.families())))
    for family in registry.families():
        print("\n[%s]" % family)
        for spec in registry.by_family(family):
            print("  %-24s %-28s 输出=%s 参数=%s 预热=%d 单位=%s%s"
                  % (spec.indicator_id, spec.version, ",".join(spec.outputs),
                     json.dumps(spec.params, ensure_ascii=False), spec.warmup_bars,
                     spec.unit,
                     "  需额外数据=%s" % ",".join(spec.requires_extra_data)
                     if spec.requires_extra_data else ""))
    print("\n[自定义入场条件 fixture]")
    for name in sorted(custom_condition_fixtures()):
        print("  %s" % name)
    print("\n[自定义退出条件 fixture]")
    for name in sorted(custom_exit_condition_fixtures()):
        print("  %s" % name)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="BT_BEHAVIOR_DAILY_V2 通用行为回测")
    parser.add_argument("--request", help="请求 JSON 文件（须落在 --request-root 内）")
    parser.add_argument("--request-root", default=".", help="请求文件允许的根目录")
    parser.add_argument("--out", help="结果 JSON 输出路径（须落在 --out-root 内）")
    parser.add_argument("--out-root", default=".", help="结果输出允许的根目录")
    parser.add_argument("--list-indicators", action="store_true", help="列出注册表中的指标与条件")
    args = parser.parse_args()

    if args.list_indicators:
        _print_indicators()
        return 0
    if not args.request:
        parser.error("需要 --request 或 --list-indicators")
        return 2   # parser.error 会先抛出 SystemExit；此行仅为满足静态检查

    request_path = resolve_within_root(args.request, args.request_root, purpose="REQUEST")
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    result = run_behavior_backtest_v2(payload)

    print("模式：%s" % result["mode"])
    print("引擎版本：%s" % result["engine_version"])
    print("注册表：%s" % result["registry_version"])
    print("指标合同：%s" % result["indicators_version"])
    print("条件合同：%s" % result["condition_contract"])
    print("退出合同：%s / %s" % (result["exit_contract"], result["exit_execution_mode"]))
    print("价格模式：%s" % result["price_mode"])
    config = result["resolved_config"]
    print("生效指标：%s"
          % ", ".join("%s(%s)" % (item["indicator_id"], json.dumps(item["params"], ensure_ascii=False))
                      for item in config["request"]["indicators"]))
    print("信号数：%d，订单数：%d，成交数：%d"
          % (len(result["signals"]), len(result["orders"]), len(result["fills"])))
    print("期末现金：%.4f，正式估值期末权益：%.4f"
          % (result["cash"], result["final_equity"]))
    print("正式估值：%s 起 %s 止，共 %d 个 session"
          % (result["official_valuation"]["start_date"],
             result["official_valuation"]["end_date"],
             result["official_valuation"]["n_days"]))
    for symbol, trace in config["condition_trace"].items():
        print("  %s 条件命中 TRUE=%d FALSE=%d UNKNOWN=%d"
              % (symbol, trace["condition_true"], trace["condition_false"],
                 trace["condition_unknown"]))
    for fill in result["fills"]:
        print("  成交 %s %s %d @ %.4f 费用 %.4f"
              % (fill["fill_time"], fill["side"], fill["quantity"], fill["price"], fill["fee"]))
    for rejection in result["rejections"]:
        print("  拒因 %s %s @ %s" % (rejection["symbol"], rejection["reason"], rejection["at"]))
    if args.out:
        target = write_result_v2(args.out, result, root=args.out_root)
        print("结果：%s" % target)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BehaviorRequestError as exc:
        print("请求不受支持：%s" % exc, file=sys.stderr)
        raise SystemExit(2)

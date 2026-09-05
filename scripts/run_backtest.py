"""运行回测并生成报告。用法：python scripts/run_backtest.py [股票数量上限]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.backtest import BacktestRunner
from chanlun_trader.config import load_config
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.report import write_report
from chanlun_trader.tdx_data import TdxData


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    runner = BacktestRunner(tdx, cfg)
    result = runner.run(limit=limit)
    bench = tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300"))
    metrics = compute_metrics(result, bench)
    report_path = write_report(result, metrics, cfg["report"]["output_dir"])
    print("回测完成")
    print(f"- 交易次数：{metrics['trade_count']}")
    print(f"- 胜率：{metrics['win_rate'] * 100:.2f}%")
    print(f"- 总收益率：{metrics['total_return'] * 100:.2f}%")
    print(f"- 最大回撤：{metrics['max_drawdown'] * 100:.2f}%")
    print(f"- 报告：{report_path}")


if __name__ == "__main__":
    main()

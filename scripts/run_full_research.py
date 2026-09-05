"""全市场选股 + 回测一次性执行，带进度输出，适合后台运行。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.backtest import BacktestRunner
from chanlun_trader.config import load_config
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.report import write_report
from chanlun_trader.screener import scan_all
from chanlun_trader.tdx_data import TdxData


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    out_dir = Path(cfg["report"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"[{time.strftime('%H:%M:%S')}] 开始全市场选股")
    t0 = time.time()
    df = scan_all(
        tdx,
        cfg,
        limit=None,
        progress_cb=lambda done, total: log(f"[{time.strftime('%H:%M:%S')}] 选股进度 {done}/{total}") if done % 100 == 0 or done == total else None,
    )
    out = out_dir / "signals_latest.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    log(f"[{time.strftime('%H:%M:%S')}] 选股完成：{len(df)} 个买点信号 -> {out}，耗时 {time.time()-t0:.0f}s")
    if not df.empty:
        log(df.groupby("signal_type").size().to_string())

    log(f"[{time.strftime('%H:%M:%S')}] 开始全市场回测")
    t1 = time.time()
    runner = BacktestRunner(tdx, cfg)
    result = runner.run(
        limit=None,
        progress_cb=lambda done, total: log(f"[{time.strftime('%H:%M:%S')}] 回测数据准备 {done}/{total}") if done % 100 == 0 or done == total else None,
    )
    log(f"[{time.strftime('%H:%M:%S')}] 回测数据准备完成，开始按日撮合")
    bench = tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300"))
    metrics = compute_metrics(result, bench)
    report_path = write_report(result, metrics, cfg["report"]["output_dir"])
    log(f"[{time.strftime('%H:%M:%S')}] 回测完成：交易 {metrics['trade_count']} 笔")
    log(f"  胜率：{metrics['win_rate']*100:.2f}%")
    log(f"  总收益率：{metrics['total_return']*100:.2f}%")
    log(f"  年化：{metrics['annual_return']*100:.2f}%")
    log(f"  最大回撤：{metrics['max_drawdown']*100:.2f}%")
    log(f"  盈亏比：{metrics['profit_loss_ratio']:.2f}")
    log(f"  基准收益：{metrics['benchmark_return']*100:.2f}%")
    log(f"  报告：{report_path}")
    log(f"  总耗时：{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

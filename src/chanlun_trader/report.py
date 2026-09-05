"""报告模块：输出 Markdown 回测报告和交易明细 CSV。"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import pandas as pd

from .presentation import format_reason


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def write_report(result: dict, metrics: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"report_{stamp}.md")
    csv_path = os.path.join(output_dir, f"trades_{stamp}.csv")

    trades = result["trades"]
    if trades:
        df = pd.DataFrame([t.__dict__ for t in trades])
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: list[str] = []
    lines.append("# 缠论选股交易系统 回测报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 回测区间：{metrics.get('start_date', '')} ~ {metrics.get('end_date', '')}")
    lines.append(f"- 初始资金：{metrics.get('initial_cash', 0):,.2f}")
    lines.append(f"- 期末资金：{metrics.get('final_cash', 0):,.2f}")
    lines.append(f"- 期末权益：{metrics.get('final_equity', 0):,.2f}")
    lines.append("")
    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 总收益率 | {_fmt_pct(metrics.get('total_return', 0))} |")
    lines.append(f"| 年化收益率 | {_fmt_pct(metrics.get('annual_return', 0))} |")
    lines.append(f"| 最大回撤 | {_fmt_pct(metrics.get('max_drawdown', 0))} |")
    lines.append(f"| 基准收益率（沪深300） | {_fmt_pct(metrics.get('benchmark_return', 0))} |")
    lines.append(f"| 交易次数（已平仓） | {metrics.get('trade_count', 0)} |")
    lines.append(f"| 胜率 | {_fmt_pct(metrics.get('win_rate', 0))} |")
    plr = metrics.get('profit_loss_ratio', 0)
    plr_str = "∞" if plr == float("inf") else f"{plr:.2f}"
    lines.append(f"| 盈亏比 | {plr_str} |")
    lines.append(f"| 盈利因子 | {metrics.get('profit_factor', 0):.2f} |")
    lines.append(f"| 平均持有天数 | {metrics.get('avg_holding_days', 0):.1f} |")
    lines.append(f"| 最长连续亏损（笔） | {metrics.get('max_consecutive_losses', 0)} |")
    lines.append(f"| 年化波动率近似 Sharpe | {metrics.get('sharpe', 0):.2f} |")
    lines.append(f"| Sortino | {metrics.get('sortino', 0):.2f} |")
    lines.append(f"| Calmar | {metrics.get('calmar', 0):.2f} |")
    lines.append("")
    lines.append("## 交易明细")
    lines.append("")
    if trades:
        lines.append("| 代码 | 信号 | 买入日 | 买入价 | 卖出日 | 卖出价 | 卖出原因 | 持有天数 | 盈亏额 | 盈亏率 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for t in trades:
            lines.append(
                f"| {t.code} | {t.signal_type} | {t.buy_date} | {t.buy_price:.3f} | {t.sell_date} | "
                f"{t.sell_price:.3f} | {format_reason(t.sell_reason) if t.sell_reason else '无'} | {t.holding_days} | {t.pnl:,.2f} | {_fmt_pct(t.pnl_pct)} |"
            )
    else:
        lines.append("无交易。")
    lines.append("")
    lines.append(f"交易明细 CSV：`{os.path.basename(csv_path)}`")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path

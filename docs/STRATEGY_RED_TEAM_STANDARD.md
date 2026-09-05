# STRATEGY RED TEAM STANDARD

## 攻击清单
- Cost x1/x2/x3
- Slippage x1/x2/x3
- Signal Delay +1/+2 sessions
- Universe Top300/500/800
- Temporal: subperiods / anchored walk-forward / rolling walk-forward
- Regime: Bull/Bear/Sideways/HighVol/LowVol/RiskOn/RiskOff
- Winner concentration: Top1/3/5/10 contribution + removal
- Month concentration: remove best month
- Sector concentration: remove best sector + Top3 contribution + HHI
- Extreme period removal: 2024-09-24 ~ 2024-10-08
- Bootstrap: trade bootstrap / block bootstrap
- Placebo: random stock / signal shuffle / date shift
- Random Skip 10%/20%/30%
- Parameter neighborhood ±10%/±20%（plateau 优先；只有精确阈值 -> PARAMETER_FRAGILE）
- Corporate Action Safety：跨无法正确处理 CA 的交易 -> BACKTEST_CERTIFICATION=CORPORATE_ACTION_UNSUPPORTED

## 输出
每个 positive Strategy 必须完成上述适用项才可 ROBUST_PRETEST。

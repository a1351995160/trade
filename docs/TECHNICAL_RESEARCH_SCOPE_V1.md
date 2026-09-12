# 技术指标及公开研究范围 V1

本轮用户明确要求扩大到技术指标与公开交易方法。以下资料用于提出假设，不能证明本账户盈利。访问日期2026-09-12，设计侧接触了公开论文摘要中的研究结果；不代表读取项目Validation或Final Test。

## 覆盖目录与处理

|类别|纳入研究的指标或方法|当前状态|
|---|---|---|
|趋势|SMA/EMA/WMA、MACD/PPO、ADX/DMI、Aroon、SAR、KAMA、TRIX、Ichimoku|MACD已实现待验证；其余待固定规则，不能称已回测|
|震荡/反转|KDJ/Stochastic、RSI、StochRSI、CCI、Williams %R、CMO、ROC、BIAS、Ultimate Oscillator|KDJ已实现待验证；同源/线性等价变体不重复计独立证据|
|波动/通道|ATR/NATR、BOLL、Donchian、Keltner、标准差、历史波动率|待固定规则；不改变原费用或风险口径|
|量价|OBV、AD/ADOSC、CMF、MFI、量比、PVT、EMV|待核对复权价格与原始量纲后实现|
|结构|缠论包含/分型/笔/中枢/背驰、蜡烛形态|首个为封闭底分型+MACD改善，仅简化派生规则，不称完整一买三买|
|横截面/市场|相对强弱、新高位置、涨跌宽度、均线上方比例|已有市场五日收益条件；新高位置待验证，宽度需历史池口径|
|数据依赖项|VWAP、筹码、逐笔资金流、盘口、期权持仓|不能用日线伪造日内路径、筹码或资金流；不为凑全而补数据|

所有技术指标不是一个有限、统一的全集。目录覆盖主流机制；“纳入研究”不等于已计算、已通过或全部堆进一个策略。每个真实参数变体登记，失败保留，不能只汇报赢家。

## 首批六条事前固定规则

精确公式、种子及预热冻结于technical_train_signals_v1.py和每批PREREGISTRATION。批5：MACD(12,26,9)正轴金叉；KDJ(9,3,3)前K<20金叉。批6：封闭底分型且MACD柱上升；月末20-session反转。批7：252-session新高接近度；20-session最大单日涨幅较低。

统一Top3、20-session固定持有、下一开盘、原10000元账户及费用/容量/hazard规则。参数来自本轮事前研究选择，不声称逐字复现论文。只筛COMPLETE、TRAIN净回报>0及关闭lot>=30；不作统计资格判断。每候选通过原服务增量登记1主+1仅确证修复额度，当前入口拒绝修复执行。原到期不变。

使用既有已核验HFQ响应收盘除以同日RAW收盘得到比例，派生信号OHLC；这是研究用途的数值派生，非厂商OHLC证明。成交始终RAW。仅沿已有合格features日期计算，缺日重启预热，独立日历不压缩。历史PIT未知、回溯复权、事后hazard局限保留。

分型不回填中间K线：右侧合并K线须被下一合并组首bar封闭后发布。必须通过前缀重放检验。月末只在独立日历下一session跨月时识别，末日未知不补月末。

## 公开依据（定义支持，不构成盈利证据）

- [TA-Lib动量目录](https://ta-lib.github.io/ta-lib-python/func_groups/momentum_indicators.html)：列出MACD、RSI、CCI、DMI、MFI等API参数；初始化细节以本轮冻结实现为准，不宣称完全TA-Lib等价。
- [TA-Lib重叠指标](https://ta-lib.github.io/ta-lib-python/func_groups/overlap_studies.html)、[量价指标](https://ta-lib.github.io/ta-lib-python/func_groups/volume_indicators.html)：用于完整性检查，不是盈利排名。
- [StockCharts重复信息](https://chartschool.stockcharts.com/table-of-contents/overview/multicollinearity)：同类指标会重复使用相同信息，跨类别组织研究。
- [通达信公式函数](https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html)：EMA、SMA、HHV、LLV、CROSS等定义支持；不用未来函数。
- [湘财分型应用研究](https://www.xcsc.com/upload/jrcpcx/20120315/160745.pdf)：2012年分型应用材料；当前采用仓库简化包含与分型，不声称完整缠论。
- [中国反转研究](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0137892)：月频排序研究启发月末反转；本账户长期仅多、费用与样本不同。
- [中国新高动量研究](https://doi.org/10.2139/ssrn.4541025)：启发新高位置；保留全部月份，不因公开或本地表现删除二月。
- [Size and Value in China](https://www.nber.org/papers/w24458)：中国因子与MAX口径参考；基本面策略需额外PIT证据，未用现价补造。
- [交易成本研究](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies)：成本与换手不可忽略；其机构多市场样本不是个人A股账户证明。

以上为人工摘要与可追踪URL，不冒充页面原文快照或厂商证明。

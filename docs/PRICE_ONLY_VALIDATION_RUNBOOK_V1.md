# Price-only 指标验收 V1：复核与证据生成

- `TRIX_V1` 的 `trix_ma` 是 `trix` 的 `signal` 期算术滚动均值。公开注册说明、实现和独立 oracle 均按此口径；此前按 EMA 得到的信号值不属于该版本契约。
- gbbq 访问保护测试自行保存、激活并恢复任务作用域，只用临时合成哨兵验证读取前拒绝。普通 pytest 不需要全局任务开关即可安全运行这两个模块。
- A1 真实 RAW 样本默认跳过；本轮不读取或重跑。其历史 PASS 不等于当前源码的重新验证。

生成逐项证据时，先在**已提交的源码**上运行 A0 公式与条件测试，并使用 `--junitxml` 保存本次 JUnit。测试进程会记录 HEAD、`src/`、`scripts/`、`tests/`、`.github/workflows/` 的 Git 树指纹，以及这些路径有无未提交修改。随后运行 `scripts/emit_price_only_evidence_v1.py --junit <本次 JUnit> --out <输出 JSON>`。

生成器核对运行时与生成时的源码树指纹。JUnit 缺身份、身份不符或任一端存在未提交源码修改时，25 项指标不得从该 JUnit 获得 VERIFIED，命令返回非零；仅报告或文档提交使 HEAD 变化而源码树不变时仍可复用。已提交的正式结果见 `reports/price_only_validation_v1/`；完整套件与 A1 的当前源码状态需分别看报告中的限定说明。

# GitHub Source Baseline V2

## 定位

本仓库是 `chanlun-trading-system` 的可审计、可继续开发的精简源代码基线，不是本地工作区的镜像，也不包含本地 Git 历史。

本次导出的唯一来源是：

- 源项目：`E:\llmwiki\chanlun-trading-system`
- 源分支：`codex/research-factory-baseline-v1`
- 固定源提交：`42fbc52a2cc10212288a293e15c630ed09ea59f5`
- 发布仓库：`https://github.com/a1351995160/trade`

导出内容聚焦于 Research Factory、治理流程、研究引擎、PIT 安全基础、测试、正式架构文档和运行所需配置。市场行情、研究运行状态、Trial/Performance 产物、AI staging、缓存、构建产物和本地环境数据均被排除。

## 克隆后的最小设置

Python 依赖可由 `requirements.txt` 安装；前端依赖使用 `frontend/package-lock.json` 锁定，并在 `frontend` 目录执行 `npm ci`。

研究代码依赖本地数据能力和 TDX 工作区。源配置中的 `E:/new_tdx_mock` 是原项目的本地依赖示例，不是仓库内的数据，也不是可移植路径。新环境必须在本地提供等价的数据目录和访问配置后，才可运行依赖真实行情或本地研究状态的集成测试。

## 可验证基线

建议先执行：

```text
python -m compileall -q src
python -m pytest --collect-only -q
cd frontend
npm ci
npm test
npm run build
```

不应把测试运行产生的 `frontend/dist`、`tsbuildinfo`、研究数据库、行情文件或运行报告加入版本库。缺少被排除的本地 Objective、Frozen Candidate、Structural 或事件数据时，依赖这些运行状态的集成测试应被视为本地环境测试缺口，而不是通过复制生产数据解决。

## 治理边界

Research Factory 是研究控制平面。Proposal、AI Design、Candidate Proposal、Freeze、Structural Preflight、Predictive Authorization、Trial、Validation 和 Evolution 之间的状态转换必须由代码治理边界控制；本基线不自动启动研究循环，不自动创建 Candidate，也不自动消耗研究预算。

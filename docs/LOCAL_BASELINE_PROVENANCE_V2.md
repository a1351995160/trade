# Local Baseline Provenance V2

## 来源锁定

本发布目录只由源项目固定提交生成：

- 源提交：`42fbc52a2cc10212288a293e15c630ed09ea59f5`
- 源分支：`codex/research-factory-baseline-v1`
- 前一基线：`3a7db558cb6f01b81bae33cd1e95bca987ce00fa`

生成过程使用 Git 固定提交的归档内容，不读取源工作区的 dirty 或 untracked 文件。源工作区中存在的研究数据、运行报告、AI staging、构建输出和测试副作用不属于本发布基线。

## 基线范围

本基线保留源代码、测试、配置、前端源文件、研究治理与架构文档，以及 `reports/README.md`。排除 `data/`、`.cache/`、`experiments/`、`research_inputs/`、`progress.md`、运行报告、外部 Alpha 包、行情/数据库/序列化研究产物和 `frontend/dist/`。

固定提交中的补全内容包括 Research Factory 的自包含依赖、研究控制台入口、AI 研究提示和相关治理文档；不包含本地研究任务结果或真实 Objective/Candidate/Trial 状态。

## 可追溯性与恢复

发布仓库使用新的单根提交建立干净历史，不能用发布仓库历史替代源项目提交历史。需要核对来源时，应回到源项目并验证固定提交哈希；需要回滚发布版本时，使用发布仓库中对应的不可变提交，不修改源项目工作区。

## 环境差异说明

源代码中的本地数据路径和 TDX 适配逻辑仅表示运行依赖，不随本基线携带数据。克隆者应自行配置本地数据提供方、缓存位置和测试夹具，并保持凭据通过环境变量或本地未跟踪配置提供。

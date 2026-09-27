# 七项计划验收证据

业务说明与完整边界见 [验收文档](../../docs/AUTONOMOUS_SYSTEM_ACCEPTANCE.md)。报告不是策略资格凭证。

| 文件 | 含义 |
|---|---|
| DATA_METADATA_INPUT.json / DATA_QUALIFICATION.json | 4 组数据、31 条既有授权/执行暴露投影；3 组涉及研究暴露、1 组独立性未知 |
| VERIFIED_HISTORICAL_INPUT.json | 原双股数据重新核验 |
| THREE_SYMBOL_INPUT.json / THREE_SYMBOL_ACCOUNT.json | 三股真实输入及504日账户核账/重复一致；固定策略亏损，不是有效性证据 |
| METHOD_DIAGNOSTIC_SCOPE.json / METHOD_APPLICABILITY.json | 固定诊断范围、独立数值参照及真实过程适用性不足 |
| VALIDATION_PREPARATION.json | 未来验证路线准备；未登记新独立机会 |
| CAPTURE_READINESS.json | 本机 TDX 服务未运行；不等于供应商没有数据 |
| REAL_RESEARCH_ATTEMPT.json | 真实模型被服务404拒绝；原请求和预算保留，无成功候选 |
| completion-fixes.xml | 预览稳定性、原预算保护和三股运行器18项通过 |
| paper-parity.xml | 公共账户与持久Paper逐日对照、恢复/去重1项通过 |
| root-focus-02.xml / multi-runner.xml | 定向入口及运行器10项、9项通过；勿与最终测试相加算覆盖率 |
| diagnostic-regressions.zip | 原宽扫描及长回归失败完整保留；其中长回归224通过/4夹具泄漏失败已修复，不声称原运行全绿 |
| lifecycle-browser.png | 合成任务创建、启动、暂停、恢复、完成的浏览器证明；1/1次，真实观察0 |

源码及全部真实账户原件保存在本机 `E:/llmwiki/autonomous-strategy-research-v1/autonomous-completion-20260927/`。仓库只提交精简证据，原始诊断日志另保留在该目录 `DIAGNOSTICS/`。

最终 Linux/Windows 验收运行 [Autonomous research completion](../../.github/workflows/autonomous-completion.yml)，同时沿用既有 CI。CI 的最新状态和本次合并身份以 PR 检查及最终交付说明为准；本文件不提前声称通过。

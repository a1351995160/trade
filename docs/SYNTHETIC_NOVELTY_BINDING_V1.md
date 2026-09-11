# 合成事前新颖性比较集绑定 V1

本模块实施用户 2026-09-10 明确批准的隔离 synthetic 新流程。它不改变原新颖性算法、阈值、统计家族、预算或 CP 权限，也不批准 Objective execution_binding。完整 R2 启动/结算组合仍受独立待批事项阻断。

## 来源与确认

部署方通过 declare-sources 显式声明当前工作区实际冻结 registry 的完整范围；服务逐一验证文件、registry hash、实际冻结合同及重建身份，不接受成员列表。范围只允许追加来源，历史哈希链和工作区当前 scope_head 拒绝缩小范围、缺失版本或退回旧范围。形成预览时必须包含实际当前合同；另建空 registry 无法提供精确自身身份。来源声明不是候选启动授权。

认证仅覆盖已声明来源，不声称全工作区/全历史新颖性。服务不发现或扫描未声明 registry、真实研究目录和备份。部署方必须声明实际批准的完整范围；本地服务不认证拥有文件写权限的管理员是否隐瞒了部署范围，也不能以可重算的 hash 对抗该管理员重写全部证据。普通客户端不能提供任意成员子集。

快照包含版本、绝对工作区及持久 workspace_id、来源路径/字节 hash/registry 版本、完整候选与合同身份、原 Gate 所需设计 allowlist、确定性排序、全部来源映射、排除前数量及精确自身排除记录。相同 ID 不同合同阻断；不同 ID 即使设计相同也参与原 Gate。绩效报告不读取，结论/收益/资格不参与成员筛选。

实际服务 preview → 操作方 confirm 产生不可覆盖测试确认；CLI 需要 GOVERNED 模式和显式 --confirm。测试驱动器通过同一服务扮演操作方，回执 test_confirmation=true、execution_authorized=false。客户端 approved=true、名字、标签或内容 hash 不能替代此入口。已完成确认可查询，不能启动或重跑 Trial。

## 准入与并发

SyntheticNoveltyTrialStartServiceV1 的新版本预览绑定完整已确认快照；实际原启动确认产生的新 intent 携带绑定，拒绝旧/不同绑定 intent，不迁移历史记录。原 canonical executor 对新版本 metadata 验证实际 intent、候选、Trial 和内容完整性，再复核当前范围与全部来源并调用原 Gate。

最后复核和原 ledger.mark_performance_accessed 位于同一短临界区，复用 ObjectiveMutationLock 以及实际 registry.write 的资源锁；退出临界区后才执行长期计算。参与原协议的并发 writer 无法穿过这个边界；不承诺阻止绕开文件锁的外部管理员直接改文件。模块不 reserve/consume/release 预算，失败结算仍归原执行器。其他启动门禁独立，比较集通过不等于可启动。终态 Trial 仍由原终态复用协议处理，不重建比较集免费重跑。

## 部署与调用

使用原锁定依赖的源码 checkout、已启用隔离探针的独立 synthetic 根。下面 ROOT、CANDIDATE、HASH、PREVIEW 都必须替换为当前真实合成服务输出；不能填真实研究目录。

```powershell
python -m chanlun_trader.research_factory.synthetic_novelty_cli --root ROOT --mode GOVERNED declare-sources --source data/research/research_factory/batches/BATCH/durable_frozen_candidate_contracts.json
python -m chanlun_trader.research_factory.synthetic_novelty_cli --root ROOT --mode GOVERNED preview --candidate-id CANDIDATE --contract-hash HASH
python -m chanlun_trader.research_factory.synthetic_novelty_cli --root ROOT --mode GOVERNED confirm --preview-id PREVIEW --confirm
python -m chanlun_trader.research_factory.synthetic_novelty_cli --root ROOT history --preview-id PREVIEW
python -m chanlun_trader.research_factory.synthetic_novelty_cli --root ROOT --mode GOVERNED start-readiness --preview-id PREVIEW --objective-id OBJECTIVE
```

CLI 不暴露执行命令；启动预览服务接入已实现，但缺失 Objective execution_binding 的完整服务正向启动未认证，不以单组件测试代替。没有新增真实研究、Paper 或订单权限。

## 需求→代码→测试→证据→限制

| 需求 | 实现 | 实际验收与证据 | 限制 |
|---|---|---|---|
| 完整声明范围/合法空集/身份 | synthetic_novelty 的 registry 读取、scope 链及 workspace 身份 | test_synthetic_novelty；空/非空、缺失/损坏/冲突/丢版本/缩范围 | 明确声明域，不声称全局 |
| 精确自身、换名、原 Gate | 设计 allowlist、来源映射及 CandidateNoveltyGateV2 | 同设计换名 EXACT_DUPLICATE、持有期邻居 PARAMETER_NEIGHBOR；原输入判定对照 | 比较专用合法合同夹具不是启动授权；未改语义指纹算法 |
| 实际确认/版本/重放 | preview/confirm/history、新启动服务、CLI | 实际服务正负向、复制到其他根、旧确认失效、CLI 显式确认 | 内容 hash 不是身份认证或独立权限 |
| 并发/重启 | 原资源锁及 canonical 短准入边界 | novelty_worker 真实子进程重启、锁内写拒绝/锁外写成功、旧确认随后拒绝 | 不含完整 Trial 进程恢复认证 |
| 盲化/其他门禁/兼容 | 设计投影、新版本 metadata 校验、旧路径保持 | 绩效文件读取断言、真实原启动服务不可用、客户端声明拒绝、旧 evidence 不带 binding | 完整 R2 仍 BLOCKED |

阶段日志位于外部证据根 novelty-*.log/XML；24 项组件测试通过后还有完整固定 HEAD 回归，最终结果以 final-novelty/ 为准。早期两个失败均是新增测试夹具的错误预期（不同语义指纹、EXACT_DUPLICATE 与 PARAMETER_NEIGHBOR 混淆），不是旧业务缺陷 red。访问拒绝用例是明确注入的 I/O 异常，不冒充操作系统 ACL 实测。

工程完成、合成组件验收、真实数据核验、真实运行授权分别报告。真实数据 NOT_VERIFIED，真实运行 NOT_AUTHORIZED，观察 0 天；Windows L1/L6 仍 OPEN_ROOT_CAUSE_UNCONFIRMED。

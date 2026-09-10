# 新建 Objective 的执行身份绑定 v2

用户于 2026-09-10 单独批准 A；本模块只用于隔离 synthetic 协议开发和验收。B 批次执行、新颖性和使用资格各有独立合同，不能互相替代。创建目标仍停在人工设计边界，不授予任何研究启动权。

## 正式服务与调用

`ResearchProposalGovernanceServiceV2(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))` 复用 v1 的审核、Objective/SearchBudget/Multiple Testing Family/lineage 暂存及恢复事务。新服务要求原测试隔离环境；外部域和只读策略拒绝。

公开入口为 `POST /api/research/evolution/v2/proposals/{proposal_id}/{action}`，动作只接受 review、preview、confirm、recover，沿用本机请求及应用执行政策检查。旧 v1 路由不变。

review 在原 action/reviewer 等字段之外必须提供：

```json
{
  "execution_binding": {
    "policy_identity": {"policy_id": "冻结政策 ID", "version": "冻结版本", "hash": "冻结政策 hash"},
    "research_period_identity": {"id": "明确窗口 ID", "start": 20240102, "end": 20240301},
    "factor_event_registry_identities": {
      "factor_registry": {"path": "data/research/factor_library_v1/registry.json", "sha256": "实际文件 SHA256"},
      "event_registry": {"path": "data/research/event_registry/registry.json", "sha256": "实际文件 SHA256"}
    }
  }
}
```

示例数值不是运行批准或默认窗口。服务读取实际冻结政策及锁，校验日期和政策窗口；读取两个显式 registry，验证格式、成员身份和文件 SHA256。无事件时仍须实际存在且可解析的空事件 registry，缺文件不能当作空集。身份记录不是数据可用性证明；实际 caller/Structural 的检查仍必须执行。

新 preview 包含工作区绝对身份、测试域、全部绑定和政策/锁/registry 文件字节证据。新 Objective 身份由原设计身份与这些证据确定性计算；再从目标身份派生 batch_id，并将政策的 version/hash 映射为既有领域字段 policy_version/policy_hash，最后填入 policy_identity.objective_id，避免循环依赖。新 Objective 包含 execution_binding_version/hash，实际创建回执使用 v2 schema。

confirm 使用原 proposal_hash、preview_hash、confirmation_token、confirmed、confirmer，另须 `test_confirmation=true`。这些客户端字段本身不创造批准；服务必须找到实际审核和预览，并重核来源。测试驱动器明确扮演合成人工确认操作方。确认前后均没有设计/启动/性能权限的自动派生。

## 一致性和兼容

创建使用既有 ObjectiveMutationLock，对同一 Proposal 串行；确认从来源复核到原短创建事务完成，持有现有 for_resource 文件锁。协作写入须使用相同资源锁；此协议不抵抗拥有全部文件写权限的管理员绕开服务直接改盘。没有第二套锁系统，也没有长期计算放入该边界。

来源变化不会自动刷新或沿用旧确认；必须通过明确的新创建流程取得新预览。已经确认的中断事务只按原暂存字节身份完成持久化，不刷新绑定，也不启动研究。历史 completed 回执只表示创建历史，不是启动权。

v1 默认 schema 保持不变；v1 确认/恢复拒绝 v2 preview，v2 拒绝 v1 preview；不迁移旧目标、不补绑旧字段、不重算历史 ID。新版本生产流程不得调用旧测试 fixture 的初始化方法补字段或扩家族额度。当前原事务预算仍为 Objective/批次 4、统计家族 1 个槽，不因 R3 要求多个候选而扩张。

## 验收范围与仍待完成

组件测试覆盖正式创建、文件来源变化、错误绑定、跨工作区、显式测试确认、v1 兼容、新进程共享锁竞争、写入目标后退出及恢复/重放。正式创建至实际设计批准、冻结和物化的正向逐字节检查新 Objective 没有事后修改。负向保留对推荐结果/绩效字段的拒绝。

集成发现原物化服务把正式目标 `risk_constraints.recommendation=DISABLED` 当作结果字段拒绝。现仅对这个精确禁止值生成临时盲化投影，原 Objective 和来源 hash 不变；ENABLED 或含绩效的对象仍拒绝。失败原文见仓库外 objective-materialization-red.log，属于真实服务衔接缺陷，不把新功能原先不存在记作 red。

完整实际 runner/engine、统计裁决、registry 和失败回流的 R2，以及 B 实际有界批次仍 IMPLEMENTING。本模块测试不代表路线完成或双平台认证。旧 e288746 集中审计 ZIP 保持不变；后续固定新 HEAD 后生成新包。

回滚：停止新 v2 创建入口，revert 本模块提交；已形成的新版本 Objective/回执保留，旧入口拒绝继续写，不删除或迁移。真实数据 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false；L1/L6 和既有 OPEN 事件不关闭。

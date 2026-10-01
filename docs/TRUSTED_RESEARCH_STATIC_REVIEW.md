# PR #28 静态扫描复核（2026-09-30）

SonarCloud 的质量门报告失败，本记录不把该状态改称通过，也没有关闭扫描规则。GitHub Actions 的行为测试另行核对。复核输入为提交 e2acdc4 的扫描结果，修正了表单提交按钮缺少显式 type 的问题，前端 12 项测试通过。

## 告警结论

| 告警范围 | 核验依据与处理 |
|---|---|
| `json:S6418`，方法 protocol 和六份 CONFIRMATION 的 auth 字段 | 指向 predictive_authorization 等源码文件的 SHA256，或 authorization_identity 内容摘要；不是密码、令牌或可独立授予权限的秘密。保留冻结原字节，判为误报。 |
| 冻结 `formal_rule_adapter_v3_frozen.py` 条件恒真 | 本轮真实方法尚未获批准，resolver 明确 applicable=false，发布必须拒绝。冻结快照忠实保存该边界，不应为了消除扫描而打开发布。 |
| `fetch_research_baostock_v1.py`、`generate_research_capabilities_v1.py`、`run_trusted_research_v1.py`、`study_formal_account_method_v3.py` 的 CLI 路径 | 维护者显式提供的本地输入／输出路径；不是模型生成的策略字段或 HTTP 可传的部署配置。采集文件名另有全匹配和重复检查，输出使用新建模式。当前调用范围内未发现跨权限边界的路径穿越。 |
| `lifecycle_deployment_v2.internal` | 配置由维护者登记，HTTP 不能传入；路径必须无重定向且位于 workspace root 内，授权文件另绑定 SHA256 与有效期。文件系统存在性检查不是公开任意路径接口。 |
| `run_strategy_account_v1.reconcile_account` | 公共服务提供已冻结 JOB，验证 sources、计划成员和 JOB.root 必须等于文件父目录；再核对固定后缀资源文件。CLI 直接使用者本来具有同等本地权限。 |
| `research_campaign_v1` | root 为部署/本地操作者参数；研究调用仅使用已登记 campaign，授权 ID 和冻结预算范围另作校验。不是网页传入任意根目录的入口。 |
| `diagnosis_research_v3._digest` | 模型只返回规则；候选 ID 与请求框架由系统产生，任务路径由公共服务冻结成 root/task_id/account/JOB.json。恢复需公共 task_id 格式与固定目录校验。 |
| `diagnosis_worker_v3.main` | 内部 worker 固定入口；请求由父进程生成，request_hash、请求内容稳定哈希和资源握手 execution 必须一致。模型不能指定 worker 类型、入口或 job_path。 |
| `PublicStrategyArchiveV3.freeze` | 直接入口为受信 CLI public-freeze；要求规范 JOB.json 路径并先独立核账 PASS。输出 PS_ ID 来自稳定哈希，归档 root 与无重定向检查仍在。未发现接收此路径的 HTTP 路由。 |
| `research_evidence_v1` | Web 仅接受格式受限 task_id，不能传 job_path；生命周期还验证部署 root 和 job_sha256。核验检查 JOB.root、runtime、批准、预算和 input 身份，结果/结算索引必须等于作业内固定路径，冻结输入读取核对哈希与无重定向。 |

核心路径告警经独立安全审查只读追踪，未发现当前公开输入可利用的越权链；其余 CLI 告警由主执行者核对。没有将哈希当作认证机制。该结论适用于当前本地单用户、维护者控制部署和任务目录的边界，不能外推为可直接开放到互联网的多用户服务；持有同等本地文件写权限的人能够修改受信运行目录，未声称防御此类主机入侵。

## 验证和限制

本轮静态审查未运行新的攻击测试；已有公共入口、路径拒绝、授权和篡改测试由远端回归覆盖。未改写账户证据、未修改统计门槛、未添加 Sonar 排除项、未伪造成功状态。SonarCloud 仍可能显示上述人工核验误报，属于外部工具状态而非行为测试通过证明。若以后增加远程路径接收或多用户权限，必须重新审查这些信任边界。

## PR #29 全范围入口复核（2026-10-02）

初次扫描在提交 `59dba06` 报告5项路径安全告警，安全质量门为ERROR。本次不统一标误报、不关闭规则，也不把该外部状态改称通过。新旧两条业务入口的信任边界分别核对，实际发现的路径缺陷已经修复。

| 位置 | 证据与处理 |
|---|---|
| `UniverseDataProviderV1._path` / 扫描 `_signature` | Windows根相对路径的`is_absolute()`可能为False，拼接后却跳出登记目录，已用普通源码路径证实。现在同时拒绝POSIX/Windows的anchor、盘符及父目录写法，并在任何文件探测之前确认属于登记根；保留解析重定向检查。 |
| 扫描 `_check_registration` | 归档复用先与当前provider实际登记的根、准确元数据路径、原始字节哈希及完整manifest对照，再读取落盘登记引用。合法外置元数据仍支持，不从同一份可替换JSON反推允许路径。 |
| 扫描 worker | 现有父进程资源握手增加父进程持有的原`intent_identity`；worker在任何来源读取前要求完整purpose与原意图身份匹配。重算落盘文件自身哈希不能替换父进程绑定。 |
| `run_strategy_account_v1.sha` | 告警链来自可信本地ETF loader。进入身份文件后，外置行情路径还受维护者配置中的`identity_sha256`原件验证；公共策略声明没有该loader或文件路径字段。未发现当前公共输入可利用的路径注入，不把规范路径检查冒充授权。 |
| `strategy_interface_v1.prepare` | runtime依赖来自维护者本地插件，或公共服务固定factory/loader所生成的JOB；公开策略声明不能传入runtime、source_hashes或loader。公共启动还绑定JOB字节与计划。不把全部文件限制到REPO，以免破坏已授权外置ETF与插件合同。 |

定向测试使用两个依赖环境：Provider各56项通过，扫描各44项通过。反例覆盖根相对、盘符相对、UNC、设备路径、父目录、解析重定向、伪造登记后重算哈希、错误父握手及公共字段注入，并验证真实受限worker与合法外置元数据。Windows E盘不能创建真实符号链接，该一项如实跳过，另有解析后重定向拒绝测试；Linux相关CI继续执行真实链接反例。

这些补强不改变账户权限、可信插件模型或统计门槛。外部Sonar结果与行为回归分别记录；最终状态见本机交付回执，不自动关闭或分类外部告警。本结论仍限于本地单用户、维护者控制部署和任务目录的运行方式。

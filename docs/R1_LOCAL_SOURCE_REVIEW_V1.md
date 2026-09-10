# R1 当前源码副本静态审查

## 结论与来源

CURRENT_EXACT_SOURCE_FILE_CHECK=FOUND；SOURCE_KIND=LOCAL_WORKTREE_SNAPSHOT；HISTORICAL_PROVENANCE=UNVERIFIED。
原路径 E:/llmwiki/chanlun-trading-system/scripts/run_engine_corrected_phase4_v3.py 为普通文件，必要父路径无重解析点。采集 UTC 2026-09-09T07:15:39.2293176Z，48,823 字节，SHA256 fa7d5439e6f152e4ac6eceffbf29fab5d0d51a5fac3e487afc1542f4384063e1。
精确 ls-files/check-ignore 均无匹配：当前未跟踪、未忽略；没有重新查询历史。SCOPED_GIT_SEARCH=PREVIOUSLY_COMPLETED_NO_MATCH。文件哈希仅固定本次字节，不证明历史提交、旧算法等价性或数值正确性；没有来源 commit/blob。

原字节保存在独立开发目录的 Git 忽略目录 tmp/r1-source-review，manifest.json 固定主文件采集信息，static-inventory.json 保存每个副本的完整 AST import 与顶层语句读取证据。共 17 个源码副本（主脚本、legacy、15 个包模块），全部只读采集且 AST 解析，不 import、不执行。原文不提交或推送。静态审查未识别硬编码凭据或真实绩效结果表；发现冻结身份常量、运行日期和结果文件引用，不读取其目标。该检查不是秘密扫描认证。

## 来源与兼容矩阵

所有条目来源均为 LOCAL_WORKTREE_SNAPSHOT，历史来源 UNVERIFIED。对比目标为调查 HEAD 023e656 的同路径源码，主脚本目标应为 scripts/run_engine_corrected_phase4_v3.py，当前部署缺失；legacy 同样缺失。原始换行和字节未修改。

| 路径 | 原字节数 | 原 SHA256 | 与部署副本比较 |
|---|---:|---|---|
| `run_engine_corrected_phase4_v3.py` | 48823 | `fa7d5439e6f152e4ac6eceffbf29fab5d0d51a5fac3e487afc1542f4384063e1` | 缺失或内容不同 |
| `scripts/run_automated_strategy_validation_v1_rerun_v2.py` | 62801 | `3c9fe6ad2ce6558515a6612369ed03dc7f3c9cfc694b511059cce19dabbd86a1` | 缺失或内容不同 |
| `src/chanlun_trader/engine/portfolio_exit.py` | 5504 | `95f3f1efcaea0926a8a7a8e48bd5977b1abba6029d8bb11e7a4bc683c5dfd647` | 仅行尾差异 |
| `src/chanlun_trader/engine/signal.py` | 2745 | `d21683b1767e342864fd682e623c4b57dae723fea91375a78889aa3d137a9d28` | 仅行尾差异 |
| `src/chanlun_trader/engine/time_types.py` | 7474 | `02342b4e991ae5276f4f04118265ee6a487f067d45054a4f23e2afaadeadb485` | 相同原字节 |
| `src/chanlun_trader/engine/asof.py` | 6450 | `49788b87a3b69e3204531dbca3d68a2412114915a1215046dd84b4fa0cff666a` | 仅行尾差异 |
| `src/chanlun_trader/engine/engine.py` | 28125 | `c1a00ef5872be4ef99b3d663f4dfbb6684f7d93b5f5707eee6271fd07b81fbfa` | 仅行尾差异 |
| `src/chanlun_trader/engine/universe.py` | 1001 | `095cf947367eea328a0476c9fdfb947dce5b20f4fa511c289ffce0aa91bf9a6a` | 仅行尾差异 |
| `src/chanlun_trader/engine/position.py` | 1682 | `f04718d89d1c79da9b543bfdfcc233bf3b9a710036df18997101c784959db251` | 仅行尾差异 |
| `src/chanlun_trader/research/strategy_validation.py` | 42648 | `a92fb250586dc0bbb7743fa83f1c81f8ce936bbe13c5d59b9b5122bd00a93410` | 仅行尾差异 |
| `src/chanlun_trader/research/data_router.py` | 9457 | `ab8cde005930bc69936ee08a5311eab92d9f14692dfc11f17edb78651c0ea19f` | 仅行尾差异 |
| `src/chanlun_trader/research/guard.py` | 3662 | `655d81fe948480b9721e60c0333c669b8c67dc787c2242bb4d703e7f091b698d` | 仅行尾差异 |
| `src/chanlun_trader/research/io_safety.py` | 10725 | `0fab21de483654fe71d79d4578856d7ebb12d963ee1b92acd5ddb0546a72215d` | 缺失或内容不同 |
| `src/chanlun_trader/research/strategy_candidate.py` | 44404 | `6cbd7c8f3e779aee1d4655b078a875664e3754c378330172e73b6f6ad2c0a149` | 仅行尾差异 |
| `src/chanlun_trader/research/strategy_semantic.py` | 41811 | `eb3c61d843178b078326050471ac89b72357dc2af41cf9e0476190dc23d7004c` | 仅行尾差异 |
| `src/chanlun_trader/research/unified_factor.py` | 40617 | `e4ed707b6030383eb19accd35cced4eca6e4a242f8e26211c88f44e95b1ae112` | 仅行尾差异 |
| `src/chanlun_trader/research/validation_policy_v2.py` | 43234 | `a0f2821115333c513767febfef8b0303afa253278655bc8ea96f131b95e57784` | 仅行尾差异 |

## 依赖边界

- corrected → scripts/run_automated_strategy_validation_v1_rerun_v2.py；直接包依赖 portfolio_exit、signal、time_types、strategy_validation。
- legacy → engine/asof、engine、signal、time_types、universe；research/data_router、guard、io_safety、strategy_candidate、strategy_semantic、strategy_validation、unified_factor。strategy_validation → validation_policy_v2；portfolio_exit → position。以上对应矩阵内精确路径，均已检查父路径后采集。
- 标准库包括 argparse/csv/gc/hashlib/json/sys/time/collections/pathlib/typing 等；第三方明确为 numpy、pandas、pytz、pyarrow，已有 requirements-p3b/p3a hash 锁。没有安装或加载原目录代码。
- 更深依赖由已提交包复用：engine 的 broker/event_log/events/fee/fill/ledger/order/order_manager/risk/security_state/sizing/slippage 以及 research/run_manifest；strategy_candidate 的 hypothesis 等。未采集这些原工作区副本，原副本版本差异与传递顶层行为 NOT_VERIFIED，不能称完整原源码闭包。包 __init__、第三方初始化和未来动态分支仍需部署隔离正向认证。
- 原主脚本及 legacy 的 AST 全树 import 未发现动态 import 调用；这不是全项目动态行为已排除。外部数据包括行情、PIT、股票池/政策、因子/事件、冻结清单及旧报告，只登记引用，不读取、不复制。

## 顶层副作用与兼容性

两个脚本均在顶层修改 sys.path；corrected 将 ROOT/src 与 ROOT/scripts 插入首位，legacy 插入 ROOT/src。ROOT 从 __file__ 计算，不能照搬成从研究数据根或偶然 cwd 加载。顶层立即 import legacy 和第三方；RESEARCH_END 绑定 legacy。main 受 __name__ 守卫，但传递 import 不受该守卫保护。

原 io_safety 顶层 AUDIT_PATH.parent.mkdir 会写相对 cwd 的 data/research/audit；部署模块已把目录创建移到显式写入，并支持 audit_sink。必须保留当前已认证模块，不恢复旧版本。其余 14 个已采集包模块按文本归一换行比较相同（time_types 原字节也相同）；这仅支持当前静态接口兼容，不证明运行或数值等价。time_types 顶层 pytz.timezone 可加载时区资源；类装饰器与第三方导入仍需隔离测试。

corrected.run_corrected_candidate 的参数与两处调用方静态匹配，返回 engine result、metrics、diagnostics 三元组；write_evidence 默认 true，显式写 CSV/metrics。legacy 所需 ResearchDataRouter/Guard、PITStateMap、load_universe_sets/load_market_index/market_regimes/build_store/load_events/sha256 以及 make_engine、StrategyCandidateCompilerV2、ts_for_date、factor_row_values 均可在静态源码找到。bootstrap_result、concentration、subperiods、regime_split、recompute_cost_stress_metrics、classification、Side 也存在。未调用正式 runner 验证类型和数值。

main_run 会 invalidate_old_run、验证旧冻结物、读取旧 Trial 和研究输入，写报告和 TrialRegistry；不能用运行该入口证明加载。原代码声明不覆盖旧证据不能代替实际审查：invalidate_old_run 存在写失效标记行为。本轮没有执行。

## 最小接入计划（未实施）

1. 评审本次真实快照及来源身份；以明确 snapshot SHA 身份进入未来候选集成，不套用旧 Git 身份或历史认证。部署缺口由“未知实现”缩小为两个已取得待审脚本；正式 loader 仍阻断。
2. 后续单独适配两个脚本的明确部署导入；保留当前包模块，尤其 io_safety。将历史批次 CLI 与可调用执行函数明确隔离，禁止 import 触发研究入口。不修改执行、退出、PIT、成本、统计和权限合同。
3. 用干净 checkout、不同 cwd、原目录不可访问、导入前隔离检验真实 loader 正向；移除各 helper 的负向仍应失败，保留现有数据根毒化哨兵。验证不写 cwd、不启动进程/网络、不访问研究目录。
4. 数值审查先列合成 golden cases：独立 BASE/10K、T+1/lot 退出、最后 session 待卖、PIT 截断、固定信号成本压力、bootstrap 固定 seed、分类与最终 BH 裁决、重启身份。现有代码中的 subperiods 固定窗口、bootstrap 方法、分类阈值、市场状态与排名语义需与冻结合同逐项核对，未经明确批准不改核心语义，不宣称历史等价。

本轮仅交快照审查和计划，不恢复生产脚本、不替换 loader。CORE_REPLACEMENT_IMPLEMENTATION_AUTHORIZED=false。
FORMAL_SOURCE_DEPENDENCY_CLOSURE=BLOCKED_PENDING_SNAPSHOT_REVIEW_AND_INTEGRATION；部署 loader 现状仍 BLOCKED_MISSING_SOURCE。
REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；R2_STARTED=false；MAIN_MERGED=false。

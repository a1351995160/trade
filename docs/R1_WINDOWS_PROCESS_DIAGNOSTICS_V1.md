# R1 Windows 进程隔离诊断

## 结论

ROOT_CAUSE=UNCONFIRMED；UNRESOLVED_CI_FINDINGS=OPEN。
本轮补齐可定位诊断，不宣称修复旧失败。预设三次独立冷启动 L1 均通过，NOT_REPRODUCED_IN_3_ATTEMPTS。未改变生产逻辑、进程白名单、隔离计数、拒绝规则或 exit=79；未新增 skip/xfail。

## 不可覆盖的历史证据

- cb31d9f76ab8983e69007d89fb6868a17e7ead6b：[独立 P3-C 34320730603](https://github.com/a1351995160/trade/actions/runs/34320730603)，Windows job 102366540959，L1 dry_run exit=79、RESEARCH_PROCESS_DISABLED、process_calls=1，104 passed / 1 failed。
- 同 SHA [综合 R1 34320730582](https://github.com/a1351995160/trade/actions/runs/34320730582)，Windows job 102366541236，P3-C 105 passed；该成功不关闭旧失败。
- 起始调查 HEAD 023e656ae2fc2d8efb2146a73dc8ca41647ba80d 六条工作流均 completed/success，包含 34321755799 和 34321755783。原始错误调用栈仍截断，未获得旧失败的 executable。
- 两个旧 Windows job 的完整日志和 run/job JSON 保存在忽略目录 tmp/r1-ci-diagnostics；不是重复运行历史任务。

## 工作流对照

两者使用相同 Windows image windows-2025-vs2026 / 20260824.214.3、CPython 3.13.15、cwd D:/a/trade/trade。相同 requirements-p3b 引用 requirements-p3a 的 hash 锁，成功安装包列表一致；PYTHONPATH=tests/isolation;src、CHANLUN_TEST_ISOLATION=1、保护根 D:/a/_temp/p3c-protected-reference、PYTHONDONTWRITEBYTECODE/PYTHONUTF8 相同。

P3-C 前步骤次序一致：隔离先验、前端、collect、Phase 1、Phase 2、P3-B，再执行相同四个 P3-C 模块。综合 R1 后续才运行 R1 用例。这些对照不能证明完整 OS 进程环境一致，也不能证明任何特定平台查询是根因；没有为此修改依赖或生产逻辑。

## 新诊断合同

tests/isolation/sitecustomize.py 在既有拒绝点增加 RESEARCH_PROCESS_DENIED JSON：executable、脱敏 argv、cwd、Python 版本及逐 frame 的 file/line/function。不读取 frame locals、源码行、完整环境，不调用网络/子进程；先维持 process_calls 累加，再打印 stderr，再抛原异常，退出汇总不变。

Windows subprocess 审计参数是命令行字符串，记录 REDACTED_COMMAND_LINE；其他平台参数列表保留 executable，其余值统一 REDACTED，不猜 shell 解析。实际拒绝程序和调用栈足以缩小定位；本轮没有已确认的业务参数需要公开。

P3-C launch 使用二进制管道；finish 在断言和解码前保存 stdout.bin、stderr.bin、result.json。父测试通过 CHANLUN_PROCESS_EVIDENCE_DIR 指定独立 synthetic 证据目录，按子进程生成唯一目录，不写 scenario.root。现有 P3-B launch 仍返回文本，经过 P3-C finish 时明确标记 P3B_DECODED_TEXT_UTF8，不将其冒充原始字节。P3-C 标记 RAW_BYTES。

独立 P3-C 与综合 R1 工作流指定 runner.temp/p3c-process-evidence，并在 always() 步骤上传它和原 JUnit。测试失败断言仍失败；没有吞异常、归零探针或预期化 L1 的 79。p3c_process_worker 本身无需变更。

## 定向复现与验证

本地新建独立 .venv，Python 3.13.5；按现有 hash 锁安装依赖。默认镜像 TLS 失败后改用官方 PyPI、保留证书和 hash 验证完成安装，没有借用其他环境或原目录 editable 包。此 Python patch 与旧 CI 3.13.15 不同，是复现限制。

每次独立 Python/pytest 启动，保护根设为原项目，隔离在 import 前启用；每次使用新 synthetic fixture 和单独外部证据子目录：

| 定向 L1 次数 | 结果 | 时间 | 日志 |
|---|---|---|---|
| 1 | 1 passed / exit 0 | 20.33s | tmp/r1-ci-diagnostics/cold-1.log |
| 2 | 1 passed / exit 0 | 22.30s | tmp/r1-ci-diagnostics/cold-2.log |
| 3 | 1 passed / exit 0 | 19.70s | tmp/r1-ci-diagnostics/cold-3.log |

三次主进程禁用探针均为 0，P3-C 子进程原字节位于同名 cold-N 目录；全部结果保留。未复现就停止定向重跑，不继续追求绿。后续一次完整受影响回归用于诊断改动验收，不改变此三次复现计数。

新增合成拒绝回归：故意不存在的 r1-denied-synthetic.exe 在创建前被阻断，验证 exit=79、process_calls=1、原异常、脱敏字段、_execute_child 文件/行号、失败前原字节保存与 synthetic 输入目录不变。它证明诊断可用，不证明旧 L1 根因。首轮测试断言未考虑 Windows 字符串参数表示而失败；改为接受两种合法脱敏表示，未调整隔离。无生产根因 red/green，因此没有生产修复。

最终实现提交后只在忽略目录 final-ci.json 和交付回复记录实际 HEAD 的 CI，不追加状态提交。旧失败即使后续全部通过也保持 OPEN。下一步最小动作是读取一次真实复发的 RESEARCH_PROCESS_DENIED 证据，再决定是否存在有确定 red/green 的最小兼容修正。

REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；R2_STARTED=false；MAIN_MERGED=false。源码取证另见 R1_LOCAL_SOURCE_REVIEW_V1.md，源码和诊断分开提交。


## 本轮工作流配置失败与修正

488d204 的 P3-C 34324090959 与 R1 34324091851 在创建 job 前失败，注释为 job.env 中 runner.temp 上下文不可用。依据 https://docs.github.com/en/actions/reference/workflows-and-actions/contexts 的 Context availability，将证据目录移到现有配置步骤，通过 RUNNER_TEMP/GITHUB_ENV 设置。保留失败，最终新 SHA 的实际 CI 单独记录；此问题不是旧 L1 隔离根因。

## 2026-09-10：P3B 文本管道诊断缺口与新增 OPEN 事件

固定 a64c49abad5b180334b765c21e3d580f22b6b81d 的综合 R1 PR run 34461942454，Windows job 102821569669，在原 P3C test_automatic_and_manual_proposal_share_process_lock[hold_domain] 中失败。竞争者 tick 子进程 exit=79，RESEARCH_PROCESS_DENIED 指向 C:\Windows\system32\cmd.exe，process_calls=1；进程被隔离拒绝，不能解释为已成功执行。该 job 尚未进入新增 R1 测试步骤。相同 HEAD 的 Linux R1 348 项通过、独立双平台 P3C 通过，不抵销本次失败。

原 P3B finish 未保存文本 stderr，pytest 断言/JUnit 截断了调用栈，无法据此确认调用来源。新增事件保持 OPEN_ROOT_CAUSE_UNCONFIRMED；L1/L6 状态不变。原始失败 run/job/log/JUnit 位于外部 final-novelty/ci/34461942454。没有通过重跑同 HEAD、扩大超时或白名单制造通过。

本轮仅为 P3B finish 在断言前追加独立证据落盘，使用原 CHANLUN_PROCESS_EVIDENCE_DIR，明确 stream_capture=P3B_DECODED_TEXT_UTF8，不冒充原始字节；原 30 秒 communicate、退出检查、保护探针检查和锁协议不变。已有合成进程拒绝测试同时覆盖 P3C 原字节和 P3B 文本，确认非零退出、完整脱敏调用栈与失败前留存。定向 4 passed/14 原选择器 deselected，novelty-p3b-diagnostic.log/XML。未复现不表示根因关闭；新固定 HEAD 后重新做整体认证。

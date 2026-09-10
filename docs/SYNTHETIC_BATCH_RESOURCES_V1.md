# 合成批次实际资源边界

本模块实现批准 B 的资源执行部分，不独立授予批次或 Trial 权限。总集成仍为 PARTIAL，批次协议及恢复验收继续实施。

仅支持 Windows 和 Linux，其他系统阻断。Windows 在挂起状态创建 worker，先加入 Job Object，再恢复主线程；虚拟环境启动器与解释器最多两个进程共享总提交内存上限，父进程关闭 Job 后子进程结束。Linux worker 在导入领域和数值组件前安装 RLIMIT_AS、内核墙钟计时与父进程死亡信号。父进程另用同一剩余时间限制等待，不因重启重置批次时间。

首版上层配置仅支持 concurrency=1、retries=0、model=NONE，以及 model_calls/tokens/cost_minor_units=0；批次预览必须明确数值，不能推断无限制。这里的内存指标分别是 Windows Job 提交内存与 Linux 地址空间，不将它们误称相同的 RSS 计量。

## 本地证据

- `batch-resources-suspended.log/XML`：3 passed，实际正常进程、内存分配失败与时间终止；原始流位于同名 `-process/batch-resources`。
- 首轮 `batch-resources.log/XML` 失败保留：单进程 Job 不适用 Windows venv 启动器；放入重型 research_factory 包也导致小内存测试在预期动作前失败。改为顶层轻量资源模块，并让启动器与解释器共享同一总量，没有扩大已声明内存上限。
- 本地通过不替代最终新 HEAD 的 Windows/Linux 认证。未增加测试 timeout、skip 或隔离白名单。

Windows 启动顺序依据 [Microsoft 线程与句柄文档](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/thread-handles-and-identifiers.md)及 [Toolhelp 快照 API](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot)。

真实数据 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false；旧 CP 预测禁令保持。资源约束成功不代表某个具体运行获准。

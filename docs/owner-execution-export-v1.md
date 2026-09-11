# OWNER侧限窗导出与独立验收

本轮从9a4a38ca0eff8e98d9afc84d4eebeae82580199e承接，只实现用户新批准的本机OWNER导出，不更改策略、原账户引擎、V4或预算。owner固定为USER_AUTHORIZED_LOCAL_DATA_OWNER，绑定本次批准文本真实SHA256，不推断个人身份。

## 运行路径

在研究代码工作区设置PYTHONPATH为本项目src，使用本项目.venv/Scripts/python.exe：

1. `scripts/build_owner_execution_package.py`：在E:/llmwiki/owner-execution-export-v1启动独立受限进程，读取既有OWNER_EXPORT_REQUEST_V1_1.json，冻结四个样本及容差，再进行源导出。已实际完成run-v1，不重复该命令覆盖历史。
2. `scripts/build_owner_execution_package.py --resume-sources`：针对已证明的本机代理路由和gbbq市场校验错误，复用首轮146缺路径和2700预热对账，只重做失败来源支路；已完成run-v2。样本、日期和容差不变。
3. `scripts/prove_owner_units_v1.py`：仅对已存四个限窗样本，先保存唯一绝对单位规则，再计算；本轮结果UNKNOWN，不换规则重跑。
4. `scripts/audit_owner_state_metadata_v1.py`：对5182已知原始状态文件逐字节读取头部，buffering=0，在rows前停止；核验六个精确预热all_stock路径。无混合状态数据行读取。
5. `scripts/build_owner_execution_package.py --finalize`：调用独立validator先查实际子件；全部通过才生成最终OWNER_DELIVERY_MANIFEST、再次核验、复制并原子重命名到研究owner-export。然后立即调用原run_train_account_v1.py --execute-approved；不在READY中间态停止。本轮实质子件未通过，因此未生成最终manifest或启动账户。

首次builder的每worker限制900秒/2048MiB、数值线程1；沿用原Windows资源组件，不放宽白名单。OWNER进程只对127.0.0.1和localhost设置NO_PROXY，防止本机服务被路由到外部代理；原公共TQClient、鉴权和网络实现未改。禁止get_divid_factors、互联网补行情与付费购买。

## 读取和证据规则

日线复用原_DAY_STRUCT格式，逐条先读日期键，仅允许日期读完整记录；只检查固定146路径和冻结样本/已知预热缺格，不扫其他盘符。TQ通过原TQClient，固定start/end、count=0、dividend_type=none、fill_data=false、禁用原响应缓存。任何窗外返回拒绝写出。

gbbq通过已安装pytdx.GbbqReader在OWNER进程内解码；全历史不落盘、不输出事件。格式/日期校验后只写20220722—20240731且属于本次5182成员的记录，重新打开输出验证窗口。源哈希、解析器哈希、总计数/窗口计数/丢弃计数及输出哈希单独记录。全文件含非沪深市场不是解析失败；最终按请求身份筛选。当前实现增加解析前审计，首轮旧失败日志缺口不能回写成已证实。

四个事前样本为每个市场按symbol排序前两名既有成员，全部492个日历session。价格容差0.005元；成交量按既定1编码单位容差；金额按float32/万元显示精度规则逐行判断。保留全部冲突。两份TQ说明对Volume单位不一致，本轮没有以多数匹配或放宽容差解决。

原始状态history头部声明含窗外范围且无历史发布时间字段。仅gbbq获得OWNER全历史解析例外，不能把该例外扩到其他源。生命周期日期可证明部分预热不在上市期；当前ST/停牌或抓取时间不可回填历史。部分state/security_master中的UNKNOWN/null不是可执行状态。

## 实际结果与验证

146个精确TDX源均不存在，修复连接后TQ同窗查询全部空；原2700预热缺格2673有生命周期解释，27仍UNKNOWN，完整历史状态仍不足。1968行样本日期/OHLC一致，金额10000倍关系通过，成交量未通过原容差。

gbbq解析192387条，日期内32530条、窗外丢弃159857条；5182成员范围内29990条。8248映射事件缺会计必要条款；22546其他类别未绑定安全会计语义。完整源扫描无记录的386成员只表示该源无记录，不代表正式资格的完备性。

独立validator已实际检查七个子件，发现成员、状态/时点、成交量、会计条款/覆盖等问题，不以缺manifest替代判定。97项针对性测试通过，包含完整合成包的独立验收和原子交付；测试网络/进程/保护路径探针为0。真实TQ/gbbq读取另有OWNER访问记录，不能混称全任务网络访问0。

唯一用户交付在E:/llmwiki/owner-execution-export-v1/delivery：OWNER_EXPORT_REPORT.md、OWNER_EXPORT_UNRESOLVED_ITEMS.json、证据矩阵、访问/工程记录和交付manifest。原失败、数据、消费、批准时钟、Windows OPEN保留。主/修复曝光0/0；OWNER_DELIVERY_PACKAGE_READY、TRAIN_EXECUTION_INPUT_READY、账户开始/完成及三个正式资格标志均false。

原子发布的实际路径只在独立PASS后出现；不能把OWNER侧未通过的staging当成研究最终输入。修复后重验要保留旧判定文件，当前命令拒绝覆盖。回滚使用git revert本轮提交；外部读取/失败记录不得删除。

# 2026-11-16 六项 Code Review 问题修复与验证

对应审查：[code-review-2026-11-16.md](code-review-2026-11-16.md)。基线 HEAD `c2f821d`，应用版本保留 `0.1.41`，本轮属于未发布源码修改。

**六项问题均已修复，对应回归通过。** 保留原有 WPS 超时、安装源码恢复、Tauri 关闭状态等未提交修改；未提交、推送、打包或发布。`CHANGELOG.md` 仅在 Unreleased 新增本轮业务修复条目，未改写发行记录。

## 修复结果

| 审查项 | 当前行为 | 验证证据 |
| --- | --- | --- |
| R1：集团省内 C2 跨日沿用旧值 | C2 仍写入数值以兼容上传，同时在工作簿隐藏定义名称中保留可重算公式，随暂存和复制一起保存；下一日按新容量重算。无公式来源的旧数值文件不再自动或跨日复用；同日显式选择的数值文件及内置固定值模板维持既有定义。保存到临时文件并回读 A2/B2/C2 和格式后替换；期间外部保存则停止。 | 连续两日仅选能销文件，实际走暂存、自动选模板、生成、复制回原目录，C2 从 49900 变为 39900；重新打开数值/公式两种视图均一致，保持单工作表、0.00 格式和原始文件字节。外部保存的 D1 与原 C2 公式保留，无临时文件遗留。 |
| R2：集团上传误报成功 | 导入须为明确 `T200 + 数组 data` 成功协议；异常结构或非空校验信息停止后续日报步骤。上传后以日期/类型/省份过滤的接口唯一回读日期、返回类型/省份及已上传状态和完成时间；状态延迟仅重查读取，绝不重传写请求。 | 实际 run_api_upload 控制流使用隔离 HTTP 响应，空对象、null、缺字段、错误结构、错误日期、未完成状态均不输出成功、不创建日报；每个样例只提交一次，锁和浏览器正常收尾。 |
| R3：Windows 指纹缓存漏检 | 删除基于 stat 复用内容指纹的缓存，提交前重新读取并核对内容哈希；正常备份/回滚流程保留。 | Python 回退事务在 A 回读期间改写 B 为同长内容并恢复 mtime，提交被拒绝，A 回滚，B 外部修改保留，临时文件和锁清理正常。 |
| R4：早报快照尺寸截断 | 重置只读工作表尺寸并流式扫描实际 XML 数据；宽度取实际行内容，保留第 31 列容量。支持范围外存在有效数据时明确报错，不静默截断。 | dimension 为 A1:A1、A1:XFD1048576 或缺失时，均得到完整 11 行日清分，新能源、偏差数据和容量计算与正常样例一致；输入字节不变。第 257 列含有效数据时明确拒绝。 |
| R5：历史模板日期显示序号 | 接续时根据模板日期格式/编码选择 Excel 日期序号、YYYYMMDD 或 MMDD；原生和 Python 写入共用同一编码参数。回读同时核对日期、编码和显示格式，保留原有底层写入函数返回契约。 | YYYYMMDD 模板接续后 C8=20260911、J8=20260908、格式 General；跨年 MMDD 模板新增 C15=102、J15=1230，保留旧日期和 A类/西电顺序。Python/Rust 两条路径均实际保存并重新打开验证。 |
| R6：strict 把整组跳过算通过 | 用标准库 unittest 结果适配器记录实际执行/跳过计数与原因，支持 --report JSON；全组跳过或零用例不再算通过。macOS 专属模块由模块内平台声明单列 N/A，不能以缺依赖冒充平台不适用。 | 将 Rust 服务路径指向不存在的临时文件，官方 `full --strict --module test_rust_service` 返回 1，报告 15 条全部跳过及原因。导入时整模块 skip、部分 skip、零发现用例分别有真实子进程回归。 |

实现入口：

- R1：[集团每日上传/upload_daily_report.py:1320](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/集团每日上传/upload_daily_report.py:1320)。
- R2：[集团每日上传/upload_daily_report.py:1433](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/集团每日上传/upload_daily_report.py:1433)、[集团每日上传/upload_daily_report.py:1478](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/集团每日上传/upload_daily_report.py:1478)。
- R3：[toolbox_workbook_commit.py:49](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/toolbox_workbook_commit.py:49)。
- R4：[每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:679](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:679)。
- R5：[交易系统信息更新/update_transaction_info.py:577](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/交易系统信息更新/update_transaction_info.py:577)。
- R6：[tools/run_tests.py:208](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/tools/run_tests.py:208)、[tools/unittest_worker.py:11](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/tools/unittest_worker.py:11)。

旧版生成的纯数值 C2 没有公式来源，程序不会猜测或反推。遇到明确提示时需重新选择原始公式模板；新生成且保存了公式来源的工作簿可直接继续跨日复用。

## 验证结果

全量统一入口执行 `tools/run_tests.py full --strict --timeout 120 --report ...`。首轮 158 个模块/文件中，149 个正常退出、5 个 macOS 专属模块明确标为 N/A、4 个失败；首轮未冒充全绿。随后修正并复跑：

1. `test_native_update_pipelines` 暴露内部写入函数的返回值兼容性问题，已恢复原有二元 bounds 返回；只有准备流程显式请求日期元数据，原生 ZIP 路径回归通过。
2. `test_toolbox` 中旧测试原本要求自动跨日沿用无来源的数值 C2，已按修复后的数据保护规则改为拒绝，并验证源文件不变、未发起取数；同日显式选择数值 C2 的原测试仍通过。
3. `test_date_range_input` 的键盘事件在 Win32 焦点尚未就绪时被丢弃。仅在测试中加入焦点就绪等待，保留真实 Escape/Return 事件和断言，10 项复跑通过；业务日期控件未修改。
4. 新入口识别到 `test_output_range_deviation` 原先实际上发现 0 条测试。已通过标准 unittest load_tests 将原有 4 个函数测试接入，4 条均实际执行并通过，未新增 pytest 依赖。

以最后一次相关复跑覆盖对应模块后的有效结果：

- **153 个模块/文件通过、5 个 macOS 专属模块 N/A、没有未解决失败或整组依赖跳过。** 总清单仍为 158 个。
- Python 共 1459 条用例，实际执行 1448 条，11 条条件跳过，计数没有重复累加多次复跑。
- 11 条内部跳过已逐项审阅：10 条属于 Unix/POSIX/macOS 或非 Windows 的语义；1 条为未启用 `TOOLBOX_REPORT_PDF_E2E=1` 的既有真实 PDF 回读开关。本轮不声称完成该 PDF 端到端验收。
- 包含 5 个 Node 测试文件、实际 Rust 工作进程/Windows 更新辅助程序回归及 Python/Rust Excel 写入路径；未修改 Rust 源码或替换默认发行二进制。
- 366 个当前源码 Python/pyw 文件 AST 检查通过，`git diff --check` 和 `version.py check` 通过。
- 原有 10 个待提交代码文件的哈希与审查时一致，包括 WPS、Tauri 宿主及恢复流程；原 README 修改与 WPS 审查文档保持原样。CHANGELOG 的已有草稿保留，只追加本轮三条业务更新。

## 证据与复跑

- [summary.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/summary.json)、[effective-results.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/effective-results.json)：有效模块结果、用例计数、完整跳过原因及 N/A 名单。
- [full-tests.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/full-tests.log)、[full-results.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/full-results.json)：首轮完整结果（含原始四项失败）。
- [integration-rerun.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/integration-rerun.log)：原生写入、交易模板、工具箱集成的复跑。
- [discovery-focus-rerun.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/discovery-focus-rerun.log)：日期弹窗与出力偏差测试发现的最终复跑。
- [group-final.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/group-final.json)：集团模板、连续两日生成、状态确认、外部保存保护的最终回归。
- [missing-service-guard.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/missing-service-guard.json)、[missing-service-guard.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/fixes-2026-11-16/missing-service-guard.log)：缺少 Rust 服务时 strict 正确失败的实测。

可使用统一入口复跑六项直接相关的回归：

```powershell
.venv/Scripts/python.exe tools/run_tests.py --strict --module test_group_upload_safety --module test_group_upload_template --module test_group_upload_shutdown --module test_workbook_commit --module test_daily_clearing_fill --module test_transaction_info_update --module test_test_runner
```

未执行生产上传、真实账号登录、在线写入、改写用户业务文件、完整安装包构建或实际安装升级。所有工作簿和 HTTP 样例来自临时隔离数据；没有为了复用登录而更改工具之间的登录态、锁或浏览器 profile。

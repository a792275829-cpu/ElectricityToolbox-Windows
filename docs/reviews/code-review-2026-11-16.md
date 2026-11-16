# 最新源码逐模块 Code Review · 2026-11-16

基线：`ElectricityToolbox-Windows`，分支 `main`，HEAD `c2f821d20a59f34b4183f4785d321287263064cf`，版本 `0.1.41`，**包含本地未提交改动**。本轮审查本机最新工作区，未拉取或核对远端。原 `ElectricityToolbox-0.1.30-source` 子目录已非有效仓库，不能作为本轮版本基线。

开始时有 12 个已跟踪文件的修改及 3 份未跟踪的 WPS 审查记录，原样保留。主要涉及 WPS 分层超时/仅填空白、安装源恢复、Tauri 关闭状态、对应测试和说明。测试运行期间未修复业务源码、未暂存、未提交、未发布；本轮新增文档只有本报告，原始证据保存在仓库外的 `review-2026-11-16/`。

**结论：确认 6 项问题，P1 一项、P2 五项；其中上轮 4 项仍存在，本轮新增确认 2 项。** 六项均有隔离复现，不以猜测的业务口径或纯代码风格作为发现。已有测试通过并不能覆盖这些触发条件。

## 发现

### R1 · P1 · 集团上传跨日自动选模板会沿用旧日 C2（上轮仍存在）

位置：[集团每日上传/upload_daily_report.py:1345](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/集团每日上传/upload_daily_report.py:1345)；调用链：`prepare_upload_files → find_province_template → update_province_excel_for_upload`，桌面暂存路径最终也会把生成的省内文件复制回原目录。

第一日计算会把 C2 公式变成数值；第二日只选能销文件时，自动模板选择优先采用距离日期最近的上一日产物。代码对数值 C2 仅记录“保留现有结果”，忽略本次已抓取的容量值，却把 A2/B2 更新为新日数据，随后允许上传。

复现回读：原公式 `=100000-20000-100`；第一日容量 50000，C2=49900。第二日自动选中第一日产物，容量改为 60000，C2 仍为 49900，按公式应为 39900。两个输出均保存后重新打开回读，网络取数使用模拟值。

建议：自动模板复用应保留原始公式或可重算的参数来源；跨日自动选到数值 C2 时应阻断或恢复可靠公式，不能静默保留旧值。增加“连续两日，第二日只选能销文件”的回归。证据：[previous-findings-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/previous-findings-repro.json) 的 `province_reuse`。

### R2 · P2 · 集团上传把异常响应判作导入成功（上轮仍存在）

位置：[集团每日上传/upload_daily_report.py:1472](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/集团每日上传/upload_daily_report.py:1472)；`parse_api_payload` 允许缺 retCode 的对象。

HTTP 200 返回空对象或缺失 data 时，`payload.get("data") or []` 将其归为空数组，随即输出“导入成功”并继续后续日报步骤。当前状态查询发生在上传之前，用于决定跳过，无法证明这次导入成功。Node 私有上传的协议修复没有覆盖这个独立的 Python 消费者。

复现：执行实际 `run_api_upload`，模拟浏览器、网络和载荷，不替换生产 JSON 解析与成功判定代码。HTTP 200 / `{}` 导致正常返回且记录“导入成功”，无真实上传。

建议：明确校验集团导入成功协议与 data 数组结构；未知结果报告“可能已提交、待核对”，不要自动重放。上传后按日期/类型/省份唯一回读完成状态，再继续创建日报。证据：[previous-findings-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/previous-findings-repro.json) 的 `group_response`。

### R3 · P2 · Python 事务指纹缓存在 Windows 下漏检外部改写（上轮仍存在）

位置：[toolbox_workbook_commit.py:64](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/toolbox_workbook_commit.py:64)；最终替换前复用缓存的位置：[toolbox_workbook_commit.py:152](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/toolbox_workbook_commit.py:152)。

缓存失效键依赖 size/mtime/ctime/inode/dev。在当前 Windows Python 中，st_ctime 是创建时间；同长原地改写并恢复 mtime 后，这些字段仍可能完全不变。最终提交前于是复用旧内容哈希，覆盖已变化的文件。注释中“ctime 会变化”的假设在支持的平台上不成立。

复现：`original → external` 同长改写并恢复 mtime，带缓存校验接受，不带缓存校验抛错。进一步运行真实 Python 回退事务，在 A 文件回读时改写 B 并恢复其 mtime，事务返回成功，B 最终变成 `prepared`，外部改动丢失。

边界：复现明确选择受支持的 Python 后端；未证明 Rust 事务具有同样问题。条件需要同长修改且时间戳恢复，故为 P2。建议最终 `os.replace` 前重新计算内容指纹，不以路径 stat 代替最终内容校验。证据：[previous-findings-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/previous-findings-repro.json) 的 `fingerprint_cache` / `transaction_edit`。

### R4 · P2 · 早报只读快照可能被过期尺寸截断，静默漏掉新能源区（上轮仍存在）

位置：[每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:700](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:700)。

快照按只读工作表声明的 dimension 遍历，未重置或校验尺寸。若第三方导出的 XLSX 实际单元格超出旧 dimension，后续行不会进入快照。新能源区消失时，下游只提示保留模板原值，仍正常返回，可能把旧数据带入新报告。

复现：采用当前 `test_daily_clearing_fill` 的样例，仅将快报 sheet dimension 改为 `A1:AE27`，保留所有真实单元格。正常样例/完整读取均得到 11 行日清分数据；当前快照仅得到 7 行，缺“分公司新能源、海风、鮀莲、归湖”，未报异常。

建议：核实或重置只读尺寸，并以实际数据或明确业务需求确定范围；不要在重置后因 max_column=None 又默认 30 列，漏掉第 31 列容量等字段。必需业务区缺失时应阻断发布或明确保留待核对状态。证据：[previous-findings-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/previous-findings-repro.json) 的 `stale_dimensions`。

### R5 · P2 · 历史交易模板追加日期显示成 Excel 序号（本轮新增）

位置：[交易系统信息更新/update_transaction_info.py:592](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/交易系统信息更新/update_transaction_info.py:592)；Rust 路径的共同参数准备：[交易系统信息更新/update_transaction_info.py:614](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/交易系统信息更新/update_transaction_info.py:614)。

新版本已支持 General 格式的 `YYYYMMDD`/`MMDD` 历史日期，但追加行仍一律写 `to_excel(...)`，同时复制旧日期格的 General 格式。于是旧行显示 `20260910`，新增行却显示 `46276`。回读仅把序号重新解成日期，未校验其显示格式，因此把这个结果当成成功。

复现：当前交易测试夹具的 C1/J1 改为 20260910/20260907、General；接续 2026-09-11 后，Python 和 Rust 两条路径的 C8 都是 `46276`、General、`is_date=False`；J8 为 `46273`、General。保存后真实回读确认。内部日期识别仍正确，本问题是新增日期的展示和模板契约失配，不是将 9 月 11 日解析成别的日期。

建议：追加时保留已识别的数字日期编码，或为新日期明确设置可读的日期格式，并同时校验底层日期与显示格式。增加 General + YYYYMMDD/MMDD 的接续回归，不能仅调用 `_as_date` 断言。证据：[legacy-date-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/legacy-date-repro.json)，脚本 [repro_legacy_dates.py](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/repro_legacy_dates.py)。

### R6 · P2 · `--strict` 把整组依赖缺失跳过记为通过，无法可靠阻断发行（本轮新增）

位置：[tools/run_tests.py:205](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/ElectricityToolbox-Windows/tools/run_tests.py:205)，严格模式判定在同文件 274 行；Windows CI 使用 `tools/run_tests.py full --strict`。

入口把退出码 0 无条件转换成 OK，并丢弃成功运行的 unittest 输出。但 unittest 的整类/整模块 skip 也以 0 退出。`--strict` 只检查 Outcome 的 SKIP 计数，因此无法识别缺少 Rust 服务等关键依赖导致的全部跳过，也不展示其原因。

复现：只对子进程设置 `TOOLBOX_SERVICE_BINARY` 为临时目录中不存在的文件，执行官方入口 `full --strict --module test_rust_service`。实际测试类被 skipUnless 全部跳过，入口却输出“通过 1 / 1，跳过 0”，退出码 0。未删除或更名实际二进制，也未修改测试文件。

建议：保留 unittest 的执行/跳过明细，使用结构化结果或独立结果记录判定“依赖不足导致整个模块未执行”；strict 对 Windows 必需模块应失败，并对允许的平台 skip 明确列出原因。不能仅用子进程退出码代表已执行验证。证据：[strict-skip-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/strict-skip-repro.json)，脚本 [repro_strict_skip.py](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/repro_strict_skip.py)。

## 逐模块覆盖记录

“未新增确认问题”仅说明本轮代码检查、调用链及已执行样例没有定位到新的确定缺陷，不表示所有输入和外部服务均正确。

| 模块 | 审查重点 | 当前结论 |
| --- | --- | --- |
| 导出上网电量 | 24 点校验、缺机组空白、清远专用接口与 D+2、日期范围 | 未新增确认问题；真实接口未调用 |
| 风力预测截图 | 96→24 点、连续 7 天、字体缓存、缺数据处理 | 未新增确认问题；所属测试模块正常退出 |
| 市场表更新 | 日期唯一匹配、D/D-2/D-6、日期索引/延展、格式/覆盖 | 模块内未新增确认问题；共享 Python 提交见 R3 |
| 现货价格分析表更新 | 重复完整记录拒绝、缺 24 点跳过、日期续接、XML 保留/回读 | 模块内未新增确认问题；共享 Python 提交见 R3 |
| 交易系统信息更新 | 历史日期与缓存公式、A类/西电行序、D 与 D-3、追加/回读 | R5；共享 Python 提交见 R3 |
| 功率调整收益统计 | 六列/海风五列、公式/覆盖、24 小时块、跨工作簿事务 | 模块内未新增确认问题；共享 Python 提交见 R3 |
| 广东电价预测 | 增量合并、96 点粒度、历史/预测日供需、时间切分与发布时间 | 未新增确认问题；未训练真实业务模型或评价收益 |
| 电力交易/出力受限分析 | 批次命名冲突、原生/兼容计算、汇总与收益回读 | 未新增确认问题；原浮点精确比较用例现正常退出 |
| 500kV 断面分析 | 原输入保护、批次输出、拓扑与去重、编辑器/节点曲线 | 未新增确认问题；12 个所属测试模块正常退出 |
| 广东运行日报解析上传 | 解析与 OCR 缓存、Excel 校验、上传等待/状态回读 | 未新增确认问题；未执行真实上传 |
| 南方分公司电力市场日报 | 手选报告日、D/D-1/D-2 文件校验、只读日期路径、双 PDF 提交 | 未新增确认问题；页面与解析回归正常退出 |
| 出清结果汇总上传 | 归类防覆盖、交易汇总、海风 96 点均值与全省第二工作表、日期一致性 | 未新增确认问题；节点价格实际样例/回读回归正常退出 |
| 私有数据批量上传 | 拆批、参数、严格响应协议、独立锁、凭据与部分失败汇报 | 未新增确认问题；5 个 Node 测试文件正常退出 |
| 生产经营早报 | A41 原文采用、实时对比口径、均价无效值、快照/日清分/模板 | R4；新口径对应回归正常退出 |
| 集团每日上传 | 自动模板、C2 计算、暂存/复制回原目录、导入响应与状态 | R1、R2 |
| WPS/KDocs 写入 | 未提交的 20/180/30 秒超时、迟到结果、仅填空白、公式保护、保存回读 | 本轮未新增确认问题；含隔离 Chromium 与服务链路回归，未连接生产表格 |
| 共享 Excel/登录/任务服务 | 指纹/回滚、各工具状态隔离、只读重登录、错误传输、退出归属 | R3；其他所选回归正常退出 |
| Windows 启动/打包/更新/测试基础设施 | 本地源码备份恢复、UTF-8 启动、关闭状态、发行来源与载荷、增量回滚、strict | R6；新未提交启动修复的 Rust/桌面回归正常退出 |

## 验证结果与边界

所有 Python/Node 测试均经仓库统一入口 `tools/run_tests.py` 执行，入口为各模块提供独立数据根与临时目录。没有手动改写真实配置或登录态。fast 与补充运行的模块集合去重后恰好等于当前 full 清单：

| 检查 | 结果 |
| --- | --- |
| fast 档 | 97/97 个模块退出成功，364.4 秒 |
| full 清单剩余模块，经 `--module` 指定 | 60/60 个模块/文件退出成功，294.1 秒 |
| 合计 | 152 个 Python 测试模块 + 5 个 Node 测试文件；未发现 FAIL/TIMEOUT |
| Rust 后端及任务服务 | 当前源码 cargo test --offline：20 项通过 |
| Tauri 宿主（含未提交的关闭状态修复） | 当前源码 cargo test --offline：3 项通过 |
| 前端类型 | tsc --noEmit 通过 |
| Python/pyw 语法 | 364 个已跟踪文件 AST 检查通过 |
| JavaScript 语法 | 17 个 js/mjs 通过 node --check |
| PowerShell 语法 | 7 个 ps1 通过 Parser，包括中文路径脚本 |
| 问题样例 | 上轮 4 项再次复现；新日期问题 Python/Rust 都复现；strict 使用真实统一入口复现 |

**上述“退出成功”不能解读为所有用例均未跳过或业务正确。** R6 已证明统一入口会隐藏 unittest 内部 skip，本轮不虚报测试用例总数或“零内部跳过”。本轮并非发行验收，没有据此宣布可以发布。旧报告中的 WPS 本机登录态污染、字体模拟平台及浮点比较失败，在当前隔离入口下对应模块均已正常退出；没有直接沿用旧报告的失败数。

未验证：真实账号登录/验证码、生产上传、在线表格写入、真实业务工作簿改写、完整 NSIS 打包及真实安装/升级、macOS、长时间并发和大规模压力。WPS 超时修复仍受浏览器整体冻结/系统级阻塞的限制；本轮只验证其现有隔离接口模型，未推断用户现场卡住的具体原因。

保留工作区原有 12 个已跟踪文件的修改及 3 份未跟踪审查记录。哈希核对中，本轮采集的已跟踪源码未发生变化；最终只新增本报告，不自动修复、提交或推送。

## 证据入口

- [summary.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/summary.json)：版本、计数和源码变化检查。
- [fast-tests.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/fast-tests.log)、[additional-tests.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/additional-tests.log)、[test-inventory.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/test-inventory.json)：统一入口输出及全清单。
- [rust-tests.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/rust-tests.log)、[tauri-tests.log](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/tauri-tests.log)：当前 Rust 源码单测输出。
- [syntax.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/syntax.json)、[powershell.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/powershell.json)：语法检查结果。
- [previous-findings-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/previous-findings-repro.json)、[recheck_previous.py](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/recheck_previous.py)：上轮四项的当前源码复现。
- [legacy-date-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/legacy-date-repro.json)、[repro_legacy_dates.py](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/repro_legacy_dates.py)：历史模板日期值和格式回读。
- [strict-skip-repro.json](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/strict-skip-repro.json)、[repro_strict_skip.py](C:/Users/AAA建材张总/Downloads/ElectricityToolbox-0.1.30-source/review-2026-11-16/repro_strict_skip.py)：strict 的整组跳过误判。

建议优先修 R1 跨日错数与 R2 上传成功误报，再修 R3/R4 的数据保护问题；R5 应补齐日期显示契约，R6 应在下次发行前完善必需模块的实际执行判定。

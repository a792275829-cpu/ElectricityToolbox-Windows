# Code Review：Windows 0.1.30 → 对齐上游 v0.1.31（2026-11-06）

审查对象为源码快照 `ElectricityToolbox-0.1.30-source`，重点是本次移植的改动集（源码包解压后 16:18–16:53 修改的 95 个文件，含新增第 16 个工具「私有数据批量上传」），并结合 `AGENTS.md` 的契约核对全仓调用链。目录内无 `.git`，无法做 commit diff，因此采用「改动文件逐个人工复核 + 全仓静态扫描 + 针对性测试」的方式。

**结论（审查当时）：不建议按当前状态发版。** 新增工具存在 3 个独立的可触发运行时缺陷，其中 2 个导致该工具在 Tk 界面或桌面服务端完全不可用；另有一处上游声称已修复的日期误选 bug 在 GUI 路径上仍然存在。业务计算、Rust 分支、登录态隔离等主体移植质量良好。

> **修复状态（2026-11-06）**：P0 三项、P1 四项已全部修复，另修 P2 的批次进度汇报、命令行凭据脱敏与平台文案，并新增 15 项不依赖 tkinter 的回归测试。当时列出的回归全部通过，逐项明细见文末「修复记录」；后续复核发现 P1-3 的 `--files` 逗号路径只修了 Python 侧，Node 端仍会拆分，已补齐并新增 4 项 Node 侧回归。P2 的 `power_source` UI 缺口、登录实现收敛及全部 P3 项未改动，理由见对应章节。

---

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 全仓 314 个 `.py` 的 `ast.parse` | 0 语法错误 |
| `run_process` 调用参数静态比对 | 22 处调用，2 处传入签名外关键字（见 P0-1） |
| 合并冲突标记 / `toolbox_shared` 残留 | 0 处（仅构建清单文档自述中出现该词） |
| 早报脚本测试（75 项） | 全部通过 |
| 南方分公司日报测试（51 项） | 全部通过 |
| 500kV 断面分析测试（64 项） | 通过，16 项需浏览器跳过；须 `cwd=500kV断面分析` 且 `PYTHONPATH` 含 `电力工具箱`，否则 3–9 项因模块路径报 ImportError |
| `tests/test_private_batch_upload.py`、`tests/test_date_inputs.py` | 本机 Python 3.13 无 tkinter，模块导入即 `ImportError`，**未验证** |

未验证边界：无 Git 基线、无 Rust 工具链／NSIS、无带 tkinter 的 Python，故 GUI 页面实际创建、真实网站登录与上传、WPS 写入、Excel 真实写入回读、安装包构建与实机安装均未执行。

---

## P0 阻断项（发版前必须修）

### P0-1 第 16 工具点击「开始上传」或「登录」即 TypeError

`电力工具箱/toolbox/private_batch_upload_page.py:190-199` 两处调用传入 `action="run"` / `action="login"`：

```python
self.run_process(self.build_upload_command(), cwd=..., status="正在上传私有数据...",
                 cancellable=False, action="run")          # :192
self.run_process(commands.login(...), cwd=..., status="正在打开登录窗口...",
                 action="login")                           # :198
```

而基类 `电力工具箱/toolbox/widgets.py:276-284` 签名为 `run_process(self, command, *, cwd, status, on_success=None, cancellable=True)`，无 `action`、无 `**kwargs`；`PrivateBatchUploadPage` 未重写该方法。AST 全仓比对确认：22 处 `.run_process(...)` 调用中只有这两处传 `action`，其余 15 个页面都不传。

影响：`confirm_upload()` / `start_login()` 一进入即抛 `TypeError`，`set_busy(True)` 都不会执行，按钮禁用、进度、日志、子进程、退出码全部失效，用户可反复点击反复报错。`action` 是服务层概念（`ui_tasks.py:159-161`），属上游移植残留。

修复：删除两处 `action=`（最小改动，与其余页面一致），让 `ui_tasks.py:160` 继续按命令中的 `--login` 推断；并补一条 mock `run_process` 后实际调用 `confirm_upload()` / `start_login()` 的回归测试。若确需保留显式 action，则应同时扩展 `widgets.run_process`、`prepare_page_worker` 和 `ui_tasks`，牵动基类，风险更高。

### P0-2 桌面／服务端登录动作 KeyError

`电力工具箱/toolbox/workflows.py:77-81` 的 login 分支用固定字典取脚本：

```python
if action == "login" and tool_id != "wps-writer":
    script = {"online-energy": ..., "wind-forecast": ..., "market-table": ...,
              "group-upload": ..., "spot-price": ..., "transaction-info": ...}[tool_id]
```

`service_catalog.py:68` 为 `private-batch-upload` 声明了 `("login", "登录")`，但字典恰好缺这一项；已核对 `service_catalog.py` 中所有声明 login 的工具，其余 6 个都在字典内、`wps-writer` 被显式排除。链路为 `service.py:74 validate()` → `workflows.py:44 plan()`；`ui_tasks.py:159-171` 在 remote 模式下同样把 login 送到 `execute_remote`。

影响：桌面端点「登录」抛 `KeyError: 'private-batch-upload'`。即便简单补 key，现有分支走 Python 脚本 `--login`，与 `commands.py:14-20` 为它单独构造的 Node `--login --headed --batch` 命令不一致。

修复：在 login 分支入口单独返回 `[(commands.login(paths, tool_id), paths.private_uploader_dir)]`（覆盖 `summary-upload` 与 `private-batch-upload`），与 `commands.py`、`runtime.py:168-169` 保持一致。

### P0-3 `loginFailure` 未定义，登录态失效时丢失诊断

`电力工具脚本/private-data-uploader-tool/scripts/upload-private-data.mjs:558`：

```js
if (isLoginPage(page)) {
  throw loginFailure('session', '进入私有数据导入页面时登录态失效，请先重新登录。');
}
```

全仓搜索 `loginFailure` 仅此 1 处，无定义、无导入。`switchTenantForImport` 被 `runBatch:707` 与 `runStatus:671` 调用；一旦切单位后被重定向到登录页（会话过期、首次运行），抛 `ReferenceError`，被 `main:802-806` 捕获后只打印该字符串并 `exitCode=1`，期望的「请先重新登录」提示完全丢失。且该函数不像目录模式 `switchTenant:402-457` 有 `ensureLogin` + 3 次重试。

修复：改为 `throw new Error('进入私有数据导入页面时登录态失效，请先重新登录。')`，并给 `switchTenantForImport` 补与 `switchTenant` 相同的重试逻辑。

---

## P1 高优先级

### P1-1「11.1 误选 11.10」只修了生成脚本，GUI 自动填充仍是子串匹配

已修好的部分：`每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:567-581` 用 `(?<!\d)(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)` + 三元组精确比较 + `finditer`，无新前缀问题。

未修好的三条路径（同一份重复实现）：

- `电力工具箱/toolbox/file_rules.py:48-58` `date_token = f"{year}.{month}.{day}"` … `date_token in path.name`
- `每日生产经营情况汇报自动生成工具/scripts/report_gui.py:166-177` 同样实现
- 调用方：`pages.py:386/404`（`ReportPage.auto_fill`）、`service.py:161`（headless `auto_match`）

本机复现（目标日 2026-11-01，token `2026.11.1`）：

```
2026.11.1出清情况.xlsx   -> 命中
2026.11.01出清情况.xlsx  -> 未命中   （补零文件名找不到）
2026.11.10出清情况.xlsx  -> 命中     （误选，按 max(mtime) 取胜）
```

用户从 GUI 自动填充后，显式路径会绕过已修好的 `find_clearing_workbook`，日期错配仍会发生。同一 `file_rules.py:29-46` 的 `find_clearing_files_for_dates` 已用 `clearing_file_date(path) == wanted` 精确比较，证明修复未覆盖此路径。

修复：三处统一复用精确日期解析（或 `clearing_file_date(path) == target_day`），删除 `report_gui.py` 的重复实现；补 11.1／11.10／补零文件名测试，覆盖 pages、service、GUI 三条链。

### P1-2 早报 A39 三厂策略对全角括号工作表名不兼容，静默失效

`generate_red_marked_report.py:264`：

```python
(name for name in workbook.sheetnames if name.replace(" ", "") == "快报(日前)")   # 半角
```

同文件 `:609` 用 `if "快报" in name and "日前" in name`（宽松）、日报 `company_clearing_xlsx.py:626` 用 `"快报  (日前)"`，三处口径不一。仓库内 `电力工具箱/tests/test_six_performance.py:54` 与 CHANGELOG 均使用全角「快报（日前）」。本次实跑早报 75 项测试时也打印了「三厂策略：wb.xlsx 中没有「快报（日前）」工作表，保留模板原文」。

影响：真实模板若为全角表名，`load_plant_strategies` 返回 `{}`，`generate()` 的 `try/except` 吞掉并 log，本次「按厂拆块取 A39」改动静默失效。`test_plant_strategies.py:67,88` 只构造半角，掩盖该场景。

修复：去空格 + 全／半角括号归一化，或按「快报」「日前」标签包含定位；补全角表名断言。

### P1-3 `--files` 用逗号拼接，路径含逗号必被截断

`电力工具箱/toolbox/commands.py:31-32`：

```python
"--files", ",".join(str(path) for path in files)
```

`upload-private-data.mjs:74-76` 按 `split(',')` 还原。Windows 合法路径可含逗号（如 `复盘,最终/日前.xlsx`），会被拆成两段 → `collectBatchFiles:534` 报「文件不存在」。README:23 声称 `--files` 可重复传多次，Node 端 `args.files.push(...)` 也支持，但 Python 侧从未使用；桌面服务 `workflows.py:188-190` 复用同一函数，同样受影响。

修复：改为 `for f in files: command.extend(("--files", str(f)))`；Node 保留 `split(',')` 兼容手写命令行；补含逗号路径测试。

### P1-4 测试缺口让 P0-1 被「无 tkinter」的归因掩盖

`电力工具箱/tests/test_private_batch_upload.py` 的页面测试（`:200-275`）只测 `apply_files` / `batch_count` / `build_upload_command` / monthly 开关，**从不调用** `confirm_upload()`、`start_login()`、`run_process()`；`setUp` 在无 tkinter 环境整体 `skipTest`。CHANGELOG:13 把「新增差异全部来自本机缺少 tkinter 的新增用例」当作可接受的解释，但真实后果是新增页面完全没被执行路径验证。

同类缺口：Node 拆批测试未覆盖恰好 12 个文件／恰好 10MB／空批次／混合边界；未验证 `--login --batch` 实际落盘的 auth_state／lock／profile 路径；批次失败、逗号路径、脱敏、并发抢锁均无测试；500kV 网页阈值重算（`test_build_500kv_map_html.py:131-142,304-341`）全是 `assertIn(字符串)`，未执行 JS 验证四处重算。

修复：页面测试改用 mock/fake tkinter 或注入式 runner，确保 `confirm_upload` 至少被真正调用一次；关键边界补真断言；阈值改动用 Node + jsdom/Playwright 做行为断言。

---

## P2 中优先级

1. **批次失败无重试、部分成功不汇报**（`upload-private-data.mjs:709-748`）：`postBatch` 抛错直接冒泡，`succeeded` 局部变量在异常路径从不输出，前 N-1 批已入库但用户只看到异常栈，极易全量重传造成重复数据；`result.items.length===0` 时（`:716-720`）把空响应当整批成功。建议按批 catch 并在异常路径也打印「已完成 N/M 批、成功 X 个文件」，对 5xx/超时做有限重试（需先确认接口幂等）。
2. **CLI 直跑路径未脱敏**（`upload-private-data.mjs:369-379, 576-590, 802-806`）：`username` 与加密 `password` 拼进登录 URL query，Playwright 失败栈常含完整 URL；`:379`/`:589` 直接打印响应体；`main:804` 只截断 `\nCall log:`。GUI／服务路径经 `toolbox_log_console.py:196`、`service.py:97`、`tasks/errors.py:77` 的 `redact()` 已正确脱敏，问题仅在 README 鼓励的命令行直跑。建议 Node 侧实现等价 `redact()` 并覆盖所有输出。
3. **macOS 文案残留在 Windows 运行路径**：`diagnostics.py:91,138,210` 提示 `setup_macos.command` 与「macOS 隐私与安全性」（`:210` 正是私有上传排障）；`upload_service.py:46` 无条件打印「正在通过 Finder 暂存…macOS 文件访问权限」，且被 `pages.py:4`、`workflows.py:245` 的集团上传路径引用；`电力工具脚本/private-data-uploader-tool/README.md:5`、`ui/dashboard.py:64`（⌘F）。多为 0.1.30 基线既有，但与本次「对齐 Windows」目标及构建清单当时的「未引入 macOS 专用内容」表述冲突（该表述已修订为「本轮未新增」），建议按 `os.name == "nt"` 分支。
4. **`power_source` 无 UI 入口**：页面 `private_batch_upload_page.py:156-160`、`service_catalog.py:60-68`、`ui_tasks.py:144`、`workflows.py:190` 恒取默认「不适用」；而 `batch-plan.mjs:96-109` 明确新能源原始功率预测数据必须带来源才能定位。`commands.private_batch` 支持该参数，测试也断言 UI 不含该字段，属有意裁剪——需业务确认是否支持该数据类型，否则在页面/README 明确标注限制。
5. **登录实现两套、URL 不一致**：`toolbox_auth_loader.standalone_login` 仅 `toolbox_login_tools.py:23` 一处调用，而 `上网电量抓取/export_online_energy.py:405-419` 的 `interactive_login` 是第二份实现，且前者打开 `LOGIN_URL`、后者打开 `FIRE_APP_URL`。与构建清单「未引入重复模块层次」表述不符，建议收敛。

---

## P3 低优先级

- `toolbox_trade_batch.py:24-42` `iter_batch_reports` 零调用方；`workflows.py:233-243` 的 trade-analysis 自建输出循环，未用 `plan_batch_outputs`，同名 stem 不同目录会静默覆盖（section-analysis 已接入并有真断言测试）。
- `广东电价预测/train_d1_forecast.py:28-63` `ensure_master_workbook` 全仓无调用方，「更新模式缺总表」修复实际接在 `workflows.py:249-252` / `pages.py:996`，该函数为冗余死代码。
- `catalog.py:35-57` 与 `pages.py:1167-1185` 两套页面工厂重复注册，名称集合一致但顺序不同（「市场表更新」第 3 vs 第 11）。
- `每日生产经营情况汇报自动生成工具/scripts/generate_red_marked_report.py:1963-1975` 三份工作簿 A2 运行日不符只 log 警告不阻断（含 P1-1 的误选），仍会产出日期错配报告；`read_workbook_run_date:373-383` 未处理 `datetime.date` 与 Excel 序列号。
- `500kV断面分析/src/analyze_500kv_section_gaps.py:374`、`deduplicate_500kv_prices.py:246` 保存后无写即回读（已有 `price_xlsx_backend.py:89-103` 的原子替换与响应确认缓解，但按 AGENTS.md 仍缺回读）。
- `电力工具箱/toolbox/spot_price_page.py:57` 「覆盖已有非空价格」默认 `True`，脚本端重复日期已显式报错，建议默认改 `False` 或加强风险提示。
- D-1 日期识别 `infer_day_ahead_comparison_date`（`company_clearing_xlsx.py:616-636`）无真实工作簿单测，仅被 `test_date_inputs.py:81` mock。
- 锁与资源细节（`upload-private-data.mjs`）：`:664/:698` 给 `acquireAuthLock(config, timeoutMs)` 多传了 `log` 实参；`:180-195/:524` stale-lock 无条件 `unlink` 存在理论竞态，`releaseLock` 未校验 pid；`:298-316` `launchContext` 中途抛错时 `finally` 拿不到 context，可能残留 Chromium 与 profile 锁；`:459-473` debug 截图与页面文本落盘且无法经 `redact()` 脱敏。
- `private_batch_upload_page.py:131,148,168` 的 `path.stat()` 未处理文件被移走／删除；`apply_files` 未先过滤超 10MB 文件却参与分批估算；同名不同目录文件在确认框只显示 `path.name`。

---

## 已检查并确认无问题

- **登录态隔离（AGENTS.md 核心契约）**：批量模式硬编码 `.batch-upload/auth_state.json`、`.batch-upload/auth_state.json.lock`、`.batch-upload/.browser-profile`，与目录模式及 `.browser-profile-uploader` 完全分离，并发不抢锁、不覆盖登录态；共享的只有账号配置本身，不属于要求隔离的登录态／锁／profile。
- **移植完整性**：无冲突标记、无 `toolbox_shared` 残留、无上游包内相对导入误入；根目录 `toolbox_*` 适配层导出的符号与全部调用方签名匹配；第 16 工具在 catalog / service_catalog / adapters / commands / workflows / ui_tasks / app / pages / service / runtime 的注册链齐全（除 P0-2）。
- **Windows 契约**：Rust `_native_invoke` 分支与 `backend_mode()` 保留可达；`toolbox_native_excel.py:31`、`runtime.py:359` 的 Windows 平台字段完整；`build_500kv_map_html.py:1708` 的 `.052 * .8` 箭头降速 20% 系数未被上游覆盖。
- **安全与进程**：`tasks/process.py:58-65` 用 list 形式 `Popen` 无 shell 注入，`utf-8` + `errors=replace` 处理中文／空格路径，`stderr=STDOUT` 未被吞，退出码与子进程组清理（`terminate:173-209`）正确。
- **业务口径**：D／D-1／D-2 全链路一致，跨月／跨年无 off-by-one；500kV 阈值前后端规则一致（`<= threshold` 跳过、反向变动判定、`SECTION_MIN_INTERVALS=4`），220kV 排除按最后电压标签 + 500kV 优先；日报与早报只读工作簿、只产出报告文件，不覆盖用户原始工作簿；`test_guangdong_price.py:265-279` 有值／格式回读与输入字节不变的正面证据。
- **拆批实现**：`batch-plan.mjs:129-148` 的 12 文件／10MB 边界（严格大于才拒绝／拆批、空批返回 `[]`）正确，Python `batch_count` 与 Node `planBatches` 逐 case 一致，页面／`workflows.py:185` 的阈值三处统一。测试覆盖不足另见 P1-4。

---

## 建议修复顺序

1. P0-1 → P0-2 → P0-3（三个独立缺陷，任一未修第 16 工具都不可发版）
2. P1-1（业务口径精确契约，且是上游声称已修复的 bug）→ P1-2 → P1-3
3. P1-4 的页面执行路径回归测试，须与上述修复同批补入，避免再次被整类 skip 掩盖
4. P2 按发版范围取舍；P3 可排入后续维护

修复后至少应补跑：早报 75 项、日报 51 项、500kV 64 项，以及一台带 tkinter 的机器上的第 16 个页面上传／登录 smoke test 与 `--login --batch` 落盘路径断言。

---

## 修复记录（2026-11-06）

### P0（3 项，全部修复）

| 问题 | 修复 |
| --- | --- |
| P0-1 `action=` 错配 | `电力工具箱/toolbox/private_batch_upload_page.py` 删除两处 `action=`，与其余 15 个页面一致 |
| P0-2 login 分支漏注册 | `电力工具箱/toolbox/workflows.py` 在 login 分支入口为 `summary-upload`／`private-batch-upload` 直接返回 `commands.login(...)` 与上传器工作目录 |
| P0-3 `loginFailure` 未定义 | `upload-private-data.mjs` 的 `switchTenantForImport` 改为抛出 `new Error`，并比照 `switchTenant` 补 3 次「重新登录 + 重试」循环 |

### P1（4 项，全部修复）

| 问题 | 修复 |
| --- | --- |
| P1-1 日期误选 | `toolbox_dates.py` 新增共享 `clearing_file_date`；`电力工具箱/toolbox/file_rules.py`、`每日生产经营情况汇报自动生成工具/scripts/report_gui.py` 的 `find_workbook_for_day` 改为按解析出的完整日期精确比较。`file_rules.py` 保留同名 re-export，既有 `from .file_rules import clearing_file_date` 调用方不受影响 |
| P1-2 全角表名 | `generate_red_marked_report.py` 先做空格／全半角括号归一化精确匹配，未命中再按「快报」「日前」标签定位 |
| P1-3 逗号路径 | `commands.private_batch` 改为逐个文件重复 `--files`，Node 侧保留 `split(',')` 兼容手写命令行 |
| P1-4 测试缺口 | 新增 `电力工具箱/tests/test_private_batch_regression.py`（15 项，不 import tkinter）：AST 校验全仓 `run_process` 关键字、login 命令一致性、逗号路径、11.1／11.10／补零、拆批四项边界、Node 语法与脱敏；另在 `test_private_batch_upload.py` 的页面类补两条真正调用 `confirm_upload()`／`start_login()` 的用例；`test_plant_strategies.py` 补全角表名用例 |

顺带修复：两个测试文件的 `run_node` 在 Windows 上用 `C:\...` 作 ESM import specifier 会报 `ERR_UNSUPPORTED_ESM_URL_SCHEME`，统一改为 `Path.as_uri()` 生成 `file://` URL。

### P2（修 3 项，留 2 项）

- **已修**：批次异常时在 `catch` 中带上「已完成 N/M 批、成功 X 个文件，请勿重复上传」，并在 `finally` 无条件打印进度汇总；空明细文案改为「无法确认逐文件结果，请核对网站数据状态」。未贸然加重试（接口幂等性未确认）。
- **已修**：Node 侧新增 `redact()`，`log()`、`safeErrorMessage()`、`main().catch` 与上传接口错误全部经脱敏；`apiLogin` 不再打印完整响应体，只回报 HTTP 状态与 `retCode`。GUI／服务路径仍由 Python `toolbox_redaction` 负责。
- **已修**：`diagnostics.py` 三处排障文案、`upload_service.py` 的 Finder 日志按 `sys.platform` 分支、`private-data-uploader-tool/README.md` 初始化说明、`ui/dashboard.py` 的 `Ctrl/⌘ F`。
- **未修（需业务确认）**：`power_source` 无 UI 入口——测试明确断言页面不含该选择器，属有意裁剪，需先确认是否支持新能源原始功率预测数据后再加字段；`standalone_login` 与 `interactive_login` 两套实现收敛属重构，留待单独改动。

### P3

全部未改动（死代码清理、页面工厂合并、Excel 写后回读、默认覆盖开关、D-1 单测、锁与资源细节等），避免与本次阻断修复混在一起引入回归。

### 修复后验证

- 315 个 `.py` 全量 `ast.parse`：0 错误；`node --check upload-private-data.mjs`：通过。
- 新增无 GUI 回归 15 项、早报 76 项（+1 全角表名）、日报 51 项、500kV 64 项（16 项需浏览器跳过）：全部通过。
- 全仓 `run_process` 签名越界调用 0 处；`loginFailure(` 残留 0；`date_token in path.name` 残留 0；`toolbox_shared` 残留 0。
- 未验证（审查当时）：系统 Python 无 tkinter，`test_private_batch_upload.py`／`test_date_inputs.py` 无法导入；本轮已在带 tkinter 的源码 `.venv` 跑通两个模块（24 项／28 项）并完成 GUI 实建。真实网站登录上传、WPS 写入、Excel 真实写入回读、安装包构建与实机安装仍未验证。
- 本目录无 `.git`，改动未提交到 `origin/development`（与 0.1.31 源码交付状态一致）。

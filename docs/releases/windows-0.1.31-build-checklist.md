# Windows 0.1.31 构建与发布清单

本版本源码已对齐 macOS 主线 `a792275829-cpu/electricity-toolbox-scripts` **v0.1.31 正式版**已发布的 UI／功能范围；上游 v0.1.31 之后的未发版提交按约定暂不移植。版本号、更新日志与发布前静态校验均已就绪。当前工作副本是**解压源码包**，本机不具备完整构建条件，因此安装包、增量补丁与 GitHub Release 需在具备工具链的构建机执行本清单。

## 本次交付范围

- 新增第 16 个工具「私有数据批量上传」，注册链覆盖 catalog、adapters、pages、service、service_catalog、workflows、commands、ui_tasks。
- 早报全套口径、日报 D 日自动识别、500kV 价差阈值与母线电压筛选、双月历日期控件、16 个页面文案精简，均按 v0.1.31 落地。
- 上游 `toolbox_shared` 包引用统一适配为 Windows 分支既有的根目录 `toolbox_*` 模块，新增 `toolbox_file_button.py`、`toolbox_login_tools.py`、`toolbox_trade_batch.py`，并补齐 `toolbox_auth_loader.standalone_login`；未引入重复模块层次。
- 保留 Windows 独立的 Rust 后端、增量更新通道、WPS 平台修复，以及 500kV 潮流箭头降速 20% 契约。
- 主窗口默认 1480×900、**最小 1280×802**（96 DPI 逻辑尺寸；Windows 按 DPI 缩放为物理像素，150% 屏实测默认 2040×1320、最小 1920×1203）：页面区使用 `Viewport` 滚动容器，右侧辅助面板保留原版 Tk 布局、不引入滚动容器，改用高度下限避免账号面板压住状态栏；实测默认与最小窗口下 16 个工具页面零压缩，账号面板与状态栏不重叠。
- 本轮未新增 macOS 专用内容（`macos-15` CI 变更、Swift 图块识别、`.command` 启动器）；仓库既有 macOS 源码启动器与 WPS 专用浏览器 `.command` 属原项目保留文件，WPS 写入工具 3 处 macOS 排障文案已改为双平台提示。

## 构建机前置条件

| 条件 | 说明 |
| --- | --- |
| 系统 | Windows 10/11 x64；MSVC 生成工具、WebView2 运行库 |
| Rust | `cargo` / `rustc`，需能编译 `desktop/src-tauri` |
| Node | 18+，`npm.cmd` 可用（`npm ci` 与 `npm run build -- --bundles nsis`） |
| Python | 3.11/3.12 且**带 tkinter**（`build.ps1 -PythonRoot` 指向该运行时），不要用精简版 Python |
| `.venv` | 仓库根目录，执行 `setup_windows.ps1`；需 `uv`、`openpyxl`、`python-docx` 等 `requirements.txt` 依赖 |
| 网络与权限 | 可 `git push` 到 `QiaoZhang013/electricity-toolbox-rust-windows` 的 `development`，可创建 Release 并上传附件 |
| 历史完整包 | v0.1.28 / v0.1.29 / v0.1.30 的 `ElectricityToolbox_X.Y.Z_windows_x64_setup.exe`（可从对应 Release 下载），用于生成最近三个版本的直达补丁 |

## 构建命令（仓库根目录，PowerShell）

```powershell
.\setup_windows.ps1
.\.venv\Scripts\python.exe packaging\tauri\version.py check --tag v0.1.31 --require-notes
.\packaging\windows\build.ps1 -PythonRoot "C:\Path\To\Python312"
```

`build.ps1` 依次执行 `prepare_payload.py`、`npm ci`、`npm run build -- --bundles nsis`，输出
`dist/windows/ElectricityToolbox_0.1.31_windows_x64_setup.exe` 与 `.sha256`，再调用
`build_updates.py` 生成最近三个版本的直达补丁及 `windows-update-manifest.json`。
跨主／次版本或需要重设基线时使用 `-FullOnly`。

同步源码（构建机具备 git 通道后）：

```powershell
git checkout development
git add <本次改动文件>
git commit -m "align Windows UI and features with upstream v0.1.31"
git push origin development
git log -1 --format=%H   # 回读远端确认一致
```

## 发布前校验门

1. `version.py check --tag v0.1.31 --require-notes` 通过，且 CHANGELOG 含具体变更。
2. 带 tkinter 的 Python 3.12 环境跑完整测试：`python -m unittest discover -s 电力工具箱/tests`，以及 500kV、早报、日报、desktop 测试；页面创建 smoke test 覆盖 **16 个工具页**，确认主窗口与 Rust 后台正常。
3. 完整安装包在 Windows 11 实机安装，回读程序文件清单（0.1.30 基线为 657 个）与版本状态栏。
4. 三份直达补丁在隔离副本**实际还原**，逐项核对文件、权限、符号链接与 SHA-256；校验索引及 GitHub 附件摘要。
5. 用原版 0.1.30 客户端执行真实增量升级并自动重启，确认配置、登录态、业务数据保留，Excel 数值与 `0.00%` 等格式回读一致；补丁失败时自动回退完整包。
6. 官方卸载程序验证：清理新程序文件并保留用户额外文件。
7. 全部通过后创建 `v0.1.31` Release，附件与 `v0.1.30` 对齐（共 14 项）：

| 附件 | 数量 |
| --- | --- |
| `ElectricityToolbox_0.1.31_windows_x64_setup.exe` + `.sha256` | 2 |
| `ElectricityToolbox-{0.1.28,0.1.29,0.1.30}-to-0.1.31-windows-x64.patch.tar.xz` + 各 `.sha256` | 6 |
| `ElectricityToolbox-0.1.31-source.zip` + `.sha256` | 2 |
| `windows-update-manifest.json` + `.sha256` | 2 |
| `ElectricityToolbox-0.1.31-Windows-verification.txt` + `.sha256` | 2 |

完整包与该目录内所有附件必须**一起上传**，不可只传补丁；历史补丁与索引中的升级路径继续保留。发布后同步更新 `main` 分支安装包直链，保留 GitHub Actions 入口。

## 本机未能执行的部分及原因

- **未提交 / 未推送 `origin/development`**：当前工作副本不是 git 仓库（无 `.git`）；本轮已在临时目录成功 `git clone` 上游 main 用于对比，源码改动仍留在本地工作副本。
- **未构建安装包 / 补丁**：本机无 `cargo`、`rustc`，无 NSIS；亦未下载 340 MB 级历史完整包用于增量还原。源码运行所需的带 tkinter Python 3.11 `.venv` 已具备。
- **未创建 Release**：缺少 GitHub 写凭据与上传链路。
- **未验证项**：真实网站上传与 WPS 文档写入、Excel 真实写入回读、Windows 10 实机与完整安装／自动重启；完整 GUI 套件含交互型用例，无人值守下未跑完，已改跑针对性模块。

## 已完成的验证证据

- 318 个 Python 文件 `ast.parse` 全部通过；本地模块引用解析 0 缺失；`toolbox_shared` 残留 0 处；无冲突标记。
- `node --check` 通过：`upload-private-data.mjs`、`batch-plan.mjs`、`region_curves.js`。
- `version.py check --tag v0.1.31 --require-notes` 通过；`desktop/package.json`、`tauri.conf.json`、`Cargo.toml`、`package-lock.json` 均为 0.1.31。
- 500kV 断面分析 64 项通过（16 项需浏览器跳过）；早报 76 项通过；南方分公司电力市场日报 51 项通过。
- 真实 GUI 实测（带 tkinter 的 `.venv`，屏幕 2160×1440／150%）：默认 2040×1320、`minsize=(1920,1203)`（逻辑 1480×900／1280×802）；预热后默认窗口与强制最小尺寸两种情况下，16 个工具页面 **0 个被压缩**，页面滚动条按内容尺寸自动出现、切换页面滚动归零，账号面板不再压住状态栏（余量 415px）。
- `test_minimum_window` 4 项、`test_login_ui` 4 项、`test_platform_ui` 14 项、`test_private_batch_regression` 19 项、`test_power_benefit_update` 16 项、`test_date_inputs` 28 项通过；`test_native_updates` 8 项因本机缺 Rust 后端跳过。

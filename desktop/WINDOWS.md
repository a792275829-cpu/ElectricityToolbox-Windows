# Windows 使用说明

目标平台为 Windows 10/11 x64。保留原有 Tk 页面与业务字段，Rust/Tauri 负责桌面宿主，Rust 任务服务负责调度与进程管理；业务计算、Excel 读取、定点更新与事务提交使用 Rust 后端。原有 Python/Tk、浏览器与文档引擎继续复用，详见 [迁移说明](../docs/RUST_MIGRATION.md)。

## 源码运行

安装带 Tcl/Tk 的 Python 3.11+、Rust 和对应的 C/C++ 构建工具。双击 `00_启动/Windows初始化.cmd` 创建仓库内 `.venv`，安装依赖与浏览器并编译原生 Excel 后端。只调试 Python 兼容路径时可用 `setup_windows.ps1 -SkipNative`，没有 Rust 后端会自动使用 Python 实现。出清结果汇总上传另外需要 Node.js。

完成初始化后双击 `00_启动/Windows启动.cmd`。也可在 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Source
```

## 构建与安装

安装 Node.js 22、Rust Windows 工具链、对应的 C/C++ 链接工具和独立可移植的 CPython 3.12（包括 Tcl/Tk）。运行初始化建立 `.venv` 后：

```powershell
.\packaging\windows\build.ps1 -PythonRoot C:\path\to\standalone-python
```

产物位于 `dist/windows/`，包含 NSIS `.exe` 和 `.sha256`。安装包自带 Python、Tcl/Tk、Node、Chromium 和业务依赖；最终用户无需安装开发工具。WebView2 缺失时安装程序会下载微软运行时，需要网络。安装按当前用户执行。

大容量构建缓存可通过 `CARGO_TARGET_DIR` 指向空间充足、路径短且不含空格的目录；`TEMP` 和 `TMP` 也可指向该盘的临时目录。脚本不将本机路径写入发行包。Windows PowerShell 5.1 脚本使用 UTF-8 BOM，以兼容中文路径。

GitHub Actions 的 Windows desktop 工作流对 Windows 核心执行回归，手动运行可生成安装包附件；不会自动发布正式版。

实际测试范围和本机性能对照见 [Windows 验证记录](WINDOWS_VALIDATION.md)。

## 性能与数据行为

- 原页面继续懒加载；后台日志批量刷新，切换页面不重新创建已经加载的业务页面。
- 正常启动不创建隐藏的 WebView2 窗口，减少空闲浏览器进程；仅在启动故障时加载诊断窗口。
- 沿用任务范围内的 Rust 进程复用及有界工作簿缓存；Rust Excel 结果封装直接移动元数据，减少大工作表复制。
- 预测特征矩阵使用小端双精度二进制传输，PDF 图表使用紧凑 RGB 数据，避免逐单元格或逐像素 JSON。
- Windows 安装版使用 Rust 后台任务服务；目录、表单和引擎信息由短时兼容进程读取，空闲时没有常驻 Python 任务服务。
- Windows 子任务先挂起创建，加入专用 Job Object 后恢复；取消仅终止该任务的进程树。
- 写入使用 Windows 内核互斥锁；保留原来的临时文件、原子替换、冲突检测和回滚规则。
- `TOOLBOX_XLSX_BACKEND=python` 或 `rust` 可用于同样输入的对照测试；默认 `auto`。不是所有工作负载都会因 Rust 而加速。
- `TOOLBOX_DOMAIN_BACKEND=python|rust|auto` 单独控制本轮业务内核；未设置时继承 Excel 后端选择。严格 `rust` 模式错误直接上报；已选择原生写入后不自动重试，防止重复写入。

复现合成 Excel 性能对照：

```powershell
.venv\Scripts\python.exe packaging\windows\benchmark_excel.py --rows 20000 --repeats 3 --output .toolbox\windows-excel-benchmark.json
```

## 使用边界

从 0.1.22 开始，安装版启动时自动检查本仓库的 Windows 正式版；也可从托盘选择“检查更新”。发现新版后点击“开始更新”，自动下载完整 x64 安装包、校验大小与 SHA-256、退出旧程序、安装并重新打开；配置和数据保留。正在运行任务时不能更新，下载可取消，安装失败会恢复旧程序并在重新打开时提示原因。客户端不会下载 macOS 的 DMG 或增量补丁。安装包暂未配置代码签名。

0.1.21 及更早 Windows 版尚无更新器，需要先从 [发布页](https://github.com/QiaoZhang013/electricity-toolbox-rust-windows/releases/latest) 手动安装新版。迁移到新 GitHub 仓库前的版本（包括 0.1.22～0.1.24）也需要手动安装一次 0.1.25 或更新版本，之后即可从新仓库在应用内更新。自动安装仅适用于完整桌面安装版。

Windows 图片标签识别使用系统 OCR，需要安装简体中文 OCR 语言功能。WPS 自动化优先使用已安装的 Chrome/Edge，独立浏览器配置和登录态与其他工具隔离。

业务网站登录、上传和真实日报必须在用户的网络与账号环境验证。打包不包含个人凭据、Cookie 或业务历史；广东电价预测如需要历史工作簿，请选择真实输入，或构建时通过 `-ForecastWorkbook` 显式提供。

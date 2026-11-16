# Windows 专用源码清理 — 2026-11-16

用户授权更新维护约定，只保留 Windows 版软件所需代码，并删除此前列出的 Mac 专用脚本与平台分支。

## 结果

- [AGENTS.md](../../AGENTS.md) 改为仅维护 Windows 10/11 x64；删除“Mac 代码原位保留”及 Mac 依赖锁规则，明确共享模块先核对 Windows 调用方再清理。
- 本轮删除 30 个文件；此前已完成的 Mac 测试删除不重复计入本轮文件数。原有未提交修改保留；本轮修改前的相关文件副本保存在仓库上一级 `windows-only-cleanup-backup/`，不进入源码载荷或 Git。
- 清理 Mac `.command` 入口、源码调试应用、DMG/PKG 安装／签名／发布、Mac Tk 运行时构建、图标资源、Mac OCR 脚本，以及依赖非 Windows `resource.getrusage` 的旧实验脚本。
- 移除 WPS 的 Mac Chrome 查找、Rosetta 启动和 `ps` 进程查询；Windows 浏览器发现、专用 profile、进程归属验证和清理继续使用现有 Windows 实现。Node 上传工具同样移除 Mac Chrome 路径查找。
- 移除 Finder/AppleScript 文件复制兜底，保留集团上传暂存、数值模板生成、回读及文件复制回原目录的流程。共享复制函数同步调用方更名为 `copy_upload_file`。
- 移除 Rust 宿主的 Mac 应用包解释器、环境变量、激活和 Reopen 分支；默认 Tauri 构建目标改为 NSIS、图标改为 ICO，保留 Windows 配置覆盖文件。
- Windows 更新器继续复用 GitHub HTTP/HTML 检测、摘要记录和版本解析；删去基础模块的 DMG 客户端。共享增量模块仅保留 Windows 使用的摘要、路径校验和最小下载路径算法。UI/服务统一选择 `WindowsUpdater`，缺少附件时明确提示 Windows x64 安装包。
- PDF 图片标签识别只调用现有 Windows OCR PowerShell 脚本，载荷必需资源清单同步改为 `.ps1`。500kV 初标仅提供 PP-OCRv6，保留独立 OCR 环境、缓存回放、分块坐标、边缘补检和站名恢复接口；移除 Mac Vision 编译与推理。风力截图继续使用随包中文字体，去除 Mac 字体分支。选文件按钮删除 Aqua 专用布局。
- 同步当前使用说明、目录地图、图标和 OCR 文档及 Unreleased 说明。历史发布／评审／性能记录不改写。

## 保留的共享代码与兼容信息

`packaging/tauri` 中的源码筛选、资源检查、默认配置、预测种子、版本同步、工作区安装与数据迁移仍被 Windows 引用，继续保留。`desktop/python/github_update.py` 和 `delta_update.py` 也保留其 Windows 调用部分。

现有标注文件可能记录 `ocr_engine=macos-vision`；仅保留读取这种历史来源标签的校验兼容，不提供该 OCR 引擎，也不启动 Mac 程序。NPM/Cargo 锁文件中的第三方可选平台依赖元数据不手动删改。`.github/workflows/maintain-macos.yml` 实际调用 Windows 检查流程，保留旧文件名以兼容既有入口，没有 Mac 构建任务。

## 删除文件

- `00_启动/GUI绘制原型.command`
- `00_启动/Tauri开发版.command`
- `00_启动/源码调试版.command`
- `00_启动/电力工具箱.command`
- `500kV断面分析/500kV 断面分析.command`
- `500kV断面分析/校准500kV节点位置.command`
- `desktop/python/auto_install.py`
- `desktop/python/installer_image.py`
- `desktop/python/remote_updater.py`
- `desktop/python/source_debug.py`
- `desktop/src-tauri/Info.plist`
- `desktop/src-tauri/icons/icon.icns`
- `native/price-xlsx/benchmark_all_trials.py`
- `native/price-xlsx/benchmark_inspection.py`
- `package_macos.command`
- `packaging/tauri/build_dmg.py`
- `packaging/tauri/build_icons.py`
- `packaging/tauri/build_pkg.py`
- `packaging/tauri/build_updates.py`
- `packaging/tauri/install_user_app.py`
- `packaging/tauri/pkg-scripts/common.sh`
- `packaging/tauri/pkg-scripts/postinstall`
- `packaging/tauri/pkg-scripts/preinstall`
- `packaging/tauri/prepare_tk.py`
- `packaging/tauri/release_index.py`
- `packaging/tauri/requirements-macos.lock`
- `packaging/tauri/validate_installer.py`
- `setup_macos.command`
- `wps自动/启动WPS专用Chrome.command`
- `广东运行日报PDF解析/gd_pdf_tool/recognize_image_labels.js`

## 验证

统一入口全量命令：

```powershell
.venv/Scripts/python.exe tools/run_tests.py full --strict --timeout 120 --report ../windows-only-full-results.json
```

首轮 153 个模块/文件中 152 个通过；`test_source_restart` 仍断言旧 Mac 应用包环境标记会禁用重启，清理该标记对应断言后复跑通过。针对性早期检查还定位并更新了一条旧 Mac 增量路径契约，现在验证共享算法默认选择 Windows 路径，全量检查通过。

新增的 Windows OCR 测试在全量选取清单形成后补入，通过统一入口单独执行；最终按模块合并全量与复跑结果，并与当前入口的完整测试清单逐项比对，未遗漏模块。

- 最终 **154 个模块/文件全部通过**，其中含 5 个 Node 测试文件；无整个模块跳过或平台不适用项。
- Python 共 1437 条用例，执行 1431 条，条件跳过 6 条。跳过项为 5 条非 Windows 语义用例及 1 条需显式启用的真实 Chromium PDF 回读；没有 Mac 用例跳过。
- Rust 宿主最终复跑 3 条通过；原生后端 7 条 + 任务服务/Windows 更新 helper 13 条通过，合计 23 条。
- `start_source_test.ps1 -Smoke` 输出 `TOOLBOX_DEPS=OK`、`TOOLBOX_SMOKE=OK`。
- 当前 345 个 Python/pyw 源码 AST 解析、`git diff --check`、版本一致性检查均通过，版本仍为 v0.1.41。
- 新增 OCR 用例用合成预测核对原图坐标和证据字段，不启动外部程序；未启动真实 PaddleOCR 模型推理。本轮没有生产登录、上传或修改用户真实工作簿。
- 未构建正式安装包、执行安装、提交、推送或发布；已有构建产物不作为此次源码清理后的正式发行包。

验证产物位于仓库上一级：`windows-only-effective-results.json`、`windows-only-summary.json`、`windows-only-full-tests.log`、`windows-only-rerun-tests.log`、`windows-only-ocr-tests.log`、`windows-only-file-button-tests.log`、`windows-only-rust-host-final.log`、`windows-only-rust-native.log`、`windows-only-source-smoke.log`。

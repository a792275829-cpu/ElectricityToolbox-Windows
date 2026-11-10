# 仓库结构地图

给维护用的文件地图：每个目录/文件是干什么的、能不能动、动了会影响哪里。
新功能或修 bug 前先看这里，再按 `AGENTS.md` 的验证策略执行。

## 一句话结构

一个 Windows 桌面应用，外壳是 Rust/Tauri，界面是原版 Tkinter，业务是 Python，
Excel 重活走 Rust 原生后端。仓库里同时存在「应用外壳」「Tk 界面框架」「16 个业务工具」「打包发布」四层。

```
用户双击  →  desktop/src-tauri（Rust 宿主，NSIS 安装包内）
              ├─ 启动 Rust 任务服务  native/price-xlsx → 变体为 电力工具箱/bin/toolbox-service.exe
              └─ 启动 Tk 界面       电力工具箱/toolbox_launcher.pyw → 电力工具箱/toolbox/*
                                       └─ 每个工具页调用对应业务目录的脚本
```

## 顶层目录

| 目录 | 作用 | 能否移动/改名 |
| --- | --- | --- |
| `desktop/` | Tauri 外壳：`src-tauri/` Rust 宿主、`src/` 前端、`python/` 桥接与更新器、`tests/` | 调整须同步打包路径、CI 与更新器 |
| `native/price-xlsx/` | Rust Excel 后端，编译成 `toolbox-price-xlsx.exe` 与 `toolbox-service.exe` | 调整须同步消费者与打包规则 |
| `电力工具箱/` | Tk 界面框架：`toolbox/` 页面、命令、服务、任务、UI 组件；`tests/` 回归 | 调整须同步页面 import 根 |
| `packaging/` | `windows/` Windows 打包；`tauri/` 版本号与通用构建脚本 | 调整须同步消费者与打包规则 |
| `00_启动/` | 双击入口。`.cmd` 只是转调根目录 `*.ps1`；`.command` 是 macOS 历史入口（保留不维护） | 可以增删，但要同步 README |
| `docs/` | 文档，索引见 `docs/README.md` | 可以，注意 CHANGELOG 里的历史链接 |
| `说明文档/` | 一页纯文本入口说明，与 README 重复度高 | 可以改内容，改入口时同步它 |
| `.github/workflows/` | CI：`windows-desktop.yml`（构建+测试）、`desktop-ci.yml`、`desktop-release.yml` | 谨慎，`main` 上的 `maintain-*.yml` 会引用它 |
| 业务工具目录（见下表） | 各工具自己的入口脚本、配置样例、模板、测试 | 不能随便改目录名，页面按路径调用 |

## 根目录文件

| 文件 | 作用 |
| --- | --- |
| `README.md` | 使用者文档：安装、启动、配置、功能操作 |
| `AGENTS.md` | 维护约定：改动边界、验证策略、发布规则。**改代码前必读** |
| `CHANGELOG.md` | 逐版本记录，版本说明的唯一来源；发布说明由它生成 |
| `NOTICE.md` | 来源与声明 |
| `docs/windows-maintenance-model.md` | 分阶段维护流程（开发、同步、上游对齐、发行、回收） |
| `docs/project-structure.md` | 本文件 |
| `setup_windows.ps1` | 首次初始化：建 `.venv`、装依赖、准备便携 Node |
| `start_windows.ps1` | 启动（优先用已构建的 native 产物，`-Source` 强制走源码） |
| `start_source_test.ps1` | 源码调试版启动，`-Smoke` 自检，数据隔离在 `.toolbox/source-test` |
| `.python-version` | 解释器版本的唯一来源：`.venv`、内置运行时与 CI 都按它对齐，`build.ps1`／`verify_payload.py` 会校验 |
| `tools/run_tests.py` | 统一测试入口（fast／full／分组／指定模块），CI 与文档都调它，不再手抄模块清单 |
| `setup_macos.command`、`package_macos.command` | macOS 历史入口，保留不维护，不参与 Windows 构建 |
| `requirements.txt` | 转引 `packaging/windows/requirements.txt`；依赖版本只在后者维护 |

## 根目录 `toolbox_*.py`（37 个扁平模块）

这些不是临时文件，是**打包时被平铺到载荷根目录的共享模块**：Windows 侧不用 `toolbox_shared/` 包结构，
而是把公共模块直接放在仓库根。调整时必须同步 import、调用路径和载荷规则，并验证源码与安装版；这些路径是当前实现，不是永久禁止重构的接口。

收录规则在 `packaging/tauri/prepare_payload.py` 的 `runtime_source`，不是逐项清单，而是按命名约定自动判断：

- 根目录文件：文件名以 `toolbox_` 开头且后缀为 `.py` → 自动进载荷。
- 业务目录文件：首层目录属于 `MODULE_DIRECTORIES`（15 个业务目录）→ 进载荷，
  但跳过路径中含 `tests`/`docs`/`scripts-dev` 的、`test_*` 文件与 `.command`。
- 统一跳过 `.lock`/`.log`/`.pyc`/`.bak`/`.tmp`、`.DS_Store`、`Thumbs.db`、`~$*`。
- 正式 Windows 构建先拒绝脏工作区，再用 `git ls-files -c` 仅导出已跟踪文件，清单记录来源提交。
- 显式开发构建使用 `git ls-files -co --exclude-standard`，包含符合规则的未跟踪文件；仅供验证，禁止发布。

由此有两个只在安装版才暴露的坑：

- 根目录新增公共模块若命名为 `foo.py` 而非 `toolbox_foo.py`，源码能跑，安装版缺文件。
- 新建业务目录必须先加进 `MODULE_DIRECTORIES`，否则该目录整体不进载荷，装完才报 ImportError。

按用途分四组：

- **界面与基础设施**：`toolbox_platform`（平台适配）、`toolbox_storage`、`toolbox_version`、`toolbox_redaction`、
  `toolbox_background_read`、`toolbox_choices`、`toolbox_dates`、`toolbox_date_input`、`toolbox_log_console`、`toolbox_file_button`
- **登录与凭据**：`toolbox_auth_loader`、`toolbox_auth_requests`、`toolbox_cli_login`、`toolbox_credentials`、`toolbox_login_tools`、`toolbox_qingneng_login`、
  `toolbox_wps_config`、`toolbox_wps_urls`
- **Excel/工作簿与业务**：`toolbox_excel_read`、`toolbox_market_workbook`、`toolbox_workbook_commit`、
  `toolbox_workbook_inspection`、`toolbox_native_compute/domain/excel/workbook/write`（Rust 后端桥接）、
  `toolbox_clearing_layout`（出清表布局）、`toolbox_trade_batch`（批量交易）、`toolbox_trade_report`（交易报告）
- **500kV 断面分析**：`toolbox_section_annotation_geometry`、`toolbox_section_basemap`、`toolbox_section_prelabel`、
  `toolbox_section_schema`、`toolbox_section_topology`、`toolbox_section_vision_review`、`toolbox_topology_web`

## 工具页 ↔ 业务目录

界面页在 `电力工具箱/toolbox/pages.py` 按 `tool_id` 注册，页面通过 `commands.py` 调业务脚本。

| 页面 tool_id | 业务目录 | 入口脚本 |
| --- | --- | --- |
| `online-energy` | `上网电量抓取/` | `export_online_energy.py` |
| `wind-forecast` | `风力预测截图/` | `wind_forecast_screenshot.py` |
| `market-table` | `市场表更新/` | `update_market_table.py` |
| `spot-price` | `现货价格更新/` | `update_spot_prices.py` |
| `transaction-info` | `交易系统信息更新/` | `update_transaction_info.py` |
| `power-benefit` | `功率调整收益统计表更新/` | `update_power_benefit.py` |
| `guangdong-price` | `广东电价预测/` | `train_d1_forecast.py`（D+1~D+15 递推） |
| `trade-analysis` | `电力工具脚本/电力交易分析工具/` | `generate_electricity_report.py` |
| `section-analysis` | `500kV断面分析/` | 分析入口 + 浏览器拓扑编辑 |
| `daily-pdf-parser` | `广东运行日报PDF解析/` | `run_daily_pdf_parser.py` |
| `market-report` | `南方分公司电力市场日报/` | 日报生成 |
| `report` | `每日生产经营情况汇报自动生成工具/` | `scripts/` |
| `summary-upload` | `广东运行日报PDF解析/` + 汇总上传 | 汇总表上传 |
| `group-upload` | `集团每日上传/` | `upload_daily_report.py` |
| `private-batch-upload` | `电力工具脚本/private-data-uploader-tool/` | `scripts/upload-private-data.mjs`（Node） |
| WPS 写入页 | `wps自动/` | `wps_excel_to_kdocs_gui.py` |

`电力工具脚本/电量汇总工具/` 是 `summary-upload` 的计算侧；`wps自动/` 的浏览器 profile、pid、登录态都在本地忽略目录，不随仓库走。

## 数据与状态放在哪

| 类型 | 位置 | 是否入库 |
| --- | --- | --- |
| 源码调试数据、独立解释器 | `.toolbox/source-test/`、`.toolbox/standalone-python/` | 否 |
| 源码调试版便携 Node | `runtime/node/`（`setup_windows.ps1` 按 `NODE_VERSION` 下载，上传功能用，约 100 MB，**勿删**） | 否 |
| 运行日志 | `logs/`、`logs/source-test/` | 否 |
| 安装版工作区 | `%APPDATA%\com.electricity.toolbox\app` | 否 |
| 安装版载荷 | `%LOCALAPPDATA%\电力工具箱\payload` | 否 |
| 用户配置、登录态、浏览器 profile | 各工具目录下的 `config.json`、`auth_state.json`、`.browser-profile/` | 否 |
| 构建产物 | `dist/`、`desktop/src-tauri/target/`、`native/price-xlsx/target/` | 否 |
| 载荷暂存 | `desktop/src-tauri/payload/`（`app`/`runtime/python` 每次归档后重建，`runtime` 可复用） | 否 |

`.venv` 的基础解释器是 `.toolbox/standalone-python`（见 `.venv/pyvenv.cfg` 的 `home`），清理前务必确认。

## 改动守则

1. 改页面/业务前，先查这个工具目录的调用方：`pages.py`（界面）、`commands.py`（命令）、`service_catalog.py`（服务表单）、`page_contracts.py`（字段契约）四层都要看。
2. 新增工具页：`pages.py` 注册 `tool_id`、`commands.py` 接线、`service_catalog.py` 补表单、`page_contracts.py` 加字段，按当前实现同步四层并检查服务模式；后续允许收敛为统一注册源，不新增重复手写清单。
3. 新增共享模块：放仓库根并按 `toolbox_*.py` 命名即自动进载荷；命名不合规则不会报错，只在安装版缺文件。新增业务目录需同时加进 `prepare_payload.py` 的 `MODULE_DIRECTORIES`。
4. 改打包或更新逻辑：先读 `packaging/windows/README.md` 与 `docs/windows-maintenance-model.md`，补丁验证必须真机还原。
5. 按 `AGENTS.md` 的风险验证规则执行；用户可见变化进入 CHANGELOG 的 Unreleased，内部整理无需凑条目。需要留存的本轮验证放 reviews，发行验收放对应 releases，不改写历史验证。
6. 清理磁盘前先看「数据与状态放在哪」：`.toolbox/standalone-python`（`.venv` 的基础解释器）与 `runtime/node`（便携 Node）删掉会直接弄坏源码调试环境，且删除后报错信息不直观；构建归档只通过 `prune_build_archive.py` 回收；其他缓存和日志按维护模型先核对再清理，不删除正在使用的目录。

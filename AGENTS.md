# electricity-toolbox 工作约定

平台范围：只保证 **Windows 10 / 11 x64** 可安装、可运行。macOS 相关代码、`*.command` 与 `packaging/tauri` 构建脚本原位保留，但不参与 Windows 构建、不再维护、不作为设计约束；新增功能不写双平台文案，也不新增 `sys.platform == "darwin"` 分支。

## 默认执行方式

- 对修改、安装、配置、修复类请求，默认直接实施，不先输出长篇方案。
- 只有业务口径、覆盖范围或破坏性操作存在实质歧义时才询问用户；其余小问题自行检查并处理。
- 优先复用仓库已有的解析、计算、登录、写入和 GUI 适配逻辑，不创建功能重复的旁路实现。
- 用户给出的中文字段名、日期口径、工作表名、单元格范围、文件路径和输出文件名均视为精确契约，不要擅自改写或推断。
- 最终汇报保持简洁，只说明：完成内容、验证结果、输出路径和仍存在的问题。

## 仓库与改动边界

- 本仓库是混合运行时项目：Rust/Tauri 宿主、Rust 任务服务与 Excel 后端、多个 Python/Tk 工具、少量 Node 工具，不要当作单一 Python 包处理。
- **源码主线是 `main`**：本机工作副本、GitHub `origin/main` 与 CI 都以它为准，提交默认推送到 `origin/main`。`development` 分支已删除（原有用提交都在 `main` 历史里，追溯用 `git log`），不要重建同名分支再把提交分开。
- 动手前先看 `docs/project-structure.md` 判断目录性质；不清楚某目录能不能改时先查该文档，不猜。
- 一次性产物不要放 `docs/` 根目录：评审与上游比对快照进 `docs/reviews/`，逐版本验证记录进 `docs/releases/vX.Y.Z.md`；`docs/README.md` 只索引当前有效文档与归档入口。
- 修改前运行 `git status --short`，记录并保留用户已有改动；不得清理、覆盖或顺带格式化无关文件。
- 只编辑完成当前任务所需的文件。真实配置、登录态、Cookie、`.browser-profile/`、业务数据、缓存与构建产物一律 Git 忽略，不入库、不写进日志和回复。
- 修改现有功能时先查找其调用方、测试和 GUI 入口，避免只修脚本而遗漏工具箱页面或启动路径。
- 验证通过后才提交：只暂存本任务相关文件，推送 `origin/main` 并回读远端提交确认一致；未通过验证的改动不提交，同步失败须说明原因和未同步内容。

## 启动与依赖

- Python 版本只有一处事实源：根目录 `.python-version`（当前 `3.11.15`）。本地 `.venv`、内置运行时和 CI 都按它安装，`build.ps1` 与 `verify_payload.py` 会校验，不一致直接报错；改动版本必须同时重建 payload。Windows 解释器是 `.venv\Scripts\python.exe`，不要把个人机器上的绝对解释器路径写入代码。
- 依赖版本只在 `packaging/windows/requirements.txt` 维护（内置运行时用的同一套 pin）；根 `requirements.txt` 只转引它，不要另写版本号。macOS 侧仍用 `packaging/tauri/requirements-macos.lock`。
- 入口：初始化 `setup_windows.ps1`，正式启动 `start_windows.ps1`，源码调试 `start_source_test.ps1`（`-Smoke` 自检输出 `TOOLBOX_SMOKE=OK`），安装包构建 `packaging/windows/build.ps1`。使用原版 Tk 页面，不另写一套外观近似的 UI。
- 构建只归档不删除：`prepare_payload.py` 与 `build.ps1` 用改名把 `payload/app`、`payload/runtime/python`、`desktop/node_modules` 移入 `dist/build-archive/<时间戳>/` 后重建，回收统一走 `packaging/windows/prune_build_archive.py`。禁止对上述目录及 `dist/build-archive/` 使用 `rm -rf`、`shutil.rmtree` 或回收站删除——Agent 会话注入的批量删除守卫在非交互子进程中没有确认通道，会 fail-closed 中断构建。细节见 `packaging/windows/README.md`。
- `build.ps1` 会先用 `build_env.ps1` 的 `Reset-BuildEnvironment` 还原被会话注入的环境（`-KeepGuard` 可保留守卫）；不确定参数时用 `-DryRun` 自检。
- 保持“复制整个文件夹到另一台 Windows 机器后可初始化并运行”的可移植目标。
- 路径处理使用 `pathlib.Path` 和脚本/仓库相对定位；打开目录及进程控制集中使用平台适配逻辑，避免把系统专用调用散布到业务页面。
- Node/npm 依赖只应作用于实际需要它们的子工具，不得变成所有 Python 工具的强制前置条件。

## Excel 与数据任务

- 修改映射前必须确认并显式保留：来源日期口径（如 `D`、`D-1`、`D-2`）、目标工作表、日期匹配方式、目标列范围、字段顺序和覆盖规则。
- 报表名称不能唯一确定字段时，不要猜测；先从真实模板、现有代码或用户提供的精确字段表确认。
- 优先按标签、日期或表头定位，避免在模板可能漂移时新增脆弱的固定行号。
- 默认不覆盖用户原始工作簿；真实验证应使用副本或明确指定的输出文件。
- 涉及 Excel 写入时，脚本退出码为 `0` 不足以证明成功；必须重新打开输出文件并回读关键单元格，核对值、字段顺序和必要的数字格式。
- 百分比、日期时间、空值和已有单元格覆盖行为必须同时验证底层值与显示格式。
- 用户明确要求“执行真实写入”时，在针对性测试通过后完成真实工作簿写入和回读，不得只做模拟测试。

## 浏览器、登录态与并发

- `上网电量抓取`、`市场表更新`、`集团每日上传` 等工具必须各自使用独立的 `auth_state.json`、锁文件和 `.browser-profile/`。
- 不得为了复用登录而把多个可并行工具重新指向同一登录态或 profile 目录。
- 修改登录、锁或 profile 逻辑时，检查所有消费者并做跨工具回归；验证退出后没有遗留锁文件。
- 凭据、Cookie、登录态和真实配置只保存在 Git 忽略的本地文件中，日志和最终回复不得输出敏感内容。
- 登录或网页自动化故障应保留第一条有效异常、退出码和复现命令；用户报告新 traceback 时按新的具体故障重新定位根因。

## 验证策略

- 采用“最小充分验证”，先跑与改动最相关的测试，再按风险扩大范围。测试统一走一个入口，不要手拼 `PYTHONPATH` 或直接 `unittest discover`（会卡在 GUI／网络用例上）：

  ```powershell
  .venv\Scripts\python.exe tools\run_tests.py                    # fast 档：工具箱非 GUI/非联网 + 桌面
  .venv\Scripts\python.exe tools\run_tests.py full               # 全量：含 GUI、联网与 500kV
  .venv\Scripts\python.exe tools\run_tests.py --group desktop     # 只跑某一组
  .venv\Scripts\python.exe tools\run_tests.py --module test_date_inputs   # 指定的模块，可重复
  ```

  入口会按模块起子进程并带超时，某个模块挂住只影响它自己，最后给出通过数与失败模块；需要 Rust 后端的模块照常用 `TOOLBOX_XLSX_BACKEND=rust` 之类的环境变量，入口会原样传给子进程。改动测试分组或新增目录时同步更新 `tools/run_tests.py` 与 `.github/workflows/`，不要在 CI 里另抄一份模块清单。
- 文案或小范围 GUI 修改：至少跑对应模块或完成页面创建 smoke test。
- 计算、解析、映射修改：运行针对性模块，并用小型或真实样本核对结果。
- 登录、真实写入、安装、并发改动：运行端到端流程，并检查实际产物或持久化状态。
- 更新器、增量补丁与打包相关用例同样用该入口的 `--group desktop`。
- 启动器 smoke test：`start_source_test.ps1 -Smoke`（输出 `TOOLBOX_SMOKE=OK`）。
- 修改依赖或启动流程时，运行 `setup_windows.ps1` 或等价的 `.venv` 导入检查，并验证启动器实际可用。
- 测试被环境、权限或外部服务阻塞时，明确报告未验证部分及具体原因，不要把部分成功表述成全部完成。

## 输出与交付

- 用户要求生成脚本、工作簿、报告或其他文件时，交付可直接使用的结果，不只提供操作说明。
- 输出文件名应稳定、可辨识，并避免覆盖输入；最终回复给出绝对路径。
- 报告验证证据时优先给出关键计数、关键单元格回读值、测试摘要或真实运行结果，不粘贴冗长完整日志。

## 版本与更新日志

- Windows 版本号跟随 macOS 基线：当前基线为 mac v0.1.32，Windows 已在其上递增到 0.1.36；对齐到新的 mac 版本时直接使用对方的 `x.y.z`。Windows 专属修补在同一基线上递增版本号，并在 `CHANGELOG.md` 标注对应的 mac 基线。已正式发布的版本号（0.1.33～0.1.36 等）不得复用。
- 自动维护根目录 `CHANGELOG.md`：每个版本写 3～6 条用户能感知的新增／修复／变更，每条一两行；它同时是应用内更新说明和 Release 正文，不要塞实现细节、单测条数或耗时数字，细节写进 `docs/releases/vX.Y.Z.md`。版本日期、用户可见变更与实际验证结果必须齐全，不得只改版本号或使用空泛占位内容。
- 用 `.venv\Scripts\python.exe packaging\tauri\version.py set X.Y.Z --notes "具体变更"` 同步版本和日志；多项变更重复传入 `--notes`。日期默认采用 Asia/Shanghai，历史记录必须保留。
- `CHANGELOG.md` 是逐版本更新内容的唯一来源；`packaging/tauri/release_notes.md` 只维护通用安装说明。发布时由 `version.py --release-notes` 提取当前版本内容并拼接安装说明。
- 发布前执行 `version.py check --tag vX.Y.Z --require-notes`，确认版本一致且更新内容非空；缺少对应日志时禁止打包发布。已发布的版本不覆盖，下一次发布递增版本号。
- 只发 Windows x64：构建 NSIS 安装包及 SHA-256，完成针对性测试与摘要校验后才发布正式版；生成物和签名私钥不进入 Git。0.1.28 起优先使用 Windows 文件增量补丁与 `windows-update-manifest.json`，缺少路径或验证失败回退完整 NSIS；0.1.27 及更早客户端需最后完整升级一次。跨主／次版本或 `build.ps1 -FullOnly` 的重大更新用完整包重设补丁基线。
- 发布补丁前必须用旧应用副本实际还原，核对全部文件、应用签名，并校验索引及 GitHub 附件摘要；禁止把测试补丁直接当发行包。
- 增量更新只在临时副本还原完整应用，校验后退出旧程序再运行安装程序，继续复用安装回滚逻辑，保留原配置、用户数据和样例。
- `main` 上的 `maintain-*.yml` 只是转发壳，调用同仓库 reusable workflow，只跑检查和构建，不创建 Release；在本仓库打 tag 不触发发布，附件由手动上传。
- macOS DMG 通道已停止：相关脚本与历史发行附件保持原状，不再构建、不再验证。

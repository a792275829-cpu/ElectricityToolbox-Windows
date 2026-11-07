# Windows 版常态维护模型

本仓库是 macOS 版 [`electricity-toolbox-scripts`](https://github.com/a792275829-cpu/electricity-toolbox-scripts) 的 Windows 移植与发行仓库。
维护方式是长期在本地保留一份源码调试版，按 mac 版逐项对齐，对齐完成后打包并更新到本仓库。

## 仓库分工

| 位置 | 内容 | 作用 |
| --- | --- | --- |
| `development`（本机工作副本） | 全部源码、文档、打包脚本 | 唯一的开发与构建来源 |
| `main` | 仅 `README.md` 与三个 `maintain-*.yml` | 安装说明与 Actions 入口 |
| `origin/development` | 与本地同步的源码 | 供他人查看源码、供 CI 运行 |
| GitHub Release | NSIS 安装包、`.sha256`、增量索引 | 对外发布与在线更新来源 |

`main` 上的 `maintain-macos.yml` / `maintain-windows.yml` 只是转发壳，调用 `development` 里的 reusable workflow，只跑检查和构建，**不会创建 Release**。
`desktop-release.yml` 的 `prepare` 作业限定 `repository == electricity-toolbox-scripts`，在本仓库打 tag 不会触发发布。发布是手动上传附件。

## 六环节流程

### ① 盯上游

- 上游仓库：`a792275829-cpu/electricity-toolbox-scripts`（macOS 主仓库）。
- 比对方式：按功能／修复逐项判断，**不整体合并**，不引入 macOS 专用实现（Swift、Vision、`fcntl` 等）。
- 基线记录：每轮把当前上游 HEAD 与版本写进 `docs/upstream-review-<日期>.md`，作为下次比对起点。

### ② 本地源码调试版

```powershell
powershell -ExecutionPolicy Bypass -File setup_windows.ps1      # 首次初始化 .venv
powershell -ExecutionPolicy Bypass -File start_source_test.ps1  # 源码调试启动
powershell -ExecutionPolicy Bypass -File start_source_test.ps1 -Smoke  # 自检，期望 TOOLBOX_SMOKE=OK
```

调试版的数据与真实使用隔离：

- 数据目录 `.toolbox/source-test/data`（经 `TOOLBOX_DATA_ROOT` 注入），日志 `logs/source-test`。
- 不读写用户真实配置、登录态与业务工作簿；登录态、`.browser-profile`、`auth_state.json` 仍由各工具独立持有并被 Git 忽略。
- 正式运行用 `start_windows.ps1`；带 `-Source` 强制走源码而非已构建的 native 产物。

### ③ 逐项对齐

1. 只改完成当前对齐所需的文件，先 `git status --short` 记录并保留已有改动。
2. 复用本仓库已有的解析、计算、登录、写入与 GUI 适配逻辑，不建旁路实现。
3. 中文字段名、日期口径（`D`/`D-1`/`D-2`）、工作表名、单元格范围、输出文件名视作精确契约。
4. 每轮补 `CHANGELOG.md`，写清用户可见的新增／修复／变更**和实际验证结果**。

针对性验证（在 `电力工具箱/` 目录逐模块运行）：

```bash
PYTHONPATH=<仓库根>;<电力工具箱>;<电力工具箱>\tests PYTHONUTF8=1 ..\.venv\Scripts\python.exe -m unittest tests.<模块>
```

Git Bash 下先把路径用 `cygpath -w` 转成 Windows 形式，否则 `PYTHONPATH` 不生效。

### ④ 打包

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -PythonRoot <standalone CPython 目录>
# 重大更新（跨主／次版本）重设补丁基线：
powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -PythonRoot <同上> -FullOnly
```

- `-PythonRoot` 必须指向**独立 CPython 发行版**（有 `python.exe`、无 `pyvenv.cfg`），不能是 `.venv`。
- 产物落 `dist/windows/`：`ElectricityToolbox_<版本>_windows_x64_setup.exe`、同名 `.sha256`，以及 `updates-<版本>/windows-update-manifest.json` 与 `.sha256`。
- 构建结束会校验增量索引确实生成，缺失即报错。历史发布列表优先走 `gh`，未安装时走 GitHub REST API，都不可用时用 `dist/windows/` 里的本地历史包。
- 载荷准备会清空并重建 `desktop/src-tauri/payload/app`、`payload/python`，这些是纯生成目录。

### ⑤ 更新到仓库

1. 提交并推送源码：`git push origin development`，并回读远端确认提交一致。
2. 打标签：`git tag v<版本>` 与源码提交对应（版本号规则见下）。
3. 在 GitHub 上创建 Release，**完整包、`.sha256`、`updates-<版本>/` 内全部附件一起上传**；只传安装包会导致客户端拿不到增量索引，退回每次下载完整包。
4. 已发布版本不得覆盖；需要再改就递增版本号。

### ⑥ 安装并回读验证

```powershell
# 静默覆盖安装（完整包直接覆盖，不受在线更新版本比较限制）
Start-Process -Wait -FilePath "dist\windows\ElectricityToolbox_<版本>_windows_x64_setup.exe" -ArgumentList "/S"
```

回读三处，缺一不可：

- `%LOCALAPPDATA%\电力工具箱\payload\manifest.json` → `version`
- 工作区 `state/installed.json` → 对应版本
- 启动后窗口「电力工作工具箱」可见，`toolbox-service.exe` 与载荷内 Python 正常拉起

## 版本号规则

**Windows 版本号跟随 macOS 版本号**：Windows `0.1.32` 对应 macOS `v0.1.32`。

- 正常一轮对齐：mac 发 `x.y.z` → Windows 发同一个 `x.y.z`。
- 同一 mac 基线的 Windows 专属修补（只能在 Windows 复现的缺陷、打包或更新通道问题）需要再次发布时，版本号递增，并在 `CHANGELOG.md` 标注「对应 mac vX.Y.Z + Windows 修补」。
- 跨主／次版本或结构性调整用 `-FullOnly` 重设补丁基线，并在发布说明中提示用户完整升级一次。
- 版本号唯一来源是 `packaging/tauri/version.py`，不要手改散落的版本字段：

```bash
.venv/Scripts/python.exe packaging/tauri/version.py set 0.1.33 --notes "具体变更"
.venv/Scripts/python.exe packaging/tauri/version.py check --tag v0.1.33 --require-notes
```

`set` 会同步 `desktop/package.json`、`desktop/src-tauri/tauri.conf.json`、`desktop/package-lock.json`、`Cargo.toml`、`Cargo.lock` 并写入 `CHANGELOG.md`；已存在的版本号会被拒绝，需要递增。

## 环境维护

### 保留什么

| 类型 | 处理 |
| --- | --- |
| `.venv/`、`runtime/`、`dist/`、`desktop/src-tauri/target/`、`native/price-xlsx/target/` | 本机保留，Git 忽略 |
| `.toolbox/`（源码调试数据、独立 Python 运行时） | 本机保留，Git 忽略 |
| `dist/windows/` 历史安装包 | 只保留最近几个版本作为补丁基准，其余可回收 |
| 登录态、`.browser-profile`、`config.json`、真实工作簿 | 保留在 Git 忽略的本地路径，绝不入库、不写入日志 |

### 定期清理（参考 mac 版 `tooling/clean_workspace.py` 的做法，先干跑再执行）

1. 列出候选并核对体积，确认后才删。
2. `dist/windows/` 按白名单处理：保留最近 N 个安装包及其 `.sha256`、`updates-*/` 索引；发布日志、一次性脚本、旧版本暂存目录可回收。
3. `desktop/src-tauri/payload/` 只留可复用的 `runtime/`；`app`、`defaults`、`bootstrap`、清单文件每次构建都会重建。
4. `logs/` 顶层按日期归档到 `logs/release-archive/`，只移动不删除。
5. 退出工具后确认没有遗留 `wps-chrome.pid`、`auth_state.json.lock` 与浏览器 profile 残留进程。

### 运行态卫生

- 同一份源码不要同时开两个工具箱实例（一个 `.venv`、一个安装包 runtime 会互抢配置与锁）。
- 自动化浏览器用完即退，避免 headless Chrome 长期占用 `9222` 端口与 profile 目录。
- 调试版与正式安装版的数据目录不同，排查问题时先确认当前实例用的是哪一个。

## 每轮检查表

- [ ] 上游基线已记录（`docs/upstream-review-<日期>.md`）
- [ ] `CHANGELOG.md` 已写明版本、变更与验证结果
- [ ] `version.py check --tag v<版本> --require-notes` 通过
- [ ] 针对性测试 + `start_source_test.ps1 -Smoke` 通过
- [ ] `git status --short` 只含本次改动，敏感文件未被跟踪
- [ ] 安装包与增量索引均已生成，`.sha256` 已核对
- [ ] Release 附件齐全（完整包 + 索引整套）
- [ ] 静默安装后三处版本回读一致
- [ ] 源码已推送到 `origin/development`，远端提交已回读确认

# Windows 维护流程

长期规则见根目录 [AGENTS.md](../AGENTS.md)，目录与调用关系见 [project-structure.md](project-structure.md)。
本文件只维护操作流程。开发、提交同步、正式发行分别完成，不要求每次修复走完整发行链路。

## 1. 日常开发：修改与验证

1. 运行 `git status --short`，保留已有修改；按结构地图查调用方、GUI、测试及载荷规则。
2. 只修改当前任务所需内容；允许短期 `codex/*` 分支，main 是唯一长期集成主线。
3. 按风险验证：文档检查链接与命令；界面做页面 smoke；计算／解析／映射跑针对性模块和样本；启动、登录、写入、并发做隔离端到端验证。
4. 用户可见变化记入 `CHANGELOG.md` 的 `[Unreleased]`；内部整理无需编造更新条目。需要保留的诊断与验证写入 `docs/reviews/`，不修改已发布版本的验证记录。
5. 汇报改动、验证与未验证部分。普通修复到此完成，不自动打版本、打包、提交推送或发布。

```powershell
.venv/Scripts/python.exe tools/run_tests.py
.venv/Scripts/python.exe tools/run_tests.py --module test_date_inputs
powershell -ExecutionPolicy Bypass -File start_source_test.ps1 -Smoke
```

测试目录与分组只在统一入口注册；CI 调用入口。新测试用模块顶层 `TOOLBOX_TEST_TAGS = ()`、
`("gui",)`、`("network",)` 或 `("gui", "network")` 声明真实需求；Mock 网络不算联网。
旧测试的启发式分类作为迁移兼容保留，不把 fast 通过当作全量证明。full 的模块内 skip 仍须审阅。

源码调试数据位于 `.toolbox/source-test/data`，日志位于 `logs/source-test`；正式使用运行
`start_windows.ps1`。隔离数据目录不意味着可以自动进行生产上传；真实账号、工作簿和上传按用户授权范围执行。
各工具的登录态、锁与 profile 始终独立。

## 2. 提交与同步：用户要求时执行

- 只暂存本任务相关文件或可可靠拆分的差异；混合改动不得整体代提交。
- 记录验证结论，失败或环境阻塞明确说明；未验证改动不伪装为可发行。
- 推送至相应工作分支或 main；同步完成后回读对应远端提交。main 不另设长期 development 分支。
- 提交同步不等于发布；无需为了同步源码先提高应用版本。

## 3. 上游对齐：需要时执行

上游 `a792275829-cpu/electricity-toolbox-scripts` 是功能参考，不是 Windows 版本号来源。
逐项选择功能与修复，不整体合并 macOS 实现。每轮在 `docs/reviews/upstream-review-<日期>.md`
记录上游版本、提交、已采用项与未采用项；上游发版不强制 Windows 跟随发版。

## 4. 正式发行：完整检查表

以下阶段只在用户要求发行时执行，所有验收通过后才公开 Release。

- [ ] 整理 Unreleased 中已完成的变化，选择严格大于当前版本且未发布的 Windows `X.Y.Z`。上游基线另外记录。
- [ ] 执行 `version.py set X.Y.Z --notes "具体变更"` 同步元数据；多条重复传 `--notes`。移除已转入版本条目的草稿，未完成草稿保留。条数按实际变化，不凑数。
- [ ] 在 `docs/releases/vX.Y.Z.md` 记录验收范围、环境与结果；不把历史记录当作本次验证证据。
- [ ] 执行版本／说明检查、`full --strict`、Rust 检查及源码 smoke。模块内 skip 审阅为 Windows 不适用才可接受，必需能力缺测即阻塞发行。
- [ ] 将发行所需修改提交并同步 main，记录提交号；从该提交的干净检出构建。正式构建拒绝未提交／未跟踪改动，不把本地业务工作簿作为种子。
- [ ] 构建 NSIS、摘要及增量索引；保存 `source-X.Y.Z.json`，核对载荷 `manifest.json` 的来源提交一致。正式与开发构建输出隔离。
- [ ] 在隔离验收环境完成全新安装、旧版覆盖升级、旧版副本补丁实际还原；核对完整文件、签名、配置与业务数据保留。安装目录不得指向用户日常工作环境。
- [ ] 回读载荷 `manifest.json`、工作区 `state/installed.json` 的版本；实际启动 Tk 窗口、Rust 服务与内置 Python。失败则修复后重新验收，不公开产物。
- [ ] 验收通过后为构建提交建立 `vX.Y.Z` 标签，并回读远端对应关系；手工创建 Release，上传完整安装包、摘要、`updates-X.Y.Z/` 全部附件及来源记录。
- [ ] 发布后核对 GitHub 附件集合、摘要与真实下载链路。已公开资产不覆盖；有问题暂停该版本分发，修复使用新版本。

```powershell
.venv/Scripts/python.exe packaging/tauri/version.py check --tag vX.Y.Z --require-notes
.venv/Scripts/python.exe tools/run_tests.py full --strict
powershell -ExecutionPolicy Bypass -File start_source_test.ps1 -Smoke
powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -PythonRoot <独立CPython目录>
# 跨主／次版本或需要重设补丁基线时添加 -FullOnly。
```

PythonRoot 是 `.python-version` 指定的独立 Windows x64 CPython，不能是 `.venv`。
正式产物位于 `dist/windows/`；开发验证显式添加 `-DevelopmentBuild`，允许工作区变化，产物进入
`dist/windows-development/`，不生成正式补丁，禁止发布。`-ForecastWorkbook` 仅允许开发验证。
生成目录仍由现有构建逻辑归档后重建；依赖与资源准备方式见 [Windows 打包说明](../packaging/windows/README.md)。

CI 的 `maintain-*.yml` 是兼容转发入口，不自动创建 Release。无标签的手动安装包构建只生成开发包；
带标签构建固定到该标签提交。正式构建与公开发布是两回事，标签不自动触发发布。
本地可先按提交构建和验收再打标签；CI 可先建标签构建，但仍须验收后才公开 Release。

## 5. 环境、迁移与回收

- 源码复制／移动后先保留旧 `.venv`，按 `.python-version` 重新初始化；不手改 `pyvenv.cfg`。
- `.toolbox/standalone-python` 可能是虚拟环境的基础解释器，`runtime/node` 是上传子工具的便携运行时；清理前查实际引用。
- 用户配置、登录态、真实工作簿只存 Git 忽略的本地路径，迁移时单独安全处理；新电脑重新登录。
- 默认不同时运行共享同一数据目录的多个实例。调试版与正式版使用独立数据目录可以并行；锁冲突不得靠删除仍在使用的锁解决。
- 构建保留会话安全守卫；环境阻塞保留第一条异常，不自动移除注入。`-KeepGuard` 作为旧参数兼容保留。
- 构建成功可用 `-PruneArchive` 回收旧快照、保留最近一份；失败现场先保留。手工清理默认仅预览，加 `--apply` 执行，拒绝仓库归档目录以外路径及链接／重解析点。
- 每轮大构建后检查空间；若需长期保留诊断，先归档到明确的诊断位置再清理。历史正式安装包至少保留当前增量基线所需版本；远端正式附件不随本地缓存清理。
- 日志按实际排障需要限期保留，无需永久只移动不删除；真实数据与凭据不按缓存策略回收。

```powershell
.venv/Scripts/python.exe packaging/windows/prune_build_archive.py --keep 1
.venv/Scripts/python.exe packaging/windows/prune_build_archive.py --keep 1 --apply
```

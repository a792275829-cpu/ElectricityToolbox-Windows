# Windows 安装与增量更新

Windows 10 / 11 x64 使用同一套原版 Tk 页面和独立的 Windows 更新通道。
0.1.28 引入文件增量更新；0.1.27 及更早版本需要最后完整升级一次。
之后点击软件中的更新按钮，优先下载总大小最小的可用补丁路径。

日常发布同时保留完整 NSIS 安装包、SHA-256、最近三个版本的直达补丁及
`windows-update-manifest.json`。历史补丁继续保留在原 Release，索引保留其
升级路径。跨主／次版本或 `build.ps1 -FullOnly` 重设基线。
本仓库仅构建和验证 Windows 发行附件。

更新时在安装目录旁建立完整临时目录，使用 NTFS 硬链接复用未改变的文件，
仅解压变化文件；不支持硬链接时自动复制。旧版与新版均按完整文件清单校验，
旧进程退出后由 Rust 助手切换目录并启动新版。失败恢复旧程序与卸载信息。
配置、登录态与业务数据继续保存在独立用户目录。

补丁缺失、摘要错误、旧程序被修改或准备失败时自动使用完整安装包；用户取消
不会触发完整下载。切换后失败会恢复旧版，同一目标版本下次重试改用完整包。
下载过程中连接短暂中断最多重试两次，可续传时接着下载；服务器忽略 Range
时安全重下，最后必须通过完整 SHA-256 校验。

构建入口 `packaging/windows/build.ps1` 会构建完整包、下载并校验历史包、
在隔离目录安装后制作及实际还原补丁，输出 `dist/windows/updates-X.Y.Z/`
（含 `windows-update-manifest.json` 及其 `.sha256`）；构建结束会校验该索引
确实生成，缺失即报错。
完整安装包和该目录内的所有附件一起上传到对应 Release；不可只上传补丁。
历史发布列表优先走 `gh` CLI，未安装时改用 GitHub REST API；两者都不可用时
只用本地历史安装包继续，不会中断打包。`dist/windows/` 下已有的同系列旧安装包
会被自动当作基准（免下载），但已发布版本的补丁路径优先占位，不会被未发布的
本地包挤掉；最近三个同系列版本仍然按 `limit` 截断。
已安装基准可通过 `build_updates.py --base-root PATH` 复用，显式指定安装包用
`--base-installer PATH`；显式提供的基准也必须来自完整安装包。
制作补丁时不得使用用户日常安装目录作为输出目录。

本地验证示例：

```powershell
.venv/Scripts/python.exe packaging/windows/build_updates.py D:/验收安装目录 `
  --base-root D:/旧版验收目录 --no-fetch-history --output dist/windows
.venv/Scripts/python.exe tools/run_tests.py --group desktop
```

补丁清单拒绝路径穿越、Windows 设备名／ADS、大小写或 Unicode 冲突、链接和
重解析点；运行助手只等待本次宿主、界面和后台三个已核对路径的进程，不扫描
或强制结束其他程序。安装到受保护目录导致无法创建同级临时目录时，使用完整
安装程序处理权限问题。

## 构建来源与开发验证

正式构建先检查干净 Git 工作区，仅导出已跟踪源码。载荷 `manifest.json` 的 `source` 和
`dist/windows/source-X.Y.Z.json` 记录提交号及开发标记。正式构建禁止携带本地业务总表。
这固定了源码边界，不宣称整个依赖下载链已经达到逐字节可复现。

未提交改动的验证使用 `build.ps1 -DevelopmentBuild -PythonRoot <目录>`；产物进入
`dist/windows-development/`，不制作正式增量附件，禁止上传 Release。
完整的提交、验收、公开顺序见 [维护流程](../../docs/windows-maintenance-model.md)。

## 构建归档与回收

`payload/app`、`payload/runtime/python`、`desktop/node_modules` 替换前改名归档到
`dist/build-archive/<时间戳>/`。构建默认保留调用环境与安全守卫，`-KeepGuard` 仅兼容旧调用；
不再默认调用历史 `Reset-BuildEnvironment`。遇到环境阻塞须报告，不通过其他删除通道绕过。

- `build.ps1 -DryRun` 或 `prepare_payload.py --dry-run` 预览归档，不改文件。
- 清理默认只预览：`prune_build_archive.py --keep 1`；显式加 `--apply` 执行。
- `build.ps1 -PruneArchive` 在构建成功后执行回收，保留最近一份；构建前先预览核对清理边界，失败现场保留至诊断结束。
- 回收只允许本仓库 `dist/build-archive/` 内、时间戳命名的快照，拒绝链接和重解析点，失败直接报错。
- 自定义 `-ArchiveRoot` 可用于构建诊断；若位于仓库归档树外，统一清理工具会拒绝，不能与 `-PruneArchive` 联用。
- 每轮大构建后检查归档体积并预览回收；日志按排障需要限期保留，用户数据不套用缓存回收规则。

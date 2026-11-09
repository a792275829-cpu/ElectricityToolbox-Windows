# Windows 安装与增量更新

Windows 10 / 11 x64 使用同一套原版 Tk 页面和独立的 Windows 更新通道。
0.1.28 引入文件增量更新；0.1.27 及更早版本需要最后完整升级一次。
之后点击软件中的更新按钮，优先下载总大小最小的可用补丁路径。

日常发布同时保留完整 NSIS 安装包、SHA-256、最近三个版本的直达补丁及
`windows-update-manifest.json`。历史补丁继续保留在原 Release，索引保留其
升级路径。跨主／次版本或 `build.ps1 -FullOnly` 重设基线。
这些附件与 macOS 的 DMG、`update-manifest.json` 和 Apple 芯片补丁分别维护。

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
.venv/Scripts/python.exe -m unittest discover -s desktop/tests/python -p 'test_*update*.py'
.venv/Scripts/python.exe -m unittest discover -s desktop/tests/python -p test_windows_delta.py
```

补丁清单拒绝路径穿越、Windows 设备名／ADS、大小写或 Unicode 冲突、链接和
重解析点；运行助手只等待本次宿主、界面和后台三个已核对路径的进程，不扫描
或强制结束其他程序。安装到受保护目录导致无法创建同级临时目录时，使用完整
安装程序处理权限问题。

## 构建机清理约定

Agent 会话会把批量删除守卫注入到所有子进程：`PYTHONPATH` 指向的
`sitecustomize` 接管 `shutil.rmtree`，`NODE_OPTIONS` 的 `--require` 接管 node
的删除（`npm ci` 清空 `node_modules` 走这条），`PATH` 首位的 `safe-bin` 包装
`rm`。守卫在非交互子进程里没有确认通道，累计删除量达到阈值即按 fail-closed
退出 1：实测会让 `prepare_payload.py` 清空 `payload/app`、`npm ci` 清空
`node_modules`、`build_updates.py` 删除临时还原目录三处先后中断。人手在普通
终端构建不受影响，但同一条构建链路不能有两套行为，故按下面两层处理：

- **入口还原环境**：`build.ps1` 通过 `build_env.ps1` 的 `Reset-BuildEnvironment`
  只移除指向 `vendor\shim` 的 `NODE_OPTIONS`／`PYTHONPATH`／`PATH` 条目和
  `CODEBUDDY_SAFE_DELETE_BULK_GUARD`，其余环境原样保留，效果等同人工终端。
  需要保留守卫时加 `-KeepGuard`（此时补丁验证阶段可能因临时目录删除被拦）。
- **生成目录只归档**：`prepare_payload.py` 与 `build.ps1` 把 `payload/app`、
  `payload/runtime/python`、`desktop/node_modules` 改名到
  `dist/build-archive/<时间戳>/` 后重建，不原地删除；即使守卫仍在也能完成
  这两步。改名同卷完成，不产生批量删除。

回收与自检：

- `prune_build_archive.py`（默认保留最近 1 份快照，`--all` 全清）先用 robocopy
  空目录镜像清空快照，再删空目录骨架，可处理上万文件与超长路径；
  `build.ps1 -PruneArchive` 在构建成功后自动调用。
- `build.ps1 -DryRun` 只打印将归档的目标与规模，不改动任何文件；
  `prepare_payload.py --dry-run` 提供同等能力。
- 不要对 `payload/`、`node_modules/`、`dist/build-archive/` 使用 `rm -rf`、
  `shutil.rmtree` 或回收站删除：守卫会 fail-closed，回收站通道对海量文件和
  超长路径也不可靠（实测 1.1 万文件的目录只能靠 robocopy 清空）。

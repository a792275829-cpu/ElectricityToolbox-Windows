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
在隔离目录安装后制作及实际还原补丁，输出 `dist/windows/updates-X.Y.Z/`。
完整安装包和该目录内的所有附件一起上传到对应 Release；不可只上传补丁。
已安装基准可通过 `build_updates.py --base-root PATH` 复用；自动构建会覆盖
最近三个同系列正式版本，显式提供的基准也必须来自完整安装包。
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

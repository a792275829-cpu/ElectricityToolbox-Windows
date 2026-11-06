## Windows 下载安装

Windows 10 / 11 x64 下载 `ElectricityToolbox_<版本>_windows_x64_setup.exe` 后安装，已包含 Python、Node、Chromium 与 Tcl/Tk，无需另装运行环境。同名 `.sha256` 可核对完整安装包。

应用内点击更新按钮后自动完成下载、校验、退出、安装与重启。0.1.28 起日常优先下载 Windows 小补丁并复用本机运行环境；补丁不可用时自动回退完整安装包，安装失败恢复旧程序。0.1.27 及更早客户端需最后完整升级一次，之后才能使用增量更新。配置、登录态和业务数据继续保存在原用户目录。

`windows-update-manifest.json` 与 `windows-x64.patch.tar.xz` 附件由软件自动选择和校验，无需手动解包；全新安装仍下载完整 EXE。Windows 不使用 macOS 的 DMG、`update-manifest.json` 或 Apple 芯片补丁。源码压缩包用于开发，不是安装包。

## macOS 下载安装

仅支持 macOS 14+、Apple 芯片。

下载 `ElectricityToolbox-<版本>-apple-silicon.dmg`，打开后先退出旧工具箱，再双击“安装到我的应用程序.command”。应用安装到 `~/Applications/电力工具箱.app` 并自动打开，也可手动拖入个人用户目录的 Applications 文件夹。

后续在应用内点击“开始更新”，自动完成下载、校验、退出、安装和重新启动，无需继续操作。日常优先使用增量补丁，多版本自动选择下载量较小的升级路径；重大更新或补丁不适用时自动使用完整 DMG。补丁在临时目录还原并校验完整应用，再生成安装镜像；自动安装继续使用原有回滚逻辑，配置、登录态、业务数据和样例保留，不请求管理员权限。0.1.12 及更早更新器仍需按旧提示完成一次升级，之后才能全自动更新。

0.1.6 及更早 PKG 版需要从发布页面下载本 DMG 迁移一次，账号、登录态、业务数据和样例继续保留在原用户数据目录。旧 `/Applications` 副本不会自动删除，确认新版正常后可手动清理，并更新旧 Dock／桌面快捷方式。

首次运行仍可能需要按 macOS“隐私与安全性”提示允许。

`.dmg.sha256` 是完整 DMG 的校验文件；`update-manifest.json` 和 `.patch.tar.xz` 供应用内增量更新使用，无需手动解包。不分发独立 App 压缩包、`.sig` 或旧 `latest.json`；源码附件无需安装。

已内置 Python、Node、Chromium 与 Tcl/Tk。

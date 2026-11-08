## Windows 下载安装

Windows 10 / 11 x64 下载 `ElectricityToolbox_<版本>_windows_x64_setup.exe` 后安装，已包含 Python、Node、Chromium 与 Tcl/Tk，无需另装运行环境。同名 `.sha256` 可核对完整安装包。

应用内点击更新按钮后自动完成下载、校验、退出、安装与重启。0.1.28 起日常优先下载 Windows 小补丁并复用本机运行环境；补丁不可用时自动回退完整安装包，安装失败恢复旧程序。0.1.27 及更早客户端需最后完整升级一次，之后才能使用增量更新。配置、登录态和业务数据继续保存在原用户目录。

`windows-update-manifest.json` 与 `windows-x64.patch.tar.xz` 附件由软件自动选择和校验，无需手动解包；全新安装仍下载完整 EXE。源码压缩包用于开发，不是安装包。

macOS 版是独立仓库与独立发布通道，本仓库只发布 Windows x64 安装包。

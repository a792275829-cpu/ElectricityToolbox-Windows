# 电力工具箱 · Windows 版

[进入最新版本下载页](https://github.com/QiaoZhang013/electricity-toolbox-rust-windows/releases/latest)

## 下载与安装

1. 打开上面的最新版本页面，展开 **Assets（附件）**。
2. 下载文件名以 `windows_x64_setup.exe` 结尾的安装包。
3. 下载完成后，在 Windows 文件资源管理器中双击安装；完成后打开“电力工具箱”。

完整 Windows x64 安装包已包含程序运行环境，无需另装 Python、Rust 或 Node.js。GitHub 自动生成的 `Source code` 压缩包用于开发，不是可直接运行的安装包。同名 `.sha256` 附件可用于核对下载文件。

首次使用时，按工具页面提示选择业务文件，并配置自己的业务网站账号。需要联网、登录或业务模板的功能，仍需具备相应条件。

## 后续更新

升级前退出旧工具箱，并备份自己的配置与业务数据。安装包用于更新程序；请保留原有数据目录。

已安装本仓库版本的用户，可在软件中点击“检查更新”。0.1.28 起日常更新优先下载小补丁并复用现有运行环境，补丁不适用时回退完整安装包；0.1.27 及更早版本需最后完整升级一次。早期使用旧 GitHub 更新地址的版本，需要先从最新版本页面手动安装一次，再使用后续更新。

[查看更新日志和历史版本](https://github.com/QiaoZhang013/electricity-toolbox-rust-windows/releases)

## 开发与维护

`main` 提供 Windows 安装说明与 Actions 入口；[已公开源码和开发文档在 `development` 分支](https://github.com/QiaoZhang013/electricity-toolbox-rust-windows/tree/development)。每个版本的修复、验证范围和发行文件，以相应发布页面说明为准。

基于 [原电力工具箱项目](https://github.com/a792275829-cpu/electricity-toolbox-scripts)，保留原版界面及作者声明。

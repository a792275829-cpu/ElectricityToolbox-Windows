# Windows 0.1.30 发布前验证

电力工具箱 Windows 0.1.30 发布前验证报告
日期：2026-11-05（Asia/Shanghai）

完整安装：657 个发行程序文件及 657 个工作区文件一致；原版 Tk 主窗口、Rust 后台、15 个工具页面通过。
包内测试：{"installed-refresh.log": 26, "installed-delta-core.log": 14, "installed-update-detection-ui.log": 16, "installed-windows-update.log": 19, "installed-compat.log": 5, "installed-browser-lifetime.log": 3}
源码针对性检查：102 项及 16 项 Windows 原生助手真实事务通过。
Windows CI：https://github.com/QiaoZhang013/electricity-toolbox-rust-windows/actions/runs/42015169944

未修改的正式 0.1.29 更新器：使用最终生产补丁完成本地端到端升级，模式 delta，自动安装并重启。
0.1.29→0.1.30 补丁：2756260 字节；索引：4119 字节。
完整包：340322412 字节；SHA-256：d3306c5f4fd7b252f9ec7bc53177311ac5663cd17c9ccbff14aa70aff901d889
升级检查：657 个程序文件、11 份隔离配置／数据样本保持；Excel 0.125 与数字格式 0.00% 回读通过；运行环境复用、缓存清理、快捷方式与注册表保留。耗时 195.08 秒。
0.1.27／0.1.28／0.1.29 三份直达补丁均用真实旧安装还原并完整核对；历史升级路径保留。
更新后的官方卸载程序已在一次性副本验证，清理程序并保留额外本地文件。

边界：本报告于公开发布前生成，公网下载与升级将在发布后另行核对。Windows 11 实机已测；Windows 10 与 macOS 实机未验证。未执行真实网站上传或 WPS 文档写入。
补丁要求原有程序文件匹配；路径缺失或校验失败时仍保留完整包回退。不会覆盖用户配置或业务数据。


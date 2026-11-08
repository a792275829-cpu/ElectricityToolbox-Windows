电力工具箱 · 目录说明（Windows 版）

本仓库维护 Windows x64 版。macOS 上游在独立仓库
a792275829-cpu/electricity-toolbox-scripts，本仓库只按功能逐项对齐它，
不合并它的打包与发布流程。

日常入口（都在 00_启动/ 下）
- Windows初始化.cmd    首次准备环境：建 .venv、装依赖
- Windows启动.cmd      启动工具箱
- 源码测试版.cmd       直接用当前源码启动原版 Tk 界面，不启动桌面外壳
- 源码测试版-自检.cmd  校验解释器与依赖

打包与发布
- Windows 打包、增量更新与发版细则：packaging/windows/README.md
- 每轮对齐 mac → 打包 → 发布 → 安装回读的完整链路：docs/windows-maintenance-model.md

文件与目录
- 仓库结构地图，含哪些目录能动能改名：docs/project-structure.md
- 全部文档索引：docs/README.md

约定
- 业务模块、模板和已有数据维持各自目录，不随意搬迁
- 账号配置、独立登录态、浏览器 profile、缓存和输出不进入版本控制
- 根目录 toolbox_*.py 是打包时平铺到载荷根的共享模块，不要移动、合并或改名
- 00_启动 下的 .command 是 macOS 历史入口，保留但不维护

改代码前先读 AGENTS.md。

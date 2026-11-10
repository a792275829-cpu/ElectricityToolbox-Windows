# Windows 桌面工程

目标为 Windows 10 / 11 x64。原版 Tk 负责业务界面，Rust/Tauri 负责单实例、托盘和进程生命周期，Rust 任务服务执行后台任务。React 页面只用于启动故障提示。

## 运行与调试

从仓库根目录执行 `setup_windows.ps1` 初始化，`start_windows.ps1` 正式启动，`start_source_test.ps1` 源码调试。
Python 精确版本读取根目录 `.python-version`，解释器为 `.venv/Scripts/python.exe`；依赖转引根目录 `requirements.txt`。
源码调试数据位于 `.toolbox/source-test/data`，与安装版隔离。修改 Rust 后运行：

```powershell
.venv\Scripts\python.exe native/price-xlsx/build.py
```

## 构建与发布

使用 `packaging/windows/build.ps1 -PythonRoot <独立 CPython 目录>` 构建 NSIS 安装包与摘要、增量索引及补丁。
构建前严格检查 Python 版本、应用版本和更新日志；生成目录归档到 `dist/build-archive/` 后重建。
CI 和历史兼容入口只运行 Windows 检查与构建，不创建 Release；打 tag 不触发发布，发行附件手动上传。
已发布版本不可覆盖，下一次发布通过版本工具递增，具体操作见 [维护模型](../docs/windows-maintenance-model.md)。

## 验证

```powershell
.venv\Scripts\python.exe tools/run_tests.py --group desktop
powershell -NoProfile -ExecutionPolicy Bypass -File start_source_test.ps1 -Smoke
```

每个测试模块独立进程与超时。真实安装、增量还原、账号登录和业务写入按维护约定单独验证。

- [Windows 使用说明](WINDOWS.md)
- [架构](docs/architecture.md)
- [运行目录](docs/directory-layout.md)
- [历史验证入口](WINDOWS_VALIDATION.md)
- [打包与更新细则](../packaging/windows/README.md)

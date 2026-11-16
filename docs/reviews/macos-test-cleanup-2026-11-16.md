# macOS 测试清理记录 — 2026-11-16

按用户要求删除 macOS 专用测试。仅调整测试及其专用辅助文件，保留业务实现、Windows 测试和现有未提交修改。

## 删除范围

删除 11 个文件：5 个 macOS 专用测试模块、2 个 Rust RPC 测试入口、1 个本地测试应用构建辅助脚本、3 个签名测试夹具。

- `desktop/tests/python/test_auto_install.py`
- `desktop/tests/python/test_delta_update.py`
- `desktop/tests/python/test_dmg_install.py`
- `desktop/tests/python/test_installer_image.py`
- `desktop/tests/python/test_pkg_installer.py`
- `desktop/tests/python/update_rpc_e2e_entry.py`
- `desktop/tests/python/update_rpc_production_entry.py`
- `packaging/tauri/build_update_test.py`
- `desktop/tests/fixtures/signed-update.pub`
- `desktop/tests/fixtures/signed-update.app.tar.gz.sig`
- `desktop/tests/fixtures/signed-update.app.tar.gz`

另从混合文件中移除 macOS 专用测试，包含 Mac Python/Node 运行时、目录打开、`.command` 启动器、Chrome 应用包与 PID 恢复、签名发行索引及打包目标检查；删除 Rust 中的 2 个 Mac 更新端到端测试和专用辅助函数。原 DMG GitHub 更新测试模块仅保留共享版本解析测试。合计删除 60 个测试函数，另移除 Windows 更新器测试中的 Mac 分支。

保留共享载荷导出、集团上传进程与日志恢复、Chrome 重启等 Windows 仍需要的测试；去除其 Mac 专用断言或改用 Windows 浏览器样例。更新界面测试的安装包样例改为 `.exe`。Windows 拒绝 `.dmg`、不访问 Mac 更新通道的防误用测试继续保留。

测试入口清单检查改为检查当前存在的已跟踪及未跟踪源码测试，避免把本次待提交删除的文件误报为未接入；平台排除机制的测试改用 Linux 模拟，保留通用入口能力。历史评审和发布记录不改写。

## 验证

- 首轮 9 个模块中 8 个通过；清单检查因待提交删除文件误报失败，修正后复跑通过。
- 合并首轮与最终复跑：13 个模块、251 条 Python 用例全部通过，0 跳过、0 不适用。
- Rust 宿主：`cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked`，3 条通过。
- 当前 358 个 Python/pyw 源码文件 AST 解析通过，`git diff --check` 通过。
- 已删除文件无残留代码引用。测试目录中剩余 macOS 文本均为 Windows 防误用检查或历史入口说明，不再有 macOS 专用用例。
- 本轮未重复执行完整测试集，未打包、提交、推送或执行生产上传。

验证产物位于仓库上一级：`macos-test-cleanup-effective-results.json`、`macos-test-cleanup-tests.log`、`macos-test-cleanup-rerun.log`、`macos-test-cleanup-rust.log` 和 `macos-test-cleanup-inventory.json`。

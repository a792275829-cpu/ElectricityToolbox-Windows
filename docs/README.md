# 文档索引

先看用途再翻文件；标「历史」的只作追溯，不代表当前行为。

## 当前有效

| 文档 | 用途 | 什么时候看 |
| --- | --- | --- |
| [`project-structure.md`](project-structure.md) | 仓库文件地图、工具页与目录对应、改动守则 | 找文件、判断能不能动 |
| [`windows-maintenance-model.md`](windows-maintenance-model.md) | 常态维护六环节：对齐 mac → 打包 → 发布 → 安装验证 | 每轮对齐与发版 |
| [`RUST_MIGRATION.md`](RUST_MIGRATION.md) | 哪些计算迁到 Rust、边界与验证口径 | 改计算、判断该走 Rust 还是 Python |
| [`upstream-review-2026-11-05.md`](upstream-review-2026-11-05.md) | 上游 mac 仓库比对基线与逐项取舍 | 下一轮对齐前确认起点 |

根目录另有：

- [`../AGENTS.md`](../AGENTS.md) — 维护约定（改动边界、验证策略、发布规则），改代码前必读
- [`../CHANGELOG.md`](../CHANGELOG.md) — 逐版本记录，版本说明的唯一来源
- [`../README.md`](../README.md) — 使用者文档（安装、配置、功能操作）
- [`../desktop/WINDOWS.md`](../desktop/WINDOWS.md) — 安装包使用边界与源码运行说明
- [`../packaging/windows/README.md`](../packaging/windows/README.md) — Windows 打包与增量更新细则

## 历史记录

只用于追溯当时的决策与验证，不要据此判断当前实现。

| 位置 | 内容 |
| --- | --- |
| [`code-review-0.1.31.md`](code-review-0.1.31.md) | 0.1.31 的代码审查记录 |
| [`releases/windows-0.1.30-verification.md`](releases/windows-0.1.30-verification.md) | 0.1.30 实机验证记录 |
| [`releases/windows-0.1.31-build-checklist.md`](releases/windows-0.1.31-build-checklist.md) | 0.1.31 构建与发布清单 |
| [`superpowers/plans/`](superpowers/plans/) | 各轮改造的实施计划（自动更新、增量更新、Rust 迁移、0.1.30 发布等） |
| [`superpowers/specs/`](superpowers/specs/) | 对应的设计说明 |

## 版本与发布文档的边界

- `CHANGELOG.md` 是逐版本内容的唯一来源，`packaging/tauri/release_notes.md` 只放通用安装说明；发布说明由 `version.py` 拼接生成。
- 历史发布的验证记录留在 `docs/releases/`，不要改写；新版本的记录直接写进 `CHANGELOG.md`。

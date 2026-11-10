# 文档索引

先看用途再翻文件；标「历史」的只作追溯，不代表当前行为。

## 当前有效

| 文档 | 用途 | 什么时候看 |
| --- | --- | --- |
| [`project-structure.md`](project-structure.md) | 仓库文件地图、工具页与目录对应、改动守则 | 找文件、判断能不能动 |
| [`windows-maintenance-model.md`](windows-maintenance-model.md) | 分阶段流程：开发、同步、上游对齐、发行前验收与回收 | 按任务阶段查阅 |
| [`RUST_MIGRATION.md`](RUST_MIGRATION.md) | 哪些计算迁到 Rust、边界与验证口径 | 改计算、判断该走 Rust 还是 Python |

根目录另有：

- [`../AGENTS.md`](../AGENTS.md) — 维护约定（改动边界、验证策略、发布规则），改代码前必读
- [`../CHANGELOG.md`](../CHANGELOG.md) — 逐版本记录，版本说明的唯一来源
- [`../README.md`](../README.md) — 使用者文档（安装、配置、功能操作）
- [`../desktop/WINDOWS.md`](../desktop/WINDOWS.md) — 安装包使用边界与源码运行说明
- [`../packaging/windows/README.md`](../packaging/windows/README.md) — Windows 打包与增量更新细则

## 历史记录

只用于追溯当时的决策与验证，不要据此判断当前实现。一次性评审、比对、验证记录统一放
`docs/reviews/` 与 `docs/releases/`，不要放在 `docs/` 根目录。

| 位置 | 内容 |
| --- | --- |
| [`reviews/code-review-0.1.31.md`](reviews/code-review-0.1.31.md) | 0.1.31 的代码审查记录 |
| [`reviews/code-review-fixes-2026-11-09.md`](reviews/code-review-fixes-2026-11-09.md) | 2026-11-09 审查问题的修复记录 |
| [`reviews/repository-conventions-2026-11-10.md`](reviews/repository-conventions-2026-11-10.md) | 本轮仓库约定扫描、整改及验证记录 |
| [`reviews/maintenance-policy-2026-11-10.md`](reviews/maintenance-policy-2026-11-10.md) | 维护流程分层、版本解耦、构建与回收门禁的调整及验证 |
| [`reviews/upstream-review-2026-11-05.md`](reviews/upstream-review-2026-11-05.md) | 最新上游比对基线 |
| [`releases/v0.1.38.md`](releases/v0.1.38.md) | 0.1.38 的发行验收范围、环境与结果 |
| [`releases/v0.1.37.md`](releases/v0.1.37.md) | 0.1.37 的发行验收范围、环境与结果 |
| [`releases/v0.1.36.md`](releases/v0.1.36.md) | 0.1.36 的历史实现与验证细节 |
| [`releases/v0.1.35.md`](releases/v0.1.35.md) | 0.1.35 的历史实现与验证细节 |
| [`releases/windows-historical-verification.md`](releases/windows-historical-verification.md) | 早期 Windows 实机验证原始记录 |
| [`reviews/`](reviews/) | 历次评审与上游比对快照（按日期命名） |
| [`releases/windows-0.1.30-verification.md`](releases/windows-0.1.30-verification.md) | 0.1.30 实机验证记录 |
| [`releases/windows-0.1.31-build-checklist.md`](releases/windows-0.1.31-build-checklist.md) | 0.1.31 构建与发布清单 |
| [`superpowers/plans/`](superpowers/plans/) | 各轮改造的实施计划（自动更新、增量更新、Rust 迁移、0.1.30 发布等） |
| [`superpowers/specs/`](superpowers/specs/) | 对应的设计说明 |

## 版本与发布文档的边界

- `CHANGELOG.md` 是逐版本内容的唯一来源，`packaging/tauri/release_notes.md` 只放通用安装说明；发布说明由 `version.py` 拼接生成。
- `CHANGELOG.md` 的版本条目直接作为用户可见的更新说明：用户可见变化先记 Unreleased，发行时按实际变化整理，不限定条数；实现细节、单测条数、耗时对比写进 `docs/releases/vX.Y.Z.md`，不要塞进 CHANGELOG。
- 历史发布的验证记录留在 `docs/releases/`，不要改写；新版本的记录按版本号新建 `docs/releases/vX.Y.Z.md`。

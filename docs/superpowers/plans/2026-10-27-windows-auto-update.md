# Windows automatic update implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship Windows full-installer updates and correct log versions as v0.1.22.
**Architecture:** Existing Python/Tk update worker downloads verified Windows assets;
the Rust task service reserves maintenance and its detached helper performs the
exit/install/rollback/relaunch transaction. Preserve the macOS updater.
**Tech Stack:** Rust/std/sha2, Python stdlib/Tk, Tauri NSIS, existing GitHub transport.
**Spec:** `docs/superpowers/specs/2026-10-27-windows-auto-update-design.md`

## Global Constraints
- Preserve the original Tk UI and macOS DMG/delta flow.
- Windows repository: `Qiaozhang13/electricity-toolbox-rust-windows`.
- Windows asset: `ElectricityToolbox_<version>_windows_x64_setup.exe`.
- Published v0.1.21 tag/assets remain immutable; release v0.1.22 after verification.
- User configuration/data and credentials stay out of Git and the installer transaction.

## Review Focus
- Old process exits early or PID is reused: acquire and validate exact process handles.
- Installer changes after download: rehash immediately before execution while denying writes/deletes.
- A busy task starts during handoff: Rust maintenance reservation blocks both directions.
- Installer partially writes and fails: restore old directory/registry and relaunch it.
- Paths contain Chinese, spaces or shell punctuation: direct process APIs and final raw NSIS `/D=`.

### Task 1: Windows metadata/download and shared version
**Files:** `desktop/python/github_update.py`, new `windows_update.py`,
new `toolbox_version.py`, `toolbox_log_console.py`, `电力工具箱/toolbox/app.py`;
tests `test_windows_update.py`, `test_log_console.py`.
**Interfaces:** `WindowsUpdater(current, client=None, opener=None)` with the existing
updater API; `toolbox_version.application_version() -> str` returns the raw version.
- [x] Write tests asserting the exact Windows asset/repository, invalid metadata rejection,
  download digest/cancellation and host-env log version; run and capture RED.
- [x] Add shared transport hooks without changing macOS defaults; implement Windows selection
  and download; centralize version lookup; run targeted tests GREEN.
- [x] Commit the independently verified metadata/log change.

### Task 2: Rust installation handoff and reservation
**Files:** new `native/price-xlsx/src/windows_update.rs`, `service_main.rs`,
`desktop/python/windows_update.py`, new `test_windows_update_helper.py`.
**Interfaces:** service executable `--windows-update-helper <plan.json>`;
plan schema 1 contains installer/digest/size/version, installation/data roots,
exact PID/image pairs and token. Gate/status paths derive from the plan directory.
- [x] Add RED tests for maintenance reservation, prepare/commit/abort, changed installer,
  helper waits/timeout, successful install and rollback with small NSIS fixtures.
- [x] Implement exact handle ownership, gate, digest lock, rollback/relaunch and outcome;
  wire Python prepare/commit/abort; run Rust and Windows helper tests GREEN.
- [x] Commit helper and reservation changes.

### Task 3: Native UI/host wiring
**Files:** `native_bridge.py`, `update_ui.py`, `desktop/src-tauri/src/main.rs`,
`test_update_ui.py`, `test_windows_platform.py`, payload selection and docs.
**Interfaces:** host supplies installed flag, installation root, data home and host PID;
Windows native bridge selects `WindowsUpdater`; macOS keeps `RemoteUpdater`.
- [x] Add RED tests for Windows selection/text, busy writes and failure unlock/notification.
- [x] Wire updater and host context, platform-specific status text and prior failure notice.
  Run desktop Python regression and host/native Rust tests GREEN.
- [x] Update Windows instructions and changelog, set v0.1.22 using the version tool; commit.

### Task 4: Package, actual upgrade and publish
**Files:** generated ignored payload/installers; release notes and verification evidence.
- [x] Build new release binaries, refresh all changed managed sources/manifest,
  build full NSIS installer and SHA-256; verify payload and version.
- [x] Test actual v0.1.21→v0.1.22 installation handoff/relaunch in an isolated Chinese/space
  directory with preserved configuration/data; test packaged log version and updater metadata.
- [x] Complete whole-change independent review and targeted fixes, commit/push latest main,
  read remote revision, then create v0.1.22 tag/release and upload installer/checksum.
- [x] Read back public release/digests and current CI; preserve local evidence and clean
  only task-owned installation/source copies. Report links and any unverified scope.

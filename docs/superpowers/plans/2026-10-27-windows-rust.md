# Windows Rust desktop implementation plan

Goal: Run the existing electricity toolbox on Windows x64, preserving its original Tk pages and using the existing Rust/Tauri host and Rust spreadsheet backend.

Architecture: Keep original widgets and business contracts. Adapt OS boundaries, package a Windows runtime and installer, and exercise the native backend and GUI on Windows. This is a continuation of the hybrid architecture, not a claim that all Python business code has been rewritten in Rust.

Execution: Implement directly in the current main checkout, as required by AGENTS.md. The user's explicit Windows request overrides the former macOS-only platform restriction. No business credentials, production uploads or original workbooks are needed for verification.

## Tasks
- [x] 1. Add failing Windows regression tests for subprocess Unicode output, cancellation, cross-process output locks and native executable discovery. Implement platform helpers, preserve POSIX behavior, and verify on Windows.
- [x] 2. Adapt Rust host runtime paths and hidden process creation; preserve original GUI, lazy pages and task-local native Excel sessions. Build/test native Rust and desktop host.
- [x] 3. Add Windows setup/start/build scripts, payload preparation and NSIS configuration. Keep macOS packaging separate. Prevent Windows clients from downloading macOS updates.
- [x] 4. Run GUI page/import smoke, native Excel value/format roundtrip and targeted task/storage regressions. Inspect real UI; record measured performance without inventing improvements.
- [x] 5. Document Windows use and limitations, update changelog and platform instructions, review changes, commit and attempt requested repository synchronization.

## Review focus
- Chinese paths and spaces must survive child-process invocation and JSON pipes.
- Cancellation must terminate only owned task descendants, including browser children.
- Concurrent writes must be rejected and locks released after process failure.
- Installed runtime paths and data roots must work without source checkout or user Python.
- Windows update checks must never offer DMG installation; unverified business workflows remain explicitly unverified.

## Progress
- Repository cloned cleanly; original 15-page Tk UI and Rust/Tauri/Python layers identified.
- Windows blockers found: POSIX pipe polling, process signals, directory flock, executable suffixes, runtime paths and macOS-only updater.
- Implemented and reviewed the Windows boundaries. Fixed the review's startup/cancellation race with suspended creation and Job Object assignment, and PowerShell 5.1 Chinese-path encoding with UTF-8 BOM.
- Verified 565 business Python tests and 135 desktop Python tests (43 platform-specific skips total), 6 native Rust tests and 2 desktop Rust tests. Original UI inspected on Windows; bundled Tk/Chromium and all 15 pages run with the standalone runtime.
- Verified all 649 managed file hashes after workspace installation. Packaged Rust helper reads Chinese paths, dates and percentages correctly. Interleaved 200,000-cell benchmark: median Rust time 1.726 s before, 1.415 s after (18% reduction), values and formats identical.
- Source version is 0.1.20. NSIS build completed (338,692,449 bytes), SHA-256 verified. Isolated installation into a Chinese/space path, actual UI startup, normal shutdown and uninstallation passed; test user data survived and installer registry entries were removed. Git push dry-run failed because terminal credentials are unavailable; the connected GitHub account also reports push=false for this repository. Local source is preserved for later authorized synchronization.

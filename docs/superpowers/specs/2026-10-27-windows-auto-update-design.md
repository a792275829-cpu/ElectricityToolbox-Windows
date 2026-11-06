# Windows automatic updates and version logging

Implement the user's requested Windows update flow and publish v0.1.22 to
`Qiaozhang13/electricity-toolbox-rust-windows`, preserving the original Tk UI.
The existing v0.1.21 release remains immutable and requires one manual upgrade.

Use the existing update window and verified GitHub download transport, with a
Windows updater selected by the native UI bridge. Windows accepts only the exact
`ElectricityToolbox_<version>_windows_x64_setup.exe` asset from that repository,
with a matching tag, positive size and GitHub SHA-256 digest. Startup checks and
the tray's manual check use the same updater. Draft, preview, old, missing or
invalid assets never start installation. API rate limits retain the public-page
fallback. macOS retains its original repository and DMG/delta flow.

The existing Rust service reserves maintenance atomically, rejecting active tasks
and new work until cancellation or installation. Download remains cancellable;
errors restore the original window. Installation uses a detached copy of the
Rust service executable in its updater-helper mode, outside the installation.
It acknowledges preparation before commit, opens handles to the exact host, UI
and service processes after verifying executable paths, and waits for them to
exit. It never scans or kills unrelated processes. A commit/abort gate and bounded
wait prevent a stalled or cancelled handoff from replacing a running app.

After a second size/digest check, hold the installer read-only, rename the old
program directory to a sibling rollback directory, and run NSIS with `/S /UPDATE`
and an unquoted final `/D=` parameter. Validate the installed manifest version;
on failure restore the old program directory and uninstall-registry snapshot.
Keep the data home outside the installation and retain it on success/failure.
Relaunch the application with its original data home. Persist update outcome and
surface a failed installation once on restart. Clean the rollback copy only on
success. Windows uses full installers; no macOS assets or unsigned delta format.

Alternatives considered: a second resident updater service adds unnecessary
processes; a new Tauri updater plugin requires changing the shared UI/RPC and
release/signature format. Reusing Python's existing network worker and a small
Rust handoff helper preserves the current architecture and process ownership.

Resolve application versions through one shared function: first the host-provided
`TOOLBOX_APP_VERSION`, then source/package or installed-manifest metadata. Both
the status bar and log header consume it. Invalid metadata falls back cleanly.

Verification includes malformed metadata, cancellation, corrupt/truncated or
changed installer, busy tasks, abort/timeout, unrelated processes, installer
failure/rollback, Chinese/space paths, preserved configuration/data, version
logging, actual NSIS upgrade/relaunch and macOS regression. Publish only after
tests, packaged-file hashes and actual installer validation pass.

References: [NSIS command line](https://nsis.sourceforge.io/Docs/Chapter3.html),
the locally generated Tauri NSIS template's `/UPDATE` branch.

# Windows incremental updates

User intent: stop repeatedly downloading and reinstalling the complete Windows
runtime for ordinary maintenance. Support Windows 10 / 11 x64, retain original
Tk UI, user configuration/data, and the separate Windows GitHub repository.

## Contract

Windows releases retain the canonical complete NSIS installer and SHA-256, plus
`windows-update-manifest.json` and version-bound
`ElectricityToolbox-BASE-to-TARGET-windows-x64.patch.tar.xz` assets. Reuse the
existing graph selection algorithm with a platform parameter; never use macOS
DMG or macOS patch assets. Prefer the least total download below full-package
size. Retain historical edges, generate direct edges from the last three
releases, reset the graph on major/minor changes or `--full-only`.

Patch format 1: `platform=windows-x64`, base/target version strings, complete
`before`/`after` managed inventories. Entries are `{type:dir}` or
`{type:file,size:int,sha256:lowercase hex}`; parents are explicit. Managed roots
are `electricity-toolbox.exe`, optional `WebView2Loader.dll`, the official `uninstall.exe`, and `payload/`.
Other regular top-level installation extras are retained locally and excluded
from patches. The NSIS uninstaller is version-bound because it enumerates
resource files; keeping an older uninstaller would leave new files behind. Archives contain `manifest.json` and only
changed regular files at `files/RELATIVE_PATH`. No executable installer scripts,
links, reparse points, traversal, Windows device names/ADS, duplicate or
case/Unicode-colliding paths. Bound entries, bytes, archive and manifest sizes.

Prepare a complete sibling `.electricity-toolbox-stage-TOKEN` directory while
the old app runs. Hard-link unchanged files on NTFS, copy when unavailable.
Before any changed file write, unlink its staged hard link. Verify the installed
base, every patch and the resulting managed inventory. Never write through a
link into the running installation; never stage inside user data. Preserve
unknown ordinary top-level files. Recheck unmanaged extras after processes exit;
reject late additions/atomic replacements rather than lose newer local bytes.

Downloaded patches and index use GitHub-published size/digest validation. Missing
paths, modified bases, or preparation errors fall back to the existing full
installer before app exit. Cancellation cleans only owned temporary artifacts
and never triggers fallback. Failed post-handoff incremental installs restore
the old directory; the next retry of that version automatically uses full mode.

Prepared metadata: JSON `{format:1,platform:'windows-x64',base,target,before,after}`.
Rust plan schema 2 retains schema-1 fields (installer points to this JSON, its
size/SHA-256, version,target_dir,app_home,processes,token,wait_timeout_ms), adding
`staging_dir`. Its exact filename is `.electricity-toolbox-stage-{token}` under
the target parent. Schema 1 remains unchanged for full installers.

Extend the existing detached Rust helper: validate owned processes and paths,
wait for graceful exit, independently verify metadata/base/staged files, snapshot
uninstall registration, rename old target to rollback sibling and stage to target,
update DisplayVersion only for the matching registered installation, validate
manifest version, relaunch. Restore directory/registry on failure, preserve home
on every path. Report mode `delta`/`full` and preserve failure evidence. Clean
owned cache/staging safely, including the existing first-startup cleanup race.

Use original update buttons and expose download/preparation phases and actual
package size in existing status text. Add bounded retry/resume for transient
Windows transfer failures if tests establish safe Range/200 handling.

Bootstrap release is 0.1.28. Existing 0.1.27 and older clients require one final
full upgrade because their installed code cannot consume patches. Do not claim
their current updater can already perform incrementals. Validate the new updater
against authentic old installation copies with public release patches; separately
validate old-client bootstrap compatibility. No fabricated production release.

## Validation and release gate

TDD: graph selection/cost limits; archive round trips including add/remove/type
changes; modified/tampered files; unsafe names/reparse/collisions; hard-link
isolation, cancellation, fallback; actual Rust commit/abort/timeout/rollback and
registry/data ownership. Build patches from three real old installations, apply
each to a disposable clone and compare complete inventory. Test original pages,
WPS concurrency/browser lifetime, existing full Windows updater, shared macOS
parsing/route regressions (macOS-only execution remains unavailable here).

Only publish after installer acceptance, real incremental install/relaunch,
user-file hashes and Excel value/format read-back, package and GitHub digests,
public download/old and new version checks. Record measured bytes/time and
Windows 11 actual / Windows 10 not machine-tested explicitly. Source stays on
development; main stays downloads/Actions only; no overwrite of 0.1.27.

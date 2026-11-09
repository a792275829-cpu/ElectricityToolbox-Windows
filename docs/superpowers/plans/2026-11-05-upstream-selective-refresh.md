# Selective upstream refresh and Windows delta validation

**Goal:** Verify the real installed Windows updater, then port useful upstream UI fixes without changing business mappings or platform-specific update channels.

**Execution:** Inline, following AGENTS.md's instruction to implement routine changes directly and keep the source checkout on development.

**Reference:** upstream/main d813bb0f13e483c89682fd918df4d356a9733860 (2026-11-05). Preserve the original repository's attribution and existing license.

## Selection and design

- Adopt the upstream two-step date range calendar and same-month selection fix in the existing spot-price and transaction-info range forms. Keep their variables, defaults, optional-start semantics, validation and generated commands unchanged. Adapt shared imports to this repository's existing root modules; do not introduce a duplicate shared-module hierarchy.
- Keep the already implemented resilient release checks, Windows WPS queue/lifetime fixes, Rust backend and separate Windows incremental channel.
- Defer upstream morning-report D-2 mappings, Qingyuan D+2 planning columns, load definitions and overwrite policies until their templates/contracts are compared; these are business changes, not interchangeable bug fixes.
- Defer upstream flow animation changes because this installation has an explicit 20% speed reduction contract.
- The real installed delta test found only an additional generated `payload/app/__pycache__/toolbox_platform.cpython-312.pyc`, with no missing or changed original program files. Back up and remove this cache once to test the unchanged published updater. Improve future delta preparation by removing only additional caches with a corresponding managed Python source from the staging copy. Never ignore modified/missing managed files, arbitrary extras, reparse points or cache-only modules; keep the live installation immutable.

## Tasks

- [x] Inspect the real installation and record its blocked handoff/current 0.1.29 state honestly. Repeat public 0.1.28 -> 0.1.29 on an isolated genuine installation; verify downloaded assets, automatic restart, managed hashes, runtime identity and preserved data.
- [x] Add and run failing cache regression tests, implement narrow staged-cache removal, rerun patch and native helper tests.
- [x] Add and run date range regression tests before porting the upstream widget; adapt the two existing range forms and verify Tk lifecycle and unchanged command arguments.
- [x] Add a red/green regression for obsolete failure dialogs after an installed target supersedes an earlier failed attempt; preserve diagnostics and notifications for newer failed targets.
- [x] Record reviewed/selected/deferred upstream commits and test evidence; integrate only task sources/docs on development under the repository's required commit/push workflow, with remote-hash verification at completion.

## Execution rulings

- The real installation became 0.1.29 outside the failed helper transaction. Do not attribute that change to the failed delta attempt; use the new isolated public-network acceptance as the end-to-end evidence. The production single-instance lock requires closing the real main window while an isolated host is tested; reopen and verify it afterward.
- Running browser profiles contain locked/volatile files. Preserve their verified pretest backup, verify the 32 stable configuration/business files and all managed program files, and explicitly exclude 2,682 active profile files from byte-for-byte claims.
- This source-only refresh does not replace immutable 0.1.29 release assets. A future installation release must use a higher version and complete the documented package/update gates.
- Independent review of cache handling, Tk events, page command contracts and obsolete-failure handling found no blocking issue. Source test invocations must include the repository root on PYTHONPATH when running page modules alone; the normal combined discovery initializes that path through existing modules.

## Validation boundaries

Do not perform real website uploads or WPS document writes. Do not put private backup manifests, profiles, credentials, generated builds or test artifacts into Git. Windows 10 and macOS runtime checks require those actual systems. Source improvements do not change already published immutable 0.1.29 assets; a future release must increment its version and pass packaging/install gates.

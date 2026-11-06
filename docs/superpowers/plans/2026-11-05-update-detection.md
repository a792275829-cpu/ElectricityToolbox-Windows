# 0.1.29 update detection and release

User contract: API unreachable -> verified public GitHub release fallback; metadata requests10s; two background retries30s apart; periodic3h; footer retry control hidden after success. Keep original Tk UI, Windows/Mac delivery separated.

- [x] Reproduce transport fallback and missing scheduler/footer control with failing tests; implement minimal shared client/UI changes. Cancel delayed startup check after early manual action and prevent overlap.
- [x] Updater76 tests:61pass,15Mac-onlyskip. Actual API-blocked Windows public detection verifies full installer and delta index digests/sizes. Earlier initialization fix14 path regressions passed.
- [x] Version29 full NSIS and pinned-runtime payload; installed original pages/browser/host and updater tests.
- [x] Build and actually restore26/27/28 patches, retaining28 index history; real28 ->29 incremental upgrade/data/Excel/cache/restart checks.
- [x] Push/read back development, exact safe source archive, draft upload and digests, publish; live public delta check, download-only main links and acceptance report. Old clients unable to reach the API may need one manual29 installation; published files remain immutable.

- Local production receipts verified: authentic unmodified28 updater, delta mode,657files, all original fixtures/Excel preserved, runtime hardlinks and first-launch cache cleanup; new official uninstaller accepted.

- Public release complete: source/tag 874b517261dbf2576f3852f9ea72d1b520e9515f; all14 attachment hashes verified. Genuine28 ->29 public delta restart 127.62s; API-blocked actual packaged detector found29 with full and delta metadata matching; main is download/Actions only.

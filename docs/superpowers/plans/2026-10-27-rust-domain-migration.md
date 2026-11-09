# Rust domain migration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Migrate all seven backend candidate groups approved by the user to actual Rust execution while preserving the original Tk UI and business outputs.

**Architecture:** Extend the existing native helper with focused domain modules and thin Python adapters. Retain Python as the Tk/Playwright/model-training/document-engine compatibility layer and as a parity oracle, not as the default implementation of migrated kernels. Add a Rust headless task service so the installed desktop no longer needs a resident Python service process. Unsupported cases must be explicit; do not silently relabel an unchanged Python algorithm as Rust.

**User constraints:** Keep all 15 original pages, exact Chinese field/sheet names, D/D-1/D-2/D-6 date semantics, Decimal and rounding rules, null/zero distinctions, styles/formulas, isolated authentication and owned-process cancellation. Do not use credentials or production writes for tests. Continue directly on the existing main checkout per AGENTS.md; preserve the working 0.1.20 installer until its replacement passes acceptance.

**Shared interfaces and ownership:** Only the root agent edits `native/price-xlsx/src/main.rs`, `Cargo.toml`, `Cargo.lock`, `toolbox_native_domain.py`, shared packaging/version files and the progress ledger. A Rust domain exports `pub fn execute(payload: &serde_json::Value) -> Result<serde_json::Value, Box<dyn std::error::Error>>`. The root dispatches named operations and wraps results as `{protocol:1,result:...}`. Python adapters call `toolbox_native_domain.invoke(domain, action, payload)`, which returns the result or None only when auto mode cannot select an available implementation. `TOOLBOX_DOMAIN_BACKEND=auto|python|rust` controls migration; strict rust errors are never suppressed. Large image/matrix payloads use binary/file transport where appropriate. Filesystem side effects never automatically retry after an ambiguous error.

**Execution environment:** Windows PowerShell, root `.venv/Scripts/python.exe`; installed Rust GNU toolchain and MinGW; reusable external `D:/electricity-toolbox-build` cargo target. Agents own disjoint files, may not spawn further agents, and request serialized root integration builds/commits instead of competing for the shared binary or Git index.

## Task 1: Native domain transport and registration (root)
Files: `toolbox_native_domain.py`, native `src/main.rs`, manifests, `电力工具箱/tests/test_native_domains.py`.
- [x] Add tests for strict/auto/python selection, capabilities, invalid replies and domain errors; observe failure.
- [x] Implement shared invocation, explicit binary override for development, capability reporting and incremental module registration.
- [x] Run transport/native regressions; record evidence and commit through root.

## Task 2: 500kV domain (agent sections)
Own: `native/price-xlsx/src/sections.rs`, `500kV断面分析/src/deduplicate_500kv_prices.py`, `analyze_500kv_section_gaps.py`, `classify_500kv_nodes.py`, relevant new parity tests.
- [x] Read current functions and tests; add failing native/Python parity tests for duplicates, normalized aliases, exact decimal means, missing prices, reversed/duplicate edges, thresholds, previous-point logic, minimum contiguous intervals and aggregation order.
- [x] Port node normalization/matching, price-row deduplication/grouping, topology edge parsing, interval calculations and edge/group/window summaries into Rust. Use batched operations, not one subprocess per row.
- [x] Wire actual default native paths while keeping UI/report formatting and Python reference fallback intact; report any remaining pure business calculation precisely.
- [x] Run focused tests and a synthetic end-to-end workbook/output comparison; self-review; report changed paths, operation actions, red/green evidence and remaining limitations to root.

## Task 3: Trading analysis and energy summary (agent trading)
Own: `native/price-xlsx/src/trading.rs`, `电力工具脚本/电力交易分析工具/generate_electricity_report.py`, `find_output_range_deviation_days.py`, `电力工具脚本/电量汇总工具/summarize_511_excel.py`, focused new parity tests.
- [x] Add failing parity tests for offer segment merging, bounds/point classification, old/new summary parsing, hourly/renewable records, company aggregation, deviation rankings, start/stop exclusions, unit ordering, Chinese sheet labels and percentage/text numeric formats.
- [x] Port the computational/parsing pipeline into Rust, accepting bulk sheet data or workbook paths and returning final report-domain structures. Keep HTML rendering and Tk controls in Python.
- [x] Wire public entry paths and helper paths to batched native execution, preserving monkeypatched/custom Python callers where required; do not only port isolated decorative helpers.
- [x] Compare complete generated workbook values/styles and report-domain outputs; run focused suites and report evidence/self-review to root.

## Task 4: Forecast preprocessing (agent forecast)
Own: `native/price-xlsx/src/forecast.rs`, `广东电价预测/gd_price_forecast/data.py`, `features.py`, `evaluation.py`, `calendar_features.py` if needed, new parity tests.
- [x] Add failing native/Python parity cases for missing dates, duplicate (date,slot) keys, leap days, quarter-hour grain, partial targets, NaNs, lag joins D-1/D-2/D-3/D-7/D-14, training splits, rolling features, exogenous visibility, holidays and evaluation metrics.
- [x] Implement preprocessing/feature construction/split and metric kernels in Rust. Calendar source lookup may stay in its compatibility library, but native feature assembly must preserve column order, values, index and dtypes.
- [x] Retain model estimators/training and reporting engines. Wire default actual native paths with strict-rust tests; do not replace elapsed-time measurements with guesses.
- [x] Run forecast tests, frame-level parity and a representative synthetic benchmark; self-review and report remaining Python computation explicitly.

## Task 5: PDF chart postprocessing (fresh agent)
Own: `native/price-xlsx/src/pdf_chart.rs`, `广东运行日报PDF解析/gd_pdf_tool/parser.py`, focused parity tests.
- [x] Test grid/axis/bar detection, color masks, category count and missing columns, tiny bars, overlapping colors, capacity overrides and exact totals against the existing implementation.
- [x] Port the remaining image/geometry/statistical kernels and declaration normalization/calibration to Rust. Keep PDFium/pdfplumber extraction, Windows OCR and Pillow rendering as engine adapters.
- [x] Avoid shipping giant nested JSON arrays per pixel; validate compact transport, output parity and genuine integration into normal PDF parsing.

## Task 6: Market API payload processing (fresh agent)
Own: `native/price-xlsx/src/market_payload.rs`, `南方分公司电力市场日报/gd_power_report/collector/market.py`, `market_analysis.py`, narrow parse/extraction adapters in market-table/transaction/power-benefit modules and tests.
- [x] Pin malformed/missing/null/zero payloads, Chinese headers, partial endpoint failures, date priority, fuel counts and consistent field order with native/Python parity tests.
- [x] Port pure JSON/table normalization, field extraction, aggregation and comparison-card generation. Keep HTTP/browser/auth adapters and original retry/partial-success policy.
- [x] Integrate all affected consumers and verify no cross-tool profile/state changes or accidental network side effects.

## Task 7: Excel inspection, targeted updates and transactional filesystem core (root/fresh agent)
Own: Rust `src/workbook_ops.rs` (or focused submodules), `toolbox_workbook_inspection.py`, `toolbox_workbook_commit.py`, targeted XML write modules and tests.
- [x] Pin sparse styles, shared/cached formulas, merged cells, dates/percentages, no-op writes, concurrent external edits and multi-file rollback failures before implementation.
- [x] Port ZIP/XML scanning, targeted patching/verification and fingerprint/backup/replace/rollback primitives. Python callbacks may verify domain contracts, but Rust must own actual migrated operations.
- [x] Never lose non-target ZIP members, formulas, dimensions or style IDs; preserve corrupt/unsupported-workbook errors and verified rollback backups.

## Task 8: Rust task service and process supervision (root)
Own: Rust service binary/modules, desktop host launch path, Python service compatibility adapter and service tests.
- [x] Add protocol-3 contract tests for ping/catalog/validate/confirmation/start/request-id deduplication/snapshots/events/cancel/shutdown, journal overflow, redaction and result/error routing.
- [x] Move task state, event journal, owned process trees, cancellation, maintenance/update reservations and worker stdout parsing into a Rust service. Keep business-worker engine adapters.
- [x] Installed Tauri must launch the native service and original Tk bridge; prove no separate resident Python service process and no hidden WebView2 on a healthy launch.
- [x] Verify cancellation of fast-exiting parents/descendants, no duplicate writes/uploads on retries, busy shutdown protection and GUI task recovery.

## Task 9: Integration, acceptance and delivery (root)
- [x] Run all relevant original suites plus strict native coverage, complete output readback, synthetic benchmarks and all 15 pages.
- [x] Request scoped review of each task and a final broad review, fix important issues with red/green evidence, and record unresolved minor limitations.
- [x] Update version/changelog and truthful migration inventory; distinguish migrated default paths from retained compatibility engines/fallbacks.
- [x] Rebuild Windows payload/NSIS, validate manifest hashes, install/start/exit/uninstall in an isolated Chinese/space path and preserve test data.
- [x] Commit task changes, attempt authorized origin/main sync without credential prompts, and deliver installer, digest and source patch if write access remains unavailable.

## Review focus
1. Date shifts must use calendar dates, not a row offset when a day is missing.
2. NaN/null, numeric zero, errors, cached formulas and percent formats must remain distinguishable.
3. Native exceptions must not cause duplicate writes, uploads or a silent fallback after side effects.
4. Task cancellation must kill only owned descendants; shutdown must preserve non-interruptible writes.
5. Report dimensions, ordering, field provenance and original UI behavior must remain equivalent.

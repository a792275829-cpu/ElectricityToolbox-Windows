# 2026-11-16 未提交改动丢失与重做清单

## 事故经过

22:00 前后，为执行「只回退早报模块」，误执行了全仓 `git checkout HEAD -- .`，把当天两批**未提交**改动一并覆盖为 0.1.42 提交内容。这些改动从未 `git add`／`commit`／`stash`，`git fsck` 的悬空对象均为 0.1.31～0.1.32 旧文件，`AppData\Local\Temp\backup_local` 是 11-06 旧备份，因此无法用 Git 或备份恢复。

当前工作树 = 0.1.42 提交 + 早报模块保留的两处修复（见文末），其余文件均等于 0.1.42。

## 必须重做的文件

### A. 日报与早报异常数据保护（15:19–15:31）

行为要求见 [report-safety-review-2026-11-16.md](report-safety-review-2026-11-16.md)。

- `南方分公司电力市场日报/gd_power_report/company_clearing_xlsx.py`
- `南方分公司电力市场日报/gd_power_report/realtime_unit_xlsx.py`
- `南方分公司电力市场日报/tests/test_company_clearing_xlsx.py`
- `南方分公司电力市场日报/tests/test_realtime_unit_xlsx.py`

### B. Windows 修改建议批次（19:03–19:05）

行为要求见 [windows-suggestions-review-2026-11-16.md](windows-suggestions-review-2026-11-16.md)。

- 海风六列：`功率调整收益统计表更新/update_power_benefit.py`、`功率调整收益统计表更新/README.md`、`电力工具箱/tests/test_power_benefit_update.py`、`电力工具箱/toolbox/power_benefit_page.py`
- 集团开机台数：`集团每日上传/upload_daily_report.py`、`电力工具箱/tests/test_group_upload_safety.py`
- WPS 统一预检：`wps自动/wps_excel_to_kdocs_gui.py`、`电力工具箱/tests/test_wps_api_recovery.py`
- 私有上传空数组：`电力工具脚本/private-data-uploader-tool/scripts/upload-private-data.mjs`、`电力工具箱/tests/test_toolbox.py`
- 出清汇总：`电力工具脚本/电量汇总工具/summarize_511_excel.py`、`电力工具脚本/电量汇总工具/tests/test_node_prices.py`、`toolbox_node_prices.py`
- 说明：`README.md`

### C. 工具箱接线（两批混合，需逐项判断）

这些文件同时承载日报页面与早报入口，重做时按评审文档对应条目取舍；早报已回退，电量未更新警告相关接线暂不需要。

- `电力工具箱/toolbox/workflows.py`
- `电力工具箱/toolbox/pages.py`
- `电力工具箱/toolbox/ui_tasks.py`
- `电力工具箱/toolbox/market_report_page.py`
- `电力工具箱/toolbox/service_catalog.py`
- `电力工具箱/tests/test_date_inputs.py`
- `电力工具箱/tests/test_market_report_manual.py`
- `电力工具箱/tests/test_report_date_responsiveness.py`

### D. 新增测试文件（已备份，可直接还原）

`dist/reverted-2026-11-16/` 下三份原件：

- `test_report_generation_safety.py` → `每日生产经营情况汇报自动生成工具/scripts/`
- `test_report_execution_safety.py` → `电力工具箱/tests/`
- `batch-execution.test.mjs` → `电力工具脚本/private-data-uploader-tool/tests/`

注意：前两份测试断言的是已回退的早报逻辑，还原后需同步调整或保留不启用。

## 可参考的字节码

`__pycache__` 里有从丢失版本编译的 `.pyc`（15:22–19:0x），本机无 Python 3.11 反编译器，仅可用 `dis` 逐段查看，不能直接当成品：

`toolbox_node_prices`、`company_clearing_xlsx`、`realtime_unit_xlsx`、`toolbox/pages`、`toolbox/workflows`、`toolbox/ui_tasks`、`toolbox/market_report_page`、`toolbox/power_benefit_page`、`toolbox/service_catalog`、`fetch_daily_data`、以及对应测试模块。

`upload_daily_report.py`、WPS 页面入口、`summarize_511_excel.py`、`*.mjs`、`README.md` 没有可用字节码，只能按评审文档重写。

## 重做后的验证

```powershell
.venv\Scripts\python.exe tools\run_tests.py --group report --group market-report
.venv\Scripts\python.exe tools\run_tests.py --module test_power_benefit_update --module test_group_upload_safety --module test_group_upload_template --module test_wps_api_recovery --module test_node_prices
.venv\Scripts\python.exe tools\run_tests.py --module batch-execution.test.mjs --module batch-response.test.mjs --module test_toolbox --module test_wps_batch_reads --module test_wps_local_files
```

当前基线：`.venv\Scripts\python.exe tools\run_tests.py` fast 档 94/94 通过（22:00 后验证）。

## 早报模块现状（按用户要求保留，不要重做）

回到 0.1.42 提交，仅保留两处修复：

1. `_ClearingWorkbookSnapshot` 的 256 列宽度校验从「打开工作簿时」推迟到「真正读该工作表时」，避免报表不使用的宽表（如「市场购电用户曲线比例」367 列）导致整份早报失败。
2. `read_workbook_run_date` 读取失败时记录首选异常，不再把「工作簿读不了」静默伪装成「读不到运行日」。

配套测试：`test_daily_clearing_fill.py` 新增 2 例、`test_workbook_dates.py` 新增 1 例；`CHANGELOG.md` 保留一条 `[Unreleased]` 记录。

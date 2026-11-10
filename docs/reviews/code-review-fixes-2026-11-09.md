# 2026-11-09 逐模块审查修复记录

以 `fd64b3f` 为修复基线（包含审查后的 payload 新鲜度校验提交）。原审查报告中的六项问题均已修复；作为未发布源码改动保留现有版本号。

| 问题 | 修复 | 回归证据 |
| --- | --- | --- |
| R1：500kV 清理逻辑删除输入或历史结果 | 只清理任务自身创建的临时目录，取消对用户输出目录中固定旧文件名的删除 | 执行完整分析，输入恰好命名为 `500kv_section_price_gap_analysis.xlsx` 并与输出同目录时，输入字节保持不变；已有三种旧产物均保留 |
| R2：WPS 把无缓存公式当空白 | 复用已打开的 XLSX archive 检查所选区域的公式缓存，缺少计算结果时在准备阶段报错 | auto/rust/python 设置均拦截无缓存公式；合法数字缓存、缓存空字符串及真实空白保持原值；区域外公式不阻断；失败发生在任何在线读取/写入之前 |
| R3：重复完整电价记录静默覆盖 | 同场站同日期出现第二条有效完整记录时明确拒绝；不按接口顺序选择 | 两种顺序、相同重复记录、开启/关闭不完整日期跳过均报错；其他场站与不完整记录不遮蔽有效价格 |
| R4：源码 GUI/CLI 批次覆盖同名报告 | 复用 `plan_batch_outputs` / `iter_batch_reports`，统一工具箱页面、独立 GUI、CLI 和服务路径；独立 GUI 在主线程捕获本批参数 | 两个目录同名、大小写相异的同名输出在启动前被拒绝，未创建输出目录；不同输入各自产出；独立 GUI 保留原有逐文件部分成功行为 |
| R5：异常 JSON 被计为上传成功 | 严格区分已知数组结果与 `T200 + 数组 data`；未知结构、空对象、null、无效逐项结果抛出协议错误并提示先核对网站状态 | 15 项 Node 测试验证异常拒绝、正常逐文件结果、网站已知的合法空数组特例；没有重放上传请求 |
| R6：Rust 服务丢失登录错误分类 | 向 worker 开启 CLI 错误协议；解析并保存 `TOOLBOX_ERROR`，保留具体登录原因，合并外层 worker 的原始命令退出码；错误字段白名单及字符串脱敏 | 真实 Rust 服务运行临时 CLI，验证码与网络错误可经任务快照到达 Tk 分类器；退出码 1/7 正确保留；成功/取消无残留错误详情；原始敏感测试字符串不进入快照 |

## 验证

- Python 117 项通过：`tests.test_review_safety`、`tests.test_wps_batch_reads`、`tests.test_spot_price_update` 共 30 项；`test_generate_500kv_blockage_map`、`test_batch_outputs` 共 7 项；`tests.test_private_batch_upload`、`tests.test_private_batch_regression`、`tests.test_review_fixes`、`tests.test_page_bindings` 共 53 项；交易分析 `test_report_summary`、`test_haimen_six_units`、`test_output_range_deviation` 共 13 项；真实 Rust 协议 `test_rust_service` 14 项。
- Node：`node --test 电力工具脚本/private-data-uploader-tool/tests/batch-response.test.mjs`，15 项通过。
- Rust：`cargo test --offline --manifest-path native/price-xlsx/Cargo.toml -- --test-threads=1`，20 项通过。
- 源码启动器：`powershell -NoProfile -ExecutionPolicy Bypass -File start_source_test.ps1 -Smoke`，输出 `TOOLBOX_DEPS=OK`、`TOOLBOX_SMOKE=OK`。
- 本地原生运行程序：通过 `native/price-xlsx/build.py` 重编译 release，并更新源码目录使用的两个 exe；更新后再次通过真实 Rust 登录错误协议用例。生成物保持 Git 忽略。`version.py check` 与 `git diff --check` 通过。
- 工作簿样例仅在临时目录创建；检查来源文件字节不变，并回读数字、空字符串、空白和已有百分比显示格式。没有改写用户业务文件或调用生产上传接口。

此次验证针对六项修复及其调用链，不代表旧审查中整个仓库的 125 个测试模块全部通过。此前字体平台模拟、浮点精确比较、原生锁未显式退出及本机 WPS 登录态影响测试的问题不在本次产品修复范围内。真实网站登录、上传和在线写入需使用实际业务环境验证。

# Windows 修改建议评估与验证

日期：2026-11-16。范围：源码修改与隔离验证，未提交、推送、打包或发布。

> **状态：未落地。** 本文对应的代码改动在 2026-11-16 22:00 的误回退中丢失，尚未重做；当前发行版不含这些行为。本文仅作为评估结论与重做依据保留，须按 [recovery-needed-2026-11-16.md](recovery-needed-2026-11-16.md) 重新实现并重新验证。

## 逐项结论

|建议|评估与处理|
|---|---|
|海风六列更新|合理，已补 L 日前电量，沿用既有日前 electricity × 10 的口径；目标列为 C/R/L/N/M/O，保持所选日期覆盖规则及数值、数字格式、样式回读。同步页面与说明。|
|集团开机台数|合理，日前、实时各要求唯一的燃煤和燃气记录，曲线各24点且为有限非负整数。缺记录、重复、缺小时、非法值均停止；正常数据仍先逐小时燃煤加燃气，再取两类市场最大值。|
|早报数值保护|合理，工作区已有相关修改，予以保留并验证。占位符保持缺失，极小分母及正负电量抵消时均价无效。|
|WPS统一预检|合理，同一文档全部区域的定位、合并状态及仅填空白所需值／公式检查结束后才执行写入队列。保留缓存校验、接口20秒与整批180秒超时、保存回读和禁止超时重放机制。预检不能代替服务端事务，写入阶段故障仍须核对结果。|
|私有上传空数组|合理，合法空数组不再虚增成功文件数；空结果和不足的明细均记录待确认数，强制查询涉及日期的数据状态。状态不能唯一对应本次文件时保留待确认，查询失败也不会变为全部成功；仍清理浏览器和登录态锁。|
|南方日报|合理，已有日期自动识别和新增机组残缺报错修改，予以保留并验证。|
|早报单次抓取及旧数提示|合理，已有三个入口共用单次抓取及完成警告修改，予以保留并验证。|
|500kV|补齐已核实拓扑的方向合理，但本地仅查到广蓄A候选识别记录，缺少广蓄A/B和五条连接的明确端点依据，尚未修改。检修叠加及分析模式也需明确来源、字段与期望行为后再迁移。|
|出清汇总|合理，取消全省数据的第二张表限制，按全省标签、96点时刻、日期和市场类型校验。海风／全省两张汇总表的标题、关键96点及数字格式在临时文件保存后回读，通过后才替换输出。|

原先已有的早报／日报修改及验证说明另见 [日报与早报保护记录](report-safety-review-2026-11-16.md)。本轮未覆盖或撤销用户已有改动。

## 验证

统一使用 `.venv/Scripts/python.exe tools/run_tests.py`，最终31个Python模块、2个Node测试文件全部通过，无跳过。第一次汇总回读检查误将合并标题非主单元格格式当作关键字段，已修复并复测通过。

- 海风：17例通过，覆盖日期限制、六列144格规划、实际文件提交后 L4=6、格式0.0000，未选日期及非目标列保留。
- 集团：安全7例、模板8例通过；验证24点同小时求和、两市场最大值、真实零值，以及缺项、重复、23/25点、空值、占位符、布尔、负数、小数和非有限数拦截。
- WPS：接口恢复27例、批量读取4例、本地文件8例通过。真实无头浏览器运行生产JavaScript，对后续区域缺失及公式超时均断言写入0次、保存0次，超时后恢复接口也不继续写；正常保存后刷新回读。
- 出清汇总：8例通过，覆盖工作表换位、日期和市场冲突、完整96点顺序与格式、源文件保留；注入末点CR2数值或格式错误时旧输出保持不变。
- 私有上传：批处理执行6例及响应协议测试通过，覆盖裸／封装空数组、缺明细、后批失败、状态查询失败、明确成功与失败；调用实际runBatch，浏览器／API使用隔离替身，不访问生产服务。
- 早报及南方日报：report与market-report共19模块通过；入口安全4例、日期响应8例、日期输入28例、私有上传页面24例通过；工具箱92例通过。
- `git diff --check` 通过。

主要复现命令：

```powershell
.venv/Scripts/python.exe tools/run_tests.py --group report --group market-report
.venv/Scripts/python.exe tools/run_tests.py --module test_power_benefit_update --module test_group_upload_safety --module test_group_upload_template --module test_wps_api_recovery --module test_node_prices
.venv/Scripts/python.exe tools/run_tests.py --module batch-execution.test.mjs --module batch-response.test.mjs --module test_toolbox --module test_wps_batch_reads --module test_wps_local_files
.venv/Scripts/python.exe tools/run_tests.py --module test_report_execution_safety --module test_report_date_responsiveness --module test_date_inputs --module test_private_batch_upload
```

未进行生产账号联网抓取／上传、真实业务工作簿覆盖、安装包验收或全量发行测试。500kV需要用户提供已核实的连接清单或来源提交，不能用候选OCR记录替代拓扑依据。

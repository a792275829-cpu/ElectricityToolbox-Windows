"""桌面服务链路契约：页面变量与 page_contracts 注册表必须逐项对齐。

安装包运行在 remote_business 模式下，页面表单值由 prepare_page_worker 采集后交给
toolbox-service 执行。字段清单一旦在注册表和采集代码间各存一份，新增开关就会静默
漏传（界面能勾、服务端收不到），因此这里固定三条契约。
"""
from __future__ import annotations

import inspect
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from toolbox.runtime import TaskRegistry, ToolPaths

        cls.paths = ToolPaths(ROOT)
        cls.registry = TaskRegistry()
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def pages(self):
        from toolbox.catalog import default_catalog

        for descriptor in default_catalog():
            yield descriptor, descriptor.factory()(self.root, self.paths, self.registry)

    def test_prepare_page_worker_accepts_explicit_action(self) -> None:
        from toolbox.ui_tasks import RemoteTaskRegistry

        parameters = inspect.signature(RemoteTaskRegistry.prepare_page_worker).parameters
        # widgets.run_process / run_in_thread 都用关键字传 action；签名缺失时
        # 安装包内每一次业务操作都会以 TypeError 失败。
        self.assertIn("action", parameters)
        self.assertIs(parameters["action"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_registered_fields_exist_on_their_pages(self) -> None:
        from toolbox.page_contracts import PAGE_BINDINGS

        checked = 0
        for descriptor, page in self.pages():
            binding = PAGE_BINDINGS.get(descriptor.tool_id)
            if binding is None:
                continue
            missing = [name for name in binding.fields if not hasattr(page, name)]
            self.assertEqual([], missing, f"{descriptor.tool_id} 的注册字段在页面上不存在")
            checked += 1
        self.assertGreaterEqual(checked, 15)


class RemotePayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from toolbox.runtime import TaskRegistry, ToolPaths

        cls.paths = ToolPaths(ROOT)
        cls.registry = TaskRegistry()
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def submit(self, page, **kwargs):
        from toolbox.ui_tasks import RemoteTaskRegistry

        captured: dict[str, object] = {}
        remote = RemoteTaskRegistry(client=None)
        remote.execute_remote = lambda tool_id, action, values, token, log: captured.update(
            tool_id=tool_id, action=action, values=values) or {}
        worker = remote.prepare_page_worker(page, kwargs.pop("status", "正在执行..."), **kwargs)
        worker(None)
        return captured

    def test_market_table_sends_overwrite_and_backup_switches(self) -> None:
        from toolbox.pages import MarketTableUpdatePage

        page = MarketTableUpdatePage(self.root, self.paths, self.registry)
        page.overwrite.set(False)
        page.create_backup.set(True)
        captured = self.submit(page, action="update")
        self.assertEqual("market-table", captured["tool_id"])
        self.assertEqual("update", captured["action"])
        self.assertIs(False, captured["values"]["overwrite"])
        self.assertIs(True, captured["values"]["create_backup"])

    def test_guangdong_predict_sends_forecast_days(self) -> None:
        from toolbox.pages import GuangdongPricePage

        with tempfile.TemporaryDirectory() as folder:
            master = Path(folder) / "总表.xlsx"
            master.touch()
            page = GuangdongPricePage(self.root, self.paths, self.registry)
            page.master_workbook.set(str(master))
            page.prediction_output_dir.set(folder)
            page.forecast_days.set(9)
            page.training_months.set(1)
            captured = self.submit(page, action="predict", process=True)
        self.assertEqual("guangdong-price", captured["tool_id"])
        self.assertEqual("predict", captured["action"])
        self.assertEqual(9, captured["values"]["forecast_days"])
        self.assertEqual(1, captured["values"]["training_months"])

    def test_login_command_is_not_treated_as_a_business_run(self) -> None:
        from toolbox.pages import OnlineEnergyPage

        page = OnlineEnergyPage(self.root, self.paths, self.registry)
        captured = self.submit(
            page, command=["python", "tool.py", "--login"], process=True, status="正在打开登录窗口..."
        )
        self.assertEqual("login", captured["action"])
        self.assertEqual({}, captured["values"])


if __name__ == "__main__":
    unittest.main()

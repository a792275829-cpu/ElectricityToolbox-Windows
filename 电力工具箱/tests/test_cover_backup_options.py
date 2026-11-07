"""市场表/交易系统 覆盖与备份开关：命令构造与桌面字段契约回归。"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from toolbox import commands
from toolbox.runtime import ToolPaths
from toolbox.service_catalog import desktop_catalog


class CoverBackupOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = ToolPaths(Path(__file__).resolve().parents[2])

    def test_market_table_flags(self) -> None:
        base = commands.market_table(self.paths, date(2026, 11, 8), Path("m.xlsx"))
        self.assertIn("--no-backup", base)
        self.assertNotIn("--skip-existing", base)
        skip = commands.market_table(
            self.paths, date(2026, 11, 8), Path("m.xlsx"),
            overwrite=False, create_backup=True,
        )
        self.assertIn("--skip-existing", skip)
        self.assertNotIn("--no-backup", skip)

    def test_transaction_info_flags_keep_create_backup(self) -> None:
        base = commands.transaction_info(
            self.paths, Path("t.xlsx"), date(2026, 11, 8),
            start_date=None, create_backup=True,
        )
        self.assertNotIn("--skip-existing", base)
        self.assertNotIn("--no-backup", base)
        skip = commands.transaction_info(
            self.paths, Path("t.xlsx"), date(2026, 11, 8),
            start_date=None, create_backup=False, overwrite=False,
        )
        self.assertIn("--skip-existing", skip)
        self.assertIn("--no-backup", skip)

    def test_desktop_catalog_exposes_switches(self) -> None:
        catalog = {tool["id"]: tool for tool in desktop_catalog(self.paths)}
        for tool_id in ("market-table", "transaction-info"):
            keys = {f["key"] for f in catalog[tool_id]["fields"]}
            self.assertIn("overwrite", keys)
            self.assertIn("create_backup", keys)


if __name__ == "__main__":
    unittest.main()

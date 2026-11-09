from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/tauri"))
import prepare_payload
import prepare_workspace


class PriceXlsxPackagingTests(unittest.TestCase):
    def test_build_is_required_before_packaging(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(prepare_payload, "run") as run:
            with self.assertRaises(FileNotFoundError):
                prepare_payload.prepare_price_xlsx(Path(tmp), "aarch64-apple-darwin")
            self.assertIn("--target", run.call_args.args[0])
            self.assertIn("--output", run.call_args.args[0])

    def test_managed_install_preserves_executable_mode(self):
        binary = ROOT / "500kV断面分析/bin" / ("toolbox-price-xlsx.exe" if os.name == "nt" else "toolbox-price-xlsx")
        if not binary.is_file():
            self.skipTest("build native/price-xlsx first")
        with tempfile.TemporaryDirectory() as tmp:
            import json
            import shutil
            base = Path(tmp)
            payload = base / "payload"
            name = binary.relative_to(ROOT).as_posix()
            staged = payload / "app" / name
            staged.parent.mkdir(parents=True)
            shutil.copy2(binary, staged)
            adapter_name = "toolbox_native_excel.py"
            self.assertTrue(prepare_payload.runtime_source(Path(adapter_name)))
            shutil.copy2(ROOT / adapter_name, payload / "app" / adapter_name)
            helpers = sorted(path.name for path in ROOT.glob("toolbox_*.py")
                             if path.name != adapter_name)
            for helper in helpers:
                self.assertTrue(prepare_payload.runtime_source(Path(helper)))
                shutil.copy2(ROOT / helper, payload / "app" / helper)
            (payload / "manifest.json").write_text(json.dumps({
                "version": "test", "build": "native-test",
                "files": {name: prepare_workspace.digest(staged),
                          adapter_name: prepare_workspace.digest(payload / "app" / adapter_name),
                          **{helper: prepare_workspace.digest(payload / "app" / helper) for helper in helpers}},
            }))
            installed = base / "installed"
            prepare_workspace.install(payload, installed)
            installed_env = dict(os.environ, PYTHONPATH=str(installed))
            installed_env.pop("TOOLBOX_NATIVE_BINARY", None)
            target = installed / name
            self.assertTrue(os.access(target, os.X_OK))
            self.assertEqual(target.read_bytes(), binary.read_bytes())
            import subprocess
            workbook = base / "installed-test.xlsx"
            for operation in ("write", "read"):
                result = subprocess.run([str(target)], input=json.dumps(dict(
                    protocol=1, operation=operation, path=str(workbook),
                    sheets=[dict(name="中文表", rows=[["电价", 123.45]])],
                )), text=True, capture_output=True, check=True)
                response = json.loads(result.stdout)
                if operation == "read":
                    self.assertEqual(response["sheets"][0]["rows"], [["电价", 123.45]])
            # Import only the installed adapter; its root must discover the
            # installed binary without consulting the source checkout.
            result = subprocess.run([sys.executable, "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd())); "
                "from toolbox_native_excel import load_value_workbook; "
                "b=load_value_workbook(sys.argv[1]); "
                "assert b.active['B1'].value == 123.45; b.close()", str(workbook)],
                cwd=installed, env=dict(installed_env, TOOLBOX_XLSX_BACKEND="rust"),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd())); "
                "import numpy as np; from openpyxl import Workbook,load_workbook; "
                "from toolbox_native_write import save_workbook,save_frame; "
                "from toolbox_native_compute import unmix,native_session; "
                "b=Workbook(); b.active['A1']='installed'; save_workbook(b,sys.argv[1]); "
                "assert load_workbook(sys.argv[1]).active['A1'].value=='installed'; "
                "ctx=native_session(); ctx.__enter__(); "
                "r=unmix(np.full((8,4,3),255,dtype=np.uint8),np.full((8,3),255.),[None,None,None]); "
                "assert np.count_nonzero(r)==0;ctx.__exit__(None,None,None); "
                "import pandas as pd; assert save_frame(pd.DataFrame({'比例':[.125]}),sys.argv[1],'整合数据',{}); "
                "assert load_workbook(sys.argv[1]).active['A2'].value==.125; "
                "from toolbox_workbook_inspection import load_inspection_pair; "
                "a,c=load_inspection_pair(sys.argv[1],columns=(1,),target_columns=(),sheet_filter=lambda n: True); "
                "assert a['整合数据']['A2'].value==.125; a.close(); c.close()", str(workbook)],
                cwd=installed, env=dict(installed_env, TOOLBOX_XLSX_BACKEND="rust",TOOLBOX_COMPUTE_BACKEND="rust"),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

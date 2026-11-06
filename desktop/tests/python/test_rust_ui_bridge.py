"""Original Tk export button against the real Rust scheduler, including retry."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
from toolbox_platform import hidden_process_options
SERVICE = Path(os.environ.get("TOOLBOX_SERVICE_BINARY", ROOT / "电力工具箱/bin/toolbox-service.exe"))


@unittest.skipUnless(os.name == "nt" and SERVICE.is_file(), "requires native Windows service")
class RustUiBridgeTests(unittest.TestCase):
    def test_original_button_recovers_uncertain_reply_without_duplicate_work(self):
        from toolbox.app import ToolboxApp
        from toolbox.ui_tasks import RemoteTaskRegistry
        with tempfile.TemporaryDirectory(prefix="native-ui-中文 ") as directory:
            workspace = Path(directory)
            script = workspace / "上网电量抓取/export_online_energy.py"
            script.parent.mkdir()
            marker = workspace / "executions.txt"
            script.write_text("from pathlib import Path\nimport time\n"
                f"p=Path({str(marker)!r})\nwith p.open('a') as out: out.write('executed\\n')\n"
                "time.sleep(.3)\nprint('NATIVE_TK_RETRY_OK',flush=True)\n", encoding="utf-8")
            process = subprocess.Popen([str(SERVICE), "--workspace", str(workspace),
                "--code-root", str(ROOT), "--python", sys.executable],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", **hidden_process_options())
            lock = threading.Lock()
            sequence = 0
            drop_first_reply = True
            class Client:
                def call(self, method, params=None):
                    nonlocal sequence, drop_first_reply
                    with lock:
                        sequence += 1
                        process.stdin.write(json.dumps(dict(id=sequence, method=method, params=params or {})) + "\n")
                        process.stdin.flush()
                        while line := process.stdout.readline():
                            response = json.loads(line)
                            if response.get("id") == sequence:
                                if "error" in response: raise RuntimeError(response["error"])
                                if method == "start" and drop_first_reply:
                                    drop_first_reply = False
                                    raise RuntimeError("synthetic lost accepted reply")
                                return response["result"]
                        raise RuntimeError("service exited")
            client = Client()
            app = registry = None
            try:
                self.assertEqual(json.loads(process.stdout.readline())["event"]["kind"], "service-ready")
                self.assertEqual(client.call("ping")["engine"], "rust")
                registry = RemoteTaskRegistry(client)
                with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}), \
                     patch("toolbox.pages.messagebox.showinfo"), patch("toolbox.pages.messagebox.showerror"):
                    app = ToolboxApp(workspace=workspace, registry=registry)
                    page = app.pages["导出上网电量"]
                    page.run_date.set("2026-10-27")
                    for _ in range(2):
                        page.start_export()
                        deadline = time.monotonic() + 15
                        while page.busy and time.monotonic() < deadline:
                            app.update(); time.sleep(.01)
                        self.assertFalse(page.busy)
                    self.assertIn("NATIVE_TK_RETRY_OK", page.log_console.contents())
                self.assertEqual(marker.read_text().splitlines(), ["executed"])
                tasks = client.call("tasks")
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["state"], "succeeded")
            finally:
                if registry is not None: registry.shutdown(timeout=5)
                if app is not None: app.destroy()
                if process.poll() is None:
                    try: client.call("shutdown", {"cancel": True})
                    finally: process.stdin.close()
                process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if not stream.closed: stream.close()

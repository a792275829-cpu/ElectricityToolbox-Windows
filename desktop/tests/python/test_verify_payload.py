"""Windows payload freshness gate used by packaging/windows/build.ps1."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/windows"))
import verify_payload

OLD = 1_000_000_000  # 2001-09-09, safely before any freshly built helper


class PayloadVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="payload-verify-")
        self.root = Path(self.temp.name)
        self.helper = self.root / "desktop/src-tauri/payload/app/电力工具箱/bin/toolbox-service.exe"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b"MZhelper")
        self.manifest = self.root / "desktop/src-tauri/payload/manifest.json"
        self.source = self.root / "native/price-xlsx/src/helper.rs"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"fn main() {}")
        os.utime(self.source, (OLD, OLD))
        (self.root / "desktop/package.json").write_text(
            json.dumps({"version": "0.1.35"}), encoding="utf-8")
        self.write_manifest("0.1.35", hashlib.sha256(self.helper.read_bytes()).hexdigest())

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, version, helper_hash):
        self.manifest.write_text(json.dumps({
            "version": version, "layout": 2,
            "files": {"电力工具箱/bin/toolbox-service.exe": helper_hash},
        }), encoding="utf-8")

    def test_freshly_rebuilt_payload_passes(self):
        self.assertIn("PAYLOAD_FRESH=OK", verify_payload.verify(self.root))

    def test_payload_from_previous_version_is_rejected(self):
        self.write_manifest("0.1.34", hashlib.sha256(self.helper.read_bytes()).hexdigest())
        with self.assertRaisesRegex(RuntimeError, "版本为 0.1.34"):
            verify_payload.verify(self.root)

    def test_helper_replaced_without_rebuilding_manifest_is_rejected(self):
        self.helper.write_bytes(b"MZtampered")
        with self.assertRaisesRegex(RuntimeError, "哈希不一致"):
            verify_payload.verify(self.root)

    def test_helper_older_than_rust_source_is_rejected(self):
        os.utime(self.helper, (OLD, OLD))
        os.utime(self.source, None)
        with self.assertRaisesRegex(RuntimeError, "早于 Rust 源码"):
            verify_payload.verify(self.root)

    def test_manifest_without_helper_is_rejected(self):
        self.manifest.write_text(json.dumps({"version": "0.1.35", "files": {}}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "未唯一记录"):
            verify_payload.verify(self.root)

    def test_missing_manifest_is_rejected(self):
        self.manifest.unlink()
        with self.assertRaisesRegex(RuntimeError, "manifest.json 缺失"):
            verify_payload.verify(self.root)

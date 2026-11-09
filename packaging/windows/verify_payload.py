"""Reject a Windows payload that was not rebuilt from the current sources.

prepare_payload.py regenerates payload/manifest.json and recompiles the Rust
helper (toolbox-service.exe). Packaging an older payload silently ships the
previous update logic even when the app version has already been bumped.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER_SUFFIX = "/bin/toolbox-service.exe"


def verify(root: Path = ROOT) -> str:
    payload = root / "desktop/src-tauri/payload"
    manifest_path = payload / "manifest.json"
    version = json.loads((root / "desktop/package.json").read_text(encoding="utf-8"))["version"]
    if not manifest_path.is_file():
        raise RuntimeError("payload/manifest.json 缺失；请先运行 build.ps1（prepare_payload.py）重新生成 payload")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        raise RuntimeError(
            f"payload 版本为 {manifest.get('version')}，应用版本为 {version}；"
            "请重新运行 build.ps1 重编译 toolbox-service.exe"
        )
    files = manifest.get("files") or {}
    keys = [name for name in files if name.endswith(HELPER_SUFFIX)]
    if len(keys) != 1:
        raise RuntimeError("payload 清单未唯一记录内置 toolbox-service.exe")
    helper = payload / "app" / keys[0]
    if not helper.is_file():
        raise RuntimeError("内置 toolbox-service.exe 缺失；请重新运行 build.ps1")
    actual = hashlib.sha256(helper.read_bytes()).hexdigest()
    if actual != files[keys[0]]:
        raise RuntimeError("内置 toolbox-service.exe 与 payload 清单哈希不一致；请重新运行 build.ps1")
    sources = sorted((root / "native/price-xlsx/src").glob("*.rs"), key=lambda path: path.stat().st_mtime)
    if sources and helper.stat().st_mtime < sources[-1].stat().st_mtime:
        raise RuntimeError(
            f"内置 toolbox-service.exe 早于 Rust 源码 {sources[-1].name}；"
            "请重新运行 build.ps1 重编译"
        )
    return f"PAYLOAD_FRESH=OK (version {version}, helper matches manifest and Rust sources)"


if __name__ == "__main__":
    try:
        print(verify())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"PAYLOAD_FRESH=FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

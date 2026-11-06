"""One version source for native status bars, logs and standalone tools."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re


def application_version() -> str:
    def valid(value):
        return isinstance(value, str) and re.fullmatch(r'v?[0-9]+(?:\.[0-9]+){1,3}', value.strip())
    value = os.environ.get('TOOLBOX_APP_VERSION', '')
    if valid(value):
        return value.strip().removeprefix('v')
    root = Path(__file__).resolve().parent
    for path in (root/'desktop/package.json', root/'desktop/src-tauri/tauri.conf.json',
                 root.parent/'manifest.json', root.parent/'state/installed.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))['version']
            if valid(value):
                return value.strip().removeprefix('v')
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return '未知'

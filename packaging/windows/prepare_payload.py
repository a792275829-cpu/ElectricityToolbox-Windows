"""Assemble a self-contained Windows x64 runtime using the shared source manifest."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging/tauri'))
import prepare_payload as common
import build_support

def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True,
                   env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}, **kwargs)


def prepare(python_root: Path, forecast_workbook: Path | None = None):
    if sys.platform != 'win32':
        raise RuntimeError('Windows payload must be built and tested on Windows x64.')
    version_info = subprocess.check_output(['rustc', '-vV'], text=True)
    target = next(line.split(': ', 1)[1] for line in version_info.splitlines() if line.startswith('host: '))
    if target not in {'x86_64-pc-windows-msvc', 'x86_64-pc-windows-gnu'}:
        raise ValueError(f'Unsupported Windows build target: {target}')
    python_root = python_root.resolve()
    if not (python_root / 'python.exe').is_file() or (python_root / 'pyvenv.cfg').exists():
        raise ValueError('--python-root must point to a standalone CPython distribution, not a venv.')
    # Check the source distribution before exporting anything.
    run([python_root / 'python.exe', '-c',
         'import struct,sys,tkinter; assert struct.calcsize("P")==8; assert sys.version_info>=(3,11); t=tkinter.Tk();t.withdraw();t.destroy()'])
    payload = ROOT / 'desktop/src-tauri/payload'
    source, runtime = payload / 'app', payload / 'runtime'
    # These are generated trees only; neither user data nor the source checkout.
    source.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    build_support.export_source(source, predicate=common.runtime_source)
    binary = source / '500kV断面分析/bin/toolbox-price-xlsx.exe'
    run([sys.executable, ROOT / 'native/price-xlsx/build.py', '--output', binary,
         '--service-output', source / '电力工具箱/bin/toolbox-service.exe'])
    common.validate_runtime_assets(source)
    defaults = payload / 'defaults'
    common.prepare_defaults(source, defaults)
    if forecast_workbook is not None:
        common.prepare_forecast_seed(defaults, forecast_workbook)
    # No invented business history: the user can select their real forecast workbook.
    bundled_python = runtime / 'python'
    if bundled_python.exists():
        shutil.rmtree(bundled_python)
    shutil.copytree(python_root, bundled_python,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages'))
    uv = shutil.which('uv') or str(Path(sys.executable).parent / 'uv.exe')
    python = bundled_python / 'python.exe'
    run([uv, 'pip', 'install', '--python', python, '--system', '--break-system-packages', '--only-binary', ':all:',
         '-r', ROOT / 'packaging/windows/requirements.txt'])
    cache = ROOT / 'dist/windows-build'
    name = f'node-v{build_support.NODE_VERSION}-win-x64'
    base = f'https://nodejs.org/dist/v{build_support.NODE_VERSION}/'
    checksums = build_support.download(base + 'SHASUMS256.txt', cache / 'SHASUMS256.txt')
    expected = next(line.split()[0] for line in checksums.read_text().splitlines()
                    if line.split()[-1] == name + '.zip')
    archive = build_support.download(base + name + '.zip', cache / (name + '.zip'), expected)
    unpack = cache / 'node'
    build_support.extract(archive, unpack)
    shutil.copytree(unpack / name, runtime / 'node', dirs_exist_ok=True)
    uploader = source / '电力工具脚本/private-data-uploader-tool'
    # Match the Python browser revision, as in the macOS payload builder.
    package = json.loads((uploader / 'package.json').read_text(encoding='utf-8'))
    package['dependencies']['playwright'] = build_support.PLAYWRIGHT_VERSION
    (uploader / 'package.json').write_text(json.dumps(package, indent=2), encoding='utf-8')
    run([runtime / 'node/node.exe', runtime / 'node/node_modules/npm/bin/npm-cli.js',
         'install', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=uploader)
    browser_env = {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': str(runtime / 'ms-playwright')}
    subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'],
                   check=True, env=browser_env)
    registry = bundled_python / 'Lib/site-packages/playwright/driver/package/browsers.json'
    if json.loads(registry.read_text()) != json.loads((uploader / 'node_modules/playwright-core/browsers.json').read_text()):
        raise RuntimeError('Python and Node Playwright browser versions differ.')
    bootstrap = payload / 'bootstrap'
    bootstrap.mkdir(exist_ok=True)
    for name in ('prepare_workspace.py', 'workspace_layout.py'):
        shutil.copy2(ROOT / 'packaging/tauri' / name, bootstrap / name)
    files = {path.relative_to(source).as_posix(): build_support.sha256(path)
             for path in sorted(source.rglob('*')) if path.is_file()}
    hashes = {path.relative_to(defaults).as_posix(): build_support.sha256(path)
              for path in sorted(defaults.rglob('*')) if path.is_file()}
    version = json.loads((ROOT / 'desktop/package.json').read_text())['version']
    manifest = dict(layout=2, version=version, target=target, files=files,
                    defaults=list(hashes), default_hashes=hashes,
                    build=hashlib.sha256(json.dumps(dict(files=files, default_hashes=hashes), sort_keys=True).encode()).hexdigest())
    (payload / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (runtime / 'target.txt').write_text(target)
    run([python, '-c', 'import tkinter,openpyxl,playwright,pandas,sklearn,PIL,pdfplumber,pypdfium2,docx;print("Windows runtime OK")'])
    print(f'Windows payload ready: {payload} ({len(files)} managed files)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--forecast-workbook', type=Path)
    args = parser.parse_args()
    prepare(args.python_root, args.forecast_workbook)
